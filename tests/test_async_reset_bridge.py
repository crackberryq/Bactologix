"""The component bridge starts observations without blocking its request thread."""
import threading
import time

import pytest

from bactologix import async_tasks, ui_backend
from bactologix.store import Store


def test_bridge_owner_is_server_generated_and_duplicate_request_stays_one_task(tmp_path, monkeypatch):
    store, session = Store(tmp_path / 'workspace'), {}
    entered, release = threading.Event(), threading.Event()
    calls = []
    def dispatch(*args):
        calls.append(args[1])
        entered.set()
        assert release.wait(2)
        return {'database_scan': {'tool': 'hybas'}}
    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    request = {'id': 'scan', 'kind': 'factory_reset_preview', 'payload': {'tool': 'hybas', 'owner': 'browser-value'}}
    started = time.monotonic()
    result = ui_backend.handle_request(store, request, session)
    try:
        assert time.monotonic() - started < .2
        assert entered.wait(1)
        assert result['async_task']['status'] == 'running'
        assert session['ui-task-owner'] != 'browser-value'
        assert ui_backend.handle_request(store, request, session) == result
        foreign = ui_backend.handle_request(store, {'id': 'poll', 'kind': 'poll_ui_task',
            'payload': {'token': result['async_task']['token'], 'owner': session['ui-task-owner']}}, {})
        assert not foreign['ok']
    finally:
        release.set()
    for index in range(100):
        response = ui_backend.handle_request(store, {'id': 'poll-' + str(index), 'kind': 'poll_ui_task',
            'payload': {'token': result['async_task']['token']}}, session)
        if response['async_task']['status'] != 'running':
            break
        time.sleep(.005)
    assert response['async_task']['result']['database_scan']['tool'] == 'hybas'
    assert calls == ['factory_reset_preview']


def test_poll_snapshot_is_display_cache_not_a_fresh_authorization(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    initial = ui_backend.snapshot(store)
    monkeypatch.setattr(ui_backend, 'doctor', lambda *a: (_ for _ in ()).throw(AssertionError('Full snapshot must not run for task polling')))
    cached = ui_backend.snapshot(store, cached=True)
    assert cached == initial
    cached['runs'].append({'id': 'changed browser copy'})
    assert ui_backend.snapshot(store, cached=True) == initial
    assert {'factory_reset_preview', 'inspect_storage', 'inspect_storage_cleanup',
            'scan_storage', 'preview_storage_cleanup', 'scan_databases',
            'check_database', 'edit_run_settings'} == async_tasks.ACTIONS
    assert 'factory_reset_execute' not in async_tasks.ACTIONS


def test_snapshot_keeps_interrupted_monitoring_distinct_from_job_success(tmp_path):
    from bactologix import supervision
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    store.update_job(jid, status='interrupted')
    supervision.save(store, jid, {'state': 'interrupted', 'cleanup_confirmed': False,
                                 'detail': 'Fixture monitoring interruption'})
    job = next(j for j in ui_backend.snapshot(store)['jobs'] if j['id'] == jid)
    assert job['status'] == 'interrupted'
    assert job['monitoring']['state'] == 'interrupted'
    assert job['monitoring']['cleanup_confirmed'] is False


def test_direct_stop_cannot_claim_control_after_unconfirmed_native_exit(tmp_path, monkeypatch):
    from bactologix import queue, supervision
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    store.update_job(jid, status='running')
    supervision.save(store, jid, {'state': 'interrupted', 'cleanup_confirmed': False})
    monkeypatch.setattr(queue, 'reconcile_interrupted', lambda _: [])
    monkeypatch.setattr(store, 'cancel', lambda _: pytest.fail('Must not acknowledge another stop'))
    with pytest.raises(ValueError, match='native process exit is still unconfirmed'):
        ui_backend.dispatch(store, 'cancel_job', {'id': jid})
