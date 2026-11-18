"""Large-reset budgets remain finite, cancellable and recoverable."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import factory_reset as reset, storage_cleanup as cleanup
from bactologix.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv('BACTOLOGIX_CHANNEL', raising=False)
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda _: None)
    monkeypatch.setattr('psutil.process_iter', lambda: [])
    return Store(tmp_path / 'workspace')


def saved_run(store, key='run-one', files=4):
    folder = store.home / 'runs' / key
    folder.mkdir(parents=True)
    for index in range(files):
        (folder / str(index)).write_text('retained until explicitly reviewed')
    row = {'id': key, 'tool': 'hybas', 'folder': str(folder), 'output': str(folder / 'outputs'),
           'config': '{}', 'sample': {}, 'inputs': [], 'completed': True}
    store.put('runs', key, row)
    return row


def queue_reset(store, plan, cancel=lambda: None):
    jid = reset.request(store, plan['id'], plan['signature'], reset.CONFIRMATION, True)
    store.update_job(jid, status='running')
    return SimpleNamespace(store=store, job=store.job(jid), check_cancel=cancel,
                           write=lambda _: None, lock_fd=None)


def test_reset_inventories_and_database_aliases_have_separate_large_policy(store, tmp_path, monkeypatch):
    row = saved_run(store)
    linked = tmp_path / 'linked-database'
    linked.mkdir()
    for index in range(8):
        (linked / str(index)).write_text('database')
    store.put('database_locations', 'hybas', {'kraken': str(linked)})
    monkeypatch.setattr(cleanup, 'MAX_ENTRIES', 1)
    monkeypatch.setattr(cleanup, 'MAX_SECONDS', 0)
    progress = []
    plan = reset.preview(store, checkpoint=lambda **values: progress.append(values))
    assert plan['inventory_complete'] and not plan['blocked']
    assert plan['file_count'] == 4
    assert any(item['stage'] == 'Checking retained database links' for item in progress)
    ordinary = cleanup.preview(store, [{'kind': 'run', 'id': row['id']}])
    assert ordinary['blocked'] and not ordinary['inventory_complete']
    ctx = queue_reset(store, plan)
    reset.execute(ctx)
    assert not Path(row['folder']).exists()
    assert len(list(linked.iterdir())) == 8
    assert store.get(reset.PLANS, plan['id']) is None
    assert store.all(cleanup._RECEIPTS)[0]['status'] == 'completed'


def test_reset_budget_is_aggregate_across_targets_and_partial_plan_is_blocked(store, monkeypatch):
    first = saved_run(store, 'run-a', 3)
    second = saved_run(store, 'run-b', 3)
    monkeypatch.setattr(cleanup, 'RESET_MAX_ENTRIES', 6)
    plan = reset.preview(store)
    assert not plan['inventory_complete']
    assert plan['file_count'] == 3
    assert any('6 file/directory entries' in reason and 'run-b' in reason for reason in plan['blocked'])
    with pytest.raises(ValueError, match='blocked'):
        queue_reset(store, plan)
    assert Path(first['folder']).exists() and Path(second['folder']).exists()
    assert not store.all(cleanup._RECEIPTS)


def test_reset_alias_and_inventory_share_one_aggregate_budget(store, tmp_path, monkeypatch):
    saved_run(store, files=2)
    linked = tmp_path / 'linked'
    linked.mkdir()
    for index in range(4):
        (linked / str(index)).touch()
    store.put('database_locations', 'hybas', {'kraken': str(linked)})
    monkeypatch.setattr(cleanup, 'RESET_MAX_ENTRIES', 6)
    plan = reset.preview(store)
    assert not plan['inventory_complete'] and plan['blocked']
    assert any('6 file/directory entries' in reason for reason in plan['blocked'])


def test_reset_timeout_reports_actual_limit_and_never_authorizes_partial_review(store, monkeypatch):
    saved_run(store)
    real_budget = cleanup._scan_budget
    def expired(**kwargs):
        budget = real_budget(**kwargs)
        budget['started'] -= cleanup.RESET_MAX_SECONDS + 1
        return budget
    monkeypatch.setattr(cleanup, '_scan_budget', expired)
    plan = reset.preview(store)
    assert not plan['inventory_complete']
    assert any('900 seconds' in reason for reason in plan['blocked'])
    with pytest.raises(ValueError, match='blocked'):
        queue_reset(store, plan)


def test_encoded_inventory_bound_is_fail_closed_and_counted_across_targets(store, monkeypatch):
    saved_run(store, 'run-a', 1)
    saved_run(store, 'run-b', 1)
    monkeypatch.setattr(cleanup, 'RESET_MAX_MANIFEST_BYTES', 600)
    plan = reset.preview(store)
    assert not plan['inventory_complete'] and plan['file_count'] == 1
    assert any('inventory metadata' in reason for reason in plan['blocked'])
    with pytest.raises(ValueError, match='blocked'):
        queue_reset(store, plan)


@pytest.mark.parametrize('stage', ['Checking retained database links', 'Reviewing managed files'])
def test_cancellation_propagates_without_saving_a_partial_plan(store, tmp_path, stage):
    row = saved_run(store, files=300)
    linked = tmp_path / 'linked'
    linked.mkdir()
    (linked / 'reference').touch()
    store.put('database_locations', 'hybas', {'kraken': str(linked)})
    def cancel(**progress):
        if progress['stage'] == stage and (stage != 'Reviewing managed files' or progress['entries'] >= 256):
            raise RuntimeError('Review cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        reset.preview(store, checkpoint=cancel)
    assert not store.all(reset.PLANS) and not store.all(cleanup._RECEIPTS)
    assert len(list(Path(row['folder']).iterdir())) == 300


def test_worker_cancellation_during_verification_happens_before_staging(store):
    row = saved_run(store)
    plan = reset.preview(store)
    def cancel():
        raise RuntimeError('Review cancelled')
    ctx = queue_reset(store, plan, cancel)
    with pytest.raises(RuntimeError, match='cancelled'):
        reset.execute(ctx)
    assert Path(row['folder']).exists()
    assert store.get('runs', row['id']) == row
    assert not store.all(cleanup._RECEIPTS)


def test_interrupted_large_reset_remains_recoverable_with_old_cleanup_caps(store, monkeypatch):
    row = saved_run(store)
    monkeypatch.setattr(cleanup, 'MAX_ENTRIES', 1)
    monkeypatch.setattr(cleanup, 'MAX_SECONDS', 0)
    plan = reset.preview(store)
    ctx = queue_reset(store, plan)
    original = cleanup._erase
    def disk_error(*_):
        raise OSError('injected disk error')
    monkeypatch.setattr(cleanup, '_erase', disk_error)
    with pytest.raises(OSError, match='injected'):
        reset.execute(ctx)
    store.update_job(ctx.job['id'], status='failed')
    receipt = store.all(cleanup._RECEIPTS)[0]
    assert receipt['operation'] == 'factory_reset' and receipt['status'] == 'partial'
    monkeypatch.setattr(cleanup, '_erase', original)
    observed = []
    recovery = cleanup.preview(store, [{'kind': 'cleanup_remaining', 'id': receipt['id']}],
                               checkpoint=lambda **values: observed.append(values))
    assert recovery['inventory_complete'] and not recovery['blocked'] and observed
    jid = cleanup.request(store, recovery['id'], recovery['signature'], 'DELETE', True)
    store.update_job(jid, status='running')
    cleanup.execute(SimpleNamespace(store=store, job=store.job(jid), check_cancel=lambda: None,
                                    write=lambda _: None, lock_fd=None))
    assert store.get(cleanup._RECEIPTS, receipt['id'])['status'] == 'completed'
    assert not Path(row['folder']).exists()


def test_preview_cache_pruning_preserves_pending_plan_and_receipts(store):
    for key in ('first', 'queued', 'third', 'latest'):
        store.put(reset.PLANS, key, {'id': key})
    store.submit('factory_reset', {'plan_id': 'queued'})
    store.put(cleanup._RECEIPTS, 'receipt', {'id': 'receipt', 'status': 'completed'})
    reset._prune_plans(store)
    assert {item['id'] for item in store.all(reset.PLANS)} == {'queued', 'latest'}
    assert store.get(cleanup._RECEIPTS, 'receipt')['status'] == 'completed'


def test_repeated_previews_keep_only_latest_and_do_not_read_old_manifest_cache(store):
    saved_run(store)
    first = reset.preview(store)
    # Cached plans do not grant dependency or ownership authority.
    with store.connect() as db:
        db.execute('UPDATE items SET body=? WHERE kind=? AND id=?', ('not-json', reset.PLANS, first['id']))
    second = reset.preview(store)
    assert not second['blocked']
    assert [item['id'] for item in store.all(reset.PLANS)] == [second['id']]


def test_streamed_manifest_signature_is_compatible_with_previous_json_digest():
    value = {'path': 'unicode-\u03b1-\U0001f600', 'rows': [{'name': str(i), 'stamp': [1, -2, 3]} for i in range(150)]}
    assert cleanup._hash(value) == hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_reset_recovery_queues_without_second_ui_scan_but_worker_rechecks(store, monkeypatch):
    saved_run(store)
    plan = reset.preview(store)
    ctx = queue_reset(store, plan)
    with monkeypatch.context() as patch:
        def fail(*_):
            raise OSError('injected disk error')
        patch.setattr(cleanup, '_erase', fail)
        with pytest.raises(OSError):
            reset.execute(ctx)
    store.update_job(ctx.job['id'], status='failed')
    receipt = store.all(cleanup._RECEIPTS)[0]
    recovery = cleanup.preview(store, [{'kind': 'cleanup_remaining', 'id': receipt['id']}])
    assert not recovery['blocked']
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, '_verify', lambda *_: pytest.fail('UI request must not repeat a large recovery scan'))
        jid = cleanup.request(store, recovery['id'], recovery['signature'], 'DELETE', True)
    store.update_job(jid, status='running')
    staged = Path(receipt['operations'][0]['quarantine'])
    (staged / '0').write_text('changed after review')
    with pytest.raises(ValueError, match='changed since preview'):
        cleanup.execute(SimpleNamespace(store=store, job=store.job(jid), check_cancel=lambda: None,
                                        write=lambda _: None, lock_fd=None))
    assert staged.exists()
    assert (staged / '0').read_text() == 'changed after review'
