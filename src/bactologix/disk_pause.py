"""Fail-closed disk guard for local workflow writes; no data is removed."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import threading
import time
import uuid
from .disk_capacity import CapacityProbe
from .disk_process import OwnedTree, OwnerWatch, SuspensionError
from .store import now

GIB = 1024 ** 3
PAUSE_BYTES, RESUME_BYTES, WARNING_BYTES = 10 * GIB, 15 * GIB, 20 * GIB
POLL_SECONDS, STALE_SECONDS = 1.0, 5.0
THRESHOLDS = {'pause_bytes': PAUSE_BYTES, 'resume_bytes': RESUME_BYTES, 'warning_bytes': WARNING_BYTES}

def _fresh(checked_at):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(checked_at)).total_seconds()
        return -2 <= age <= STALE_SECONDS
    except (ValueError, TypeError):
        return False

def snapshot(store, jid):
    record = store.get('disk_safety', jid)
    if not record:
        return None
    job = store.job(jid)
    live = bool(job and job['status'] == 'running' and not job.get('cancel'))
    record['live'] = live
    record['can_resume'] = bool(live and record.get('state') == 'paused' and record.get('can_resume') and _fresh(record.get('checked_at')))
    request = store.get('disk_safety_requests', jid, {})
    record['resume_requested'] = bool(live and record.get('state') == 'paused' and request.get('generation') == record.get('generation'))
    return record

def request_resume(store, jid, expected_generation):
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
        raise ValueError('Refresh the run before requesting resume.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        saved = db.execute("SELECT body FROM items WHERE kind='disk_safety' AND id=?", (jid,)).fetchone()
        state = json.loads(saved[0]) if saved else {}
        if not job or job['status'] != 'running' or job['cancel'] or job['kind'] != 'workflow':
            raise ValueError('Only a currently paused workflow can be resumed.')
        if state.get('state') != 'paused' or state.get('generation') != expected_generation:
            raise ValueError('The pause changed. Refresh the run before resuming.')
        if not state.get('can_resume') or not _fresh(state.get('checked_at')):
            raise ValueError('Resume requires a fresh check with at least 15 GiB available on every required volume.')
        previous = db.execute("SELECT body FROM items WHERE kind='disk_safety_requests' AND id=?", (jid,)).fetchone()
        if previous and json.loads(previous[0]).get('generation') == expected_generation:
            return json.loads(previous[0])
        request = {'generation': expected_generation, 'nonce': uuid.uuid4().hex, 'requested_at': now()}
        db.execute("INSERT INTO items VALUES ('disk_safety_requests',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body", (jid, json.dumps(request)))
    return request

class Samples:
    """A stalled mount never blocks the worker's stop handling or its exit."""
    def __init__(self, store, run):
        self.store, self.run = store, run
        self.lock, self.wake, self.done = threading.Lock(), threading.Event(), threading.Event()
        self.pending, self.epoch, self.result = [], 0, None
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._loop, daemon=True, name='disk-capacity')
        self.thread.start()

    def include_command(self, cwd, extra_env):
        with self.lock:
            self.pending.append((cwd, dict(extra_env or {})))
            self.epoch += 1
            self.result = None
            self.started = time.monotonic()
        self.wake.set()

    def _loop(self):
        probe = None
        while not self.done.is_set():
            with self.lock:
                pending, self.pending = self.pending, []
                epoch = self.epoch
            sample_started = time.monotonic()
            try:
                if probe is None:
                    probe = CapacityProbe(self.store, self.run)
                for cwd, extra_env in pending:
                    probe.include_command(cwd=cwd, extra_env=extra_env)
                value = probe.sample()
            except Exception as exc:
                value = {'volumes': [], 'errors': ['Storage monitoring failed: ' + str(exc)], 'checked_at': now()}
                with self.lock:
                    # Retry requested paths after failed discovery/registration.
                    self.pending = pending + self.pending
            with self.lock:
                if epoch == self.epoch:
                    self.result = (value, time.monotonic(), sample_started)
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def current(self):
        with self.lock:
            result, started = self.result, self.started
        if result is None:
            if time.monotonic() - started < STALE_SECONDS:
                return None
            return {'volumes': [], 'errors': ['Required storage checks did not finish within 5 seconds.'], 'checked_at': None}, 0
        value, completed, sample_started = result
        value = dict(value)
        if time.monotonic() - completed > STALE_SECONDS:
            value['errors'] = list(value.get('errors') or []) + ['Required storage checks are stale.']
        return value, sample_started

    def close(self):
        self.done.set()
        self.wake.set()


