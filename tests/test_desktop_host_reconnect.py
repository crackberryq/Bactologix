"""A WSL observer wakes late while the original Windows launcher is still alive."""
import sqlite3
import time
import uuid

import pytest

from bactologix import desktop, desktop_reconnect, engine
from bactologix.store import Store
from test_desktop_heartbeat import Clock


def windows_files(directory, owner, stamp):
    desktop.atomic(directory / 'heartbeat.json', {'session': owner, 'time': int(stamp)})
    desktop.atomic(directory / 'windows-storage.json', {
        'schema': 1, 'session': owner, 'time': stamp, 'distribution': 'Ubuntu-22.04',
        'volumes': [], 'errors': [], 'swap_required': None,
    })


@pytest.fixture
def connected(tmp_path, monkeypatch):
    clock = Clock()
    clock.wall = float(int(time.time()))
    monkeypatch.setattr(desktop, 'time', clock)
    store = Store(tmp_path / 'workspace')
    key = uuid.uuid4().hex
    directory = tmp_path / 'Bactologix-Dev' / 'sessions' / key
    directory.mkdir(parents=True)
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu-22.04')
    monkeypatch.setenv(desktop.STORAGE_SESSION_DIR, str(directory))
    windows_files(directory, key, clock.wall)
    session = desktop.Session(store, directory, key)
    session.tick('', True)
    monkeypatch.setenv(desktop.ENV, key)
    job = store.submit('workflow', {'run_id': 'run-1'})
    assert store.claim()['id'] == job
    return clock, session, store, job


def wake_after_gap(clock, session):
    # Both WSL readers have been unable to run for the entire gap. Windows
    # continues on its own clock, and the installed launcher uses whole seconds.
    clock.advance(123.8)
    windows_files(session.root, session.key, int(clock.wall))
    assert clock.wall - desktop.read(session.root / 'heartbeat.json')['time'] == pytest.approx(.8)


def worker_stop_reason(store, job):
    state = store.control_state(job)
    return desktop.session_stop_reason(state['session'], store=store, owner=state['owner'], job_id=job)


@pytest.mark.parametrize('first_reader', ['worker', 'launcher'])
def test_same_windows_owner_after_both_wsl_observers_miss_123_seconds(connected, first_reader):
    clock, session, store, job = connected
    wake_after_gap(clock, session)
    if first_reader == 'worker':
        assert not worker_stop_reason(store, job)
    assert not session.tick('', True)
    assert session.phase == 'ready'
    assert store.job(job)['cancel'] == 0
    assert not worker_stop_reason(store, job)


def test_confirmed_close_during_global_stall_wins_when_worker_wakes_first(connected):
    clock, session, store, job = connected
    wake_after_gap(clock, session)
    desktop.atomic(session.root / 'request.json', {
        'session': session.key, 'action': 'confirm_close', 'nonce': 'confirmed-after-stall',
    })
    assert worker_stop_reason(store, job) == (
        'The owning desktop session requested a stop (Window closed).')
    session.tick('', True)
    assert store.job(job)['cancel'] == 1
    assert session.reason == 'Window closed'


@pytest.mark.parametrize('which', ['stale', 'other-session'])
def test_stale_or_other_windows_owner_cannot_renew_lease(connected, which):
    clock, session, store, job = connected
    clock.advance(123.8)
    if which == 'other-session':
        windows_files(session.root, uuid.uuid4().hex, clock.wall)
    state = store.control_state(job)
    assert desktop._session_lease_reason(state['session'], store=store, owner=state['owner']) == desktop.HEARTBEAT_EXPIRED
    assert not worker_stop_reason(store, job)
    session.tick('', True)
    assert store.job(job)['cancel'] == 0


def test_disconnected_session_recovers_on_fresh_host_files(connected):
    clock, session, store, job = connected
    # Both control checks repeatedly observe the silent host for a whole lease.
    for _ in range(3):
        clock.advance(9)
        assert not session.tick('', True)
        assert not worker_stop_reason(store, job)
    clock.advance(4)
    session.tick('', True)
    assert store.job(job)['cancel'] == 0
    wake_after_gap(clock, session)
    assert not worker_stop_reason(store, job)
    session.tick('', True)
    assert session.phase == 'ready'
    assert store.job(job)['cancel'] == 0


def test_worker_uses_the_job_owners_bound_directory(connected, monkeypatch, tmp_path):
    clock, session, store, job = connected
    clock.advance(123.8)
    # A shared worker may inherit a different desktop's environment. Even a
    # directory with the same basename cannot substitute for the stored path.
    other = tmp_path / 'other-workspace' / session.key
    other.mkdir(parents=True)
    windows_files(other, session.key, clock.wall)
    monkeypatch.setenv(desktop.STORAGE_SESSION_DIR, str(other))
    state = store.control_state(job)
    assert desktop._session_lease_reason(state['session'], store=store, owner=state['owner']) == desktop.HEARTBEAT_EXPIRED
    assert not worker_stop_reason(store, job)


