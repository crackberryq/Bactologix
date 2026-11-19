"""Existing shared Kraken databases through the location picker, run editor and swap adviser."""
from pathlib import Path

import pytest
import yaml

from bactologix import databases, swap_adviser
from bactologix.store import Store
from bactologix.ui_backend import dispatch
from test_databases import kraken
from test_native import installation, sample
from test_run_config import patch
from test_swap_adviser import host, storage


@pytest.fixture(params=['dev', 'stable'])
def workspace(request, tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', request.param)
    # The native installation and sample fixtures use this same folder.
    store = Store(tmp_path)
    store.put('configs', 'hybas', 'db_root: db\nrun_kraken: true\n')
    return store


@pytest.fixture
def shared_db(tmp_path_factory, monkeypatch):
    # Exercise Explorer-to-WSL conversion without requiring a real Windows mount.
    folder = kraken(tmp_path_factory.mktemp('external-drive') / 'Shared data' / 'Kraken')
    windows = r'C:\Shared data\Kraken'
    mount = Path('/mnt/c/Shared data/Kraken')
    real_resolve = Path.resolve

    def resolve(path, strict=False):
        if path.is_relative_to(mount):
            path = folder / path.relative_to(mount)
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, 'resolve', resolve)
    return windows, folder


@pytest.fixture
def capacity(monkeypatch):
    monkeypatch.setattr(swap_adviser.hardware, 'inventory', lambda *args, **kwargs: host())
    monkeypatch.setattr(swap_adviser, '_storage', lambda *args: storage())


@pytest.fixture
def approved_installation(installation, monkeypatch):
    # The native runner fixture uses a local Git commit.
    commit = installation['version']['commit']

    def approve(_store, tool, sha):
        if (tool, sha) == ('hybas', commit):
            return {'configuration_schema': 'hybas-v1'}
        return None

    monkeypatch.setattr('bactologix.dev_catalog.approval', approve)
    return installation


