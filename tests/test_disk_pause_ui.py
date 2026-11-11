"""Storage pause presentation and durable alerts use software-only records."""
from copy import deepcopy

import pytest

from bactologix import disk_pause, notifications, ui_backend
from bactologix.store import Store, now
from test_notifications import job, preferences, workspace


def paused_record(jid, **changes):
    return {'id': jid, 'run_id': 'run-one', 'state': 'paused', 'generation': 1,
            'pause_kind': 'processes_suspended', 'reason': 'low_space',
            'volumes': [{'label': 'Windows D:', 'path': '/mnt/d',
                         'available_bytes': 8 * 1024**3}], 'errors': [],
            'checked_at': now(), 'can_resume': False, 'resume_requested': False,
            'thresholds': {'pause_bytes': 10 * 1024**3,
                           'resume_bytes': 15 * 1024**3,
                           'warning_bytes': 20 * 1024**3}, **changes}


@pytest.fixture
def pause_workspace(workspace, monkeypatch):
    # Test alerts independently of worker/process ownership, with durable data.
    monkeypatch.setattr(disk_pause, 'snapshot', lambda store, jid: store.get('disk_safety', jid))
    return workspace


def test_pause_alert_once_per_generation_across_poll_and_reopen(pause_workspace):
    store = pause_workspace
    jid = job(store, status='running')
    guard = paused_record(jid)
    store.put('disk_safety', jid, guard)
    first = notifications.panel(store)
    assert first['unread'] == 1
    row = first['items'][0]
    assert row['title'] == 'Paused — low disk space'
    assert row['category'] == 'jobs' and row['event_type'] == 'disk_pause'
    assert row['target'] == {'kind': 'job', 'id': jid}
    assert notifications.panel(Store(store.home))['items'] == first['items']
    assert len(notifications.claim_desktop(store, {'ids': [row['id']]})['notification_delivery']) == 1
    assert notifications.claim_desktop(store, {'ids': [row['id']]})['notification_delivery'] == []
    guard.update(state='monitoring', can_resume=False)
    store.put('disk_safety', jid, guard)
    resolved = notifications.panel(store)
    assert resolved['unread'] == 0 and not resolved['items'][0]['active']
    guard.update(state='paused', generation=2)
    store.put('disk_safety', jid, guard)
    again = notifications.panel(store)
    assert len(again['items']) == 2 and again['unread'] == 1
    assert len(again['desktop_candidates']) == 1


@pytest.mark.parametrize('change,title', [
    ({'pause_kind': 'before_launch'}, 'Waiting for storage'),
    ({'reason': 'monitor_unavailable'}, 'Paused — storage check unavailable'),
])
def test_waiting_and_unavailable_alerts_are_distinct(pause_workspace, change, title):
    jid = job(pause_workspace, status='running')
    pause_workspace.put('disk_safety', jid, paused_record(jid, **change))
    row = notifications.panel(pause_workspace)['items'][0]
    assert row['title'] == title
    if change.get('pause_kind') == 'before_launch':
        assert 'suspended' not in row['body']
        assert 'No tool process has started for this command' in row['body']


def test_disabled_job_alerts_and_terminal_attempt_retires_pause(pause_workspace):
    store = pause_workspace
    notifications.configure(store, preferences(jobs=False))
    jid = job(store, status='running')
    store.put('disk_safety', jid, paused_record(jid))
    doc = notifications.panel(store)
    assert doc['unread'] == 0 and doc['desktop_candidates'] == []
    assert doc['items'][0]['read']
    store.update_job(jid, status='cancelled', finished=now())
    alert = next(row for row in notifications.panel(store)['items'] if row.get('event_type') == 'disk_pause')
    assert not alert['active']


def test_claim_rechecks_resolved_disk_pause(pause_workspace):
    jid = job(pause_workspace, status='running')
    guard = paused_record(jid)
    pause_workspace.put('disk_safety', jid, guard)
    key = notifications.panel(pause_workspace)['items'][0]['id']
    guard['state'] = 'monitoring'
    pause_workspace.put('disk_safety', jid, guard)
    result = ui_backend.dispatch(pause_workspace, 'claim_desktop_notifications', {'ids': [key]})
    assert result['notification_delivery'] == []


def test_resume_dispatch_exact_generation_without_claiming_resumed(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(ui_backend.updates, 'assert_current', lambda store: None)
    monkeypatch.setattr(ui_backend.updates, 'status', lambda store: {'active_job': None})
    monkeypatch.setattr(disk_pause, 'request_resume', lambda store, jid, generation: calls.append((jid, generation)))
    result = ui_backend.dispatch(workspace, 'resume_disk_paused_job', {'job_id': 'job-one', 'generation': 4})
    assert calls == [('job-one', 4)]
    assert result['message'].startswith('Resume requested.') and 'checked again' in result['message']
    with pytest.raises(ValueError, match='current storage pause'):
        ui_backend.dispatch(workspace, 'resume_disk_paused_job', {'job_id': 'job-one', 'generation': 4, 'pid': 123})
    assert calls == [('job-one', 4)]


def test_cached_snapshot_refreshes_guard_without_changing_lock_status(workspace, monkeypatch):
    from bactologix import queue
    jid = job(workspace, status='running')
    guard = paused_record(jid)
    monkeypatch.setattr(queue, 'reconcile_interrupted', lambda store: [])
    monkeypatch.setattr(ui_backend.readiness, 'job_guidance', lambda store, job: None)
    monkeypatch.setattr(ui_backend.updates, 'status', lambda store: {'active_job': 'update', 'restart_required': False})
    monkeypatch.setattr(disk_pause, 'snapshot', lambda store, key: deepcopy(guard) if key == jid else None)
    monkeypatch.setitem(ui_backend._SNAPSHOTS, workspace.home, {'runs': []})
    first = ui_backend.snapshot(workspace)
    assert first['jobs'][0]['disk_safety']['state'] == 'paused'
    assert first['jobs'][0]['status'] == 'running'
    guard.update(resume_requested=True)
    second = ui_backend.snapshot(workspace)
    assert second['jobs'][0]['disk_safety']['resume_requested'] is True
    assert 'pid' not in second['jobs'][0]['disk_safety']
