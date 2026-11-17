"""UI bridge preserves frozen records during explicit recovery actions."""
import pytest
from bactologix import supervision
from bactologix.ui_backend import dispatch, snapshot
from test_resource_repair import stale_run


def test_vfdb_repair_bridge_creates_reviewed_independent_draft(stale_run):
    store, parent = stale_run
    detail = dispatch(store, 'inspect_run', {'id': parent['id']})
    assert detail['detail']['has_external_vfdb']
    response = dispatch(store, 'preview_vfdb_repair', {'id': parent['id']})
    plan = response['resource_repair']
    assert response['keep_modal'] and plan['available']
    assert len(store.all('runs')) == 1 and not store.jobs()
    result = dispatch(store, 'apply_vfdb_repair', {'id': parent['id'], 'signature': plan['signature']})
    assert result['open_run'] != parent['id'] and not result['keep_modal']
    assert not result['detail']['has_external_vfdb']
    assert store.get('runs', parent['id']) == parent and not store.jobs()


def test_snapshot_reports_monitoring_separately_from_workflow(stale_run):
    store, parent = stale_run
    jid = store.submit('workflow', {'run_id': parent['id'], 'action': 'run'})
    store.update_job(jid, status='interrupted')
    supervision.save(store, jid, {'state': 'interrupted', 'cleanup_confirmed': True,
                                 'detail': 'Control database unavailable', 'updated_at': 'fixture'})
    doc = snapshot(store)
    job = next(j for j in doc['jobs'] if j['id'] == jid)
    assert job['status'] == 'interrupted'
    assert job['monitoring']['cleanup_confirmed'] is True
    assert doc['download_options']['basis']['available']
    assert 'reason' in doc['download_options']['basis']


def test_stop_does_not_claim_control_after_monitoring_exit(stale_run, monkeypatch):
    store, parent = stale_run
    jid = store.submit('workflow', {'run_id': parent['id'], 'action': 'run'})
    store.update_job(jid, status='running')
    supervision.save(store, jid, {'state': 'interrupted', 'cleanup_confirmed': False})
    monkeypatch.setattr('bactologix.queue.reconcile_interrupted', lambda _: [])
    with pytest.raises(ValueError, match='cannot restore process ownership'):
        dispatch(store, 'cancel_job', {'id': jid})
    assert store.job(jid)['status'] == 'running' and not store.job(jid)['cancel']


def test_storage_resume_rejects_an_interrupted_monitor(stale_run, monkeypatch):
    store, parent = stale_run
    jid = store.submit('workflow', {'run_id': parent['id'], 'action': 'run'})
    supervision.save(store, jid, {'state': 'interrupted', 'cleanup_confirmed': False})
    monkeypatch.setattr('bactologix.disk_pause.request_resume', lambda *args: pytest.fail('Unsafe resume request'))
    with pytest.raises(ValueError, match='monitoring needs attention'):
        dispatch(store, 'resume_disk_paused_job', {'job_id': jid, 'generation': 1})
