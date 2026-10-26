"""Filesystem and adoption contracts: keep existing data and envs intact."""
import json
from pathlib import Path
import subprocess

import pytest

from bactologix.catalog import PIPELINES
from bactologix.engine import Context, connect_existing
from bactologix.local import browse_folders, discover_environments, discover_installations, inspect_installation, local_path
from bactologix.runs import command, create_run, register_sample
from bactologix.store import Store
from bactologix.ui_backend import dispatch


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "HybAs checkout with spaces"
    root.mkdir()
    (root / "Hybas.smk").write_text('rule all:\n    input: []\n')
    (root / "environment.yaml").write_text('dependencies: [python=3.11, snakemake=9.6.2]\n')
    (root / "config.yaml").write_text('threads: 2\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                    'commit', '-qm', 'Local fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', commit)
    return root


def test_browse_paging_filter_and_symlink(tmp_path):
    store = Store(tmp_path / 'workspace')
    root = tmp_path / 'samples'; root.mkdir()
    for n in range(105):
        (root / f'sample {n:03}').mkdir()
    (root / '.hidden').mkdir()
    (root / 'reads.fastq').write_text('data')
    (root / 'shortcut').symlink_to(root / 'sample 001', target_is_directory=True)
    first = browse_folders(store, root)
    second = browse_folders(store, root, offset=100)
    assert len(first['folders']) == 100 and first['more']
    assert len(second['folders']) == 6 and not second['more']
    assert not set(x['path'] for x in first['folders']) & set(x['path'] for x in second['folders'])
    hidden = browse_folders(store, root, query='hidden', show_hidden=True)
    assert hidden['folders'][0]['name'] == '.hidden'
    assert browse_folders(store, root / 'shortcut')['path'] == str(root / 'sample 001')
    with pytest.raises(ValueError, match='does not exist'):
        browse_folders(store, root / 'missing')
    with pytest.raises(ValueError, match='absolute'):
        browse_folders(store, 'relative')


def test_windows_paths_for_wsl(monkeypatch):
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu-22.04')
    assert str(local_path(r'C:\Users\jimi\sample')) == '/mnt/c/Users/jimi/sample'
    assert str(local_path(r'\\wsl.localhost\Ubuntu-22.04\home\jimi')) == '/home/jimi'
    with pytest.raises(ValueError, match='distribution'):
        local_path(r'\\wsl$\Other-Distro\home\jimi')


def test_discovery_is_bounded_and_never_follows_links(tmp_path, checkout):
    store = Store(tmp_path / 'workspace')
    (tmp_path / 'cycle').symlink_to(tmp_path, target_is_directory=True)
    skipped = tmp_path / 'outputs' / 'nested'; skipped.mkdir(parents=True)
    (skipped / 'Hybas.smk').write_text('not a checkout')
    result = discover_installations(store, 'hybas', str(tmp_path))
    assert [x['path'] for x in result['candidates']] == [str(checkout)]
    assert result['visited'] < 30
    assert result['candidates'][0]['supported']


def test_inspection_recognizes_edits_and_rejects_wrong_pipeline(checkout):
    (checkout / 'config.yaml').write_text('threads: 99\n')
    info = inspect_installation(checkout, 'hybas')
    assert info['modified'] and info['supported']
    with pytest.raises(ValueError, match='BaSIS.smk'):
        inspect_installation(checkout, 'basis')


def test_env_discovery_reads_conda_metadata_without_running_python(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    prefix = tmp_path / 'runner'; meta = prefix / 'conda-meta'; meta.mkdir(parents=True)
    (meta / 'python-3.11.9-0.json').write_text(json.dumps({'name': 'python', 'version': '3.11.9'}))
    (meta / 'snakemake-minimal-9.6.2-0.json').write_text(json.dumps({'name': 'snakemake-minimal', 'version': '9.6.2'}))
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/conda')
    calls = []
    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({'envs': [str(prefix)]}), '')
    monkeypatch.setattr('bactologix.local.subprocess.run', fake_run)
    row = discover_environments(store)['environments'][0]
    assert row['snakemake'] == '9.6.2' and row['python'] == '3.11.9'
    assert calls == [['/conda', 'env', 'list', '--json']]


def test_connect_copies_committed_source_reuses_runner_and_cache(tmp_path, checkout, monkeypatch):
    store = Store(tmp_path / 'workspace')
    prefix = tmp_path / 'existing runner'; (prefix / 'conda-meta').mkdir(parents=True)
    (prefix / 'bin').mkdir(); (prefix / 'bin/python').write_text('existing environment sentinel')
    cache = checkout / '.snakemake/conda'; cache.mkdir(parents=True)
    (cache / 'sentinel').write_text('cached tools')
    (checkout / 'config.yaml').write_text('threads: 99\n')
    (checkout / 'results').mkdir(); (checkout / 'results/report.html').write_text('original report')
    info = inspect_installation(checkout, 'hybas')
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/conda')
    checks = []
    monkeypatch.setattr('bactologix.engine.check_controller', lambda ctx, conda, p, t, requirements: checks.append((str(p), t)))
    payload = dict(tool='hybas', path=str(checkout), commit=info['commit'], controller=str(prefix), rule_cache=str(cache))
    jid = store.submit('connect', payload)
    ctx = Context(store, store.job(jid))
    try:
        connect_existing(ctx)
    finally:
        ctx.log.close()
    item = store.get('installs', store.get('settings', 'active-hybas'))
    assert item['ready'] and item['controller'] == str(prefix)
    assert item['rule_cache'] == str(cache)
    assert checks == [(str(prefix), 'hybas')]
    source = Path(item['source'])
    assert (source / 'config.yaml').read_text() == 'threads: 2\n'
    assert not (source / 'results').exists()
    assert (checkout / 'config.yaml').read_text() == 'threads: 99\n'
    assert (checkout / 'results/report.html').read_text() == 'original report'
    assert (cache / 'sentinel').read_text() == 'cached tools'
    assert (prefix / 'bin/python').read_text() == 'existing environment sentinel'
    log = store.log_path(jid).read_text()
    assert 'github.com' not in log and 'env create' not in log
    sample = tmp_path / 'sample'; raw = sample / 'raw/illumina'; raw.mkdir(parents=True)
    (raw / 'test.fastq').write_text('@test\nA\n+\nI\n')
    run = create_run(store, 'hybas', register_sample(store, 'test', sample, 'illumina_only'), 'threads: 2')
    argv = command(store, run, 'dry-run')
    assert argv[argv.index('--conda-prefix') + 1] == str(cache)


def test_connect_revalidates_commit_and_never_bypasses_adapter(tmp_path, checkout, monkeypatch):
    store = Store(tmp_path / 'workspace')
    info = inspect_installation(checkout, 'hybas')
    queued = []
    monkeypatch.setattr('bactologix.ui_backend.enqueue', lambda *args: queued.append(args))
    with pytest.raises(ValueError, match='changed'):
        dispatch(store, 'connect_installation', dict(tool='hybas', path=str(checkout), commit='0'*40))
    with pytest.raises(ValueError, match='environment folder'):
        dispatch(store, 'connect_installation', dict(tool='hybas', path=str(checkout), commit=info['commit'], runner_mode='existing', controller=''))
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', '0'*40)
    with pytest.raises(ValueError, match='compatibility'):
        dispatch(store, 'connect_installation', dict(tool='hybas', path=str(checkout), commit=info['commit']))
    assert queued == [] and store.all('installs') == []


def test_failed_controller_check_does_not_activate(tmp_path, checkout, monkeypatch):
    store = Store(tmp_path / 'workspace')
    prefix = tmp_path / 'runner'; (prefix / 'conda-meta').mkdir(parents=True)
    (prefix / 'bin').mkdir(); (prefix / 'bin/python').touch()
    info = inspect_installation(checkout, 'hybas')
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/conda')
    def fail(*args):
        raise RuntimeError('Wrong Snakemake version')
    monkeypatch.setattr('bactologix.engine.check_controller', fail)
    jid = store.submit('connect', dict(tool='hybas', path=str(checkout), commit=info['commit'], controller=str(prefix)))
    ctx = Context(store, store.job(jid))
    try:
        with pytest.raises(RuntimeError, match='Wrong Snakemake'):
            connect_existing(ctx)
    finally:
        ctx.log.close()
    assert not store.get('settings', 'active-hybas')
    assert not store.all('installs')[0]['ready']
    assert (prefix / 'bin/python').exists()
