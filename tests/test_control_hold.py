"""Native heartbeat and real SQLite locks exercise the control recovery path."""
import sqlite3
import sys
import threading
import time

import psutil
import pytest

from bactologix import desktop, disk_pause, engine, supervision, worker
from test_supervision import background, fixture, wait_for


def writer_command(marker, done):
    script = ('from pathlib import Path\nimport sys,time\n'
              'marker,done=map(Path,sys.argv[1:])\n'
              'while not done.exists():\n marker.touch(); time.sleep(.02)\n')
    return [sys.executable, '-c', script, str(marker), str(done)]


@pytest.mark.parametrize('kind,after', [('workflow', 'resume'), ('install', 'resume'),
                                         ('workflow', 'stop'), ('workflow', 'low-space')])
def test_same_command_waits_through_long_control_outage(fixture, monkeypatch, kind, after):
    store, jid, marker, _, capacity = fixture
    if kind == 'install':
        with store.connect() as db:
            db.execute('UPDATE jobs SET kind=? WHERE id=?', (kind, jid))
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', .25)
    monkeypatch.setattr(engine, 'SETUP_CONTROL_GRACE_SECONDS', .25)
    monkeypatch.setattr(engine, 'CONTROL_HOLD_SECONDS', 15)
    ctx = engine.Context(store, store.claim())
    done = marker.with_name('finish')
    thread, outcome = background(ctx, writer_command(marker, done))
    def monitoring():
        return supervision.snapshot(store, jid) or {}
    try:
        wait_for(marker.exists)
        identity = supervision.snapshot(store, jid, 'process')['identities'][0]
        native = psutil.Process(identity['pid'])
        for _ in range(2 if after == 'resume' else 1):
            with store.connect() as locker:
                locker.execute('PRAGMA locking_mode=EXCLUSIVE')
                locker.execute('BEGIN EXCLUSIVE')
                wait_for(lambda: monitoring().get('control_hold') is True)
                assert monitoring()['control_operation'] == 'job and desktop controls'
                assert native.status() == psutil.STATUS_STOPPED
                stamp = marker.stat().st_mtime_ns
                if after == 'low-space':
                    capacity['free'] = 8 * disk_pause.GIB
                    wait_for(lambda: not supervision.remaining([identity]))
                if after == 'stop':
                    locker.execute('UPDATE jobs SET cancel=1 WHERE id=?', (jid,))
                time.sleep(.3)
                assert marker.stat().st_mtime_ns == stamp
            if after in {'stop', 'low-space'}:
                thread.join(6)
                assert len(outcome) == 1 and isinstance(outcome[0], engine.Cancelled)
            else:
                wait_for(lambda: monitoring().get('state') == 'recovered')
                assert native.create_time() == identity['created']
                wait_for(lambda: marker.stat().st_mtime_ns > stamp)
        if after == 'resume':
            done.touch()
            thread.join(6)
            assert not outcome
        assert not thread.is_alive()
        assert supervision.snapshot(store, jid, 'process')['cleanup_confirmed'] is True
    finally:
        done.touch()
        store.cancel(jid)
        thread.join(8)
        if ctx.disk_guard is not None:
            ctx.disk_guard.finish('succeeded' if after == 'resume' else 'cancelled')
        ctx.log.close()


def test_worker_wal_stays_open_without_blocking_user_writes(fixture, monkeypatch):
    store, jid, marker, _, _ = fixture
    finish = marker.with_name('finish')
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(writer_command(marker, finish)))
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', .3)
    thread = threading.Thread(target=worker.work, args=(store,))
    thread.start()
    try:
        wait_for(marker.exists)
        with store.connect(timeout=.05) as db:
            db.execute('PRAGMA locking_mode=EXCLUSIVE')
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                db.execute('BEGIN EXCLUSIVE')
        # A regular WAL writer still works. Keep it until disk-status writes
        # stall while readers and independent storage checks continue.
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('state') == 'degraded')
            time.sleep(.5)  # The normal grace expired. Storage readings still protect the job.
            stamp = marker.stat().st_mtime_ns
            wait_for(lambda: marker.stat().st_mtime_ns > stamp)
            assert not supervision.snapshot(store, jid).get('control_hold')
            assert store.job(jid)['status'] == 'running'
            db.execute('UPDATE jobs SET cancel=1 WHERE id=?', (jid,))
        wait_for(lambda: store.job(jid)['status'] == 'cancelled')
        assert supervision.snapshot(store, jid, 'process')['cleanup_confirmed']
    finally:
        finish.touch()
        store.cancel(jid)
        thread.join(8)


