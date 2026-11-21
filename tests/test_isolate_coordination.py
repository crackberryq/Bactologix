"""Exports freeze their own workspace while allowing reads and cancellation."""
import pytest

from bactologix.store import Store
from bactologix import readiness


@pytest.mark.parametrize('pending_kind', ['workflow', 'storage_cleanup', 'report_refresh', 'smoke'])
def test_export_requires_idle_workspace(tmp_path, pending_kind):
    store = Store(tmp_path / 'workspace')
    payload = {'run_id': 'run-fixture', 'action': 'run'} if pending_kind == 'workflow' else {}
    store.submit(pending_kind, payload)
    with pytest.raises(ValueError, match='queued|running'):
        store.submit('isolate_export', {'plan_id': 'plan', 'token': 'token'})
    assert len(store.jobs()) == 1


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_export_preserves_references_but_allows_read_cancel_and_other_workspace(tmp_path, status):
    store = Store(tmp_path / 'regular')
    store.put('samples', 'sample', {'id': 'sample', 'path': '/original'})
    job = store.submit('isolate_export', {'plan_id': 'plan', 'token': 'token'})
    store.update_job(job, status=status)
    with pytest.raises(ValueError, match='isolate export'):
        store.put('samples', 'sample', {'id': 'sample', 'path': '/changed'})
    with pytest.raises(ValueError, match='isolate export'):
        store.submit('smoke', {})
    with pytest.raises(ValueError, match='isolate export'):
        store.submit('storage_cleanup', {})
    assert store.get('samples', 'sample')['path'] == '/original'
    other = Store(tmp_path / 'dev')
    other.put('samples', 'sample', {'id': 'sample', 'path': '/independent'})
    assert other.get('samples', 'sample')['path'] == '/independent'
    store.cancel(job)
    assert store.job(job)['cancel'] == 1
    store.update_job(job, status='cancelled')
    store.put('samples', 'sample', {'id': 'sample', 'path': '/changed'})
    assert store.get('samples', 'sample')['path'] == '/changed'


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
@pytest.mark.parametrize('has_receipt', [False, True])
def test_export_recovery_opens_receipt_without_claiming_deletion(tmp_path, status, has_receipt):
    store = Store(tmp_path / 'workspace')
    job = store.submit('isolate_export', {'plan_id': 'plan', 'token': 'token'})
    if has_receipt:
        store.put('isolate_export_receipts', job, {'id': job, 'status': 'running', 'verification': 'pending'})
    store.update_job(job, status=status, error='Destination unavailable')
    result = readiness.job_guidance(store, store.job(job))
    assert result['category'] == 'isolate_export'
    assert result['action'] == ('isolate-export-receipt' if has_receipt else 'go-samples')
    assert result['receipt_id'] == (job if has_receipt else None)
    assert 'retained' in result['text']
