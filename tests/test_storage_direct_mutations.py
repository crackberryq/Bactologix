"""Direct SQL and multi-step edits must honor an outstanding cleanup."""
import pytest

from bactologix import databases, database_runs, identification, maintenance
from bactologix.store import Store


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

