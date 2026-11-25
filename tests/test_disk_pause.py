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
                    'errors': list(values['errors']), 'checked_at': now(),
                    'windows_sampled_at': values.get('host_time')}
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


def test_missing_storage_allows_launch(safety, tmp_path):
    _, _, ctx, values = safety
    values['errors'] = ['Ubuntu capacity unavailable']
    marker = tmp_path / 'launched'
    ctx.command([sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).touch()', str(marker)])
    assert marker.exists()


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
        values['available'] = 4 * disk_pause.GIB
        wait_for(lambda: (disk_pause.snapshot(store, jid) or {}).get('state') == 'paused')
        state = disk_pause.snapshot(store, jid)
        assert state['pause_kind'] == 'processes_suspended'
        assert all(p.status() == psutil.STATUS_STOPPED for p in processes)
        stopped_sizes = sizes()
        time.sleep(.25)
        assert sizes() == stopped_sizes
        assert store.job(jid)['status'] == 'running'
        values['available'] = 5 * disk_pause.GIB
        wait_for(lambda: disk_pause.snapshot(store, jid)['can_resume'])
        time.sleep(.15)
        assert sizes() == stopped_sizes
        disk_pause.request_resume(store, jid, state['generation'])
        wait_for(lambda: all(a > b for a, b in zip(sizes(), stopped_sizes)))
        # SIGCONT precedes saving the resumed state; output growth alone can
        # still leave the previous storage pause visible in the database.
        wait_for(lambda: disk_pause.snapshot(store, jid)['state'] == 'monitoring')
        values['available'] = 3 * disk_pause.GIB
        wait_for(lambda: (current := disk_pause.snapshot(store, jid))['state'] == 'paused'
                 and current['generation'] > state['generation'])
        state = disk_pause.snapshot(store, jid)
        assert state['reason'] == 'low_space'
        assert not state['can_resume']
        assert 'below 5 GiB' in state['pause_details'][0]
        assert store.log_path(jid).read_text().count('Storage safety pause requested.') == 2
        values['available'] = 5 * disk_pause.GIB
        wait_for(lambda: disk_pause.snapshot(store, jid)['can_resume'])
        recovered = disk_pause.snapshot(store, jid)
        assert recovered['errors'] == []
        assert recovered['pause_details'] == state['pause_details']
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


@pytest.mark.parametrize(('free', 'low'), [(5 * disk_pause.GIB, False), (5 * disk_pause.GIB - 1, True)])
def test_exact_boundary_and_per_volume_freshness(free, low):
    current = {'available_bytes': free, 'sampled_at': now()}
    old = dict(current, sampled_at='2000-01-01T00:00:00+00:00')
    assert disk_pause._low_readings({'volumes': [old, current], 'stale': True}) == ([current] if low else [])


def test_stalled_probe_warns_and_does_not_block_stop(safety, tmp_path, monkeypatch):
    store, jid, context, values = safety
    monkeypatch.setattr(disk_pause, 'STALE_SECONDS', .2)
    marker = tmp_path / 'started'
    code = 'from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); time.sleep(30)'
    thread, result = background(context, [sys.executable, '-c', code, str(marker)])
    try:
        wait_for(marker.exists)
        values['hang'] = True
        wait_for(lambda: (disk_pause.snapshot(store, jid) or {}).get('state') == 'unavailable')
        assert disk_pause.snapshot(store, jid)['generation'] == 0
        started = time.monotonic()
        store.cancel(jid)
        thread.join(4)
        assert not thread.is_alive()
        assert time.monotonic() - started < 4
        assert len(result) == 1 and isinstance(result[0], Cancelled)
    finally:
        store.cancel(jid)
        thread.join(3)


def test_temporary_storage_delay_keeps_native_writer_running(safety, tmp_path, monkeypatch):
    store, jid, context, values = safety
    monkeypatch.setattr(disk_pause, 'STALE_SECONDS', .3)
    progress = tmp_path / 'progress'
    complete = tmp_path / 'complete'
    code = ('from pathlib import Path\nimport sys,time\n'
            'with Path(sys.argv[1]).open("ab", buffering=0) as out:\n'
            '    while not Path(sys.argv[2]).exists():\n'
            '        out.write(b"x")\n        time.sleep(.02)')
    thread, result = background(context, [sys.executable, '-c', code, str(progress), str(complete)])
    try:
        wait_for(progress.exists)
        values['host_time'] = time.time() - .5
        wait_for(lambda: disk_pause.snapshot(store, jid)['state'] == 'unavailable')
        assert not disk_pause.snapshot(store, jid)['can_resume']
        written = progress.stat().st_size
        wait_for(lambda: progress.stat().st_size > written)
        values['host_time'] = None
        wait_for(lambda: disk_pause.snapshot(store, jid)['state'] == 'monitoring')
        assert disk_pause.snapshot(store, jid)['generation'] == 0
        complete.touch()
        thread.join(5)
        assert not thread.is_alive() and result == []
        log = store.log_path(jid).read_text()
        assert log.count('Storage monitoring unavailable.') == log.count('Fresh storage readings received.') == 1
        assert 'Storage safety pause requested.' not in log
    finally:
        complete.touch()
        thread.join(3)


@pytest.mark.parametrize(('running', 'prior_gib', 'errors', 'expected_trigger'), [
    (False, 140, [], 'stale'),  # A checkpoint before exec must still require fresh storage.
    (True, 5, [], 'stale'),
    (True, 4, [], 'stale'),
    (True, 140, ['Windows swap volume missing'], 'monitor_unavailable'),
])
def test_stale_retry_requires_healthy_capacity_and_active_command(safety, monkeypatch,
                                                                 running, prior_gib, errors, expected_trigger):
    from unittest.mock import Mock
    _, _, ctx, _ = safety
    guard = ctx.disk_guard
    guard.samples.close()
    guard.samples.thread.join(1)
    stamp = time.monotonic() - disk_pause.STALE_SECONDS - .1
    reading = {'volumes': [{'available_bytes': gib * disk_pause.GIB} for gib in (100, prior_gib)],
               'errors': errors, 'checked_at': now()}
    guard.samples.result = (reading, stamp, stamp)
    with monkeypatch.context() as patch:
        tree = Mock(spec=disk_pause.OwnedTree)
        patch.setattr(guard, 'tree', tree)
        patch.setattr(guard, 'watch', Mock(spec=disk_pause.OwnerWatch))
        assert guard.tick(command_running=running)
        tree.freeze.assert_not_called()
        assert guard.record['state'] == 'unavailable'


def test_fast_guarded_command_succeeds_and_finish_is_recorded(safety):
    store, jid, context, _ = safety
    context.command([sys.executable, '-c', 'pass'])
    context.disk_guard.finish('succeeded')
    assert disk_pause.snapshot(store, jid)['state'] == 'finished'
    assert context.disk_guard.tree is None
    assert context.disk_guard.watch is None


def test_resume_waits_for_host_observation_after_the_request(safety):
    store, jid, ctx, values = safety
    guard = ctx.disk_guard
    def poll(condition):
        guard.tick()
        return condition()
    values.update(available=4 * disk_pause.GIB, host_time=time.time())
    wait_for(lambda: poll(lambda: guard.record['state'] == 'paused'))
    values.update(available=5 * disk_pause.GIB, host_time=time.time())
    wait_for(lambda: poll(lambda: guard.record['can_resume']))
    guard.publish(force=True)
    disk_pause.request_resume(store, jid, guard.record['generation'])
    guard.tick()
    assert guard.resume_after is not None
    # Repeated reads of the identical host sample cannot authorize this thaw.
    time.sleep(.15)
    assert not guard.tick()
    assert guard.record['state'] == 'paused'
    assert store.get('disk_safety_requests', jid)
    values['host_time'] = time.time()
    wait_for(lambda: poll(lambda: guard.record['state'] == 'monitoring'))
    assert store.get('disk_safety_requests', jid) is None


def test_resume_requires_new_sample_and_never_retries_rejected_request(safety):
    store, jid, context, _ = safety
    guard = context.disk_guard
    guard.samples.close()
    class ControlledSamples:
        def __init__(self):
            self.available = 4 * disk_pause.GIB
            self.errors = ['Output volume unavailable']
            self.started = time.monotonic()
            self.wake = threading.Event()
        def current(self):
            return {'volumes': [{'available_bytes': self.available}], 'errors': self.errors, 'checked_at': now()}, self.started
        def close(self):
            pass
    sample = guard.samples = ControlledSamples()
    assert not guard.tick()
    sample.errors.clear()
    sample.errors = []
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
    sample.errors = ['Output volume unavailable']
    assert not guard.tick()
    assert store.get('disk_safety_requests', jid) is None
    sample.errors = []
    sample.available = 40 * disk_pause.GIB
    sample.started = time.monotonic()
    assert not guard.tick()  # Free space does not retry a rejected request.
    guard.publish(force=True)
    disk_pause.request_resume(store, jid, generation)
    guard.tick()
    sample.started = time.monotonic()
    check_cancel = guard.check_cancel
    def capacity_falls_while_checking_cancel():
        check_cancel()
        sample.errors = ['Output volume unavailable']
        sample.started = time.monotonic()
    guard.check_cancel = capacity_falls_while_checking_cancel
    assert not guard.tick()
    assert guard.record['state'] == 'paused'
    assert store.get('disk_safety_requests', jid) is None

    guard.check_cancel = check_cancel
    sample.errors = []
    sample.available = 40 * disk_pause.GIB
    sample.started = time.monotonic()
    assert not guard.tick()
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
        def fail_pause(self, *, timeout):
            raise disk_pause.SuspensionError('Fixture cannot verify suspension')
        monkeypatch.setattr(disk_pause.OwnedTree, 'freeze', fail_pause)
        values['available'] = 4 * disk_pause.GIB
        thread.join(8)
        assert not thread.is_alive()
        assert len(result) == 1 and isinstance(result[0], disk_pause.SuspensionError)
        wait_for(lambda: not process.is_running() or process.status() == psutil.STATUS_ZOMBIE)
        context.disk_guard.finish('running')  # Worker holds the job until it can confirm cleanup.
        state = disk_pause.snapshot(store, jid)
        assert state['state'] == 'failed' and state['result'] == 'interrupted'
        assert state['pause_trigger'] == 'low_space'
        assert 'below 5 GiB' in state['pause_details'][0]
        assert 'Storage safety pause requested.' in store.log_path(jid).read_text()
    finally:
        store.cancel(jid)
        thread.join(3)
        if process is not None and process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
            process.kill()
