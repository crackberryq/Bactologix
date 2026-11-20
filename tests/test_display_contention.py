"""Real SQLite read and write contention at the Streamlit display boundary."""
import fcntl
import sqlite3
import time

import pytest

from bactologix import ui_backend, update_checks
from bactologix.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_UPDATE_CHECKS', '0')
    return Store(tmp_path / 'workspace')


def test_tab_keeps_last_snapshot_and_recovers_without_replaying_resume(store, monkeypatch):
    previous = ui_backend.display_snapshot(store)
    session = {}
    def submit(*args):
        return {'job': store.submit('smoke', {})}
    monkeypatch.setattr(ui_backend, 'dispatch', submit)
    request = {'id': 'original-action', 'kind': 'workflow', 'payload': {}}
    accepted = ui_backend.handle_request(store, request, session)
    with (store.home / 'worker.lock').open('a+') as owner, store.connect() as locker:
        fcntl.flock(owner, fcntl.LOCK_EX)
        locker.execute('PRAGMA locking_mode=EXCLUSIVE')
        locker.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        stale = ui_backend.display_snapshot(store)
        assert time.monotonic() - started < 3
        assert stale['workspace_busy'] and stale['jobs'] == previous['jobs']
        assert ui_backend.handle_request(store, request, session) == accepted
    fresh = ui_backend.display_snapshot(store)
    assert not fresh.get('workspace_busy')
    assert [row['id'] for row in fresh['jobs']] == [accepted['job']]
    assert not previous.get('workspace_busy')


def test_first_view_waits_without_claiming_fresh_data_and_nonlocks_propagate(store, monkeypatch):
    with (store.home / 'worker.lock').open('a+') as owner, store.connect() as locker:
        fcntl.flock(owner, fcntl.LOCK_EX)
        locker.execute('PRAGMA locking_mode=EXCLUSIVE')
        locker.execute('BEGIN EXCLUSIVE')
        assert ui_backend.display_snapshot(store) is None
    assert ui_backend.display_snapshot(store)['jobs'] == []
    def corrupt(*args, **kwargs):
        raise sqlite3.DatabaseError('database disk image is malformed')
    monkeypatch.setattr(ui_backend, 'snapshot', corrupt)
    with pytest.raises(sqlite3.DatabaseError, match='malformed'):
        ui_backend.display_snapshot(store)


def test_due_or_recent_update_poll_under_wal_writer(store, monkeypatch):
    # A normal WAL writer still allows real reads. Even a due background check
    # that cannot acquire its optional write lock leaves the view fresh.
    state = ui_backend.display_snapshot(store)
    monkeypatch.delenv('BACTOLOGIX_UPDATE_CHECKS')
    monkeypatch.setattr(ui_backend, 'snapshot', lambda *args, **kwargs: state)
    with store.connect() as locker:
        locker.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        assert ui_backend.display_snapshot(store) is state
        assert time.monotonic() - started < 3
    store.put('update_checks', update_checks.STATE_KEY, {'attempt_epoch': time.time()})
    with store.connect() as locker:
        locker.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        assert update_checks.tick(store) is False
        assert time.monotonic() - started < 3


def test_streamlit_workspace_stays_mounted_across_exclusive_lock(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import bactologix

    home = tmp_path / 'streamlit-workspace'
    for key in ('BACTOLOGIX_HOME', 'BACTOLOGIX_DEV_HOME'):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setenv('BACTOLOGIX_UPDATE_CHECKS', '0')
    store = Store(home)
    page = AppTest.from_file(str(Path(bactologix.__file__).with_name('app.py')), default_timeout=10)

    def check(busy):
        page.run()
        assert not page.exception
        component, = page.get('bidi_component')
        assert json.loads(component.proto.json)['workspace'].get('workspace_busy', False) == busy

    with (home / 'worker.lock').open('a+') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        check(False)
        with store.connect() as locker:
            locker.execute('PRAGMA locking_mode=EXCLUSIVE')
            locker.execute('BEGIN EXCLUSIVE')
            check(True)
        check(False)
