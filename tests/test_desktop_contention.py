"""Desktop lease contention must neither reopen a stop nor strand the UI child."""
from contextlib import contextmanager
import json
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import Mock
import uuid

import pytest

from bactologix import desktop, launcher, restart
from bactologix.store import CONTROL_TIMEOUT, Store


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.delenv(desktop.ENV, raising=False)
    store = Store(tmp_path / 'workspace')
    key = uuid.uuid4().hex
    control = tmp_path / 'control'
    control.mkdir()
    item = desktop.Session(store, control, key)
    desktop.atomic(control / 'heartbeat.json', {'session': key, 'time': time.time()})
    item.tick('http://127.0.0.1:8501', True)
    return store, item


def request(item, action):
    value = {'session': item.key, 'nonce': uuid.uuid4().hex, 'action': action}
    desktop.atomic(item.root / 'request.json', value)
    return value


def watch_busy(store, monkeypatch):
    original = store.connect
    busy = threading.Event()
    @contextmanager
    def connect(**kwargs):
        try:
            with original(**kwargs) as db:
                yield db
        except sqlite3.OperationalError:
            busy.set()
            raise
    monkeypatch.setattr(store, 'connect', connect)
    return original, busy


@pytest.mark.parametrize('closing', [False, True])
def test_real_writer_contention_retries_heartbeat_or_confirmed_close(session, monkeypatch, closing):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    owned = store.submit('smoke', {})
    monkeypatch.delenv(desktop.ENV)
    unrelated = store.submit('smoke', {})
    sent = request(item, 'confirm_close') if closing else None
    original, busy = watch_busy(store, monkeypatch)
    outcome = []
    def tick():
        try:
            outcome.append(item.tick('http://127.0.0.1:8501', True))
        except BaseException as exc:
            outcome.append(exc)
    with original() as writer:
        writer.execute('BEGIN IMMEDIATE')
        thread = threading.Thread(target=tick)
        thread.start()
        try:
            assert busy.wait(3), 'Cause an actual SQLITE_BUSY before releasing the fixture writer'
        finally:
            writer.rollback()
            thread.join(3)
    assert not thread.is_alive() and outcome == [closing]
    state = store.get('desktop_sessions', item.key)
    assert item.phase == state['phase'] == ('stopping' if closing else 'ready')
    assert state['cancelled'] is closing
    assert store.job(owned)['cancel'] == int(closing)
    assert store.job(unrelated)['cancel'] == 0
    if closing:
        assert item.nonce == sent['nonce']


@pytest.mark.parametrize('closing', [False, True])
def test_desktop_poll_survives_an_exhausted_heartbeat_and_reads_pending_close(session, monkeypatch, closing):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    owned = store.submit('install', {})
    tick = item.tick
    exhausted = threading.Event()
    outcome = []

    def observe_tick(url, ready):
        try:
            return tick(url, ready)
        except sqlite3.OperationalError:
            exhausted.set()
            raise

    def poll():
        try:
            outcome.append(item.poll('', True))
        except Exception as exc:
            outcome.append(exc)

    monkeypatch.setattr(item, 'tick', observe_tick)
    with store.connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        thread = threading.Thread(target=poll)
        thread.start()
        try:
            assert exhausted.wait(4), 'Exhaust all short heartbeat attempts before releasing the lock'
            if closing:
                request(item, 'confirm_close')
        finally:
            writer.rollback()
            thread.join(4)
    assert outcome == [closing]
    assert store.job(owned)['cancel'] == int(closing)
    assert store.get('desktop_sessions', item.key)['phase'] == ('stopping' if closing else 'ready')


@pytest.mark.parametrize(('error', 'should_retry'), [
    ('database is locked', True), ('database disk image is malformed', False),
])
def test_desktop_poll_retries_only_transient_control_read_failures_within_lease(session, monkeypatch, error, should_retry):
    _, item = session
    item.phase = "starting"  # Startup still has a bounded retry window.
    attempts = []

    def interrupted(url, ready):
        attempts.append(True)
        raise desktop.MonitoringInterrupted('control read failed') from sqlite3.OperationalError(error)

    monkeypatch.setattr(item, 'tick', interrupted)
    clock = Mock()
    clock.monotonic.side_effect = [0., 1., desktop.LEASE_SECONDS]
    monkeypatch.setattr(desktop, 'time', clock)
    with pytest.raises(desktop.MonitoringInterrupted, match='control read failed'):
        item.poll('', True)
    assert len(attempts) == (2 if should_retry else 1)


