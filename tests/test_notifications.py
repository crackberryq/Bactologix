"""Local alert history is durable, deduplicated, and explicit about outcomes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier

import pytest

from bactologix import notifications
from bactologix.store import Store
from bactologix.ui_backend import dispatch


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    monkeypatch.setattr(notifications.update_checks, 'panel', lambda store: {
        'auto_check': store.get('notification_test', 'auto', True),
        'checking': False, 'last_attempt': None, 'last_success': None, 'errors': {}})
    monkeypatch.setattr(notifications.update_checks, 'candidates',
                        lambda store: store.get('notification_test', 'candidates', []))
    monkeypatch.setattr(notifications.update_checks, 'configure',
                        lambda store, enabled: store.put('notification_test', 'auto', enabled))
    return store


def job(store, status='succeeded', kind='workflow', action='run', rid='run-one', finished=None):
    # SQL fixtures avoid queue admission/worker execution; these are saved
    # outcome records, not biological analysis fixtures.
    index = len(store.jobs()) + 1
    jid = f'job-{index:04d}'
    payload = {'action': action, 'run_id': rid, 'tool': 'hybas'}
    when = finished or f'2026-11-10T10:{index // 60:02d}:{index % 60:02d}+00:00'
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,kind,payload,status,created,finished) VALUES (?,?,?,?,?,?)',
                   (jid, kind, json.dumps(payload), status, when, when if status in notifications.TERMINAL else None))
    return jid


def initialized(store):
    return notifications.panel(store)


def candidate(identity='app:next'):
    return {'id': identity, 'category': 'updates', 'title': 'Bactologix update available',
            'body': 'Review a newer compatible release.', 'target': {'kind': 'app_update'}}


def preferences(**changes):
    return {'jobs': True, 'review': True, 'updates': True, 'auto_update_checks': True, **changes}


def test_initial_history_is_saved_read_without_desktop_flood(workspace):
    job(workspace, status='succeeded')
    job(workspace, status='failed')
    first = notifications.panel(workspace)
    assert len(first['items']) == 2
    assert all(row['read'] for row in first['items'])
    assert first['unread'] == 0 and first['desktop_candidates'] == []
    reopened = notifications.panel(Store(workspace.home))
    assert reopened['items'] == first['items']


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'cancelled', 'interrupted'])
def test_terminal_job_after_initialization_becomes_one_unread_alert(workspace, status):
    initialized(workspace)
    jid = job(workspace, status='running')
    assert notifications.panel(workspace)['items'] == []
    workspace.update_job(jid, status=status, finished='2026-11-10T11:00:00+00:00')
    first = notifications.panel(workspace)
    assert first['unread'] == 1
    assert len(first['desktop_candidates']) == 1
    row = first['items'][0]
    assert row['target'] == {'kind': 'job', 'id': jid}
    assert {'succeeded': 'finished'}.get(status, status) in row['title']
    assert notifications.panel(workspace)['items'] == first['items']


@pytest.mark.parametrize('action, label', [('dry-run', 'Validation finished'),
    ('prepare', 'Tool preparation finished'), ('check-environments', 'Environment check finished'),
    ('run', 'Run finished'), ('continue', 'Run finished')])
def test_success_title_distinguishes_validation_preparation_and_execution(workspace, action, label):
    initialized(workspace)
    job(workspace, action=action)
    row = notifications.panel(workspace)['items'][0]
    assert row['title'] == label
    assert 'safe' not in row['body'].lower() and 'validated' not in row['body'].lower()


def test_retry_has_new_job_identity_without_replaying_old_result(workspace):
    initialized(workspace)
    first = job(workspace, status='failed')
    old = notifications.panel(workspace)['items'][0]
    notifications.mark_read(workspace, {'ids': [old['id']]})
    second = job(workspace)
    doc = notifications.panel(workspace)
    assert len(doc['items']) == 2 and doc['unread'] == 1
    assert {row['target']['id'] for row in doc['items']} == {first, second}
    assert doc['desktop_candidates'][0]['target']['id'] == second


def waiting_run():
    return {'id': 'run-one', 'tool': 'hybas', 'status': 'awaiting_review',
            'identification': {'state': 'awaiting_review', 'evidence_sha256': 'a' * 64}}


def test_pause_for_review_is_not_a_finished_run(workspace):
    initialized(workspace)
    job(workspace)
    row = waiting_run()
    doc = notifications.panel(workspace, runs=[row])
    assert [item['category'] for item in doc['items']] == ['review']
    assert doc['items'][0]['title'] == 'Run waiting for review'
    assert doc['items'][0]['target'] == {'kind': 'run', 'id': row['id']}
    assert doc['unread'] == 1


def test_resolving_pause_does_not_relabel_old_paused_job_as_finished_run(workspace):
    initialized(workspace)
    job(workspace)
    run = waiting_run()
    notifications.panel(workspace, runs=[run])
    run.update(status='draft', identification={'state': 'continuation_ready', 'evidence_sha256': 'a' * 64})
    doc = notifications.panel(workspace, runs=[run])
    assert not any(row['title'] == 'Run finished' for row in doc['items'])
    assert doc['unread'] == 0 and doc['desktop_candidates'] == []


def test_review_resolution_and_archive_retire_alerts(workspace):
    run = waiting_run()
    first = notifications.panel(workspace, runs=[run])
    key = first['items'][0]['id']
    assert first['unread'] == 1
    run['archived'] = True
    doc = notifications.panel(workspace, runs=[run])
    assert doc['items'][0]['id'] == key
    assert doc['items'][0]['read'] and not doc['items'][0]['active']
    assert doc['desktop_candidates'] == []
    assert notifications.claim_desktop(workspace, {'ids': [key]})['notification_delivery'] == []


def test_claim_action_reconciles_resolved_and_archived_review(workspace):
    run = waiting_run()
    # Saved runs have an identification state but no derived UI status field.
    saved = deepcopy(run)
    saved.pop('status')
    workspace.put('runs', saved['id'], saved)
    doc = notifications.panel(workspace, runs=[run])
    key = doc['items'][0]['id']
    workspace.put('run_visibility', run['id'], {'id': run['id'], 'archived': True})
    result = dispatch(workspace, 'claim_desktop_notifications', {'ids': [key]})
    assert result['notification_delivery'] == []
    assert notifications.panel(workspace, runs=[{**run, 'archived': True}])['unread'] == 0


def test_cached_update_notification_retired_after_install_or_source_change(workspace):
    workspace.put('notification_test', 'candidates', [candidate()])
    doc = notifications.panel(workspace)
    key = doc['items'][0]['id']
    assert doc['unread'] == 1
    workspace.put('notification_test', 'candidates', [])
    result = dispatch(workspace, 'claim_desktop_notifications', {'ids': [key]})
    assert result['notification_delivery'] == []
    after = notifications.panel(workspace)
    assert after['unread'] == 0 and not after['items'][0]['active']


def test_offline_retained_candidate_does_not_repeatedly_alert(workspace):
    workspace.put('notification_test', 'candidates', [candidate()])
    first = notifications.panel(workspace)
    key = first['items'][0]['id']
    assert len(notifications.claim_desktop(workspace, {'ids': [key]})['notification_delivery']) == 1
    assert notifications.panel(Store(workspace.home))['desktop_candidates'] == []
    assert notifications.panel(workspace)['unread'] == 1


def test_category_mute_marks_existing_alerts_read_and_keeps_history(workspace):
    initialized(workspace)
    job(workspace)
    first = notifications.panel(workspace)
    notifications.configure(workspace, preferences(jobs=False, auto_update_checks=False))
    job(workspace, status='failed')
    muted = notifications.panel(workspace)
    assert len(muted['items']) == 2 and muted['unread'] == 0
    assert all(row['read'] for row in muted['items'])
    assert muted['desktop_candidates'] == []
    assert not muted['settings']['jobs'] and not muted['settings']['auto_update_checks']
    notifications.configure(workspace, preferences())
    assert notifications.panel(workspace)['unread'] == 0
    job(workspace)
    assert notifications.panel(workspace)['unread'] == 1
    assert any(row['id'] == first['items'][0]['id'] for row in muted['items'])


def test_mark_read_selected_all_and_pruned_ids_are_idempotent(workspace):
    initialized(workspace)
    job(workspace)
    job(workspace, status='failed')
    doc = notifications.panel(workspace)
    key = doc['items'][0]['id']
    dispatch(workspace, 'mark_notifications_read', {'ids': [key, key, 'already-pruned']})
    assert notifications.panel(workspace)['unread'] == 1
    dispatch(workspace, 'mark_notifications_read', {'all': True})
    assert notifications.panel(workspace)['unread'] == 0
    dispatch(workspace, 'mark_notifications_read', {'all': True})
    assert len(notifications.panel(workspace)['items']) == 2


def test_desktop_claim_is_once_across_concurrent_tabs_and_keeps_in_app_unread(workspace):
    initialized(workspace)
    job(workspace)
    key = notifications.panel(workspace)['items'][0]['id']
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=10)
        return notifications.claim_desktop(Store(workspace.home), {'ids': [key]})['notification_delivery']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(len(rows) for rows in results) == 1
    after = notifications.panel(workspace)
    assert after['unread'] == 1 and after['desktop_candidates'] == []
    assert after['items'][0]['desktop_attempted'] is True


@pytest.mark.parametrize('ids', [None, [], 'a', [1], ['a'] * 4])
def test_desktop_claim_rejects_invalid_or_unbounded_selection(workspace, ids):
    with pytest.raises(ValueError, match='between one and 3'):
        notifications.claim_desktop(workspace, {'ids': ids})


@pytest.mark.parametrize('change', [lambda p: p.pop('jobs'), lambda p: p.update(jobs='yes'),
    lambda p: p.update(auto_update_checks=1), lambda p: p.update(extra=True)])
def test_preferences_are_strict_booleans_and_complete(workspace, change):
    payload = preferences()
    change(payload)
    with pytest.raises(ValueError):
        notifications.configure(workspace, payload)
    assert notifications.settings(workspace) == preferences()


def test_retention_keeps_latest_results_and_tombstones_prevent_reappearance(workspace, monkeypatch):
    monkeypatch.setattr(notifications, 'MAX_ITEMS', 2)
    ids = [job(workspace) for _ in range(4)]
    first = notifications.panel(workspace)
    assert [row['target']['id'] for row in first['items']] == ids[-1:-3:-1]
    retained = {row['id'] for row in first['items']}
    for _ in range(3):
        assert {row['id'] for row in notifications.panel(workspace)['items']} == retained
    newest = job(workspace)
    after = notifications.panel(workspace)
    assert len(after['items']) == 2 and after['items'][0]['target']['id'] == newest


def test_alerts_do_not_expose_input_paths_error_text_or_sample_labels(workspace):
    initialized(workspace)
    jid = job(workspace, status='failed')
    sensitive = '/home/private/secret-isolate-123'
    workspace.update_job(jid, error=sensitive)
    rows = [{'id': 'run-one', 'tool': 'hybas', 'label': 'secret-label',
             'sample': {'id': 'secret-sample', 'path': sensitive}}]
    item = notifications.panel(workspace, runs=rows)['items'][0]
    text = item['title'] + ' ' + item['body']
    assert sensitive not in text and 'secret-label' not in text and 'secret-sample' not in text
    assert item['target']['id'] == jid


def test_notifications_preserve_jobs_runs_inputs_and_configuration(workspace, tmp_path):
    path = tmp_path / 'sample.txt'
    path.write_text('retained input')
    workspace.put('samples', 'sample', {'id': 'sample', 'path': str(path.parent)})
    workspace.put('runs', 'run-one', {'id': 'run-one', 'tool': 'hybas', 'config': 'unchanged: true'})
    workspace.put('configs', 'hybas', 'manual: retained')
    initialized(workspace)
    job(workspace)
    before = {key: deepcopy(workspace.all(key)) for key in ('samples', 'runs', 'configs')}
    before['jobs'] = deepcopy(workspace.jobs())
    doc = notifications.panel(workspace)
    notifications.claim_desktop(workspace, {'ids': [doc['items'][0]['id']]})
    notifications.mark_read(workspace, {'all': True})
    notifications.configure(workspace, preferences(updates=False))
    assert before == {**{key: workspace.all(key) for key in ('samples', 'runs', 'configs')}, 'jobs': workspace.jobs()}
    assert path.read_text() == 'retained input'


def test_collection_uses_preferences_current_at_its_transaction(workspace, monkeypatch):
    initialized(workspace)
    job(workspace)
    original = notifications.settings
    def changed_after_read(store):
        previous = original(store)
        store.put('notification_state', 'preferences', {'jobs': False, 'review': True, 'updates': True})
        return previous
    monkeypatch.setattr(notifications, 'settings', changed_after_read)
    doc = notifications.panel(workspace)
    assert doc['items'][0]['read'] and not doc['unread']
    monkeypatch.setattr(notifications, 'settings', original)
    workspace.put('notification_state', 'preferences', {'jobs': True, 'review': True, 'updates': True})
    assert notifications.panel(workspace)['desktop_candidates'] == []


def test_new_paused_attempt_alerts_even_when_identification_evidence_is_unchanged(workspace):
    initialized(workspace)
    job(workspace)
    run = waiting_run()
    first = notifications.panel(workspace, runs=[run])
    notifications.mark_read(workspace, {'all': True})
    job(workspace)
    again = notifications.panel(workspace, runs=[run])
    assert again['unread'] == 1
    assert len(again['desktop_candidates']) == 1
    assert again['desktop_candidates'][0]['id'] != first['items'][0]['id']
    assert sum(item.get('active', True) for item in again['items']) == 1
