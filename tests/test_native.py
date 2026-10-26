import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import pytest
import yaml
from bactologix.catalog import PIPELINES
from bactologix.engine import Cancelled, Context, controller_key
from bactologix.runs import (bind_paths, clone_runs, create_run, import_samples, parse_config,
                             register_sample, stage_inputs, validate_launch)
from bactologix.store import Store


@pytest.fixture
def workspace(tmp_path):
    return Store(tmp_path / "workspace with spaces")


@pytest.fixture
def installation(workspace, tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Hybas.smk").write_text("rule all:\n    input: []\n")
    (source / "config.yaml").write_text("threads: 1\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setitem(PIPELINES["hybas"], "commit", commit)
    item = {"id": "test-install", "tool": "hybas", "source": str(source), "controller": str(tmp_path / "controller"),
            "ready": True, "version": {"commit": commit, "ref": "test"}}
    workspace.put("installs", item["id"], item)
    workspace.put("settings", "active-hybas", item["id"])
    return item


@pytest.fixture
def sample(workspace, tmp_path):
    folder = tmp_path / "sample ; literal"
    raw = folder / "raw/illumina"
    raw.mkdir(parents=True)
    (raw / "sample_R1.fastq").write_text("@test\nACGT\n+\nIIII\n")
    (raw / "sample_R2.fastq").write_text("@test\nACGT\n+\nIIII\n")
    return register_sample(workspace, "sample", str(folder), "illumina_only")


def test_yaml_rejects_recursive_and_non_mapping():
    for text in ("- item", "value: &a [*a]", "when: 2020-01-01", "number: .nan"):
        with pytest.raises(ValueError):
            parse_config(text)
    assert parse_config("custom: {list: [true, 4, null]}\n")["custom"]["list"] == [True, 4, None]


def test_durable_queue_claim_and_cancel(workspace):
    one = workspace.submit("smoke", {})
    two = workspace.submit("smoke", {})
    workspace.cancel(one)
    assert Store(workspace.home).job(one)["status"] == "cancelled"
    assert workspace.claim()["id"] == two
    assert workspace.claim() is None


def test_sample_import_atomic_on_invalid_row(workspace, tmp_path):
    text = f"sample_id,input_folder\nfirst,{tmp_path}\ninvalid,/missing-bactologix-test-path\n"
    with pytest.raises(ValueError):
        import_samples(workspace, text)
    assert workspace.all("samples") == []


def test_duplicate_sample_does_not_overwrite(workspace, sample, tmp_path):
    with pytest.raises(ValueError):
        register_sample(workspace, "SAMPLE", str(tmp_path), "hybrid")
    assert workspace.get("samples", "sample") == sample


def test_outputs_isolated_config_preserved(workspace, installation, sample):
    cfg = "threads: 4\ncustom: {unknown: [1, true, null]}\nkraken_decision_mode: aware\n"
    run = create_run(workspace, "hybas", sample, cfg)
    frozen = yaml.safe_load(run["config"])
    assert frozen["custom"] == {"unknown": [1, True, None]}
    assert str(workspace.home) in frozen["base_dir"]
    assert (Path(run["output"]) / "raw/illumina/sample_R1.fastq").is_symlink()
    assert not (Path(sample["path"]) / "work").exists()
    with pytest.raises(ValueError, match="dry-run"):
        validate_launch(workspace, run["id"], "run")
    validate_launch(workspace, run["id"], "dry-run")
    Path(run["folder"], "config.yaml").write_text("changed: true\n")
    with pytest.raises(ValueError, match="configuration changed"):
        validate_launch(workspace, run["id"], "dry-run")


def test_input_drift_blocks_execution(workspace, installation, sample):
    run = create_run(workspace, "hybas", sample, "threads: 1")
    path = Path(sample["path"]) / "raw/illumina/sample_R1.fastq"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="Input changed"):
        validate_launch(workspace, run["id"], "dry-run")


def test_clone_preserves_parent_and_custom_fields(workspace, installation, sample):
    parent = create_run(workspace, "hybas", sample, "kraken_decision_mode: aware\ncustom: {keep: true}\npolish_mode: none")
    before = copy.deepcopy(parent)
    children = clone_runs(workspace, parent["id"], True, ["none", "tripolish"])
    assert workspace.get("runs", parent["id"]) == before
    assert len({parent["output"], *(c["output"] for c in children)}) == 3
    for child in children:
        cfg = yaml.safe_load(child["config"])
        assert cfg["kraken_decision_mode"] == "off"
        assert cfg["run_kraken"] is False
        assert cfg["custom"]["keep"] is True
        assert child["parent"] == parent["id"]


def test_missing_input_cleanup_preserves_original(workspace, installation, sample):
    wrong = dict(sample, mode="hybrid")
    with pytest.raises(ValueError, match="No FASTQ"):
        create_run(workspace, "hybas", wrong, "threads: 1")
    assert list((workspace.home / "runs").iterdir()) == []
    assert (Path(sample["path"]) / "raw/illumina/sample_R1.fastq").is_file()


def test_resource_path_contract(workspace):
    with pytest.raises(ValueError, match="absolute"):
        bind_paths({"modules": {"mobsuite": {"database": "relative/db"}}}, "basis", workspace, {})
    cfg = bind_paths({"deep": {"placement_cache_dir": "/tmp/cache"}}, "basis", workspace, {})
    assert cfg["deep"]["placement_cache_dir"] == "/tmp/cache"
    assert Path(cfg["resources"]["directory"]).is_absolute()


def test_controller_identity_uses_content_and_pipeline():
    assert controller_key("hybas", b"a") == controller_key("hybas", b"a")
    assert len({controller_key("hybas", b"a"), controller_key("hybas", b"b"), controller_key("basis", b"a")}) == 3


def test_command_preserves_literal_arguments(workspace, tmp_path):
    jid = workspace.submit("smoke", {})
    context = Context(workspace, workspace.job(jid))
    target = tmp_path / "not shell ; $(touch bad)"
    try:
        context.command([sys.executable, "-c", "import sys;from pathlib import Path;Path(sys.argv[1]).write_text('ok')", str(target)])
    finally:
        context.log.close()
    assert target.read_text() == "ok"
    assert not (tmp_path / "bad").exists()


def test_stop_reaches_running_process(workspace):
    jid = workspace.submit("smoke", {})
    workspace.update_job(jid, status="running")
    context = Context(workspace, workspace.job(jid))
    timer = threading.Timer(0.5, workspace.cancel, args=(jid,))
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            context.command([sys.executable, "-c", "import time;time.sleep(120)"])
    finally:
        timer.join()
        context.log.close()
    assert time.monotonic() - started < 5



def test_database_selection_applies_to_new_drafts_preserves_previous(workspace, installation, sample, tmp_path):
    from bactologix import databases
    locations = []
    for name in ['first', 'second']:
        folder = tmp_path / name
        folder.mkdir()
        for index in ['hash.k2d', 'opts.k2d', 'taxo.k2d']:
            (folder / index).write_text('software fixture')
        locations.append(folder)
    before = None
    for folder in locations:
        text = databases.save(workspace, 'hybas', {'kraken': str(folder)}, databases.panel(workspace, 'hybas')['revision'])
        run = create_run(workspace, 'hybas', sample, text)
        cfg = yaml.safe_load(run['config'])
        assert (Path(cfg['db_root']) / cfg['kraken_db_name']).resolve() == folder
        if before is None:
            before = run
    old = workspace.get('runs', before['id'])
    assert old['config'] == before['config']
    oldcfg = yaml.safe_load(old['config'])
    assert (Path(oldcfg['db_root']) / oldcfg['kraken_db_name']).resolve() == locations[0]
    validate_launch(workspace, before['id'], 'dry-run')


def test_remove_sample_preserves_files_runs_jobs_and_reregistration(workspace, installation, sample, monkeypatch):
    from bactologix.ui_backend import dispatch, snapshot
    from bactologix.runs import check_run_files, command
    run = create_run(workspace, 'hybas', sample, 'threads: 1')
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'dry-run'})
    before_job = workspace.job(jid)
    original = {p: p.read_bytes() for p in Path(sample['path']).rglob('*') if p.is_file()}
    frozen = {p: p.read_bytes() for p in Path(run['folder']).rglob('*') if p.is_file()}
    result = dispatch(workspace, 'remove_sample', {'id': sample['id'], 'sample': sample})
    assert result['removed_sample'] == sample['id'] and result['keep_modal']
    assert not snapshot(workspace)['samples']
    assert workspace.get('runs', run['id']) == run and workspace.job(jid) == before_job
    assert all(p.read_bytes() == content for p, content in {**original, **frozen}.items())
    check_run_files(run)
    monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/fixture/conda')
    assert '--dry-run' in command(workspace, run, 'dry-run')
    with pytest.raises(ValueError, match='no longer exists'):
        dispatch(workspace, 'create_runs', {'ids': [sample['id']], 'tool': 'hybas', 'cores': 1, 'memory_mb': 16000})
    registered = register_sample(workspace, sample['id'], sample['path'], sample['mode'])
    assert registered['path'] == sample['path']
    assert workspace.get('runs', run['id']) == run


