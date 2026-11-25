"""Fail-closed disk guard for local workflow writes; no data is removed."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import threading
import time
import uuid
from .disk_capacity import CapacityProbe as MonitoringProbe, ProtectedCapacityProbe as CapacityProbe, SAMPLE_MAX_AGE_SECONDS
from .disk_process import OwnedTree, OwnerWatch, ProcessOwner, STORAGE_SUSPEND_SECONDS, SuspensionError
from . import storage_events
from .store import CONTROL_TIMEOUT, now

GIB = 1024 ** 3
PAUSE_BYTES, RESUME_BYTES, WARNING_BYTES = (5 * GIB,) * 3
POLL_SECONDS, STALE_SECONDS = 1.0, SAMPLE_MAX_AGE_SECONDS
ACTIVE_RECHECK_MAX_AGE_SECONDS = 15.0
THRESHOLDS = {'pause_bytes': PAUSE_BYTES, 'resume_bytes': RESUME_BYTES, 'warning_bytes': WARNING_BYTES}

def stop_limit(volume):
    return PAUSE_BYTES


def _resume_capacity(value):
    volumes = value.get('volumes') or []
    return bool(volumes) and not value.get('errors') and all(
        type(volume.get('available_bytes')) is int and volume['available_bytes'] > RESUME_BYTES
        for volume in volumes)

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
    request = store.get('disk_safety_requests', jid, {})
    return display_state(job, record, request)

def display_state(job, record, request):
    if not record:
        return None
    live = bool(job and job['status'] == 'running' and not job.get('cancel'))
    record['live'] = live
    record['can_resume'] = bool(live and record.get('state') == 'paused' and record.get('can_resume') and _fresh(record.get('checked_at')))
    record['resume_requested'] = bool(live and record.get('state') == 'paused' and request.get('generation') == record.get('generation'))
    return record

def request_resume(store, jid, expected_generation):
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
        raise ValueError('Refresh the run before requesting resume.')
    click = uuid.uuid4().hex
    storage_events.append(store, jid, 'resume_received', expected_generation, request_id=click)
    try:
        with store.connect(timeout=CONTROL_TIMEOUT) as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            saved = db.execute("SELECT body FROM items WHERE kind='disk_safety' AND id=?", (jid,)).fetchone()
            state = json.loads(saved[0]) if saved else {}
            if not job or job['status'] != 'running' or job['cancel'] or job['kind'] != 'workflow':
                raise ValueError('Only a currently paused workflow can be resumed.')
            if state.get('state') != 'paused' or state.get('generation') != expected_generation:
                raise ValueError('The pause changed. Refresh the run before resuming.')
            if not state.get('can_resume') or not _fresh(state.get('checked_at')):
                raise ValueError('Resume requires a fresh check with more than 5 GiB free on every protected filesystem.')
            previous = db.execute("SELECT body FROM items WHERE kind='disk_safety_requests' AND id=?", (jid,)).fetchone()
            if previous and json.loads(previous[0]).get('generation') == expected_generation:
                storage_events.append(store, jid, 'resume_duplicate', expected_generation, request_id=click)
                return json.loads(previous[0])
            request = {'generation': expected_generation, 'nonce': click, 'requested_at': now()}
            db.execute("INSERT INTO items VALUES ('disk_safety_requests',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body", (jid, json.dumps(request)))
    except Exception as exc:
        storage_events.append(store, jid, 'resume_rejected', expected_generation, request_id=click, reason=str(exc))
        raise
    return request

class Samples:
    """A stalled mount never blocks the worker's stop handling or its exit."""
    def __init__(self, store, run, *, initial_env=None):
        self.store, self.run = store, run
        self.lock, self.wake, self.done = threading.Lock(), threading.Event(), threading.Event()
        self.epoch, self.result = 0, None
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._loop, daemon=True, name='disk-capacity')
        self.advisory = []
        self.advisory_command = (None, initial_env)
        self.thread.start()
        threading.Thread(target=self._observe_other_volumes, daemon=True, name='storage-display').start()

    def _observe_other_volumes(self):
        # A stalled external mount must never delay the critical sampler.
        probe = None
        while not self.done.is_set():
            try:
                cwd, env = self.advisory_command
                if probe is None:
                    probe = MonitoringProbe(self.store, self.run, cwd=cwd, extra_env=env)
                else:
                    probe.include_command(cwd=cwd, extra_env=env)
                rows = probe.sample()['volumes']
                with self.lock:
                    self.advisory = rows
            except (OSError, ValueError, RuntimeError):
                with self.lock:
                    self.advisory = []
            self.done.wait(5)

    def include_command(self, cwd, extra_env):
        with self.lock:
            self.advisory_command = (cwd, dict(extra_env or {}))
            self.epoch += 1
            self.result = None
            self.started = time.monotonic()
        self.wake.set()

    def _loop(self):
        probe = None
        while not self.done.is_set():
            with self.lock:
                epoch = self.epoch
            sample_started = time.monotonic()
            try:
                if probe is None:
                    probe = CapacityProbe(self.store, self.run)
                # Root and the backing volume are independent of the command directory.
                value = probe.sample()
            except Exception as exc:
                value = {'volumes': [], 'errors': ['Storage monitoring failed: ' + str(exc)], 'checked_at': now()}
            completed = time.monotonic()
            value['probe_ms'] = round((completed - sample_started) * 1000, 1)
            host_time = value.get('windows_sampled_at')
            if host_time is not None:
                # A desktop snapshot can span multiple polls. Rereading a
                # pre-resume measurement must not make it a new observation.
                completed -= max(0.0, time.time() - host_time)
                sample_started = min(sample_started, completed)
            with self.lock:
                if epoch == self.epoch:
                    self.result = (value, completed, sample_started)
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
        protected_ids = {row.get('id') for row in value.get('volumes', [])}
        with self.lock:
            value['advisory_volumes'] = [row for row in self.advisory if row['id'] not in protected_ids]
        if time.monotonic() - completed > STALE_SECONDS:
            value['stale'] = True
            value['stale_only'] = not value.get('errors')
            value['errors'] = list(value.get('errors') or []) + ['Required storage checks are stale.']
        return value, sample_started

    def close(self):
        self.done.set()
        self.wake.set()


