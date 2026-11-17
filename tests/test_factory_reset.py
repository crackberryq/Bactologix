"""Destructive reset tests use isolated temporary workspaces and inert files only."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import factory_reset as reset, storage_cleanup as cleanup
from bactologix.store import Store


def file(path, data=b'preserved data'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv('BACTOLOGIX_CHANNEL', raising=False)
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda _: None)
    # Protected container infrastructure has the same uid. Process failures have
    # dedicated injected tests; ordinary fixtures model an idle workstation.
    monkeypatch.setattr('psutil.process_iter', lambda: [])
    return Store(tmp_path / 'workspace')


def run(store, key='run-one', **updates):
    folder = store.home / 'runs' / key
    file(folder / 'outputs/result.txt')
    row = {'id': key, 'tool': 'hybas', 'folder': str(folder), 'output': str(folder / 'outputs'),
           'config': '{}', 'sample': {}, 'inputs': [], 'completed': True, **updates}
    store.put('runs', key, row)
    return row


def installation(store):
    key = 'hybas-install'
    source = store.home / 'sources' / key
    file(source / 'Hybas.smk')
    controller = store.home / 'controllers' / ('hybas-' + 'a' * 20)
    file(controller / 'conda-meta/history')
    row = {'id': key, 'tool': 'hybas', 'source': str(source), 'controller': str(controller),
           'controller_origin': 'managed', 'controller_digest': 'a' * 20,
           'rule_cache': str(store.home / 'envs/hybas'), 'version': {'commit': 'b' * 40}, 'ready': True}
    store.put('installs', key, row)
    store.put('settings', 'active-hybas', key)
    return row


def database(store, *, parent=None, key='database-0123456789ab', redownload=False):
    parent = parent or store.home / 'resources/hybas'
    destination = parent / ('bactologix-hybas-kraken-' + key.removeprefix('database-'))
    payload = {'id': key, 'tool': 'hybas', 'resource': 'kraken', 'installation': 'hybas-install',
               'commit': 'b' * 40, 'parent': str(parent), 'destination': str(destination),
               'cores': 1, 'memory_mb': 1024, 'lineage': ''}
    # Submit through the regular queue so the matching operation is durable.
    if not store.get('installs', 'hybas-install'):
        installation(store)
    if redownload:
        from bactologix.database_redownload import SCHEMA, CHOICE, _digest
        payload['redownload_review'] = {'schema': SCHEMA, 'choice': CHOICE, 'request_sha256': _digest(payload)}
    jid = store.submit('database_redownload' if redownload else 'database_download', payload)
    store.update_job(jid, status='succeeded')
    root = file(destination / 'standard/hash.k2d').parent
    row = {**payload, 'request': payload, 'job': jid, 'status': 'completed', 'result_path': str(root)}
    store.put('database_downloads', key, row)
    return row


def execute(store, preview, *, confirm_db=False, cancel=lambda: None):
    jid = reset.request(store, preview['id'], preview['signature'], reset.CONFIRMATION, True,
                        reset.DATABASE_CONFIRMATION if confirm_db else '')
    store.update_job(jid, status='running')
    ctx = SimpleNamespace(store=store, job=store.job(jid), check_cancel=cancel, write=lambda _: None, lock_fd=None)
    try:
        result = reset.execute(ctx)
    except BaseException:
        store.update_job(jid, status='failed')
        raise
    store.update_job(jid, status='succeeded')
    return result


def paths(plan):
    return {Path(row['path']) for row in plan['paths']}


def test_default_reset_keeps_database_inputs_runtime_configs_and_receipt(store, tmp_path):
    item = installation(store)
    original = file(tmp_path / 'originals/reads.fastq')
    sample = {'id': 'sample', 'path': str(original.parent), 'inputs': [{'path': str(original)}]}
    store.put('samples', sample['id'], sample)
    saved = run(store, sample=sample, inputs=[{'path': str(original)}])
    db = database(store)
    config = 'db_root: ' + db['destination'] + '\nkraken_db_name: standard\n'
    store.put('configs', 'hybas', config)
    bootstrap = file(store.home / 'tools/miniforge/bin/conda')
    model = file(store.home / 'cache/huggingface/model.bin')
    package = file(store.home / 'cache/pip/download.whl')
    report = file(store.home / 'report-refreshes/edition/result.html')
    store.put('report_refreshes', 'edition', {'id': 'edition', 'run_id': saved['id'], 'path': str(report)})
    store.put('reports', 'edition', {'id': 'edition', 'run': saved['id'], 'path': str(report)})
    old_job = store.submit('smoke', {})
    store.update_job(old_job, status='succeeded')
    log = file(store.log_path(old_job))
    preview = reset.preview(store)
    assert not preview['blocked'] and preview['delete_databases'] is False
    assert preview['logical_bytes'] > 0 and preview['file_count'] >= 5
    assert Path(saved['folder']) in paths(preview)
    result = execute(store, preview)
    assert result['status'] == 'completed'
    for path in (original, bootstrap, model, Path(db['result_path']) / 'hash.k2d'):
        assert path.read_bytes() == b'preserved data'
    for path in (Path(saved['folder']), Path(item['source']), Path(item['controller']), package, report, log):
        assert not path.exists()
    assert store.get('configs', 'hybas') == config and store.get('database_downloads', db['id']) == db
    assert store.get('samples', 'sample') == sample
    for kind in ('runs', 'installs', 'reports', 'report_refreshes', 'environment_checks'):
        assert store.all(kind) == []
    assert store.get('settings', 'active-hybas') is None
    assert store.job(old_job) is not None
    receipt = store.get(cleanup._RECEIPTS, result['id'])
    assert receipt['operation'] == 'factory_reset' and receipt['records_retired']
    assert receipt['preserved'] and Store(store.home).get(cleanup._RECEIPTS, result['id']) == receipt


def test_separately_confirmed_unlinked_managed_database_can_be_deleted_with_its_run(store):
    db = database(store)
    saved = run(store, config='db_root: ' + db['destination'] + '\nkraken_db_name: standard\n')
    preview = reset.preview(store, True)
    assert not preview['blocked'] and Path(db['destination']) in paths(preview)
    with pytest.raises(ValueError, match='separate confirmation'):
        execute(store, preview)
    assert Path(db['destination']).is_dir()
    execute(store, preview, confirm_db=True)
    assert not Path(db['destination']).exists() and not Path(saved['folder']).exists()
    assert store.get('database_downloads', db['id']) is None


@pytest.mark.parametrize('registration', ['location', 'config', 'reference', 'nested_alias'])
def test_manual_or_ambiguous_links_are_preserved_even_with_matching_download(store, registration, tmp_path):
    db = database(store)
    root = Path(db['result_path'])
    if registration == 'location':
        store.put('database_locations', 'hybas', {'kraken': {'path': str(root)}})
    elif registration == 'config':
        store.put('configs', 'hybas', 'db_root: ' + db['destination'] + '\nkraken_db_name: standard\n')
    elif registration == 'reference':
        store.put('reference_packages', 'legacy', {'path': str(root)})
    else:
        linked = tmp_path / 'linked/database'
        linked.mkdir(parents=True)
        (linked / 'nested').symlink_to(root, target_is_directory=True)
        store.put('database_locations', 'hybas', {'kraken': {'path': str(linked)}})
    preview = reset.preview(store, True)
    assert not preview['blocked']
    execute(store, preview, confirm_db=True)
    assert (root / 'hash.k2d').read_bytes() == b'preserved data'
    assert store.get('database_downloads', db['id']) == db


@pytest.mark.parametrize('selected', [False, True])
def test_database_nested_inside_selected_run_is_excluded_not_erased(store, selected):
    saved = run(store)
    linked = file(Path(saved['folder']) / 'manual-db/index.bin').parent
    store.put('database_locations', 'hybas', {'kraken': {'path': str(linked)}})
    preview = reset.preview(store, selected)
    assert not preview['blocked']
    assert Path(saved['folder']) not in paths(preview)
    assert Path(saved['output']) in paths(preview)
    execute(store, preview, confirm_db=selected)
    assert (linked / 'index.bin').exists()
    assert not Path(saved['output']).exists()


def test_run_only_manual_database_reference_still_protects_nested_data(store):
    saved = run(store)
    linked = file(Path(saved['folder']) / 'db/standard/hash.k2d').parent
    saved['config'] = 'db_root: ' + str(linked.parent) + '\n'
    store.put('runs', saved['id'], saved)
    preview = reset.preview(store, True)
    assert not preview['blocked']
    execute(store, preview, confirm_db=True)
    assert (linked / 'hash.k2d').exists()


@pytest.mark.parametrize('bad', ['missing_job', 'mismatched_request', 'external', 'symlink'])
def test_database_ownership_requires_matching_record_and_real_inside_destination(store, tmp_path, bad):
    db = database(store)
    root = Path(db['destination'])
    if bad == 'missing_job':
        db['job'] = 'missing'
    elif bad == 'mismatched_request':
        db['request'] = {**db['request'], 'memory_mb': 1}
    elif bad == 'external':
        moved = tmp_path / 'external-db'
        root.rename(moved)
        db['destination'] = str(moved)
        root = moved
    else:
        moved = tmp_path / 'external-db'
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
    store.put('database_downloads', db['id'], db)
    preview = reset.preview(store, True)
    assert not preview['blocked'] and root not in paths(preview)
    execute(store, preview, confirm_db=True)
    assert (root / 'standard/hash.k2d').exists()


def test_related_workspace_reference_protects_managed_database(store):
    db = database(store)
    other = Store(store.home.parent / 'other-workspace')
    other.put('reference_packages', 'keep', {'path': db['result_path']})
    preview = reset.preview(store, True)
    assert not preview['blocked'] and str(other.home) in preview['checked_workspaces']
    execute(store, preview, confirm_db=True)
    assert (Path(db['result_path']) / 'hash.k2d').exists()


@pytest.mark.parametrize('change', ['file', 'new_database_link', 'job'])
def test_stale_preview_cannot_delete_data(store, change):
    saved = run(store)
    preview = reset.preview(store)
    if change == 'file':
        file(Path(saved['output']) / 'new.txt')
    elif change == 'new_database_link':
        store.put('database_locations', 'hybas', {'kraken': {'path': saved['output']}})
    else:
        store.submit('smoke', {})
    with pytest.raises(ValueError):
        execute(store, preview)
    assert Path(saved['output']).is_dir()


@pytest.mark.parametrize('unsafe', ['symlink', 'mount', 'native_lock', 'fifo', 'escape'])
def test_unsafe_targets_are_blocked_before_any_deletion(store, tmp_path, monkeypatch, unsafe):
    saved = run(store)
    folder = Path(saved['folder'])
    if unsafe == 'symlink':
        moved = tmp_path / 'unowned'
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
    elif unsafe == 'mount':
        monkeypatch.setattr(cleanup, '_mounts', lambda: {folder / 'outputs'})
    elif unsafe == 'native_lock':
        file(folder / 'workflow/.snakemake/locks/input.lock')
    elif unsafe == 'fifo':
        import os
        os.mkfifo(folder / 'special')
    else:
        saved['folder'] = str(tmp_path / 'outside')
        store.put('runs', saved['id'], saved)
    preview = reset.preview(store)
    assert preview['blocked']
    with pytest.raises(ValueError):
        execute(store, preview)
    assert (folder / 'outputs/result.txt').exists()


def test_unknown_native_ownership_blocks_reset_even_for_terminal_job(store):
    saved = run(store)
    jid = store.submit('smoke', {})
    store.update_job(jid, status='failed')
    file(store.home / 'logs' / (jid + '.process.json'), b'[]')
    preview = reset.preview(store)
    assert any('identities' in value for value in preview['blocked'])
    with pytest.raises(ValueError):
        execute(store, preview)
    assert Path(saved['folder']).exists()


def test_unknown_user_process_blocks_reset(store, monkeypatch):
    import psutil
    run(store)
    class Unknown:
        pid = 99999999
        def uids(self):
            raise psutil.AccessDenied(self.pid)
        def name(self):
            return 'unknown'
    monkeypatch.setattr('psutil.process_iter', lambda: [Unknown()])
    preview = reset.preview(store)
    assert any('Cannot inspect' in value for value in preview['blocked'])


def test_partial_reset_has_durable_receipt_and_uses_existing_recovery(store, monkeypatch):
    saved = run(store)
    preview = reset.preview(store)
    real_erase = cleanup._erase
    monkeypatch.setattr(cleanup, '_erase', lambda *_: (_ for _ in ()).throw(OSError('injected disk error')))
    with pytest.raises(OSError, match='injected disk'):
        execute(store, preview)
    receipt = store.all(cleanup._RECEIPTS)[0]
    assert receipt['status'] == 'partial' and receipt['records_retired']
    assert receipt['operation'] == 'factory_reset'
    assert any(Path(op['quarantine']).exists() for op in receipt['operations'])
    with pytest.raises(ValueError, match='recovery'):
        store.submit('smoke', {})
    monkeypatch.setattr(cleanup, '_erase', real_erase)
    recovery = cleanup.preview(store, [{'kind': 'cleanup_remaining', 'id': receipt['id']}])
    assert not recovery['blocked']
    jid = cleanup.request(store, recovery['id'], recovery['signature'], 'DELETE', True)
    store.update_job(jid, status='running')
    cleanup.execute(SimpleNamespace(store=store, job=store.job(jid), check_cancel=lambda: None,
                                    write=lambda _: None, lock_fd=None))
    assert store.get(cleanup._RECEIPTS, receipt['id'])['status'] == 'completed'
    assert not Path(saved['folder']).exists()


@pytest.mark.parametrize('confirmation,acknowledged', [('DELETE', True), (reset.CONFIRMATION, False),
                                                     (reset.CONFIRMATION, 1)])
def test_typed_developer_confirmation_is_required(store, confirmation, acknowledged):
    saved = run(store)
    preview = reset.preview(store)
    with pytest.raises(ValueError, match='type RESET THIS WORKSPACE'):
        reset.request(store, preview['id'], preview['signature'], confirmation, acknowledged)
    assert Path(saved['folder']).exists() and not store.jobs()


def test_recorded_tool_environment_removed_unproven_environment_retained(store):
    item = installation(store)
    cache = store.home / 'envs/hybas'
    prefix = file(cache / ('c' * 32) / 'conda-meta/history').parent.parent
    unknown = file(cache / ('d' * 32) / 'conda-meta/history').parent.parent
    check = {'id': 'assembly', 'path': str(prefix), 'cache': str(cache), 'definition': 'e' * 64}
    payload = {'installation': item['id'], 'action': 'install', 'cache': str(cache),
               'commit': item['version']['commit'], 'definitions': {'assembly': check['definition']}}
    jid = store.submit('environments', payload)
    store.update_job(jid, status='succeeded')
    store.put('environment_checks', item['id'], {'assembly': check})
    preview = reset.preview(store)
    assert not preview['blocked'] and prefix in paths(preview) and unknown not in paths(preview)
    execute(store, preview)
    assert not prefix.exists() and (unknown / 'conda-meta/history').exists()


def test_receipt_write_failure_precedes_any_file_removal(store, monkeypatch):
    saved = run(store)
    preview = reset.preview(store)
    monkeypatch.setattr(cleanup, '_write_receipt', lambda *_: (_ for _ in ()).throw(OSError('receipt unavailable')))
    with pytest.raises(OSError, match='receipt unavailable'):
        execute(store, preview)
    assert (Path(saved['output']) / 'result.txt').exists()
    assert store.get('runs', saved['id']) == saved


def test_runtime_nested_in_selected_cache_is_excluded(store, monkeypatch):
    import sys
    app = file(store.home / 'cache/pip/app-runtime/bin/python').parent.parent
    sibling = file(app.parent / 'old-download.whl')
    monkeypatch.setattr(sys, 'prefix', str(app))
    preview = reset.preview(store)
    assert not preview['blocked'] and app not in paths(preview) and sibling in paths(preview)
    execute(store, preview)
    assert (app / 'bin/python').exists() and not sibling.exists()


@pytest.mark.parametrize('redownload', [False])
def test_database_metadata_retires_when_selected_ancestor_is_removed(store, redownload):
    saved = run(store)
    db = database(store, parent=Path(saved['folder']) / 'managed-databases', redownload=redownload)
    saved['config'] = 'db_root: ' + db['destination'] + '\nkraken_db_name: standard\n'
    store.put('runs', saved['id'], saved)
    preview = reset.preview(store, True)
    assert not preview['blocked'] and Path(saved['folder']) in paths(preview)
    execute(store, preview, confirm_db=True)
    assert not Path(db['destination']).exists() and store.get('database_downloads', db['id']) is None


@pytest.mark.parametrize('field', ['db_proteins', 'database_manifest', 'tree_review_registry', 'placement_cache_dir', 'future_model_input'])
def test_native_config_paths_outside_picker_preserve_nested_manual_data(store, field):
    import yaml
    saved = run(store, tool='basis')
    original = file(Path(saved['folder']) / 'manual-db/proteins.faa')
    config = {'modules': {'pathogenfinder2': {field: str(original)}},
              'bactomics_root': str(Path(saved['folder']) / 'outputs'),
              'samples': str(Path(saved['folder']) / 'samples.tsv')}
    saved['config'] = yaml.safe_dump(config)
    store.put('runs', saved['id'], saved)
    store.put('configs', 'basis', yaml.safe_dump({'modules': config['modules']}))
    preview = reset.preview(store, False)
    assert not preview['blocked'] and Path(saved['folder']) not in paths(preview)
    execute(store, preview)
    assert original.read_bytes() == b'preserved data'
    assert not Path(saved['output']).exists()


def test_per_sample_override_protects_database_nested_in_controller(store):
    import yaml
    item = installation(store)
    original = file(Path(item['controller']) / 'manual-db/proteins.faa')
    saved = run(store, tool='basis', config=yaml.safe_dump({'sample_module_overrides': {
        'sample': {'pathogenfinder2': {'db_proteins': str(original)}}}}))
    preview = reset.preview(store, True)
    assert not preview['blocked'] and Path(item['controller']) not in paths(preview)
    execute(store, preview, confirm_db=True)
    assert original.read_bytes() == b'preserved data'
    assert not Path(saved['output']).exists()


def test_home_relative_database_configuration_protects_manual_data(store, monkeypatch):
    import yaml
    original_expanduser = Path.expanduser
    def expanduser(path):
        if str(path) == '~':
            return store.home.parent
        if str(path).startswith('~/'):
            return store.home.parent / str(path)[2:]
        return original_expanduser(path)
    monkeypatch.setattr(Path, 'expanduser', expanduser)
    saved = run(store, tool='basis')
    original = file(Path(saved['folder']) / 'manual-db/proteins.faa')
    value = '~/' + str(original.relative_to(store.home.parent))
    store.put('configs', 'basis', yaml.safe_dump({'modules': {'pathogenfinder2': {'db_proteins': value}}}))
    preview = reset.preview(store, True)
    assert not preview['blocked'] and Path(saved['folder']) not in paths(preview)
    execute(store, preview, confirm_db=True)
    assert original.read_bytes() == b'preserved data'
    assert not Path(saved['output']).exists()


def test_unlinked_genescope_build_requires_managed_database_opt_in(store):
    key = 'reference-0123456789ab'
    destination = store.home / 'resources/micpscope/fixture-1.0' / key
    payload = {'id': key, 'tool': 'micpscope', 'application': {'id': 'fixture', 'version': '1.0'},
               'destination': str(destination), 'installation': 'fixture-install'}
    jid = store.submit('database_build', payload)
    store.update_job(jid, status='succeeded')
    original = file(destination / 'database/proteins.faa')
    row = {**payload, 'job': jid, 'status': 'completed', 'result_path': str(original.parent)}
    store.put('database_builds', key, row)
    assert destination not in paths(reset.preview(store))
    preview = reset.preview(store, True)
    assert not preview['blocked'] and destination in paths(preview)
    execute(store, preview, confirm_db=True)
    assert not destination.exists() and store.get('database_builds', key) is None


def test_confirmation_queues_without_repeating_directory_inventory(store, monkeypatch):
    saved = run(store)
    preview = reset.preview(store)
    def forbidden(*args, **kwargs):
        raise AssertionError('The confirmation request must leave full inventory to its worker')
    monkeypatch.setattr(reset, '_build', forbidden)
    jid = reset.request(store, preview['id'], preview['signature'], reset.CONFIRMATION, True)
    assert store.job(jid)['kind'] == 'factory_reset' and store.job(jid)['status'] == 'queued'
    assert (Path(saved['output']) / 'result.txt').exists()
    assert store.get('runs', saved['id']) == saved
    assert store.all(cleanup._RECEIPTS) == []


@pytest.mark.parametrize('change', ['new_file', 'new_manual_database_link'])
def test_worker_revalidates_queued_reset_before_any_staging_or_record_retirement(store, monkeypatch, change):
    saved = run(store)
    preview = reset.preview(store)
    # Changes may occur between preview and confirmation. Confirmation queues the
    # exact reviewed plan cheaply; execution must reject its stale inventory.
    if change == 'new_file':
        file(Path(saved['output']) / 'new-reference.faa')
    else:
        store.put('database_locations', 'hybas', {'kraken': {'path': saved['output']}})
    jid = reset.request(store, preview['id'], preview['signature'], reset.CONFIRMATION, True)
    store.update_job(jid, status='running')
    def forbidden(*args, **kwargs):
        raise AssertionError('A stale reset must fail before any file is staged')
    monkeypatch.setattr(cleanup, '_stage', forbidden)
    ctx = SimpleNamespace(store=store, job=store.job(jid), check_cancel=lambda: None,
                          write=lambda _: None, lock_fd=None)
    with pytest.raises(ValueError, match='changed since preview'):
        reset.execute(ctx)
    assert (Path(saved['output']) / 'result.txt').exists()
    assert store.get('runs', saved['id']) == saved
    assert store.all(cleanup._RECEIPTS) == []
