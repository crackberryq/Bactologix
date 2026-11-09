"""Dev input registration and continuation cannot race cleanup."""
import pytest

from bactologix import aware_outputs, manifest_inputs
from bactologix.store import Store


@pytest.mark.parametrize('operation,args', [
    (manifest_inputs.commit, ('/not-read/inputs.json', 'reviewed-token')),
    (aware_outputs.commit, ('/not-read/manifest.json', 'reviewed-token', ['primary'])),
    (aware_outputs.continue_assembly, ('run-parent', 'reviewed-token')),
])
@pytest.mark.parametrize('pending', ['queued', 'interrupted'])
def test_dev_input_mutations_wait_for_cleanup(tmp_path, operation, args, pending):
    store = Store(tmp_path / 'workspace')
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
