"""A short SQLite writer conflict must not discard an explicit Stop request."""
from contextlib import contextmanager
import sqlite3
import sys
import threading
import time

import pytest

from bactologix.store import CONTROL_BACKOFF, CONTROL_TIMEOUT, Store


def observed_connections(store, monkeypatch):
    original = store.connect
    failed = threading.Event()
    attempts = []
    @contextmanager
    def connect(**kwargs):
        if kwargs.get('timeout') == CONTROL_TIMEOUT:
            attempts.append(time.monotonic())
        try:
            with original(**kwargs) as db:
                yield db
        except sqlite3.OperationalError:
            failed.set()
            raise
    monkeypatch.setattr(store, 'connect', connect)
    return original, failed, attempts


def cancelling(store, jid):
    outcome = []
    def target():
        try:
            store.cancel(jid)
        except BaseException as exc:
            outcome.append(exc)
    thread = threading.Thread(target=target)
    thread.start()
    return thread, outcome


@pytest.mark.parametrize('running', [False, True])
def test_real_writer_released_after_first_busy_attempt_preserves_stop(tmp_path, monkeypatch, running):
    store = Store(tmp_path)
    jid = store.submit('smoke', {})
    if running:
        store.claim()
    original, failed, attempts = observed_connections(store, monkeypatch)
    with original() as writer:
        writer.execute('BEGIN IMMEDIATE')
        thread, outcome = cancelling(store, jid)
        try:
            assert failed.wait(3), 'The fixture must cause a real SQLite BUSY result before releasing its writer'
        finally:
            writer.rollback()
            thread.join(3)
    assert not thread.is_alive()
    assert not outcome, f'Transient writer contention discarded Stop: {outcome}'
    assert len(attempts) >= 2
    assert store.job(jid)['cancel'] == 1
    assert store.job(jid)['status'] == ('running' if running else 'cancelled')
    store.cancel(jid)  # Repeating the idempotent request preserves the same state.
    assert store.job(jid)['cancel'] == 1


def test_persistent_writer_is_bounded_and_does_not_acknowledge_stop(tmp_path, monkeypatch):
    store = Store(tmp_path)
    jid = store.submit('smoke', {})
    original, _, attempts = observed_connections(store, monkeypatch)
    with original() as writer:
        writer.execute('BEGIN IMMEDIATE')
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            store.cancel(jid)
        assert time.monotonic() - start < 3, 'Stop must not inherit SQLite\'s ordinary 30-second timeout'
        assert len(attempts) == len(CONTROL_BACKOFF) + 1
        assert writer.execute('SELECT cancel,status FROM jobs WHERE id=?', (jid,)).fetchone()[:] == (0, 'queued')


@pytest.mark.parametrize('code', [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_PROTOCOL])
def test_retry_rolls_back_failed_transaction_before_repeating_cancel(tmp_path, monkeypatch, code):
    store = Store(tmp_path)
    jid = store.submit('smoke', {})
    original = store.connect
    attempts, states = [], []
    class Fault:
        def __init__(self, db): self.db = db
        def execute(self, sql, *args):
            result = self.db.execute(sql, *args)
            if sql == 'BEGIN IMMEDIATE':
                states.append(tuple(self.db.execute('SELECT cancel,status FROM jobs WHERE id=?', (jid,)).fetchone()))
            if sql.startswith('UPDATE jobs SET cancel=1') and len(attempts) == 1:
                error = sqlite3.OperationalError('injected post-statement lock conflict')
                error.sqlite_errorcode = code
                raise error
            return result
    @contextmanager
    def connect(**kwargs):
        attempts.append(True)
        with original(**kwargs) as db:
            yield Fault(db)
    monkeypatch.setattr(store, 'connect', connect)
    store.cancel(jid)
    assert len(attempts) == 2
    assert states == [(0, 'queued'), (0, 'queued')], 'A failed transaction must be rolled back before retry'
    with original() as db:
        assert tuple(db.execute('SELECT cancel,status FROM jobs WHERE id=?', (jid,)).fetchone()) == (1, 'cancelled')


def test_retry_rechecks_running_application_update_exemption(tmp_path, monkeypatch):
    store = Store(tmp_path)
    jid = store.submit('app_update', {})
    original, failed, attempts = observed_connections(store, monkeypatch)
    with original() as writer:
        writer.execute('BEGIN IMMEDIATE')
        thread, outcome = cancelling(store, jid)
        try:
            assert failed.wait(3)
            writer.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
            writer.commit()
        finally:
            writer.rollback()
            thread.join(3)
    assert not thread.is_alive() and len(attempts) >= 2
    assert len(outcome) == 1 and isinstance(outcome[0], ValueError)
    assert 'cannot be cancelled' in str(outcome[0])
    assert store.job(jid)['status'] == 'running' and store.job(jid)['cancel'] == 0


def test_nontransient_database_error_is_not_retried(tmp_path, monkeypatch):
    store = Store(tmp_path)
    jid = store.submit('smoke', {})
    attempts = []
    @contextmanager
    def connect(**kwargs):
        attempts.append(True)
        error = sqlite3.OperationalError('database disk image is malformed')
        error.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
        raise error
        yield  # This fixture fails while opening the transaction.
    monkeypatch.setattr(store, 'connect', connect)
    with pytest.raises(sqlite3.OperationalError, match='malformed'):
        store.cancel(jid)
    assert attempts == [True]


def test_retried_stop_reaches_live_native_command_after_real_contention(tmp_path, monkeypatch):
    from bactologix.engine import Cancelled, Context
    from bactologix import supervision
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    ctx = Context(store, store.claim())
    marker = tmp_path / 'started'
    outcome = []
    def execute():
        try:
            ctx.command([sys.executable, '-c',
                'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', str(marker)])
        except BaseException as exc:
            outcome.append(exc)
    command = threading.Thread(target=execute)
    command.start()
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        # A separate Store represents the UI; observer writes cannot trigger this
        # fixture's signal that the cancellation transaction itself encountered BUSY.
        ui_store = Store(store.home)
        original, failed, attempts = observed_connections(ui_store, monkeypatch)
        with original() as writer:
            writer.execute('BEGIN IMMEDIATE')
            stop, errors = cancelling(ui_store, jid)
            try:
                assert failed.wait(3)
            finally:
                writer.rollback()
                stop.join(3)
        assert not stop.is_alive() and not errors and len(attempts) >= 2
        command.join(5)
        assert not command.is_alive() and len(outcome) == 1 and isinstance(outcome[0], Cancelled)
        process = supervision.snapshot(store, jid, 'process')
        assert process['cleanup_confirmed'] and not supervision.remaining(process['identities'])
    finally:
        store.cancel(jid)
        command.join(5)
        ctx.log.close()
