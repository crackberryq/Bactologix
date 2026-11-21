"""Critical storage is global; recovery is explicit and freshly verified."""
import fcntl
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from bactologix import disk_stop
from bactologix.disk_pause import GIB
from bactologix.store import Store, now


def volume(ident, path, gib):
    return dict(kind='linux', id=ident, label=ident, path=str(path),
                paths=[str(path)], available_bytes=gib * GIB, total_bytes=80 * GIB)


def test_workspace_disk_stop_and_explicit_recovery(tmp_path, monkeypatch):
    from bactologix.queue import change
    store = Store(tmp_path / 'workspace')
    waiting = store.submit('smoke', {})
    unlock = store.submit('workflow_unlock', {'run_id': 'saved'})
    rows = [volume('workspace', store.home, 20), volume('swap', tmp_path, 10)]
    initial = dict(checked_at=now(), volumes=rows, errors=[])
    with store.connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        message = disk_stop.trip(store, {'id': 'active'}, initial, [rows[1]])
        assert 'swap' in message and disk_stop.stop_reason(store) == message

    latch = disk_stop.snapshot(Store(store.home))
    assert latch['blocked'] and latch['job_id'] == 'active'
    assert store.queue_snapshot()['paused'] and store.queue_snapshot()['storage_stop']['id'] == latch['id']
    with pytest.raises(ValueError, match='Workspace storage stop'):
        store.submit('smoke', {})
    assert store.claim() is None
    assert store.job(unlock)['status'] == 'queued'
    with pytest.raises(ValueError, match='Workspace storage stop'):
        change(store, 'resume', store.queue_snapshot()['revision'])

    current = deepcopy(initial)
    class Probe:
        anchors = {}

        def __init__(self, store, run, **kwargs):
            pass

        def include_paths(self, paths):
            assert paths == {str(store.home): 'workspace', str(tmp_path): 'swap'}

        def sample(self):
            return deepcopy(current)
    monkeypatch.setattr(disk_stop, 'CapacityProbe', Probe)
    with pytest.raises(ValueError, match='changed'):
        disk_stop.release(store, 'wrong-generation')
    with (store.home / 'worker.lock').open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match='still stopping'):
            disk_stop.release(store, latch['id'])

    current['volumes'][1]['available_bytes'] = 14 * GIB
    with pytest.raises(ValueError, match='at least 15 GiB'):
        disk_stop.release(store, latch['id'])
    assert disk_stop.snapshot(store)['volumes'][1]['available_bytes'] == 14 * GIB
    current['volumes'].pop()
    with pytest.raises(ValueError, match='Missing original storage location: swap'):
        disk_stop.release(store, latch['id'])
    assert len(disk_stop.snapshot(store)['required_volumes']) == 2
    current['volumes'].append(volume('replacement', tmp_path, 16))
    with pytest.raises(ValueError, match='Missing original storage location: swap'):
        disk_stop.release(store, latch['id'])
    current['volumes'][1] = volume('swap', tmp_path, 15)
    current['checked_at'] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    with pytest.raises(ValueError, match='fresh storage check'):
        disk_stop.release(store, latch['id'])
    current['checked_at'] = now()
    disk_stop.release(store, latch['id'])
    assert disk_stop.snapshot(store) is None
    assert store.queue_snapshot()['paused']
    assert store.job(waiting)['held'] and store.job(unlock)['held']
    assert store.claim() is None


def test_deleted_run_folder_can_recover_on_original_filesystem(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    removed = store.home / 'runs/removed'
    removed.mkdir()
    device = removed.stat().st_dev
    initial = dict(checked_at=now(), volumes=[volume('linux:' + str(device), removed, 9)], errors=[])
    disk_stop.trip(store, {'id': 'previous'}, initial, initial['volumes'])
    removed.rmdir()
    monkeypatch.setattr(disk_stop.CapacityProbe, '_reading',
                        staticmethod(lambda path: (path.stat().st_dev, 16 * GIB, 100 * GIB)))
    disk_stop.release(store, disk_stop.snapshot(store)['id'])
    assert store.queue_snapshot()['paused'] and disk_stop.snapshot(store) is None


@pytest.mark.parametrize(('roles', 'free', 'stop', 'can_resume'), [
    (['wsl_swap'], int(9.98 * GIB), 5 * GIB, True),
    (['wsl_swap'], 5 * GIB, 5 * GIB, False),
    (['wsl_swap'], 5 * GIB + 1, 5 * GIB, True),
    (['wsl_swap', 'wsl_backing'], int(9.98 * GIB), 10 * GIB, False),
    ([], int(9.98 * GIB), 10 * GIB, False),
])
def test_swap_volume_limits(roles, free, stop, can_resume):
    from bactologix.disk_pause import _resume_capacity, stop_limit
    row = {'kind': 'windows_host', 'roles': roles, 'available_bytes': free}
    assert stop_limit(row) == stop
    assert _resume_capacity({'volumes': [row]}) is can_resume
