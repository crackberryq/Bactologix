"""Workspace-wide stop on measured critical disk space, independent of SQLite."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid

from .disk_capacity import ProtectedCapacityProbe as CapacityProbe
from .disk_pause import GIB, stop_limit, _fresh, _resume_capacity
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
    readings = ', '.join(f"{v.get('label') or v.get('path') or 'Drive'} ({v['available_bytes'] / GIB:.2f} GiB free, {stop_limit(v) // GIB} GiB limit)"
                         for v in critical)
    reason = f'Workspace storage stop. {readings} reached the storage stop threshold. The queue is blocked.'
    _save(store, {'id': uuid.uuid4().hex, 'blocked': True, 'job_id': job['id'], 'reason': reason,
                  'checked_at': value['checked_at'], 'volumes': value['volumes'],
                  'required_volumes': value['volumes'], 'errors': value.get('errors') or []})
    return reason


def _check(store, state):
    probe = CapacityProbe(store, {})
    value = probe.sample()
    # Historical latches included swap and output mounts. Recheck the
    # installation's protected roles; do not require a retired external mount.
    for old in state['required_volumes']:
        if {'linux_root', 'wsl_backing'}.intersection(old.get('roles', [])) and not any(
                r['id'] == old['id'] for r in value['volumes']):
            value['errors'].append('Missing original storage location: ' + old['label'])
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
        if not _resume_capacity(value) or any(v['available_bytes'] <= stop_limit(v) for v in value['volumes']):
            state.update(volumes=value.get('volumes') or [], errors=value.get('errors') or [],
                         checked_at=value.get('checked_at'))
            _save(store, state)
            raise ValueError('The queue remains blocked. A fresh storage check must confirm more than '
                             '5 GiB free on every protected filesystem. ' + ' '.join(state['errors']))
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
