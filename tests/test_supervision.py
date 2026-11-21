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

from bactologix import disk_pause, engine, queue, supervision, worker
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
    monkeypatch.setattr(engine, 'CONTROL_HOLD_SECONDS', .3)
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', 1.0)
    store, jid, marker, command, _ = fixture
    with store.connect() as db:
        db.execute('UPDATE jobs SET kind=? WHERE id=?', (kind, jid))
    faults = inject_control_error(store, marker, monkeypatch, failures=None)
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    started = time.monotonic()
    worker.work(store)
    assert time.monotonic() - started < 5
    if kind == 'workflow':
        assert faults['attempts'] > 4
    else:
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


def test_low_disk_stops_after_transient_control_read(fixture, monkeypatch):
    store, jid, marker, command, capacity = fixture
    ctx = Context(store, store.claim())
    inject_control_error(store, marker, monkeypatch, failures=2)
    thread, outcome = background(ctx, command)
    try:
        wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('state') == 'degraded')
        capacity['free'] = 9 * disk_pause.GIB
        thread.join(8)
        assert not thread.is_alive() and isinstance(outcome[0], Cancelled)
        assert 'Workspace storage stop' in str(outcome[0])
        record = supervision.snapshot(store, jid, 'process')
        assert record['cleanup_confirmed'] and not supervision.remaining(record['identities'])
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
    monkeypatch.setattr(engine, 'CONTROL_HOLD_SECONDS', .3)
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', .5)
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


@pytest.mark.parametrize('low_space', [False, True])
def test_workflow_survives_writer_contention_and_still_enforces_disk_limits(fixture, low_space):
    store, jid, marker, _, capacity = fixture
    ctx = Context(store, store.claim())
    finish = marker.with_name('finish')
    code = ('from pathlib import Path\nimport sys,time\n'
            'marker,finish=map(Path,sys.argv[1:])\n'
            'while not finish.exists():\n marker.touch(); time.sleep(.02)\n')
    thread, outcome = background(ctx, [sys.executable, '-c', code, str(marker), str(finish)])
    try:
        wait_for(marker.exists)
        with store.connect() as writer:
            writer.execute('BEGIN IMMEDIATE')
            # WAL readers can still poll cancellation while disk status writes are locked.
            wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('state') == 'degraded')
            process = supervision.snapshot(store, jid, 'process')
            before = marker.stat().st_mtime_ns
            wait_for(lambda: marker.stat().st_mtime_ns > before)
            if low_space:
                capacity['free'] = 9 * disk_pause.GIB
                # Even a blocked SQLite writer cannot keep the native process running after a
                # fresh critical measurement. The final database reconciliation can wait.
                wait_for(lambda: not supervision.remaining(process['identities']))
            else:
                assert thread.is_alive()
        if not low_space:
            wait_for(lambda: supervision.snapshot(store, jid)['state'] == 'recovered')
            finish.touch()
        thread.join(5)
        assert not thread.is_alive()
        if low_space:
            assert len(outcome) == 1 and isinstance(outcome[0], Cancelled)
        else:
            assert not outcome
            assert supervision.snapshot(store, jid, 'process')['returncode'] == 0
        process = supervision.snapshot(store, jid, 'process')
        assert process['cleanup_confirmed'] and not supervision.remaining(process['identities'])
    finally:
        finish.touch()
        store.cancel(jid)
        thread.join(5)
        ctx.disk_guard.finish('cancelled' if low_space else 'succeeded')
        ctx.log.close()


@pytest.mark.parametrize('error, transient', [('database is locked', True), ('database disk image is malformed', False)])
def test_disk_status_defers_a_locked_writer_and_reports_corruption(fixture, monkeypatch, error, transient):
    monkeypatch.setattr(engine, 'CONTROL_HOLD_SECONDS', .3)
    store, jid, marker, command, _ = fixture
    original_put = store.put
    attempts = []
    def locked_disk_status(kind, key, value):
        if kind == 'disk_safety' and value['state'] == 'monitoring' and marker.exists():
            attempts.append(time.monotonic())
            raise sqlite3.OperationalError(error)
        return original_put(kind, key, value)
    monkeypatch.setattr(store, 'put', locked_disk_status)
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', 1.0)
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    worker_thread = threading.Thread(target=worker.work, args=(store,))
    worker_thread.start()
    try:
        if transient:
            wait_for(lambda: attempts and attempts[-1] - attempts[0] >= 1.5)
            before = marker.stat().st_mtime_ns
            wait_for(lambda: marker.stat().st_mtime_ns > before)
            assert store.job(jid)['status'] == 'running'
            store.cancel(jid)
            wait_for(lambda: store.job(jid)['status'] == 'cancelled')
        else:
            worker_thread.join(6)
            assert not worker_thread.is_alive()
            assert len(attempts) == 1
            assert store.job(jid)['status'] == 'interrupted'
            assert store.queue_snapshot()['paused']
        process = supervision.snapshot(store, jid, 'process')
        assert process['cleanup_confirmed'] and not supervision.remaining(process['identities'])
    finally:
        store.cancel(jid)
        worker_thread.join(5)


