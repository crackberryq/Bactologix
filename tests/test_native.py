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