class Guard:
    def __init__(self, store, job, check_cancel, write):
        self.store, self.job = store, job
        self.check_cancel, self.write = check_cancel, write
        self.tree, self.watch = None, None
        self.last_publish, self.resume_nonce, self.resume_after = 0, None, None
        run = store.get('runs', job['payload'].get('run_id'), {})
        self.samples = Samples(store, run)
        self.record = {'id': job['id'], 'run_id': job['payload'].get('run_id'),
            'state': 'monitoring', 'generation': 0, 'pause_kind': None, 'reason': None,
            'volumes': [], 'errors': [], 'checked_at': None, 'can_resume': False,
            'thresholds': dict(THRESHOLDS)}

    def publish(self, force=False):
        if force or time.monotonic() - self.last_publish >= POLL_SECONDS:
            self.record['updated_at'] = now()
            self.store.put('disk_safety', self.job['id'], self.record)
            self.last_publish = time.monotonic()

    def checkpoint(self):
        while True:
            self.check_cancel()
            if self.tick():
                return
            time.sleep(.1)

    def before_command(self, cwd=None, extra_env=None):
        self.samples.include_command(cwd, extra_env)
        self.checkpoint()

    def attach(self, proc):
        self.tree = OwnedTree(proc.pid)
        self.watch = OwnerWatch(self.tree)
        self.tick()

    def tick(self):
        if self.tree is not None:
            self.tree.refresh()
            self.watch.publish()
        sample = self.samples.current()
        if sample is None:
            return False
        value, completed = sample
        volumes, errors = value.get('volumes') or [], list(value.get('errors') or [])
        valid = bool(volumes) and all(isinstance(v.get('available_bytes'), int) and not isinstance(v['available_bytes'], bool) and v['available_bytes'] >= 0 for v in volumes)
        if not valid:
            errors.append('Required storage capacities are unavailable or invalid.')
        low = valid and any(v['available_bytes'] <= PAUSE_BYTES for v in volumes)
        ready = valid and not errors and all(v['available_bytes'] >= RESUME_BYTES for v in volumes)
        old_state = self.record['state']
        self.record.update(volumes=volumes, errors=errors, checked_at=value.get('checked_at'))
        if old_state == 'paused' or errors or low:
            if self.tree is not None:
                try:
                    self.tree.freeze()
                except Exception as exc:
                    # Terminate owned work before attempting failure persistence.
                    self.record.update(state='failed', reason='pause_failed', can_resume=False,
                        errors=errors + [str(exc)])
                    raise
                self.watch.publish()
            if old_state != 'paused':
                self.record.update(state='paused', generation=self.record['generation'] + 1,
                    paused_at=now(), pause_kind='processes_suspended' if self.tree else 'before_launch',
                    reason='monitor_unavailable' if errors else 'low_space')
                self.publish(force=True)
                self.write('Storage safety paused this run: ' + ('required capacity checks are unavailable.' if errors else 'a required volume has 10 GiB or less available.'))
            if errors:
                self.record['reason'] = 'monitor_unavailable'
            elif low:
                self.record['reason'] = 'low_space'
            self.record['can_resume'] = ready
            request = self.store.get('disk_safety_requests', self.job['id'], {})
            if request.get('generation') == self.record['generation']:
                if request.get('nonce') != self.resume_nonce:
                    self.resume_nonce, self.resume_after = request.get('nonce'), time.monotonic()
                    self.samples.wake.set()
                elif completed > self.resume_after:
                    with self.store.connect() as db:
                        db.execute("DELETE FROM items WHERE kind='disk_safety_requests' AND id=?", (self.job['id'],))
                    if ready:
                        self.check_cancel()
                        if self.tree is not None:
                            self.tree.resume()
                        self.record.update(state='monitoring', can_resume=False, pause_kind=None, reason=None, resumed_at=now())
                        self.write('Storage checks passed on every required volume; resumed by user request.')
                    else:
                        self.write('Resume was not applied: fresh storage checks did not meet the 15 GiB requirement.')
            self.publish(force=old_state != self.record['state'])
            return self.record['state'] != 'paused'
        self.record.update(state='warning' if any(v['available_bytes'] < WARNING_BYTES for v in volumes) else 'monitoring', can_resume=False)
        if self.record['state'] == 'warning' and old_state != 'warning':
            self.write('Storage is running low: less than 20 GiB available. Automatic pause begins at 10 GiB.')
        self.publish(force=old_state != self.record['state'])
        return True

    def stop(self):
        # Signals precede writes, which may fail on a full disk.
        if self.tree is not None:
            self.tree.stop()
        self.record.update(state='stopping', can_resume=False)
        self.publish(force=True)

    def signal(self, sig):
        if self.tree is not None:
            self.tree.signal(sig)

    def detach(self, failed=False):
        tree, watch = self.tree, self.watch
        clean = False
        try:
            if tree is not None:
                remaining = tree.refresh()
                if remaining:
                    tree.kill()
                    if not failed:
                        raise SuspensionError('The command exited while owned child processes remained; stopped remaining work. Review the log before continuing.')
                clean = not remaining
        finally:
            self.tree, self.watch = None, None
            if watch is not None:
                watch.close(clean=clean)

    def finish(self, result):
        self.samples.close()
        try:
            self.detach(failed=True)
        finally:
            self.record.update(state='failed' if result == 'failed' else 'finished', result=result, can_resume=False)
            self.publish(force=True)