def test_picker_existing_kraken_is_reused_by_drafts_and_saved_run_swap(
        workspace, shared_db, capacity, approved_installation, sample):
    windows, folder = shared_db
    assert not folder.is_relative_to(workspace.home)
    before = {p.name: p.stat() for p in folder.iterdir()}
    check = dispatch(workspace, 'check_database', {'tool': 'hybas', 'id': 'kraken', 'path': windows})
    assert check['database_check']['path'] == str(folder)

    saved = dispatch(workspace, 'save_databases', {
        'tool': 'hybas', 'changes': {'kraken': windows}, 'revision': databases.panel(workspace, 'hybas')['revision']})
    config = yaml.safe_load(saved['config']['text'])
    alias = Path(config['db_root']) / config['kraken_db_name']
    assert Path(config['db_root']).is_relative_to(workspace.home)
    assert alias.samefile(folder)
    assert (Path(config['db_root']) / 'busco' / config['busco_lineage']).resolve().is_relative_to(workspace.home)

    defaults = dispatch(workspace, 'recommend_swap', {'tool': 'hybas', 'memory_mb': 8192})
    assert defaults['swap_recommendation']['database']['resolved_path'] == str(folder)
    run = dispatch(workspace, 'create_runs', {
        'tool': 'hybas', 'ids': [sample['id']], 'cores': 1, 'memory_mb': 8192})['detail']
    check = next(row for row in run['readiness']['rows'] if row['id'] == 'kraken_memory')
    assert check['resolved_path'] == str(folder)
    assert check['core_bytes'] == sum(stat.st_size for stat in before.values())
    assert next(row for row in run['database_paths'] if row['id'] == 'kraken')['path'] == str(folder)

    # A later change of defaults must leave the existing run bound to its original DB.
    reset = dispatch(workspace, 'save_databases', {
        'tool': 'hybas', 'changes': {'kraken': None}, 'revision': databases.panel(workspace, 'hybas')['revision']})
    assert yaml.safe_load(reset['config']['text'])['db_root'] != str(alias.parent)
    existing = dispatch(workspace, 'recommend_swap', {'tool': 'hybas', 'run_id': run['id'], 'memory_mb': 8192})
    assert existing['swap_recommendation']['database']['resolved_path'] == str(folder)
    assert {p.name: (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()} == {
        name: (stat.st_ino, stat.st_size, stat.st_mtime_ns) for name, stat in before.items()}


def test_explorer_path_in_editor_is_used_before_and_after_saving(
        workspace, shared_db, capacity, approved_installation, sample):
    windows, folder = shared_db
    original = workspace.get('configs', 'hybas')
    config = {'tool': 'hybas', 'text': original, 'changes': patch(kraken_db_name=windows)}
    result = dispatch(workspace, 'recommend_swap', {
        'tool': 'hybas', 'memory_mb': 8192, 'config': config})['swap_recommendation']
    assert result['available'], result['message']
    assert result['database']['resolved_path'] == str(folder)
    assert workspace.get('configs', 'hybas') == original

    result = dispatch(workspace, 'save_default_config', {
        **config, 'revision': databases.panel(workspace, 'hybas')['revision']})
    assert result['default_config_saved']
    run = dispatch(workspace, 'create_runs', {
        'tool': 'hybas', 'ids': [sample['id']], 'cores': 1, 'memory_mb': 8192})['detail']
    frozen = yaml.safe_load(Path(run['folder'], 'config.yaml').read_text())
    assert (Path(frozen['db_root']) / frozen['kraken_db_name']).resolve() == folder
    report = next(row for row in run['readiness']['rows'] if row['id'] == 'kraken_memory')
    assert report['resolved_path'] == str(folder)


def test_missing_index_is_rejected_without_changing_defaults(workspace, shared_db):
    windows, folder = shared_db
    revision = databases.panel(workspace, 'hybas')['revision']
    (folder / 'hash.k2d').unlink()
    with pytest.raises(ValueError, match=r'hash\.k2d'):
        dispatch(workspace, 'save_databases', {
            'tool': 'hybas', 'changes': {'kraken': windows}, 'revision': revision})
    assert databases.panel(workspace, 'hybas')['revision'] == revision


@pytest.mark.parametrize('workspace', ['dev'], indirect=True)
def test_related_workspace_cleanup_protects_shared_kraken_after_defaults_change(
        workspace, approved_installation, sample, tmp_path_factory, monkeypatch):
    from bactologix.runs import create_run
    from test_storage_cleanup import plan, saved_run

    with monkeypatch.context() as stable:
        stable.setenv('BACTOLOGIX_CHANNEL', 'stable')
        # Storage discovers sibling workspaces. Give the owner a private parent
        # so unrelated pytest cases cannot become part of the deletion review.
        owner = Store(tmp_path_factory.mktemp('database-owner') / 'stable')
    owner.put('settings', 'storage-other-workspaces', [str(workspace.home)])
    old = saved_run(owner)
    shared = kraken(Path(old['folder']) / 'shared-index')
    assert not shared.is_relative_to(workspace.home)
    revision = databases.panel(workspace, 'hybas')['revision']
    config = databases.save(workspace, 'hybas', {'kraken': str(shared)}, revision)

    before = plan(owner, ('run', old['id']))
    assert any(str(workspace.home) in reason for reason in before['blocked'])
    frozen = create_run(workspace, 'hybas', sample, config)
    databases.save(workspace, 'hybas', {'kraken': None}, databases.revision(config))

    after = plan(owner, ('run', old['id']))
    assert any(frozen['id'] in reason and str(workspace.home) in reason for reason in after['blocked'])
    assert (shared / 'hash.k2d').is_file()


def test_calculate_with_unsaved_picker_selection_uses_it_without_saving(workspace, shared_db, capacity):
    windows, folder = shared_db
    original = workspace.get('configs', 'hybas')
    revision = databases.panel(workspace, 'hybas')['revision']
    result = dispatch(workspace, 'recommend_swap', {
        'tool': 'hybas', 'memory_mb': 8192, 'kraken_path': windows})['swap_recommendation']
    assert result['available'], result['message']
    assert result['database']['resolved_path'] == str(folder)
    assert workspace.get('configs', 'hybas') == original
    assert databases.panel(workspace, 'hybas')['revision'] == revision
    assert not list((workspace.home / 'resources/hybas/locations').glob('*'))
    assert not workspace.jobs()
