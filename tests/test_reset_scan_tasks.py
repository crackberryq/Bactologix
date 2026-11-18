"""Large observations publish local progress and stop at cooperative checkpoints."""
import threading
import time

import pytest

from bactologix import async_tasks as tasks, ui_backend
from bactologix.store import Store


@pytest.mark.parametrize('invalidate', ['cancel', 'supersede', 'timeout'])
def test_scan_progress_is_owned_and_cancelled_work_stops_before_result(tmp_path, monkeypatch, invalidate):
    store = Store(tmp_path / 'workspace')
    ready, release, stopped = threading.Event(), threading.Event(), threading.Event()
    passed_checkpoint = []

    def dispatch(_store, _kind, payload):
        if payload.get('new'):
            return {'new': True}
        tasks.checkpoint(stage='Reviewing managed files', entries=310_000, path='/fixture/cache')
        ready.set()
        try:
            assert release.wait(3)
            tasks.checkpoint(stage='Saving complete review', entries=320_000)
            passed_checkpoint.append(True)
            return {'unexpected': 'cancelled review'}
        finally:
            stopped.set()

    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    row = tasks.submit(store, 'owner', 'large-preview', 'factory_reset_preview', {})
    try:
        assert ready.wait(1)
        progress = tasks.poll(store, 'owner', row['token'])['progress']
        assert progress['entries'] == 310_000 and progress['path'] == '/fixture/cache'
        with pytest.raises(ValueError, match='another session'):
            tasks.poll(store, 'other-owner', row['token'])
        if invalidate == 'cancel':
            tasks.cancel(store, 'owner', row['token'])
        elif invalidate == 'supersede':
            tasks.submit(store, 'owner', 'new-preview', 'factory_reset_preview', {'new': True})
        else:
            with tasks._LOCK:
                tasks._TASKS[row['token']]['started'] -= tasks.TASK_TIMEOUTS['factory_reset_preview'] + 1
            assert tasks.poll(store, 'owner', row['token'])['status'] == 'timed_out'
    finally:
        release.set()
    assert stopped.wait(1)
    deadline = time.monotonic() + 2
    while tasks._TASKS[row['token']]['active'] and time.monotonic() < deadline:
        time.sleep(.005)
    assert not tasks._TASKS[row['token']]['active']
    assert not passed_checkpoint
    assert 'result' not in tasks.poll(store, 'owner', row['token'])
    assert not store.all('factory_reset_plans') and not store.jobs()


def test_checkpoint_without_background_session_is_inert():
    tasks.checkpoint(stage='Direct API preview', entries=5)
