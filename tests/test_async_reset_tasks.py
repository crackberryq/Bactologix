"""UI observations do not block navigation or authorize saved-run execution."""
import threading
import time
from types import SimpleNamespace

import pytest

from bactologix import async_tasks as tasks, ui_backend
from bactologix.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'workspace')


def finished(store, token, owner='tab-a'):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        row = tasks.poll(store, owner, token)
        with tasks._LOCK:
            active = tasks._TASKS[token]['active']
        if not active:
            return row
        time.sleep(.005)
    pytest.fail('Background fixture did not finish')


def test_slow_observation_returns_immediately_and_ignores_workflow_queue(store, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    def dispatch(*args):
        entered.set()
        assert release.wait(2)
        return {'database_scan': {'tool': 'hybas'}}
    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    store.submit('workflow', {'run_id': 'long-run', 'action': 'run'})
    started = time.monotonic()
    row = tasks.submit(store, 'tab-a', 'request-a', 'factory_reset_preview', {'tool': 'hybas'})
    try:
        assert time.monotonic() - started < .2
        assert entered.wait(1)
        assert row['status'] == 'running'
        assert tasks.poll(store, 'tab-a', row['token'])['status'] == 'running'
    finally:
        release.set()
    assert finished(store, row['token'])['result']['database_scan']['tool'] == 'hybas'
    assert len(store.jobs()) == 1  # No job inserted behind the real workflow.


def test_request_deduplication_and_session_workspace_isolation(store, tmp_path, monkeypatch):
    monkeypatch.setattr(ui_backend, 'dispatch', lambda *a: {'database_check': {'path': '/fixture'}})
    args = (store, 'tab-a', 'request-a', 'factory_reset_preview', {'tool': 'hybas'})
    row = tasks.submit(*args)
    assert finished(store, row['token'])['status'] == 'completed'
    assert tasks.submit(*args)['token'] == row['token']
    with pytest.raises(ValueError, match='different task'):
        tasks.submit(store, 'tab-a', 'request-a', 'factory_reset_preview', {'tool': 'basis'})
    with pytest.raises(ValueError, match='another session'):
        tasks.poll(store, 'tab-b', row['token'])
    with pytest.raises(ValueError, match='another session'):
        tasks.poll(Store(tmp_path / 'other'), 'tab-a', row['token'])


def test_new_request_discards_late_old_result(store, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    def dispatch(store, kind, payload):
        if payload['root'] == 'old':
            entered.set()
            assert release.wait(2)
        return {'database_scan': {'root': payload['root']}}
    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    old = tasks.submit(store, 'tab-a', 'old', 'factory_reset_preview', {'tool': 'hybas', 'root': 'old'})
    try:
        assert entered.wait(1)
        new = tasks.submit(store, 'tab-a', 'new', 'factory_reset_preview', {'tool': 'hybas', 'root': 'new'})
        assert finished(store, new['token'])['result']['database_scan']['root'] == 'new'
        assert tasks.poll(store, 'tab-a', old['token'])['status'] == 'superseded'
    finally:
        release.set()
    assert 'result' not in finished(store, old['token'])


def test_cancel_and_timeout_keep_capacity_until_filesystem_work_returns(store, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    clock = [100.0]
    monkeypatch.setattr(tasks, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(tasks, 'MAX_ACTIVE_PER_WORKSPACE', 1)
    def dispatch(*args):
        entered.set()
        assert release.wait(2)
        return {'unexpected': 'late result'}
    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    row = tasks.submit(store, 'tab-a', 'first', 'factory_reset_preview', {'tool': 'hybas'})
    try:
        assert entered.wait(1)
        clock[0] += tasks.TASK_TIMEOUTS['factory_reset_preview'] + 1
        assert tasks.poll(store, 'tab-a', row['token'])['status'] == 'timed_out'
        assert tasks.cancel(store, 'tab-a', row['token'])['status'] == 'cancelled'
        with pytest.raises(ValueError, match='still finishing'):
            tasks.submit(store, 'tab-a', 'second', 'factory_reset_preview', {'tool': 'hybas'})
    finally:
        release.set()
    assert 'result' not in finished(store, row['token'])


def test_saved_settings_change_rejects_completed_result(store, monkeypatch):
    monkeypatch.setattr(ui_backend, 'dispatch', lambda *a: {'database_scan': {}})
    row = tasks.submit(store, 'tab-a', 'request', 'factory_reset_preview', {'tool': 'hybas'})
    assert finished(store, row['token'])['status'] == 'completed'
    store.put('configs', 'hybas', 'threads: 4\n')
    result = tasks.poll(store, 'tab-a', row['token'])
    assert result['status'] == 'stale' and 'result' not in result


def test_reset_preview_has_its_own_budget_without_freeing_timed_out_threads(store, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    clock = [100.0]
    monkeypatch.setattr(tasks, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def dispatch(*args):
        entered.set()
        assert release.wait(2)
        return {'reset_plan': {'id': 'fixture'}}
    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    row = tasks.submit(store, 'tab-a', 'reset-preview', 'factory_reset_preview', {'delete_databases': False})
    try:
        assert entered.wait(1)
        clock[0] += tasks.TASK_TIMEOUT + 1
        assert tasks.poll(store, 'tab-a', row['token'])['status'] == 'running'
        clock[0] += tasks.TASK_TIMEOUTS['factory_reset_preview']
        assert tasks.poll(store, 'tab-a', row['token'])['status'] == 'timed_out'
        assert tasks._ACTIVE[tasks._workspace(store)] == 1
    finally:
        release.set()
    assert 'result' not in finished(store, row['token'])


def test_failed_tasks_are_local_and_expire(store, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(tasks, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def dispatch(*args):
        raise ValueError('Choose an existing folder')
    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    row = tasks.submit(store, 'tab-a', 'request', 'factory_reset_preview', {'tool': 'hybas'})
    result = finished(store, row['token'])
    assert result['status'] == 'failed' and result['message'] == 'Choose an existing folder'
    clock[0] += tasks.RESULT_TTL + 1
    with pytest.raises(ValueError, match='expired'):
        tasks.poll(store, 'tab-a', row['token'])


def test_actions_that_can_launch_or_save_are_not_background_observations(store):
    for kind in ('run', 'revise_run', 'factory_reset_execute', 'execute_storage_cleanup', 'save_databases', 'recommend_swap', 'database_redownload_preview'):
        with pytest.raises(ValueError, match='cannot run'):
            tasks.submit(store, 'tab-a', kind, kind, {})


def test_result_history_is_bounded(store, monkeypatch):
    monkeypatch.setattr(tasks, 'MAX_TASKS_PER_WORKSPACE', 2)
    monkeypatch.setattr(ui_backend, 'dispatch', lambda *a: {})
    tokens = []
    for index in range(3):
        row = tasks.submit(store, 'tab-a', str(index), 'factory_reset_preview', {'tool': 'hybas'})
        finished(store, row['token'])
        tokens.append(row['token'])
    with pytest.raises(ValueError, match='expired'):
        tasks.poll(store, 'tab-a', tokens[0])
    assert tasks.poll(store, 'tab-a', tokens[-1])['status'] == 'completed'