class Guard(ProcessOwner):
    def __init__(self, store, job, check_cancel, write, *, initial_env=None, control_operation=None):
        self.store, self.job = store, job
        self.check_cancel, self.write = check_cancel, write
        self.control_operation = control_operation
        self.tree, self.watch = None, None
        self.last_publish, self.resume_nonce, self.resume_after = 0, None, None
        self.publish_pending = False
        self.control_suspended = False
        self.recheck_until = None
        run = store.get('runs', job['payload'].get('run_id'), {})
        self.samples = Samples(store, run, initial_env=initial_env)
        self.record = {'id': job['id'], 'run_id': job['payload'].get('run_id'),
            'state': 'monitoring', 'generation': 0, 'pause_kind': None, 'reason': None,
            'volumes': [], 'errors': [], 'checked_at': None, 'can_resume': False,
            'thresholds': dict(THRESHOLDS)}

    def log_storage_event(self, event, generation, **detail):
        try:
            storage_events.append(self.store, self.job['id'], event, generation, **detail)
        except OSError as exc:
            self.record['storage_event_write_error'] = str(exc)

    def _control(self, key, operation, *args, poll=True):
        if self.control_operation is None:
            return operation(*args)
        return self.control_operation(operation, *args, op_key=key, poll=poll)

    def publish(self, force=False, *, poll=True):
        if force or self.publish_pending or time.monotonic() - self.last_publish >= POLL_SECONDS:
            self.record['updated_at'] = now()
            # Store.put returns the recorded value. None means the supervisor
            # deferred a locked write. Keep sampling and retry the newest state.
            started = time.monotonic()
            saved = self._control('disk-status', self.store.put, 'disk_safety', self.job['id'], self.record, poll=poll)
            self.record['write_ms'] = round((time.monotonic() - started) * 1000, 1)
            if not self.publish_pending:
                self.write_started = started
            self.record['write_delay_ms'] = round((time.monotonic() - getattr(self, 'write_started', started)) * 1000, 1)
            self.publish_pending = saved is None
            if self.publish_pending:
                return False
            self.last_publish = time.monotonic()
        return True

    def _consume_resume(self, request):
        # Only the matching request may authorize a thaw. Keep the tree frozen
        # until SQLite confirms that we consumed the reviewed generation/nonce.
        with self.store.connect(timeout=CONTROL_TIMEOUT) as db:
            if self.control_suspended:
                # Prove the pending writer can acquire SQLite again without
                # consuming the user's request while the separate control hold
                # prevents a thaw. The disk guard will apply the same nonce
                # after control recovery and check the full resume threshold.
                db.execute('BEGIN IMMEDIATE')
                return False
            deleted = db.execute("""DELETE FROM items WHERE kind='disk_safety_requests' AND id=?
                                 AND json_extract(body,'$.generation')=? AND json_extract(body,'$.nonce')=?""",
                                 (self.job['id'], request['generation'], request['nonce']))
            return deleted.rowcount == 1

    def checkpoint(self):
        while True:
            self.check_cancel()
            if self.tick():
                return
            time.sleep(.1)

    def before_command(self, cwd=None, extra_env=None):
        self.samples.include_command(cwd, extra_env)
        self.checkpoint()

    def control_recovery_ready(self, after):
        if self.record['state'] == 'paused':
            # Ownership of the freeze returns to the disk guard. Its normal
            # user-requested resume still requires a fresh reading above the reserve.
            return True
        sample = self.samples.current()
        if sample is None or sample[1] <= after:
            return False
        value = sample[0]
        volumes = value.get('volumes') or []
        return bool(volumes) and not value.get('errors') and all(
            type(v.get('available_bytes')) is int and v['available_bytes'] > stop_limit(v) for v in volumes)

    def attach(self, proc):
        self.tree = OwnedTree(proc.pid)
        self.watch = OwnerWatch(self.tree)
        self.tick()

    def tick(self, *, command_running=False):
        if self.tree is not None:
            self.tree.refresh()
            self.watch.publish()
        sample = self.samples.current()
        if sample is None:
            return False
        value, sample_started = sample
        volumes, errors = value.get('volumes') or [], list(value.get('errors') or [])
        valid = bool(volumes) and all(isinstance(v.get('available_bytes'), int) and not isinstance(v['available_bytes'], bool) and v['available_bytes'] >= 0 for v in volumes)
        if not valid:
            errors.append('Required storage capacities are unavailable or invalid.')
        critical = [v for v in volumes if type(v.get('available_bytes')) is int and 0 <= v['available_bytes'] <= stop_limit(v)]
        if critical and not value.get('stale') and _fresh(value.get('checked_at')):
            from . import disk_stop
            from .engine import Cancelled
            self.record.update(state='stopping', reason='workspace_disk_stop', pause_trigger='workspace_disk_stop',
                               volumes=volumes, checked_at=value['checked_at'], can_resume=False, errors=errors)
            reason = disk_stop.trip(self.store, self.job, value, critical)
            self.record['pause_details'] = [reason]
            # Context retains ownership through its native-command cleanup.
            # Never wait for a SQLite write before stopping the tools.
            raise Cancelled(reason)
        ready = _resume_capacity(value)
        old_state = self.record['state']
        self.record['advisory_volumes'] = value.get('advisory_volumes', [])
        self.record.update(probe_ms=value.get('probe_ms'), volumes=volumes, errors=errors, checked_at=value.get('checked_at'))
        workflow_run = self.job['kind'] == 'workflow' and self.job['payload'].get('action') in {'run', 'continue'}
        if not workflow_run and errors:
            # Setup operations use measured capacity for the workspace stop.
            # A workflow-only user-resumable pause cannot be serviced by setup.
            self.record.update(state='unavailable', reason='monitor_unavailable', can_resume=False)
            self.publish(force=old_state != 'unavailable')
            return True
        if (command_running and self.tree is not None and not self.control_suspended
                and old_state in {'monitoring', 'warning', 'rechecking'} and value.get('stale_only')
                and valid and all(v['available_bytes'] > stop_limit(v) for v in volumes)):
            # Use the original measurement time. Even a stream of newer but
            # already-stale snapshots cannot restart the current retry window.
            deadline = sample_started + ACTIVE_RECHECK_MAX_AGE_SECONDS
            if self.recheck_until is not None:
                deadline = min(deadline, self.recheck_until)
            if time.monotonic() < deadline:
                self.recheck_until = deadline
                if old_state != 'rechecking':
                    self.samples.wake.set()
                    self.write('Storage reading delayed. Rechecking capacity while the current command runs.')
                self.record.update(state='rechecking', can_resume=False)
                self.publish(force=old_state != 'rechecking')
                return False  # A pending recheck never grants launch or resume.
        self.recheck_until = None
        if old_state == 'paused' or errors:
            if old_state != 'paused':
                details = list(errors)
                trigger = 'stale' if value.get('stale_only') else 'monitor_unavailable'
                self.log_storage_event('pause_requested', self.record['generation'] + 1,
                                      reason=trigger, details=details, checked_at=value.get('checked_at'),
                                      volumes=volumes)
                self.record.update(pause_trigger=trigger, pause_details=details)
                try:
                    self.write('Storage safety pause requested. ' + ' '.join(details))
                except OSError:
                    pass  # The log volume itself may be full. Still attempt the suspension.
            if self.tree is not None:
                try:
                    self.tree.freeze(timeout=STORAGE_SUSPEND_SECONDS)
                except Exception as exc:
                    # Terminate owned work before attempting failure persistence.
                    self.record.update(state='failed', reason='pause_failed', can_resume=False,
                        errors=errors + [str(exc)])
                    raise
                self.watch.publish()
            if old_state != 'paused':
                self.record['pause_start_mono'] = time.monotonic()
                self.record.update(state='paused', generation=self.record['generation'] + 1,
                    paused_at=now(), pause_kind='processes_suspended' if self.tree else 'before_launch',
                    reason='monitor_unavailable')
                self.log_storage_event('pause_confirmed', self.record['generation'],
                                      reason=trigger, kind=self.record['pause_kind'])
                self.write('Storage safety paused this run.')
            if errors:
                self.record['reason'] = 'monitor_unavailable'
            self.record['can_resume'] = ready
            request = self._control('disk-resume-read', self.store.get, 'disk_safety_requests', self.job['id'], {})
            if request and request.get('generation') == self.record['generation']:
                if request.get('nonce') != self.resume_nonce:
                    self.resume_nonce, self.resume_after = request.get('nonce'), time.monotonic()
                    self.samples.wake.set()
                elif sample_started > self.resume_after and self._control('disk-resume-consume', self._consume_resume, request):
                    if ready:
                        self.check_cancel()
                        # Reading cancellation after a lock can outlast the
                        # previous capacity sample. Never thaw on stale data.
                        latest = self.samples.current()
                        ready = bool(latest and latest[1] > self.resume_after and _resume_capacity(latest[0]))
                    if ready:
                        if self.tree is not None:
                            self.tree.resume()
                        self.log_storage_event('resume_confirmed', self.record['generation'],
                                              request_id=request['nonce'])
                        since = self.record.pop('pause_start_mono', time.monotonic())
                        self.record['paused_total_s'] = self.record.get('paused_total_s', 0) + max(0, time.monotonic() - since)
                        self.record.update(state='monitoring', can_resume=False, pause_kind=None, reason=None, resumed_at=now())
                        self.write('Storage checks passed on every required volume; resumed by user request.')
                    else:
                        self.record['can_resume'] = False
                        self.log_storage_event('resume_rejected', self.record['generation'],
                                              request_id=request['nonce'], reason='storage recheck failed')
                        self.write('Resume was not applied: fresh storage checks did not meet the recovery requirements for each drive.')
            saved = self.publish(force=old_state != self.record['state'])
            return saved and self.record['state'] != 'paused'
        self.record.update(state='monitoring', can_resume=False)
        if old_state == 'rechecking':
            self.write('Fresh storage readings received. The current command continued without a pause.')
        return self.publish(force=old_state != self.record['state'])

    def stop(self):
        # Signals precede writes, which may fail on a full disk.
        if self.tree is not None:
            self.tree.stop()
        self.record.update(state='stopping', can_resume=False)
        self.publish(force=True)

    def signal(self, sig):
        if self.tree is not None:
            self.tree.signal(sig)

    def finish(self, result):
        self.samples.close()
        try:
            self.detach(failed=True)
        finally:
            pause_failed = self.record.get('reason') == 'pause_failed'
            if pause_failed and result == 'running':
                # The worker retains ownership until cleanup is verified. The
                # failed disk guard has nevertheless interrupted this attempt.
                result = 'interrupted'
            self.record.update(state='failed' if pause_failed or result == 'failed' else 'finished',
                               result=result, can_resume=False)
            self.publish(force=True, poll=False)