def test_stale_removal_cannot_unregister_replacement_sample(workspace, sample, tmp_path):
    from bactologix.ui_backend import dispatch
    dispatch(workspace, 'remove_sample', {'id': sample['id'], 'sample': sample})
    replacement = register_sample(workspace, sample['id'], str(tmp_path), sample['mode'])
    with pytest.raises(ValueError, match='registration changed'):
        dispatch(workspace, 'remove_sample', {'id': sample['id'], 'sample': sample})
    assert workspace.get('samples', sample['id']) == replacement


def test_remove_sample_archives_only_matching_idle_runs(workspace, installation, sample, tmp_path):
    from bactologix.runs import remove_sample
    from bactologix.ui_backend import snapshot
    idle = create_run(workspace, 'hybas', sample, '{}')
    busy = create_run(workspace, 'hybas', sample, '{}')
    other = create_run(workspace, 'hybas', sample, '{}')
    # A reused sample ID must not hide historical runs from another input folder.
    other['sample'] = {**sample, 'path': str(tmp_path / 'different-inputs')}
    workspace.put('runs', other['id'], other)
    jid = workspace.submit('workflow', {'run_id': busy['id'], 'action': 'dry-run'})
    result = remove_sample(workspace, sample['id'], sample)
    assert result == {'archived': [idle['id']], 'active': [busy['id']]}
    rows = {r['id']: r for r in snapshot(workspace)['runs']}
    assert rows[idle['id']]['archived']
    assert not rows[busy['id']]['archived'] and not rows[other['id']]['archived']
    assert workspace.get('runs', idle['id']) == idle
    assert workspace.job(jid)['status'] == 'queued'
    register_sample(workspace, sample['id'], sample['path'], sample['mode'])
    assert workspace.get('run_visibility', idle['id'])['archived']


