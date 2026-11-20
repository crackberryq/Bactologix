"""Workspace-wide stop on measured critical disk space, independent of SQLite."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid

from .disk_capacity import CapacityProbe
from .disk_pause import GIB, RESUME_BYTES, _fresh, _resume_capacity
from .store import now


def _path(store):
    return store.home / 'storage-stop.json'


def _save(store, record):
    # The worker owns worker.lock when tripping; recovery acquires the same lock.
    # Atomic replacement keeps polling jobs and the desktop on a complete record.
    target = _path(store)
    with tempfile.NamedTemporaryFile(mode='w', dir=store.home, prefix='.storage-stop-', delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)


def snapshot(store):
    try:
        value = json.loads(_path(store).read_text())
        if (not isinstance(value, dict) or not isinstance(value.get('required_volumes'), list)
                or not isinstance(value.get('reason'), str) or not value.get('id')):
            raise ValueError('Invalid storage stop record.')
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'blocked': True, 'reason': 'The workspace storage stop record cannot be read. The queue remains blocked.'}


def stop_reason(store, job=None):
    state = snapshot(store)
    if not state:
        return None
    if (job and job['kind'] == 'storage_cleanup' and state.get('id')
            and job['payload'].get('storage_stop_id') == state['id']):
        return None
    return state['reason']


def trip(store, job, value, critical):
    previous = snapshot(store)
    if previous:
        return previous['reason']
    readings = ', '.join(f"{v.get('label') or v.get('path') or 'Drive'} ({v['available_bytes'] / GIB:.2f} GiB free)"
                         for v in critical)
    reason = f'Workspace storage stop. {readings} reached the 10 GiB limit. The queue is blocked.'
    _save(store, {'id': uuid.uuid4().hex, 'blocked': True, 'job_id': job['id'], 'reason': reason,
                  'checked_at': value['checked_at'], 'volumes': value['volumes'],
                  'required_volumes': value['volumes'], 'errors': value.get('errors') or []})
    return reason


def _check(store, state):
    required = state['required_volumes']
    linux_paths = {path: row.get('label', 'Monitored drive') for row in required if row.get('kind') == 'linux'
                   for path in row.get('paths') or [row['path']]}
    # All original temporary mounts are included explicitly. Use an already
    # required directory for this short probe's own default temporary path.
    probe = CapacityProbe(store, {}, extra_env={'TMPDIR': str(store.home)})
    probe.include_paths(linux_paths)
    value = probe.sample()
    observed = {row['id']: set(row.get('paths') or [row['path']]) for row in value['volumes']}
    missing = []
    for row in required:
        paths = set(row.get('paths') or [row['path']])
        found = observed.get(row['id'], set())
        for path in paths - found:
            # A reviewed cleanup may remove the run folder. Its nearest live
            # parent must still belong to the original measured filesystem.
            anchor = probe.anchors.get(path) if row.get('kind') == 'linux' else None
            if not anchor or 'linux:' + str(anchor[1]) != row['id'] or str(anchor[0]) not in found:
                missing.append(row.get('label', row['id']))
                break
    if missing:
        value['errors'] = list(value.get('errors') or []) + ['Missing original storage location: ' + ', '.join(missing)]
    return value


def release(store, expected_id):
    """User-requested recovery. Old waiting work stays held until selected."""
    from .queue import control, save_control
    with (store.home / 'worker.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('The worker is still stopping. Wait for the active tools to exit before rechecking.') from exc
        state = snapshot(store)
        if not state or not expected_id or state.get('id') != expected_id:
            raise ValueError('The storage stop changed. Refresh Jobs & progress before continuing.')
        value = _check(store, state)
        if not _resume_capacity(value) or not _fresh(value.get('checked_at')):
            state.update(volumes=value.get('volumes') or [], errors=value.get('errors') or [],
                         checked_at=value.get('checked_at'))
            _save(store, state)
            raise ValueError('The queue remains blocked. A fresh storage check must confirm at least '
                             f'{RESUME_BYTES // GIB} GiB free on every monitored drive. ' + ' '.join(state['errors']))
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Each operation uses one workspace worker; orphaned work also holds
            # worker.lock. A stale running row still needs normal reconciliation.
            if db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                raise ValueError('An active attempt still needs reconciliation before the storage stop can clear.')
            db.execute("""INSERT INTO job_queue(job_id,position,held)
                          SELECT id,rowid,1 FROM jobs WHERE status='queued'
                          ON CONFLICT(job_id) DO UPDATE SET held=1""")
            queue = control(db)
            queue.update(paused=True, pause_reason='storage_recovered')
            save_control(db, queue)
        _path(store).unlink()
    return {'cleared_at': now()}
