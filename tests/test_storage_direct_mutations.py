"""Direct SQL and multi-step edits must honor an outstanding cleanup."""
import threading

import pytest

from bactologix import databases, database_runs, identification, maintenance, hardware
from bactologix.storage_guard import exclusive
from bactologix.store import Store
from bactologix.ui_backend import dispatch


@pytest.mark.parametrize('operation,args', [
    (databases.save, ('hybas', {}, 'old-revision')),
    (maintenance.set_cache, ('installation', '/unused-cache')),
    (database_runs.apply, (['run-parent'], {}, 'old-signature')),
    (identification.save, ('run-parent', 'keep_all', None, 'old-signature')),
])
@pytest.mark.parametrize('pending', ['queued', 'interrupted'])
def test_direct_edits_cannot_change_references_during_cleanup(tmp_path, operation, args, pending):
    store = Store(tmp_path / 'workspace')
    sample = {'id': 'sample', 'path': str(tmp_path / 'inputs'), 'mode': 'hybrid'}
    store.put('samples', sample['id'], sample)
    if pending == 'queued':
        store.submit('storage_cleanup', {'plan_id': 'reviewed-plan'})
        expected = 'Storage cleanup is queued or running'
    else:
        store.put('storage_cleanup_receipts', 'cleanup-interrupted',
                  {'id': 'cleanup-interrupted', 'status': 'partial'})
        expected = 'interrupted storage cleanup needs recovery'
    with store.connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM items ORDER BY kind,id')]

    with pytest.raises(ValueError, match=expected):
        operation(store, *args)

    with store.connect() as db:
        after = [tuple(row) for row in db.execute('SELECT * FROM items ORDER BY kind,id')]
    assert after == before
    assert store.get('samples', sample['id']) == sample


def compute_write(store, monkeypatch, kind):
    info = {'id': 'fixture', 'cpu': {'available': 4},
            'memory': {'total_mb': 16384, 'available_mb': 16384}}
    monkeypatch.setattr(hardware, 'inventory', lambda store, refresh=False: info)
    store.put('configs', 'hybas', 'custom_path: /retained/input\n')
    panel = hardware.panel(store)
    payload = {'tool': 'hybas', 'text': 'custom_path: /new/input\nbactologix_compute: {swap_mb: 8192}\n',
               'revision': panel['revisions']['hybas'], 'cores': 2, 'memory_mb': 4096,
               'gpu_mode': 'cpu', 'swap_mb': 98304, 'hardware_id': info['id'],
               'revisions': panel['revisions'], 'preset': 'low', 'tools': ['hybas']}
    return lambda: dispatch(store, kind, payload)


@pytest.mark.parametrize('kind', ['save_config', 'save_default_config', 'save_compute_defaults', 'apply_resource_preset'])
@pytest.mark.parametrize('pending', ['queued', 'running', 'interrupted', 'isolate_export'])
def test_compute_and_yaml_defaults_preserve_storage_guard(tmp_path, monkeypatch, kind, pending):
    store = Store(tmp_path / 'workspace')
    operation = compute_write(store, monkeypatch, kind)
    if pending == 'interrupted':
        store.put('storage_cleanup_receipts', 'interrupted', {'id': 'interrupted', 'status': 'partial'})
        expected = 'interrupted storage cleanup'
    else:
        store.submit('isolate_export' if pending == 'isolate_export' else 'storage_cleanup', {})
        if pending == 'running':
            store.claim()
        expected = 'isolate export' if pending == 'isolate_export' else 'Storage cleanup'
    with store.connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM items ORDER BY kind,id')]
    with pytest.raises(ValueError, match=expected):
        operation()
    with store.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM items ORDER BY kind,id')] == before


@pytest.mark.parametrize('kind', ['save_config', 'save_default_config', 'save_compute_defaults', 'apply_resource_preset'])
def test_concurrent_cleanup_lock_blocks_compute_and_yaml_defaults(tmp_path, monkeypatch, kind):
    store = Store(tmp_path / 'workspace')
    operation = compute_write(store, monkeypatch, kind)
    locked, release = threading.Event(), threading.Event()
    def cleanup():
        with exclusive(store):
            locked.set()
            release.wait(10)
    thread = threading.Thread(target=cleanup)
    thread.start()
    try:
        assert locked.wait(5)
        with pytest.raises(ValueError, match='Storage cleanup or a workspace operation'):
            operation()
        assert store.get('configs', 'hybas') == 'custom_path: /retained/input\n'
        assert not store.all('resource_defaults')
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()