def test_advancing_second_windows_pulse_without_storage_helper(connected, monkeypatch):
    clock, session, store, job = connected
    wake_after_gap(clock, session)
    (session.root / 'windows-storage.json').unlink()
    read = desktop_reconnect._read
    pulses = []

    def host_read(directory, name):
        if name == 'heartbeat.json':
            if pulses:
                clock.advance(1)
                desktop.atomic(directory / name, {'session': session.key, 'time': int(clock.wall)})
            pulses.append(int(clock.wall))
        return read(directory, name)

    monkeypatch.setattr(desktop_reconnect, '_read', host_read)
    assert not worker_stop_reason(store, job)
    assert len(pulses) >= 2 and pulses[1] > pulses[0]


@pytest.mark.parametrize(('stop', 'reason'), [
    ('confirm_close', 'The owning desktop session requested a stop (Window closed).'),
    ('cancel_job', 'A stop request was recorded for this job.'),
])
def test_stop_racing_a_fresh_native_witness_takes_priority(connected, monkeypatch, stop, reason):
    clock, session, store, job = connected
    wake_after_gap(clock, session)
    read = desktop_reconnect._read

    def host_read(directory, name):
        record = read(directory, name)
        if name == 'windows-storage.json':
            if stop == 'cancel_job':
                store.cancel(job)
            else:
                desktop.atomic(directory / 'request.json', {
                    'session': session.key, 'action': 'confirm_close', 'nonce': 'concurrent-close',
                })
        return record

    monkeypatch.setattr(desktop_reconnect, '_read', host_read)
    assert worker_stop_reason(store, job) == reason


def test_host_recheck_leaves_sqlite_available_to_other_writers(connected, monkeypatch):
    clock, session, store, job = connected
    wake_after_gap(clock, session)
    read = desktop_reconnect._read
    checked = []

    def host_read(directory, name):
        with store.connect(timeout=0) as db:
            db.execute('BEGIN IMMEDIATE')
            db.rollback()
        checked.append(name)
        return read(directory, name)

    monkeypatch.setattr(desktop_reconnect, '_read', host_read)
    assert not worker_stop_reason(store, job)
    session.tick('', True)
    assert 'heartbeat.json' in checked
    assert store.job(job)['cancel'] == 0


def test_reused_linux_supervisor_pid_cannot_validate_windows_files(connected):
    clock, session, store, job = connected
    wake_after_gap(clock, session)
    record = store.get('desktop_sessions', session.key)
    record['supervisor']['created'] -= 60
    store.put('desktop_sessions', session.key, record)
    state = store.control_state(job)
    assert desktop._session_lease_reason(state['session'], store=store, owner=state['owner']) == desktop.HEARTBEAT_EXPIRED
    assert not worker_stop_reason(store, job)


def test_workspace_relocks_during_host_check_then_worker_honors_stop(connected, monkeypatch):
    clock, session, store, job = connected
    ctx = engine.Context(store, store.job(job))
    wake_after_gap(clock, session)
    read = desktop_reconnect._read
    locker = sqlite3.connect(store.home / 'workspace.sqlite3', check_same_thread=False)

    def host_read(directory, name):
        record = read(directory, name)
        if name == 'windows-storage.json':
            locker.execute('PRAGMA locking_mode=EXCLUSIVE')
            locker.execute('BEGIN EXCLUSIVE')
        return record

    monkeypatch.setattr(desktop_reconnect, '_read', host_read)
    try:
        try:
            assert not ctx.refresh_desktop_cancel(poll=True)
            assert ctx.monitor['state'] == 'degraded'
            assert ctx.control_deadlines and ctx.cancel_reason is None
        finally:
            locker.close()
        store.cancel(job)
        assert ctx.refresh_desktop_cancel(poll=True)
        assert ctx.cancel_reason.startswith('A stop request was recorded for this job')
    finally:
        ctx.log.close()


def test_launcher_wakes_from_blocked_database_call_after_global_stall(connected, monkeypatch):
    clock, session, store, job = connected
    tick = session.tick
    attempts = []

    def database_delayed_tick(url, ready):
        attempts.append(True)
        if len(attempts) == 1:
            assert session.observe_heartbeat()
            # The main loop entered SQLite before both WSL observers stopped
            # receiving CPU. The call finally reports BUSY after they wake.
            wake_after_gap(clock, session)
            raise sqlite3.OperationalError('database is locked')
        return tick(url, ready)

    monkeypatch.setattr(session, 'tick', database_delayed_tick)
    clock.sleep = clock.advance
    assert not session.poll('', True)
    assert len(attempts) == 2
    assert session.phase == 'ready' and not store.job(job)['cancel']
