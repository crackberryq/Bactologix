"""Software-only checks of disk safety; fixtures never exhaust a real disk."""
import sys
import threading
import time
import psutil
import pytest
from bactologix import disk_pause
from bactologix.engine import Cancelled, Context
from bactologix.store import Store, now


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for storage safety fixture')


@pytest.fixture
def safety(tmp_path, monkeypatch):
    values = {'available': 40 * disk_pause.GIB, 'errors': [], 'hang': False}
    release = threading.Event()
    class Capacity:
        def __init__(self, *args, **kwargs):
            pass
        def include_command(self, **kwargs):
            pass
        def sample(self):
            if values['hang']:
                release.wait(20)
            return {'volumes': [{'id': 'fixture', 'label': 'Fixture disk', 'path': str(tmp_path),
                       'available_bytes': values['available'], 'total_bytes': 100 * disk_pause.GIB, 'kind': 'linux'}],
                    'errors': list(values['errors']), 'checked_at': now()}
    monkeypatch.setattr(disk_pause, 'CapacityProbe', Capacity)
    monkeypatch.setattr(disk_pause, 'POLL_SECONDS', .05)
    monkeypatch.delenv('BACTOLOGIX_DESKTOP_SESSION', raising=False)
    store = Store(tmp_path / 'workspace')
    jid = store.submit('workflow', {'run_id': 'fixture', 'action': 'run'})
    store.update_job(jid, status='running')
    context = Context(store, store.job(jid))
    yield store, jid, context, values
    release.set()
    context.disk_guard.finish(store.job(jid)['status'])
    context.log.close()


def background(context, command):
    result = []
    def target():
        try:
            context.command(command)
        except BaseException as exc:
            result.append(exc)
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, result


def test_prelaunch_wait_exact_floor_requires_manual_resume(safety, tmp_path):
    store, jid, context, values = safety
    values['available'] = 10 * disk_pause.GIB
    marker = tmp_path / 'launched'
    thread, result = background(context, [sys.executable, '-c', 'from pathlib import Path; Path(__import__("sys").argv[1]).touch()', str(marker)])
    try:
        wait_for(lambda: (disk_pause.snapshot(store, jid) or {}).get('state') == 'paused')
        state = disk_pause.snapshot(store, jid)
        assert state['pause_kind'] == 'before_launch'
        assert not marker.exists()
        with pytest.raises(ValueError):
            disk_pause.request_resume(store, jid, state['generation'])
        values['available'] = 14 * disk_pause.GIB
        time.sleep(.15)
        assert not disk_pause.snapshot(store, jid)['can_resume']
        values['available'] = 15 * disk_pause.GIB
        wait_for(lambda: disk_pause.snapshot(store, jid)['can_resume'])
        assert not marker.exists()
        with pytest.raises(ValueError):
            disk_pause.request_resume(store, jid, state['generation'] - 1)
        disk_pause.request_resume(store, jid, state['generation'])
        thread.join(8)
        assert not thread.is_alive()
        assert result == []
        assert marker.exists()
    finally:
        store.cancel(jid)
        thread.join(3)

WRITER = r'''
import os, subprocess, sys, time
from pathlib import Path
folder = Path(sys.argv[1])
role = sys.argv[2]
(folder / (role + '.pid')).write_text(str(os.getpid()))
if role == 'parent':
    subprocess.Popen([sys.executable, __file__, str(folder), 'child'], start_new_session=True)
with (folder / (role + '.out')).open('ab', buffering=0) as output:
    while True:
        output.write(b'x')
        time.sleep(.01)
'''


def test_owned_detached_tree_stops_writing_resumes_and_cancels(safety, tmp_path):
    store, jid, context, values = safety
    script = tmp_path / 'writer.py'
    script.write_text(WRITER)
    thread, result = background(context, [sys.executable, str(script), str(tmp_path), 'parent'])
    processes = []
    def sizes():
        return [(tmp_path / (role + '.out')).stat().st_size for role in ('parent', 'child')]
    try:
        wait_for(lambda: all((tmp_path / (role + '.out')).exists() for role in ('parent', 'child')))
        processes = [psutil.Process(int((tmp_path / (role + '.pid')).read_text())) for role in ('parent', 'child')]
        values['available'] = 9 * disk_pause.GIB
        wait_for(lambda: (disk_pause.snapshot(store, jid) or {}).get('state') == 'paused')
        state = disk_pause.snapshot(store, jid)
        assert state['pause_kind'] == 'processes_suspended'
        assert all(p.status() == psutil.STATUS_STOPPED for p in processes)
        stopped_sizes = sizes()
        time.sleep(.25)
        assert sizes() == stopped_sizes
        assert store.job(jid)['status'] == 'running'
        values['available'] = 40 * disk_pause.GIB
        wait_for(lambda: disk_pause.snapshot(store, jid)['can_resume'])
        time.sleep(.15)
        assert sizes() == stopped_sizes
        disk_pause.request_resume(store, jid, state['generation'])
        wait_for(lambda: all(a > b for a, b in zip(sizes(), stopped_sizes)))
        values['errors'] = ['Windows backing drive unavailable']
        wait_for(lambda: disk_pause.snapshot(store, jid)['state'] == 'paused')
        assert disk_pause.snapshot(store, jid)['reason'] == 'monitor_unavailable'
        assert not disk_pause.snapshot(store, jid)['can_resume']
        store.cancel(jid)
        thread.join(8)
        assert not thread.is_alive()
        assert len(result) == 1 and isinstance(result[0], Cancelled)
        wait_for(lambda: all(not p.is_running() or p.status() == psutil.STATUS_ZOMBIE for p in processes))
    finally:
        store.cancel(jid)
        thread.join(3)
        for process in processes:
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                process.kill()


