"""Desktop lease contention must neither reopen a stop nor strand the UI child."""
from contextlib import contextmanager
import json
import signal
import sqlite3
import subprocess
import sys
import threading
import time
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


def test_expired_lease_cannot_be_refreshed_back_to_ready(session, monkeypatch):
    store, item = session
    monkeypatch.setenv(desktop.ENV, item.key)
    owned = store.submit('smoke', {})
    store.put('desktop_sessions', item.key, {'phase': 'ready', 'last_seen': time.time() - 100})
    item.publish('')
    assert item.phase == 'stopping' and desktop.owner_cancelled(store, owned)
    assert store.job(owned)['cancel'] == 1


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
