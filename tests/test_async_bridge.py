"""The component bridge starts observations without blocking its request thread."""
import threading
import time

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
    request = {'id': 'scan', 'kind': 'scan_databases', 'payload': {'tool': 'hybas', 'owner': 'browser-value'}}
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
    assert calls == ['scan_databases']


def test_poll_snapshot_is_display_cache_not_a_fresh_authorization(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    initial = ui_backend.snapshot(store)
    monkeypatch.setattr(ui_backend, 'doctor', lambda *a: (_ for _ in ()).throw(AssertionError('Full snapshot must not run for task polling')))
    cached = ui_backend.snapshot(store, cached=True)
    assert cached == initial
    cached['runs'].append({'id': 'changed browser copy'})
    assert ui_backend.snapshot(store, cached=True) == initial
    assert {'factory_reset_preview', 'database_redownload_preview', 'recommend_swap'} <= async_tasks.ACTIONS
    assert 'factory_reset_execute' not in async_tasks.ACTIONS