def test_failed_close_preserves_nonce_reason_and_durable_phase_until_retry(session, monkeypatch):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    owned = store.submit('smoke', {})
    sent = request(item, 'confirm_close')
    before = store.get('desktop_sessions', item.key)
    with store.connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            item.tick('', True)
        assert item.nonce != sent['nonce']
        assert item.phase == 'ready' and item.pending_stop_reason == 'Window closed'
        saved = json.loads(writer.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (item.key,)).fetchone()[0])
        assert saved == before
        writer.rollback()
    # A later heartbeat must honor the pending Stop, never renew durable ready.
    item.publish('')
    assert item.phase == 'stopping' and store.job(owned)['cancel'] == 1
    assert store.get('desktop_sessions', item.key)['reason'] == 'Window closed'
    item.tick('', True)
    assert item.nonce == sent['nonce']


@pytest.mark.parametrize('previous', [
    {'phase': 'stopping', 'cancelled': True, 'reason': 'Explicit close'},
    {'phase': 'ready', 'cancelled': True, 'reason': 'Recorded cancellation'},
    {'phase': 'stopped', 'cancelled': True, 'reason': 'Previously stopped'},
])
def test_stale_heartbeat_or_reopen_cannot_clear_durable_stop(session, monkeypatch, previous):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    owned = store.submit('smoke', {})
    store.put('desktop_sessions', item.key, dict(previous, last_seen=time.time()))
    item.phase = 'ready'  # A stale in-memory publisher, not a new authority.
    item.publish('')
    item.gate('ready')
    state = store.get('desktop_sessions', item.key)
    assert state['phase'] == ('stopped' if previous['phase'] == 'stopped' else 'stopping')
    assert state['cancelled'] is True and state['reason'] == previous['reason']
    assert item.phase == state['phase'] and store.job(owned)['cancel'] == 1


def test_expired_lease_allows_ready_session_to_continue(session, monkeypatch):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    owned = store.submit('smoke', {})
    store.put('desktop_sessions', item.key, {'phase': 'ready', 'last_seen': time.time() - 100})
    item.publish('')
    assert item.phase == 'ready' and not desktop.owner_cancelled(store, owned)
    assert store.job(owned)['cancel'] == 0


def test_atomic_stop_preserves_running_application_update(session, monkeypatch):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('app_update', {})
    store.claim()
    item.stop('Explicit close')
    assert item.phase == 'stopping'
    assert store.job(jid)['status'] == 'running' and store.job(jid)['cancel'] == 0
    with pytest.raises(RuntimeError, match='owned jobs remain'):
        item.finish('')
    assert store.get('desktop_sessions', item.key)['phase'] == 'stopping'
    store.update_job(jid, status='succeeded')
    item.finish('')
    assert store.get('desktop_sessions', item.key)['phase'] == 'stopped'