@pytest.mark.parametrize('action', ['recover', 'stop', 'close', 'expire'])
def test_desktop_owned_native_command_during_exclusive_lock(fixture, monkeypatch, action):
    from bactologix.ui_backend import dispatch

    store, jid, marker, _, _ = fixture
    owner, anchor = 'e' * 32, time.time()
    store.put('job_owners', jid, owner)
    store.put('desktop_sessions', owner, {'phase': 'ready', 'last_seen': anchor})
    monkeypatch.setattr(desktop, 'LEASE_SECONDS', 2.0)
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', 1.0)
    ctx = engine.Context(store, store.claim())
    pulse = desktop._lease_path(store, owner).with_suffix('.pulse.json')
    done = marker.with_name('finish')
    with desktop.job_stop_channel(store, ctx.job):
        thread, outcome = background(ctx, writer_command(marker, done))
        try:
            wait_for(marker.exists)
            identities = supervision.snapshot(store, jid, 'process')['identities']
            with store.connect() as locker:
                locker.execute('PRAGMA locking_mode=EXCLUSIVE')
                locker.execute('BEGIN EXCLUSIVE')
                wait_for(lambda: (supervision.snapshot(store, jid) or {}).get('state') == 'degraded')
                # Outlast the durable lease and the SQLite grace. The worker
                # can verify the same desktop, native process, and disk locally.
                until = time.monotonic() + 2.25
                while time.monotonic() < until:
                    desktop.atomic(pulse, {'session': owner, 'anchor': anchor, 'heartbeat': time.time(),
                                           'monotonic': time.monotonic()})
                    time.sleep(.09)
                before = marker.stat().st_mtime_ns
                wait_for(lambda: marker.stat().st_mtime_ns > before)
                assert not supervision.snapshot(store, jid).get('control_hold')
                if action == 'stop':
                    result = dispatch(store, 'cancel_job', {'id': jid})
                    assert 'Stop requested' in result['message']
                elif action == 'close':
                    desktop.atomic(pulse, {'session': owner, 'stopping': True, 'reason': 'Window closed'})
                if action != 'recover':
                    wait_for(lambda: not supervision.remaining(identities))
            if action == 'recover':
                wait_for(lambda: supervision.snapshot(store, jid)['state'] == 'recovered')
                assert supervision.snapshot(store, jid, 'process')['identities'] == identities
                done.touch()
            thread.join(8)
            assert not thread.is_alive()
            if action == 'recover':
                assert not outcome
            else:
                assert len(outcome) == 1 and isinstance(outcome[0], engine.Cancelled)
                expected = {'stop': 'stop request', 'close': 'Window closed', 'expire': 'heartbeat expired'}
                assert expected[action] in str(outcome[0])
            assert supervision.snapshot(store, jid, 'process')['cleanup_confirmed']
        finally:
            done.touch()
            store.cancel(jid)
            thread.join(8)
            ctx.disk_guard.finish('succeeded' if action == 'recover' else 'cancelled')
            ctx.log.close()


def test_cached_desktop_cannot_authorize_a_new_command(fixture, monkeypatch):
    store, jid, marker, _, _ = fixture
    owner = 'e' * 32
    store.put('job_owners', jid, owner)
    store.put('desktop_sessions', owner, {'phase': 'ready', 'last_seen': time.time()})
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', .2)
    ctx = engine.Context(store, store.claim())
    try:
        ctx.command([sys.executable, '-c', 'pass'])
        with store.connect() as locker:
            locker.execute('PRAGMA locking_mode=EXCLUSIVE')
            locker.execute('BEGIN EXCLUSIVE')
            with pytest.raises(supervision.MonitoringInterrupted, match='database is locked'):
                ctx.command([sys.executable, '-c', 'import sys; open(sys.argv[1], "x").close()', str(marker)])
        assert not marker.exists()
    finally:
        ctx.disk_guard.finish('interrupted')
        ctx.log.close()


def test_unverifiable_suspension_interrupts_without_another_claim(fixture, monkeypatch):
    from bactologix.disk_process import OwnedTree, STORAGE_SUSPEND_SECONDS, SuspensionError
    from test_supervision import inject_control_error
    store, jid, marker, command, _ = fixture
    second = store.submit('smoke', {})
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', .1)
    monkeypatch.setattr(worker, 'assert_current', lambda store: None)
    monkeypatch.setattr(worker, 'execute', lambda ctx: ctx.command(command))
    inject_control_error(store, marker, monkeypatch, failures=None, message='database is locked')
    def cannot_suspend(tree, *, timeout):
        assert timeout == STORAGE_SUSPEND_SECONDS
        raise SuspensionError('Fixture: one process could not be suspended')
    monkeypatch.setattr(OwnedTree, 'freeze', cannot_suspend)
    worker.work(store)
    assert store.job(jid)['status'] == 'interrupted'
    assert store.queue_snapshot()['paused'] and store.job(second)['status'] == 'queued'
    info = supervision.snapshot(store, jid)
    assert 'Could not verify suspension' in info['detail'] and info['cleanup_confirmed']
