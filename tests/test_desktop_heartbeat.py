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
def test_stale_unrelated_or_late_beats_cannot_revive_work(owner, bad):
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
    assert item.tick('', True) is True
    assert store.job(job)['cancel'] and item.reason.startswith('Desktop supervisor disconnected')
    pulse(item, clock.wall + 1)
    assert item.tick('', True) is True
    assert not item.record_contended_lease()


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