def test_stalled_probe_pauses_and_does_not_block_stop(safety, tmp_path, monkeypatch):
    store, jid, context, values = safety
    monkeypatch.setattr(disk_pause, 'STALE_SECONDS', .2)
    marker = tmp_path / 'started'
    code = 'from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); time.sleep(30)'
    thread, result = background(context, [sys.executable, '-c', code, str(marker)])
    try:
        wait_for(marker.exists)
        values['hang'] = True
        wait_for(lambda: (disk_pause.snapshot(store, jid) or {}).get('state') == 'paused')
        state = disk_pause.snapshot(store, jid)
        assert state['reason'] == 'monitor_unavailable'
        assert not state['can_resume']
        started = time.monotonic()
        store.cancel(jid)
        thread.join(4)
        assert not thread.is_alive()
        assert time.monotonic() - started < 4
        assert len(result) == 1 and isinstance(result[0], Cancelled)
    finally:
        store.cancel(jid)
        thread.join(3)


def test_fast_guarded_command_succeeds_and_finish_is_recorded(safety):
    store, jid, context, _ = safety
    context.command([sys.executable, '-c', 'pass'])
    context.disk_guard.finish('succeeded')
    assert disk_pause.snapshot(store, jid)['state'] == 'finished'
    assert context.disk_guard.tree is None
    assert context.disk_guard.watch is None


def test_monitor_failure_before_launch_retains_outputs_and_is_cancellable(safety, tmp_path):
    store, jid, context, values = safety
    values['errors'] = ['Required Windows host capacity unavailable']
    original = tmp_path / 'existing-result'
    original.write_bytes(b'keep every byte')
    marker = tmp_path / 'should-not-launch'
    thread, result = background(context, [sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).touch()', str(marker)])
    try:
        wait_for(lambda: (disk_pause.snapshot(store, jid) or {}).get('state') == 'paused')
        state = disk_pause.snapshot(store, jid)
        assert state['reason'] == 'monitor_unavailable'
        assert state['pause_kind'] == 'before_launch'
        assert not marker.exists()
        assert original.read_bytes() == b'keep every byte'
        store.cancel(jid)
        thread.join(4)
        assert not thread.is_alive()
        assert isinstance(result[0], Cancelled)
    finally:
        store.cancel(jid)
        thread.join(3)


def test_resume_requires_new_sample_and_never_retries_rejected_request(safety):
    store, jid, context, _ = safety
    guard = context.disk_guard
    guard.samples.close()
    class ControlledSamples:
        def __init__(self):
            self.available = 9 * disk_pause.GIB
            self.started = time.monotonic()
            self.wake = threading.Event()
        def current(self):
            return {'volumes': [{'available_bytes': self.available}], 'errors': [], 'checked_at': now()}, self.started
        def close(self):
            pass
    sample = guard.samples = ControlledSamples()
    assert not guard.tick()
    sample.available = 40 * disk_pause.GIB
    guard.tick()
    guard.publish(force=True)
    generation = guard.record['generation']
    request = disk_pause.request_resume(store, jid, generation)
    assert disk_pause.request_resume(store, jid, generation)['nonce'] == request['nonce']
    guard.tick()
    assert not guard.tick()  # Existing measurement cannot satisfy a new request.
    assert guard.record['state'] == 'paused'
    sample.started = time.monotonic()
    sample.available = 9 * disk_pause.GIB
    assert not guard.tick()
    assert store.get('disk_safety_requests', jid) is None
    sample.available = 40 * disk_pause.GIB
    sample.started = time.monotonic()
    assert not guard.tick()  # Free space does not retry a rejected request.
    guard.publish(force=True)
    disk_pause.request_resume(store, jid, generation)
    guard.tick()
    sample.started = time.monotonic()
    assert guard.tick()


def test_pause_verification_failure_terminates_owned_command(safety, tmp_path, monkeypatch):
    store, jid, context, values = safety
    marker = tmp_path / 'pid'
    code = 'from pathlib import Path; import os,sys,time; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)'
    thread, result = background(context, [sys.executable, '-c', code, str(marker)])
    process = None
    try:
        wait_for(marker.exists)
        process = psutil.Process(int(marker.read_text()))
        def fail_pause(self):
            raise disk_pause.SuspensionError('Fixture cannot verify suspension')
        monkeypatch.setattr(disk_pause.OwnedTree, 'freeze', fail_pause)
        values['available'] = 9 * disk_pause.GIB
        thread.join(8)
        assert not thread.is_alive()
        assert len(result) == 1 and isinstance(result[0], disk_pause.SuspensionError)
        wait_for(lambda: not process.is_running() or process.status() == psutil.STATUS_ZOMBIE)
        context.disk_guard.finish('failed')
        assert disk_pause.snapshot(store, jid)['state'] == 'failed'
    finally:
        store.cancel(jid)
        thread.join(3)
        if process is not None and process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
            process.kill()