def test_persistent_control_failure_still_cleans_ui_child_and_restores_signal(session, monkeypatch):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('smoke', {})
    request(item, 'confirm_close')
    registered = restart.register(store, 'http://127.0.0.1:8501')
    proc = subprocess.Popen([sys.executable, '-u', '-c', 'import time; print("UI fixture",flush=True); time.sleep(60)'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    reader = threading.Thread(target=lambda: list(proc.stdout))
    reader.start()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        with store.connect() as writer:
            writer.execute('BEGIN IMMEDIATE')
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                item.tick('', True)
            started = time.monotonic()
            with pytest.raises(RuntimeError, match='not confirmed'):
                launcher.finish_server(store, registered, proc, reader, previous, item, '', False)
            assert time.monotonic() - started < 3
            assert proc.poll() is not None and not reader.is_alive() and proc.stdout.closed
            assert signal.getsignal(signal.SIGTERM) == previous
            assert unrelated.poll() is None
            status = desktop.read(item.root / 'status.json')
            assert status['phase'] == 'error' and status['jobs'] is None
            assert status['shutdown_confirmed'] is False and status['reason'] == 'Window closed'
            assert item.phase == 'ready'  # No successful durable gate was invented.
            assert tuple(writer.execute('SELECT cancel,status FROM jobs WHERE id=?', (jid,)).fetchone()) == (0, 'queued')
            writer.rollback()
    finally:
        signal.signal(signal.SIGTERM, previous)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        reader.join(2)
        if not proc.stdout.closed:
            proc.stdout.close()
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_restart_handoff_does_not_stop_desktop_owned_work(session, monkeypatch):
    store, item = session
    registered = restart.register(store, '')
    class Process:
        stdout = type('Pipe', (), {'close': lambda self: None})()
        def poll(self): return 0
    class Reader:
        def join(self, **kwargs): pass
        def is_alive(self): return False
    def unexpected(*args, **kwargs):
        raise AssertionError('Update restart must preserve the desktop owner')
    monkeypatch.setattr(item, 'stop', unexpected)
    monkeypatch.setattr(item, 'finish', unexpected)
    launcher.finish_server(store, registered, Process(), Reader(), signal.getsignal(signal.SIGTERM), item, '', True)
    assert store.get('desktop_sessions', item.key)['phase'] == 'ready'


def test_gate_retry_rolls_back_session_and_job_changes_together(session, monkeypatch):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('smoke', {})
    original = store.connect
    states, injected = [], []
    class Fault:
        def __init__(self, db): self.db = db
        def execute(self, sql, *args):
            result = self.db.execute(sql, *args)
            if sql == 'BEGIN IMMEDIATE':
                saved = json.loads(self.db.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (item.key,)).fetchone()[0])
                cancelled = self.db.execute('SELECT cancel FROM jobs WHERE id=?', (jid,)).fetchone()[0]
                states.append((saved['phase'], cancelled))
            if sql.startswith('UPDATE jobs SET cancel=1') and not injected:
                injected.append(True)
                error = sqlite3.OperationalError('locking protocol')
                error.sqlite_errorcode = sqlite3.SQLITE_PROTOCOL
                raise error
            return result
    @contextmanager
    def connect(**kwargs):
        with original(**kwargs) as db:
            yield Fault(db)
    monkeypatch.setattr(store, 'connect', connect)
    item.stop('Explicit close')
    assert states == [('ready', 0), ('ready', 0)]
    assert item.phase == 'stopping' and store.job(jid)['cancel'] == 1


@pytest.mark.parametrize('invalid', [True, float('nan'), float('inf')])
def test_invalid_durable_timestamp_is_not_silently_renewed(session, invalid):
    from bactologix.supervision import MonitoringInterrupted
    store, item = session
    previous = {'phase': 'ready', 'last_seen': invalid}
    store.put('desktop_sessions', item.key, previous)
    with pytest.raises(MonitoringInterrupted, match='invalid'):
        item.publish('')
    assert item.phase == 'ready'
    # Keep the original diagnostic record; never manufacture a fresh ready lease.
    with store.connect() as db:
        assert db.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (item.key,)).fetchone()[0] == json.dumps(previous)


@pytest.mark.parametrize(('exclusive', 'closing'), [(False, False), (True, False), (False, True)])
def test_windows_heartbeats_bridge_real_writer_lock_past_lease(session, monkeypatch, exclusive, closing):
    store, item = session
    monkeypatch.setattr(desktop, 'LEASE_SECONDS', 2)
    item.tick('', True)
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('smoke', {})
    anchored = store.get('desktop_sessions', item.key)['last_seen']
    outcome = []

    def poll():
        try:
            outcome.append(item.poll('', True))
        except Exception as exc:
            outcome.append(exc)

    with store.connect() as writer:
        if exclusive:
            writer.execute('PRAGMA locking_mode=EXCLUSIVE')
        writer.execute('BEGIN EXCLUSIVE' if exclusive else 'BEGIN IMMEDIATE')
        worker = threading.Thread(target=poll)
        worker.start()
        try:
            while time.time() < anchored + desktop.LEASE_SECONDS + .45 and worker.is_alive():
                desktop.atomic(item.root / 'heartbeat.json', {'session': item.key, 'time': time.time()})
                time.sleep(.04)
            assert worker.is_alive(), outcome
            saved = json.loads(writer.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?",
                                              (item.key,)).fetchone()[0])
            assert saved['last_seen'] == anchored and time.time() - anchored > desktop.LEASE_SECONDS
            assert not desktop.session_value_stopped(saved, store=store, owner=item.key)
            if closing:
                request(item, 'confirm_close')
                deadline = time.monotonic() + 2
                while not desktop.owner_cancelled(store, jid) and time.monotonic() < deadline:
                    time.sleep(.02)
                assert desktop.owner_cancelled(store, jid), 'A confirmed stop must reach readers while SQLite is busy'
        finally:
            writer.rollback()
            # EXCLUSIVE locking mode keeps its read lock through connection close.
    worker.join(4)
    assert outcome == [closing]
    assert bool(store.job(jid)['cancel']) == closing
    assert store.get('desktop_sessions', item.key)['phase'] == ('stopping' if closing else 'ready')


