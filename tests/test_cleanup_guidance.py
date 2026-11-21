"""Cleanup recovery routes to its receipt, never to workflow setup or resume."""
import copy

import pytest

from bactologix import readiness
from test_native import workspace


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
def test_cleanup_with_receipt_routes_to_exact_deletion_record(workspace, status):
    job_id = workspace.submit('storage_cleanup', {'plan_id': 'plan-fixture'})
    receipt = {'id': job_id, 'status': 'partial', 'operations': [
        {'original': '/fixture/old-result', 'quarantine': '/fixture/staged-result', 'state': 'staged'}]}
    workspace.put('storage_cleanup_receipts', job_id, receipt)
    workspace.update_job(job_id, status=status, error='Worker stopped during cleanup')
    before = copy.deepcopy(workspace.job(job_id))

    result = readiness.job_guidance(workspace, before)

    assert result['category'] == 'storage_cleanup'
    assert result['action'] == 'cleanup-receipt' and result['receipt_id'] == job_id
    assert result['run_id'] is None and result['step'] is None
    assert 'Some files may already be removed' in result['text']
    assert 'validate' not in result['text'] and 'Run / resume' not in result['text']
    assert workspace.job(job_id) == before
    assert workspace.get('storage_cleanup_receipts', job_id) == receipt


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
def test_cleanup_without_receipt_routes_to_storage_without_claiming_deletion(workspace, status):
    job_id = workspace.submit('storage_cleanup', {'plan_id': 'expired-plan'})
    workspace.update_job(job_id, status=status, error='Plan unavailable')

    result = readiness.job_guidance(workspace, workspace.job(job_id))

    assert result['category'] == 'storage_cleanup'
    assert result['action'] == 'go-storage' and result['receipt_id'] is None
    assert 'Do not assume' in result['text']
    assert result['confidence'] == ('Recorded interruption' if status == 'interrupted' else 'Recorded failure')


def test_interrupted_recovery_job_opens_original_receipt_even_if_plan_expired(workspace):
    job_id = workspace.submit('storage_cleanup', {'plan_id': 'expired-plan'})
    workspace.put('storage_cleanup_receipts', 'original-cleanup', {
        'id': 'original-cleanup', 'status': 'deleting', 'recovery_job': job_id})
    workspace.update_job(job_id, status='interrupted')

    result = readiness.job_guidance(workspace, workspace.job(job_id))

    assert result['receipt_id'] == 'original-cleanup'
    assert result['action'] == 'cleanup-receipt'


def test_failed_recovery_before_staging_still_links_existing_receipt(workspace):
    job_id = workspace.submit('storage_cleanup', {'plan_id': 'recovery-plan'})
    workspace.put('storage_cleanup_receipts', 'original-cleanup', {'id': 'original-cleanup', 'status': 'partial'})
    workspace.put('storage_cleanup_plans', 'recovery-plan', {
        'id': 'recovery-plan', 'candidates': [{'kind': 'cleanup_remaining', 'id': 'original-cleanup'}]})
    workspace.update_job(job_id, status='failed', error='Plan changed')

    result = readiness.job_guidance(workspace, workspace.job(job_id))

    assert result['receipt_id'] == 'original-cleanup'


def test_active_cleanup_does_not_offer_recovery(workspace):
    job_id = workspace.submit('storage_cleanup', {})
    assert readiness.job_guidance(workspace, workspace.job(job_id)) is None