def test_archive_restore_preserves_run_and_blocks_stale_launch(workspace, installation, sample):
    from bactologix.ui_backend import dispatch, snapshot
    run = create_run(workspace, 'hybas', sample, '{}')
    files = {p: p.read_bytes() for p in Path(run['folder']).rglob('*') if p.is_file()}
    result = dispatch(workspace, 'archive_runs', {'ids': [run['id']]})
    assert result['archived_runs'] == [run['id']]
    assert snapshot(workspace)['runs'][0]['archived']
    with pytest.raises(ValueError, match='Restore this run'):
        validate_launch(workspace, run['id'], 'dry-run')
    # This second guard covers archiving between validation and queue insertion.
    with pytest.raises(ValueError, match='Restore this run'):
        workspace.submit('workflow', {'run_id': run['id'], 'action': 'dry-run'})
    dispatch(workspace, 'restore_runs', {'ids': [run['id']]})
    assert not snapshot(workspace)['runs'][0]['archived']
    assert workspace.get('runs', run['id']) == run
    assert all(p.read_bytes() == data for p, data in files.items())
    validate_launch(workspace, run['id'], 'dry-run')
    assert workspace.submit('workflow', {'run_id': run['id'], 'action': 'dry-run'})


@pytest.mark.parametrize('state', ['queued', 'running', 'missing'])
def test_archive_batch_is_atomic_for_active_or_missing_runs(workspace, installation, sample, state):
    from bactologix.runs import archive_runs
    idle = create_run(workspace, 'hybas', sample, '{}')
    other = create_run(workspace, 'hybas', sample, '{}')
    if state == 'missing':
        other['id'] = 'missing-run'
    else:
        jid = workspace.submit('workflow', {'run_id': other['id'], 'action': 'dry-run'})
        workspace.update_job(jid, status=state)
    with pytest.raises(ValueError):
        archive_runs(workspace, [idle['id'], other['id']])
    assert workspace.all('run_visibility') == []