def test_old_proof_cannot_revive_expired_or_disconnected_owner(session, monkeypatch):
    store, item = session
    state = store.get('desktop_sessions', item.key)
    current = time.time()
    atomic_record = {'session': item.key, 'recorded': state['last_seen'], 'heartbeat': current}
    desktop.atomic(item.proof_path, atomic_record)
    monkeypatch.setattr(desktop, 'LEASE_SECONDS', .05)
    time.sleep(.07)
    desktop.atomic(item.root / 'heartbeat.json', {'session': item.key, 'time': time.time()})
    item.proven_until = current + .05
    assert not item.record_contended_lease()
    assert desktop._session_lease_reason(state, store=store, owner=item.key) == desktop.HEARTBEAT_EXPIRED
    assert not desktop.session_value_stopped(state, store=store, owner=item.key)
    # A proof of a different committed database version cannot bridge this one.
    desktop.atomic(item.proof_path, dict(atomic_record, recorded=state['last_seen'] - .1, heartbeat=time.time()))
    assert desktop._session_lease_reason(state, store=store, owner=item.key) == desktop.HEARTBEAT_EXPIRED
    assert not desktop.session_value_stopped(state, store=store, owner=item.key)


def test_idle_restart_poll_returns_promptly_through_exclusive_read_lock(session):
    store, item = session
    with store.connect() as writer:
        writer.execute('PRAGMA locking_mode=EXCLUSIVE')
        writer.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        assert restart.pending(store, {'id': item.key}) is None
        assert time.monotonic() - started < 1
        writer.rollback()


def test_stop_still_commits_when_local_lease_record_is_unwritable(session, monkeypatch):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('smoke', {})

    def unavailable(*args):
        raise OSError('Disk unavailable')

    monkeypatch.setattr(desktop, 'atomic', unavailable)
    item.stop('Window closed')
    assert store.job(jid)['cancel'] == 1
    assert store.get('desktop_sessions', item.key)['cancelled']


def test_heartbeat_file_read_does_not_hold_sqlite_writer_lock(session, monkeypatch):
    store, item = session
    reading, release = threading.Event(), threading.Event()
    original = desktop._owner_record
    outcome = []

    def delayed(path, owner):
        if path == item.pulse_path:
            reading.set()
            assert release.wait(5)
        return original(path, owner)

    def publish():
        try:
            item.gate('ready', heartbeat=True)
        except BaseException as exc:
            outcome.append(exc)

    monkeypatch.setattr(desktop, '_owner_record', delayed)
    thread = threading.Thread(target=publish)
    thread.start()
    try:
        assert reading.wait(2)
        store.put('disk_safety', 'job-test', {'state': 'running'})
        assert store.get('disk_safety', 'job-test')['state'] == 'running'
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not outcome


def test_last_window_close_reaches_owned_worker_while_database_is_busy(session):
    store, item = session
    saved = store.get('desktop_sessions', item.key)
    request(item, 'close')
    with store.connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        assert not item.record_contended_lease()
        assert 'Window closed' in desktop.session_stop_reason(saved, store=store, owner=item.key, check_host=False)
    assert item.tick('', True)


@pytest.mark.parametrize("closing", [False, True])
def test_ready_desktop_reconnects_after_stale_pulse_and_real_lock(session, monkeypatch, closing):
    store, item = session
    monkeypatch.setattr(desktop, 'LEASE_SECONDS', 2)
    monkeypatch.setenv(desktop.ENV, item.key)
    item.tick('', True)
    outcome = []
    with store.connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        thread = threading.Thread(target=lambda: outcome.append(item.poll('', True)), daemon=True)
        thread.start()
        deadline = time.monotonic() + 6
        while not desktop.workspace_reconnecting(store) and time.monotonic() < deadline:
            time.sleep(.05)
        assert desktop.workspace_reconnecting(store)
        writer.rollback()
    thread.join(.4)
    assert thread.is_alive()
    if closing:
        request(item, 'confirm_close')
    else:
        desktop.atomic(item.root / 'heartbeat.json', {'session': item.key, 'time': time.time()})
    thread.join(4)
    assert outcome == [closing]
    assert not desktop.workspace_reconnecting(store)
    assert store.get('desktop_sessions', item.key)['phase'] == ('stopping' if closing else 'ready')
