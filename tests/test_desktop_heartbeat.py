"""Desktop owner evidence must outlast a transient Windows/WSL sample gap."""
import errno
import time
import uuid

import pytest

from bactologix import desktop
from bactologix.store import Store


class Clock:
    def __init__(self):
        self.wall = time.time()
        self.mono = time.monotonic()

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


@pytest.fixture
def owner(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(desktop, 'time', clock)
    store, key = Store(tmp_path / 'workspace'), uuid.uuid4().hex
    root = tmp_path / key
    root.mkdir()
    item = desktop.Session(store, root, key)
    desktop.atomic(root / 'heartbeat.json', {'session': key, 'time': clock.wall})
    item.tick('', True)
    monkeypatch.setenv(desktop.ENV, key)
    job = store.submit('smoke', {})
    return item, store, clock, job


def pulse(item, stamp):
    desktop.atomic(item.root / 'heartbeat.json', {'session': item.key, 'time': stamp})


def test_advancing_host_heartbeat_ahead_of_wsl_keeps_work(owner):
    item, store, clock, job = owner
    for _ in range(12):
        clock.advance(4)
        pulse(item, int(clock.wall) + 2)
        assert item.poll('', True) is False
        assert item.heartbeat_observed == clock.mono
    assert item.phase == 'ready' and not store.job(job)['cancel']
    assert item.record_contended_lease()
    assert desktop.read(item.proof_path)['heartbeat'] == clock.wall
    clock.advance(.5)
    assert item.record_contended_lease()
    assert desktop.read(item.proof_path)['heartbeat'] == clock.wall - .5


@pytest.mark.parametrize('miss', ['absent', 'malformed', 'other-session'])
def test_missing_pulse_does_not_cancel_an_established_desktop(owner, miss):
    item, store, clock, job = owner
    item.started -= 90  # The app window has been open longer than one lease.
    clock.advance(5)
    path = item.root / 'heartbeat.json'
    if miss == 'absent':
        path.unlink()
    elif miss == 'malformed':
        path.write_text('{')
    else:
        desktop.atomic(path, {'session': 'another-desktop', 'time': clock.wall})
    assert item.poll('', True) is False
    assert not store.job(job)['cancel']
    clock.advance(22)
    pulse(item, clock.wall)
    assert item.poll('', True) is False
    assert item.heartbeat_observed == clock.mono
    assert item.phase == 'ready'


@pytest.mark.parametrize('bad', ['repeat', 'reverse', 'mismatched', 'far-future', 'late-fresh'])
def test_only_fresh_matching_beats_restore_connection_while_work_continues(owner, bad):
    item, store, clock, job = owner
    original = item.last_host_heartbeat
    for i in range(3):
        clock.advance(9)
        if bad == 'reverse':
            pulse(item, original - i - 1)
        elif bad == 'far-future':
            pulse(item, clock.wall + 3600)
        elif bad == 'mismatched':
            desktop.atomic(item.root / 'heartbeat.json', {'session': uuid.uuid4().hex, 'time': clock.wall})
        assert item.tick('', True) is False
    clock.advance(4)
    if bad == 'late-fresh':
        pulse(item, clock.wall)
    assert not item.tick('', True)
    assert not store.job(job)['cancel']
    assert bool(item.disconnect_detail) == (bad != 'late-fresh')
    pulse(item, clock.wall + 1)
    assert not item.tick('', True)
    assert not item.disconnect_detail


def test_confirmed_close_during_read_gap_stops_immediately(owner):
    item, store, clock, job = owner
    (item.root / 'heartbeat.json').unlink()
    clock.advance(5)
    desktop.atomic(item.root / 'request.json',
                   {'session': item.key, 'action': 'confirm_close', 'nonce': 'confirmed'})
    assert item.tick('', True) is True
    assert store.job(job)['cancel'] and item.reason == 'Window closed'


def block_status_replacement(item, monkeypatch, code, *, times=None):
    original = desktop.os.replace
    attempts = []
    def replace(source, target):
        if target == item.root / 'status.json' and (times is None or len(attempts) < times):
            attempts.append(target)
            raise OSError(code, 'fixture: desktop status replacement failed')
        return original(source, target)
    monkeypatch.setattr(desktop.os, 'replace', replace)
    return attempts


def test_transient_windows_status_sharing_conflict_keeps_session_and_job(owner, monkeypatch):
    item, store, clock, job = owner
    attempts = block_status_replacement(item, monkeypatch, errno.EACCES, times=1)
    assert not item.poll('old', True)
    assert not item.poll('new', True)
    assert len(attempts) == 1
    assert desktop.read(item.root / 'status.json')['url'] == 'new'
    assert item.status_blocked_since is None and not store.job(job)['cancel']


def test_persistent_status_sharing_failure_is_bounded(owner, monkeypatch):
    item, store, clock, job = owner
    block_status_replacement(item, monkeypatch, errno.EBUSY)
    assert not item.poll('', True)
    for _ in range(2):
        clock.advance(12)
        pulse(item, clock.wall)
        assert not item.poll('', True)
    clock.advance(8)
    pulse(item, clock.wall)
    with pytest.raises(OSError) as failure:
        item.poll('', True)
    assert failure.value.errno == errno.EBUSY


def test_status_disk_full_fails_without_sharing_retry(owner, monkeypatch):
    item, store, clock, job = owner
    block_status_replacement(item, monkeypatch, errno.ENOSPC)
    with pytest.raises(OSError) as failure:
        item.poll('', True)
    assert failure.value.errno == errno.ENOSPC and item.status_blocked_since is None


def test_final_status_retries_a_sharing_conflict_before_exit(owner, monkeypatch):
    item, store, clock, job = owner
    item.stop()
    clock.sleep = clock.advance
    attempts = block_status_replacement(item, monkeypatch, errno.EACCES, times=1)
    item.finish('')
    assert len(attempts) == 1
    assert desktop.read(item.root / 'status.json')['phase'] == 'stopped'


def test_live_monitor_uses_only_the_job_owners_observed_windows_pulse(owner, monkeypatch):
    from bactologix.ui_backend import dispatch
    item, store, clock, job = owner
    assert store.claim()['id'] == job
    # Historical telemetry reflects a long pause in WSL observation.
    history = [clock.wall - 13 * 3600]
    store.put('job_telemetry', job, dict(schema=1, id=job, monitor_cycle={'heartbeats': history}))
    other_key = uuid.uuid4().hex
    other_dir = item.root.parent / other_key
    other_dir.mkdir()
    other = desktop.Session(store, other_dir, other_key)
    monkeypatch.setenv(desktop.ENV, other_key)
    clock.advance(2)
    pulse(other, clock.wall)
    other.tick('', True)
    own_stamp = item.heartbeat_wall
    usage = dispatch(store, 'job_metrics', {'id': job})['usage']
    assert usage['launcher_pulse']['observed_at'] == own_stamp < other.heartbeat_wall
    assert store.get('job_telemetry', job)['monitor_cycle']['heartbeats'] == history

    # SQLite session polls continue after Windows stops emitting valid pulses.
    clock.advance(desktop.LEASE_SECONDS + 1)
    desktop.atomic(item.root / 'heartbeat.json', {'session': other_key, 'time': clock.wall})
    item.tick('', True)
    pulse(other, clock.wall)
    other.tick('', True)
    assert store.get('desktop_sessions', item.key)['last_seen'] == clock.wall
    assert desktop.displayed_launcher_pulse(store, job) is None
    # Reassignment, cancellation and completion are each observed on a fresh read.
    store.put('job_owners', job, other_key)
    assert desktop.displayed_launcher_pulse(store, job)['observed_at'] == other.heartbeat_wall
    store.cancel(job)
    assert desktop.displayed_launcher_pulse(store, job) is None
    store.update_job(job, status='succeeded', cancel=0)
    assert 'launcher_pulse' not in dispatch(store, 'job_metrics', {'id': job})['usage']


def test_display_pulse_never_probes_windows_or_waits_for_a_writer(owner, monkeypatch):
    item, store, _, job = owner
    assert store.claim()['id'] == job
    monkeypatch.setattr(desktop, 'read', lambda *_: pytest.fail('Display read touched Windows'))
    monkeypatch.setattr(desktop, 'confirm_windows_owner', lambda *_: pytest.fail('Display started supervisor probe'))
    assert desktop.displayed_launcher_pulse(store, job)['observed_at'] == item.heartbeat_wall
    with store.connect() as db:
        db.execute('PRAGMA locking_mode=EXCLUSIVE')
        db.execute('BEGIN EXCLUSIVE')
        before = time.monotonic()
        assert desktop.displayed_launcher_pulse(store, job) is None
        assert time.monotonic() - before < .5
    session = store.get('desktop_sessions', item.key)
    session.pop('launcher_heartbeat')  # A launcher predating the display field.
    store.put('desktop_sessions', item.key, session)
    assert desktop.displayed_launcher_pulse(store, job) is None


def test_resource_sampler_picks_newest_observer_even_if_pulse_file_is_stale(owner):
    from types import SimpleNamespace
    from bactologix.engine import Context
    item, store, _, _ = owner
    now = time.time()
    session = {'launcher_heartbeat': now - 1}
    ctx = SimpleNamespace(disk_guard=None, verified_desktop=(item.key, session), store=store)
    desktop.atomic(item.proof_path, {'session': item.key, 'heartbeat': now - 2})
    desktop.atomic(item.pulse_path, {'session': item.key, 'heartbeat': now - 13 * 3600})
    assert Context.monitor_sources(ctx)[1] == now - 1
    session.clear()
    assert Context.monitor_sources(ctx)[1] == now - 2
    desktop.atomic(item.proof_path, {'session': item.key, 'stopping': True})
    assert Context.monitor_sources(ctx)[1] is None