@pytest.mark.parametrize('kind', ['install', 'workflow'])
@pytest.mark.parametrize('cancel', [False, True])
def test_native_work_remains_supervised_during_real_exclusive_lock(fixture, tmp_path, kind, cancel):
    store, jid, *_ = fixture
    if kind == 'install':
        with store.connect() as db:
            db.execute('UPDATE jobs SET kind=? WHERE id=?', (kind, jid))
    ctx = Context(store, store.claim())
    # The command spawns a helper after workspace reads fail.
    # Its native identity must be recorded before the database is available again.
    script = tmp_path / 'setup.py'
    script.write_text('''
from pathlib import Path
import os
import subprocess
import sys
import time

folder = Path(__file__).parent
if len(sys.argv) > 1:
    (folder / 'child').write_text(str(os.getpid()))
    while not (folder / 'finish').exists():
        time.sleep(.02)
else:
    (folder / 'started').touch()
    while not (folder / 'spawn').exists():
        time.sleep(.02)
    subprocess.run([sys.executable, __file__, 'child'], check=True)
    (folder / 'installed').touch()
''')
    thread, outcome = background(ctx, [sys.executable, str(script)])
    try:
        wait_for((tmp_path / 'started').exists)
        with store.connect() as writer:
            writer.execute('PRAGMA locking_mode=EXCLUSIVE')
            writer.execute('BEGIN EXCLUSIVE')
            # Four failed reads exhausted the previous retry budget.
            wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('retry_count', 0) >= 4)
            (tmp_path / 'spawn').touch()
            child = tmp_path / 'child'
            wait_for(lambda: child.exists() and child.read_text().isdigit())
            child_pid = int(child.read_text())
            wait_for(lambda: any(i['pid'] == child_pid for i in
                                 supervision.snapshot(store, jid, 'process')['identities']))
            assert supervision.snapshot(store, jid)['state'] == 'degraded'
            if cancel:
                writer.execute('UPDATE jobs SET cancel=1 WHERE id=?', (jid,))
        if not cancel:
            wait_for(lambda: supervision.snapshot(store, jid)['state'] == 'recovered')
            (tmp_path / 'finish').touch()
        thread.join(5)
        assert not thread.is_alive()
        if cancel:
            assert len(outcome) == 1 and isinstance(outcome[0], Cancelled)
            assert not (tmp_path / 'installed').exists()
        else:
            assert not outcome and (tmp_path / 'installed').is_file()
        record = supervision.snapshot(store, jid, 'process')
        assert record['cleanup_confirmed'] and not supervision.remaining(record['identities'])
    finally:
        (tmp_path / 'spawn').touch()
        (tmp_path / 'finish').touch()
        thread.join(5)
        if ctx.disk_guard is not None:
            ctx.disk_guard.finish('cancelled' if cancel else 'succeeded')
        ctx.log.close()


def test_setup_interrupts_and_pauses_queue_on_persistent_control_loss(fixture, monkeypatch):
    from bactologix import engine
    monkeypatch.setattr(engine, 'CONTROL_HOLD_SECONDS', .3)
    store, jid, marker, command, _ = fixture
    with store.connect() as db:
        db.execute("UPDATE jobs SET kind='environments' WHERE id=?", (jid,))
    monkeypatch.setattr(engine, 'SETUP_CONTROL_GRACE_SECONDS', 1.0)
    inject_control_error(store, marker, monkeypatch, failures=None, message='database is locked')
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    started = time.monotonic()
    worker.work(store)
    assert 1 <= time.monotonic() - started < 8
    assert store.job(jid)['status'] == 'interrupted' and store.queue_snapshot()['paused']
    monitor = supervision.snapshot(store, jid)
    assert monitor['cleanup_confirmed'] and monitor['retry_count'] > 4
    assert not supervision.remaining(supervision.snapshot(store, jid, 'process')['identities'])


def test_setup_recovers_locked_process_registration_and_completion(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    jid = store.submit('environments', {})
    ctx = Context(store, store.claim())
    installed = tmp_path / 'installed'
    original = store.put_process
    retried = set()

    def put(jid, record):
        completed = record['cleanup_confirmed']
        if completed not in retried:
            retried.add(completed)
            # Gate stays closed until registration. A subsequent write conflict
            # after success must retry bookkeeping without relaunching setup.
            assert installed.exists() == completed
            raise sqlite3.OperationalError('database is locked')
        return original(jid, record)

    monkeypatch.setattr(store, 'put_process', put)
    try:
        ctx.command([sys.executable, '-c',
                     'import sys; open(sys.argv[1], "x").close()', str(installed)])
        assert retried == {False, True} and installed.exists()
        assert store.get('processes', jid)['cleanup_confirmed']
        assert supervision.snapshot(store, jid, 'process')['returncode'] == 0
    finally:
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
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', .5)
    store, jid, marker, command, _ = fixture
    ctx = Context(store, store.claim())
    original = store.put_process
    calls = []
    def put(jid, value):
        calls.append(value.copy())
        if not value['cleanup_confirmed']:
            assert not marker.exists()
            raise sqlite3.OperationalError('database is locked')
        return original(jid, value)
    monkeypatch.setattr(store, 'put_process', put)
    try:
        with pytest.raises(supervision.MonitoringInterrupted): ctx.command(command)
        assert not marker.exists()
        assert len([call for call in calls if not call['cleanup_confirmed']]) > 1
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
