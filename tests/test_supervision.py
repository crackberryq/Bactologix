"""Failure injection uses harmless native writers and never modifies a real run."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import psutil
import pytest

from bactologix import disk_pause, queue, supervision, worker
from bactologix.engine import Cancelled, Context
from bactologix.store import Store, now, transient_lock


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Software supervision fixture timed out')


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    capacity = {'free': 40 * disk_pause.GIB}
    class Probe:
        def __init__(self, *args, **kwargs): pass
        def include_command(self, **kwargs): pass
        def sample(self):
            return {'volumes': [{'id': 'test', 'path': str(tmp_path), 'label': 'Fixture',
                'available_bytes': capacity['free'], 'total_bytes': 100 * disk_pause.GIB,
                'kind': 'linux'}], 'errors': [], 'checked_at': now()}
    monkeypatch.setattr(disk_pause, 'CapacityProbe', Probe)
    monkeypatch.setattr(disk_pause, 'POLL_SECONDS', .05)
    monkeypatch.delenv('BACTOLOGIX_DESKTOP_SESSION', raising=False)
    store = Store(tmp_path / 'workspace')
    store.put('runs', 'fixture', {'id': 'fixture', 'commit': 'a' * 40})
    jid = store.submit('workflow', {'action': 'run', 'run_id': 'fixture'})
    marker = tmp_path / 'writer.out'
    code = 'import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);\nwhile True:\n p.write_text(str(time.time())); time.sleep(.02)'
    command = [sys.executable, '-c', code, str(marker)]
    return store, jid, marker, command, capacity


def inject_control_error(store, marker, monkeypatch, *, failures=1, message='locking protocol'):
    original = store.connect
    state = {'attempts': 0, 'timeouts': []}
    class Fault:
        def __init__(self, db): self.db = db
        def __getattr__(self, name): return getattr(self.db, name)
        def execute(self, sql, *args):
            if sql == 'SELECT kind,status,cancel FROM jobs WHERE id=?' and marker.exists():
                if failures is None or state['attempts'] < failures:
                    state['attempts'] += 1
                    raise sqlite3.OperationalError(message)
            return self.db.execute(sql, *args)
    @contextmanager
    def connect(**kwargs):
        state['timeouts'].append(kwargs.get('timeout', 30))
        with original(**kwargs) as db:
            yield Fault(db)
    monkeypatch.setattr(store, 'connect', connect)
    return state


def background(ctx, command):
    outcome = []
    def run():
        try:
            ctx.command(command)
        except BaseException as exc:
            outcome.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


@pytest.mark.parametrize('message', ['locking protocol', 'database is locked', 'database table is locked'])
def test_transient_control_fault_keeps_owned_writer_running_and_stop_works(fixture, monkeypatch, message):
    store, jid, marker, command, _ = fixture
    ctx = Context(store, store.claim())
    faults = inject_control_error(store, marker, monkeypatch, message=message)
    thread, outcome = background(ctx, command)
    try:
        wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('state') == 'recovered')
        assert thread.is_alive() and store.job(jid)['status'] == 'running'
        before = marker.read_text()
        wait_for(lambda: marker.read_text() != before)
        process = supervision.snapshot(store, jid, 'process')
        assert process['source_commit'] == 'a' * 40
        assert process['run_id'] == 'fixture' and process['command_sha256']
        assert process['pgid'] == process['pid']
        assert process['created'] == psutil.Process(process['pid']).create_time()
        assert process['cleanup_confirmed'] is False
        store.cancel(jid)
        thread.join(5)
        assert not thread.is_alive() and isinstance(outcome[0], Cancelled)
        assert faults['attempts'] == 1 and .05 in faults['timeouts']
        assert supervision.snapshot(store, jid, 'process')['cleanup_confirmed'] is True
        assert store.get('processes', jid)['pid'] is None
    finally:
        store.cancel(jid)
        thread.join(5)
        ctx.disk_guard.finish('cancelled')
        ctx.log.close()


@pytest.mark.parametrize('kind', ['workflow', 'smoke'])
def test_persistent_control_failure_is_interrupted_not_native_failed(fixture, monkeypatch, kind):
    store, jid, marker, command, _ = fixture
    with store.connect() as db:
        db.execute('UPDATE jobs SET kind=? WHERE id=?', (kind, jid))
    faults = inject_control_error(store, marker, monkeypatch, failures=None)
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    started = time.monotonic()
    worker.work(store)
    assert time.monotonic() - started < 5
    assert faults['attempts'] == 4
    assert store.job(jid)['status'] == 'interrupted'
    assert store.queue_snapshot()['paused']
    monitor = supervision.snapshot(store, jid)
    assert monitor['state'] == 'interrupted' and monitor['cleanup_confirmed'] is True
    assert monitor['command_returncode'] is None
    process = supervision.snapshot(store, jid, 'process')
    assert not supervision.remaining(process['identities'])
    before = marker.read_text()
    time.sleep(.1)
    assert marker.read_text() == before


def test_cancel_received_during_retry_is_honoured(fixture, monkeypatch):
    store, jid, marker, command, _ = fixture
    ctx = Context(store, store.claim())
    inject_control_error(store, marker, monkeypatch, failures=3)
    thread, outcome = background(ctx, command)
    try:
        wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('state') == 'degraded')
        store.cancel(jid)
        thread.join(5)
        assert not thread.is_alive() and isinstance(outcome[0], Cancelled)
    finally:
        store.cancel(jid)
        thread.join(5)
        ctx.disk_guard.finish('cancelled')
        ctx.log.close()


def test_low_disk_guard_still_pauses_after_transient_control_read(fixture, monkeypatch):
    store, jid, marker, command, capacity = fixture
    ctx = Context(store, store.claim())
    inject_control_error(store, marker, monkeypatch, failures=2)
    thread, outcome = background(ctx, command)
    try:
        wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('state') == 'degraded')
        capacity['free'] = 9 * disk_pause.GIB
        wait_for(lambda: (store.get('disk_safety', jid) or {}).get('state') == 'paused')
        record = supervision.snapshot(store, jid, 'process')
        assert psutil.Process(record['pid']).status() == psutil.STATUS_STOPPED
        before = marker.read_text()
        time.sleep(.2)
        assert marker.read_text() == before
        store.cancel(jid)
        thread.join(5)
        assert not thread.is_alive() and isinstance(outcome[0], Cancelled)
    finally:
        store.cancel(jid)
        thread.join(5)
        ctx.disk_guard.finish('cancelled')
        ctx.log.close()


def test_monitor_diagnostic_write_failure_cannot_disable_cancel(fixture, monkeypatch):
    store, jid, marker, command, _ = fixture
    original = supervision.save
    def save(store, jid, value, kind='monitor'):
        if kind == 'monitor': raise OSError('fixture diagnostic disk unavailable')
        return original(store, jid, value, kind)
    monkeypatch.setattr(supervision, 'save', save)
    ctx = Context(store, store.claim())
    faults = inject_control_error(store, marker, monkeypatch)
    thread, outcome = background(ctx, command)
    try:
        wait_for(lambda: faults['attempts'] == 1)
        store.cancel(jid)
        thread.join(5)
        assert not thread.is_alive() and isinstance(outcome[0], Cancelled)
    finally:
        store.cancel(jid)
        thread.join(5)
        ctx.disk_guard.finish('cancelled')
        ctx.log.close()


def test_nontransient_sqlite_error_is_not_retried(fixture, monkeypatch):
    store, jid, marker, command, _ = fixture
    faults = inject_control_error(store, marker, monkeypatch, failures=None, message='database disk image is malformed')
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    worker.work(store)
    assert faults['attempts'] == 1
    assert store.job(jid)['status'] == 'interrupted'
    error = sqlite3.OperationalError('unrelated message')
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY | (2 << 8)
    assert transient_lock(error)


def test_cleanup_unconfirmed_remains_busy_until_identity_checks_pass(fixture, monkeypatch):
    store, jid, marker, command, _ = fixture
    inject_control_error(store, marker, monkeypatch, failures=None)
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    monkeypatch.setattr(supervision, 'verify_exit', lambda identities: False)
    worker.work(store)
    assert store.job(jid)['status'] == 'running'
    assert supervision.snapshot(store, jid)['cleanup_confirmed'] is False
    assert store.get('processes', jid)['pid'] is not None
    assert queue.reconcile_interrupted(store) == [jid]  # Real harmless child did exit.
    assert store.job(jid)['status'] == 'interrupted'


def test_live_or_reused_identity_and_corrupt_record_recovery(tmp_path):
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    store.claim()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    identity = {'pid': child.pid, 'created': psutil.Process(child.pid).create_time()}
    try:
        supervision.save(store, jid, {'identities': [identity], 'cleanup_confirmed': False}, 'process')
        assert queue.reconcile_interrupted(store) == []
        with pytest.raises(supervision.MonitoringInterrupted): queue.recover_interrupted(store)
        assert child.poll() is None and store.job(jid)['status'] == 'running'
        supervision.path(store, jid).write_text('{truncated')
        assert supervision.snapshot(store, jid)['state'] == 'unavailable'
        supervision.path(store, jid, 'process').write_text('{truncated')
        assert queue.reconcile_interrupted(store) == []
        with pytest.raises(supervision.MonitoringInterrupted): queue.recover_interrupted(store)
        # A PID with a different start time is never adopted or signalled.
        supervision.save(store, jid, {'identities': [dict(identity, created=identity['created'] - 1)], 'cleanup_confirmed': False}, 'process')
        assert queue.reconcile_interrupted(store) == [jid]
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_native_nonzero_exit_remains_native_failure(fixture):
    store, jid, _, _, _ = fixture
    ctx = Context(store, store.claim())
    try:
        with pytest.raises(RuntimeError, match='code 4'):
            ctx.command([sys.executable, '-c', 'raise SystemExit(4)'])
        assert supervision.snapshot(store, jid) is None
        assert supervision.snapshot(store, jid, 'process')['returncode'] == 4
    finally:
        ctx.disk_guard.finish('failed')
        ctx.log.close()


def test_fast_ungated_commands_repeatedly_complete(tmp_path):
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    ctx = Context(store, store.claim())
    try:
        for _ in range(40):
            ctx.command(['/bin/true'])
            assert store.get('processes', jid)['pid'] is None
            assert supervision.snapshot(store, jid, 'process')['cleanup_confirmed'] is True
    finally:
        ctx.log.close()


def test_nonworkflow_detached_descendant_cannot_be_reported_clean_until_stopped(tmp_path):
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    ctx = Context(store, store.claim())
    child_file = tmp_path / 'child.json'
    child = 'import os,time,json,psutil; from pathlib import Path; Path(' + repr(str(child_file)) + ').write_text(json.dumps({"pid":os.getpid(),"created":psutil.Process().create_time()})); time.sleep(60)'
    leader = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + '], start_new_session=True); time.sleep(.7)'
    try:
        with pytest.raises(RuntimeError, match='owned child processes remained'):
            ctx.command([sys.executable, '-c', leader])
        identity = json.loads(child_file.read_text())
        assert not supervision.remaining([identity])
        record = supervision.snapshot(store, jid, 'process')
        assert identity in record['identities'] and record['cleanup_confirmed']
    finally:
        if child_file.exists():
            for item in supervision.remaining([json.loads(child_file.read_text())]):
                psutil.Process(item['pid']).kill()
        ctx.log.close()


def test_real_sqlite_writer_contention_does_not_stall_native_safety(fixture):
    store, jid, marker, command, _ = fixture
    ctx = Context(store, store.claim())
    thread, outcome = background(ctx, command)
    try:
        wait_for(marker.exists)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            started = time.monotonic()
            thread.join(3)
            assert not thread.is_alive(), 'Native safety must not wait for SQLite\'s default 30s timeout'
            assert time.monotonic() - started < 3
            assert isinstance(outcome[0], supervision.MonitoringInterrupted)
            record = supervision.snapshot(store, jid, 'process')
            assert record['cleanup_confirmed'] and not supervision.remaining(record['identities'])
        assert supervision.snapshot(store, jid)['state'] == 'interrupted'
    finally:
        store.cancel(jid)
        thread.join(5)
        ctx.disk_guard.finish('interrupted')
        ctx.log.close()


@pytest.mark.parametrize('value', [[], None, {}, {'cleanup_confirmed': 'yes', 'identities': []},
    {'cleanup_confirmed': False, 'identities': []},
    {'cleanup_confirmed': False, 'identities': [{'pid': 1, 'created': 'yesterday'}]}])
def test_invalid_process_record_shape_blocks_explicit_recovery_but_not_ui(tmp_path, value):
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    store.claim()
    supervision.path(store, jid, 'process').write_text(json.dumps(value))
    with pytest.raises(supervision.MonitoringInterrupted): queue.recover_interrupted(store)
    assert queue.reconcile_interrupted(store) == []
    assert store.job(jid)['status'] == 'running'


def test_bad_owner_metadata_is_monitoring_interruption(fixture, monkeypatch):
    store, jid, marker, command, _ = fixture
    store.put('job_owners', jid, ['malformed'])
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    worker.work(store)
    assert store.job(jid)['status'] == 'interrupted'
    assert not marker.exists()


def test_app_update_preserves_desktop_cancellation_exemption(tmp_path):
    store = Store(tmp_path / 'workspace')
    jid = store.submit('app_update', {})
    store.put('job_owners', jid, ['unrelated broken desktop record'])
    ctx = Context(store, store.claim())
    try:
        ctx.command(['/bin/true'])
        assert supervision.snapshot(store, jid) is None
        assert store.get('processes', jid)['cleanup_confirmed'] is True
    finally:
        ctx.log.close()


def test_process_identity_must_be_saved_before_native_exec_gate_opens(fixture, monkeypatch):
    store, jid, marker, command, _ = fixture
    ctx = Context(store, store.claim())
    original = store.put_process
    calls = []
    def put(jid, value):
        calls.append(value.copy())
        if len(calls) == 1:
            raise sqlite3.OperationalError('database is locked')
        return original(jid, value)
    monkeypatch.setattr(store, 'put_process', put)
    try:
        with pytest.raises(supervision.MonitoringInterrupted): ctx.command(command)
        assert not marker.exists()
        assert calls[0]['identities'] and calls[0]['cleanup_confirmed'] is False
        assert supervision.snapshot(store, jid, 'process')['cleanup_confirmed'] is True
    finally:
        ctx.disk_guard.finish('interrupted')
        ctx.log.close()


def test_process_inspection_gap_cannot_become_verified_cleanup(fixture, monkeypatch):
    from bactologix.disk_process import OwnedTree
    store, jid, marker, command, _ = fixture
    original = OwnedTree._refresh
    injected = []
    def refresh(tree):
        if marker.exists() and not injected:
            injected.append(True)
            raise psutil.AccessDenied(tree.root_identity[0])
        return original(tree)
    monkeypatch.setattr(OwnedTree, '_refresh', refresh)
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    worker.work(store)
    assert injected and store.job(jid)['status'] == 'running'
    record = supervision.snapshot(store, jid, 'process')
    assert record['ownership_complete'] is False and record['cleanup_confirmed'] is False
    assert not supervision.remaining(record['identities'])
    assert queue.reconcile_interrupted(store) == []
    with pytest.raises(supervision.MonitoringInterrupted): queue.recover_interrupted(store)
