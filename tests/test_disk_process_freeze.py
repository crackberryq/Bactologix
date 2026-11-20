import signal

import psutil
import pytest

from bactologix import disk_process


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class DelayedStop:
    def __init__(self, clock, delay):
        self.clock, self.delay = clock, delay
        self.stop_requested = None
        self.signals = []

    def status(self):
        if self.stop_requested is not None and self.clock.now >= self.stop_requested + self.delay:
            return psutil.STATUS_STOPPED
        return psutil.STATUS_DISK_SLEEP

    def send_signal(self, sig):
        self.signals.append(sig)
        if sig == signal.SIGSTOP and self.stop_requested is None:
            self.stop_requested = self.clock.now


@pytest.fixture
def owned(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(disk_process, 'time', clock)
    tree = object.__new__(disk_process.OwnedTree)
    tree.stopped_by_us = set()
    return clock, tree


def test_storage_pause_waits_for_delayed_stop_and_checks_new_descendant(owned):
    clock, tree = owned
    root_id, child_id, previously_stopped_id = (10, 1), (11, 2), (12, 3)
    root = DelayedStop(clock, 2.5)
    child = DelayedStop(clock, .3)
    previously_stopped = DelayedStop(clock, 0)
    previously_stopped.stop_requested = 0

    def members():
        current = {root_id: root, previously_stopped_id: previously_stopped}
        if clock.now >= .5:
            current[child_id] = child
        return current

    tree.refresh = members
    tree.freeze(timeout=disk_process.STORAGE_SUSPEND_SECONDS)

    assert 2 < clock.now < disk_process.STORAGE_SUSPEND_SECONDS
    assert root.signals == child.signals == [signal.SIGSTOP]
    assert previously_stopped.signals == []
    assert tree.stopped_by_us == {root_id, child_id}


def test_descendant_appearing_at_confirmation_is_also_stopped(owned):
    clock, tree = owned
    root, child = DelayedStop(clock, 0), DelayedStop(clock, 0)
    before = {(10, 1): root}
    after = {(10, 1): root, (11, 2): child}
    scans = iter((before, before))
    tree.refresh = lambda: next(scans, after)

    tree.freeze()

    assert root.signals == child.signals == [signal.SIGSTOP]
    assert child.status() == psutil.STATUS_STOPPED


def test_slow_initial_scan_still_allows_time_for_acknowledgement(owned):
    clock, tree = owned
    process = DelayedStop(clock, .1)

    def members():
        if clock.now == 0:
            clock.sleep(2.05)
        return {(10, 1): process}

    tree.refresh = members
    tree.freeze(timeout=2)

    assert process.signals == [signal.SIGSTOP]
    assert process.status() == psutil.STATUS_STOPPED


def test_immediate_stop_is_verified_after_slow_signal_delivery(owned):
    clock, tree = owned

    class SlowSignal(DelayedStop):
        def send_signal(self, sig):
            super().send_signal(sig)
            clock.sleep(2.05)

    process = SlowSignal(clock, 0)
    tree.refresh = lambda: {(10, 1): process}
    tree.freeze(timeout=2)

    assert process.signals == [signal.SIGSTOP]
    assert process.status() == psutil.STATUS_STOPPED


def test_unconfirmed_new_child_at_deadline_cannot_be_reported_as_stopped(owned):
    clock, tree = owned
    root = DelayedStop(clock, 1)
    child = DelayedStop(clock, 60)

    def members():
        current = {(10, 1): root}
        if clock.now >= 1:
            current[(11, 2)] = child
        return current

    tree.refresh = members
    with pytest.raises(disk_process.SuspensionError) as exc:
        tree.freeze(timeout=1)

    assert root.status() == psutil.STATUS_STOPPED
    assert child.status() == psutil.STATUS_DISK_SLEEP
    assert root.signals == [signal.SIGSTOP]
    assert child.signals == []
    assert '11' in str(exc.value) and psutil.STATUS_DISK_SLEEP in str(exc.value)


@pytest.mark.parametrize('storage,expected_timeout', [(False, 2.0), (True, 5.0)])
def test_missing_acknowledgement_expires_once_without_resuming(owned, storage, expected_timeout):
    clock, tree = owned
    process = DelayedStop(clock, 30)
    tree.refresh = lambda: {(10, 1): process}
    with pytest.raises(disk_process.SuspensionError, match='did not acknowledge suspension'):
        if storage:
            tree.freeze(timeout=disk_process.STORAGE_SUSPEND_SECONDS)
        else:
            tree.freeze()

    assert clock.now == pytest.approx(expected_timeout)
    assert process.signals == [signal.SIGSTOP]
