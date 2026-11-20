"""Local desktop supervisor protocol. Never terminate a WSL distribution.

The Windows owner controls a dedicated browser profile. Jobs carry the session
identity in SQLite; shared workers use the existing cooperative cancellation.
"""
from contextlib import contextmanager
import errno
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from .store import CONTROL_BACKOFF, CONTROL_TIMEOUT, transient_lock
from .supervision import MonitoringInterrupted


ENV = 'BACTOLOGIX_DESKTOP_SESSION'
STORAGE_SESSION_DIR = 'BACTOLOGIX_WINDOWS_STORAGE_SESSION_DIR'
LEASE_SECONDS = 30
HOST_CLOCK_TOLERANCE = 2.0


def atomic(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temporary, path)


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def owner_cancelled(store, jid):
    state = store.control_state(jid)
    return bool(state['owner']) and session_value_stopped(state['session'], store=store, owner=state['owner'])


def session_stopped(store, owner):
    if not owner:
        return False
    def read(db):
        row = db.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (owner,)).fetchone()
        return json.loads(row[0]) if row else None
    session = store.control_read(read)
    return session_value_stopped(session, store=store, owner=owner)


def _timestamp(value):
    return type(value) in (float, int) and math.isfinite(value)


def _lease_path(store, owner):
    if not re.fullmatch(r'[a-f0-9]{32}', owner or ''):
        raise MonitoringInterrupted('Desktop session identity is invalid.')
    return store.home / 'logs' / ('desktop-' + owner + '.lease.json')


def recorded_session_stop(session):
    if not session or not (session.get('cancelled') is True or session.get('phase') in {'stopping', 'stopped'}):
        return None
    return _desktop_stop_text(session.get('reason'))


def _desktop_stop_text(reason):
    if isinstance(reason, str) and reason.strip():
        return 'The owning desktop session requested a stop (' + ' '.join(reason.split())[:300] + ').'
    return 'The owning desktop session signalled a stop.'


def _owner_record(path, owner):
    try:
        record = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise MonitoringInterrupted('Desktop control recovery record is unreadable.') from exc
    if not isinstance(record, dict) or (record and record.get('session') != owner):
        raise MonitoringInterrupted('Desktop control recovery record is invalid.')
    return record


def _pulse_covers(pulse, recorded):
    # The observer starts at a valid local lease. Once a beat is missed for a
    # full lease it exits permanently; a later single pulse cannot fill a gap.
    anchor, beat, observed = (pulse.get(k) for k in ('anchor', 'heartbeat', 'monotonic'))
    return (pulse.get('stopping') is not True and all(_timestamp(t) for t in (anchor, recorded, beat, observed))
            and anchor <= recorded and 0 <= time.time() - beat < LEASE_SECONDS
            and 0 <= time.monotonic() - observed < LEASE_SECONDS)


def session_stop_reason(session, *, store=None, owner=None):
    if session is not None and (not isinstance(session, dict) or not _timestamp(session.get('last_seen', 0))):
        raise MonitoringInterrupted('Desktop session control metadata is invalid.')
    if not session:
        return 'The owning desktop session record was missing.'
    recorded_stop = recorded_session_stop(session)
    if recorded_stop:
        return recorded_stop
    recorded = session.get('last_seen', 0)
    proof, pulse = {}, {}
    if store is not None and owner is not None:
        # Only the owning launcher publishes a live proof during contention or
        # a stop on close. Jobs still check their own durable cancellation flag.
        path = _lease_path(store, owner)
        proof = _owner_record(path, owner)
        if proof.get('stopping') is True:
            return _desktop_stop_text(proof.get('reason'))
        pulse = _owner_record(path.with_suffix('.pulse.json'), owner)
        if pulse.get('stopping') is True:
            return _desktop_stop_text(pulse.get('reason'))
    current = time.time()
    if current - recorded <= LEASE_SECONDS:
        return None
    heartbeat = proof.get('heartbeat')
    if (proof.get('recorded') == recorded and _timestamp(heartbeat)
            and 0 <= current - heartbeat < LEASE_SECONDS):
        return None
    if _pulse_covers(pulse, recorded):
        return None
    return 'The owning desktop session heartbeat expired.'


def session_value_stopped(session, *, store=None, owner=None):
    return session_stop_reason(session, store=store, owner=owner) is not None


def request_browser(store):
    key = os.environ.get(ENV)
    if not key:
        return False
    state = store.get('desktop_sessions', key)
    if not state or state.get('phase') != 'ready':
        raise ValueError('The desktop session is stopping or unavailable.')
    store.put('desktop_browser', key, uuid.uuid4().hex)
    return True


class Session:
    def __init__(self, store, directory, key):
        if not re.fullmatch(r'[a-f0-9]{32}', key or ''):
            raise ValueError('Invalid desktop session identity')
        self.store, self.key, self.root = store, key, Path(directory).resolve()
        if not self.root.is_dir():
            raise ValueError('Desktop control directory is missing')
        self.started = time.monotonic()
        self.last_host_heartbeat = None
        self.heartbeat_observed = None
        self.heartbeat_wall = None
        self.disconnect_detail = ''
        self.nonce = None
        self.phase = 'starting'
        self.reason = ''
        self.pending_stop_reason = None
        self.last_committed = None
        self.proven_until = 0
        self.proof_path = _lease_path(store, key)
        self.pulse_path = self.proof_path.with_suffix('.pulse.json')
        self.stop_recorded = False
        self.status_blocked_since = None
        self.publish('')

    def jobs(self):
        def query(db):
            rows = db.execute("SELECT j.* FROM jobs j JOIN items i ON i.kind='job_owners' AND i.id=j.id "
                              "WHERE i.body=? AND j.status IN ('queued','running')", (json.dumps(self.key),)).fetchall()
            return [dict(row) for row in rows]
        return self.store.control_read(query)

    def gate(self, phase, *, heartbeat=False):
        # This session-specific transaction is idempotent. Retry only after a
        # failed transaction has rolled back; never republish a cached lease row.
        for attempt in range(len(CONTROL_BACKOFF) + 1):
            try:
                with self.store.connect(timeout=CONTROL_TIMEOUT) as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (self.key,)).fetchone()
                    previous = json.loads(row[0]) if row else {}
                    if not isinstance(previous, dict):
                        raise ValueError('Desktop session control metadata is invalid.')
                    terminal = previous.get('cancelled') is True or previous.get('phase') in {'stopping', 'stopped'}
                    expired = bool(row) and session_value_stopped(previous, store=self.store, owner=self.key)
                    selected = previous.get('phase', phase) if heartbeat else phase
                    reason = self.pending_stop_reason or previous.get('reason') or self.reason
                    if terminal:
                        selected = 'stopped' if previous.get('phase') == 'stopped' or phase == 'stopped' else 'stopping'
                        reason = previous.get('reason') or reason
                    elif self.pending_stop_reason or expired:
                        selected = 'stopping'
                        reason = reason or 'Desktop session lease expired; stopping owned work'
                    cancelled = terminal or selected in {'stopping', 'stopped'}
                    value = dict(previous, phase=selected, last_seen=time.time(), reason=reason,
                                 cancelled=cancelled)
                    db.execute("INSERT INTO items VALUES ('desktop_sessions',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                               (self.key, json.dumps(value)))
                    if cancelled:
                        # Gate new submissions and stop this session's work at the
                        # same write boundary. A running app update must finish.
                        db.execute("UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END "
                                   "WHERE status IN ('queued','running') AND NOT(kind='app_update' AND status='running') "
                                   "AND id IN (SELECT id FROM items WHERE kind='job_owners' AND body=?)", (json.dumps(self.key),))
                self.last_committed = value['last_seen']
                self.proven_until = self.last_committed + LEASE_SECONDS
                self.phase, self.reason = value['phase'], value['reason']
                if cancelled:
                    self.pending_stop_reason = self.reason
                return
            except sqlite3.Error as exc:
                if not transient_lock(exc) or attempt >= len(CONTROL_BACKOFF):
                    raise
                time.sleep(CONTROL_BACKOFF[attempt])

    def record_stop(self):
        if not self.stop_recorded:
            try:
                atomic(self.proof_path, {'session': self.key, 'stopping': True, 'reason': self.pending_stop_reason})
            except OSError:
                return  # Still try the durable stop. Any older proof expires.
            self.stop_recorded = True

    def stop(self, reason='Window closed'):
        # Same SQLite write boundary used by submit: after gating, a racing UI
        # cannot sneak in another job between cancellation and server shutdown.
        self.pending_stop_reason = self.pending_stop_reason or self.reason or reason
        self.record_stop()
        self.gate('stopping')

    def write_status(self, value):
        # A Windows scanner can hold the previous file open across os.replace.
        # Live polls can publish the newest snapshot on the next tick. At exit
        # there is no next tick, so allow a short final retry before reporting it.
        final = value['phase'] in {'stopped', 'error'}
        final_deadline = time.monotonic() + 2.0 if final else None
        while True:
            try:
                atomic(self.root / 'status.json', value)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EPERM, errno.EBUSY}:
                    raise
                current = time.monotonic()
                if self.status_blocked_since is None:
                    self.status_blocked_since = current
                if current - self.status_blocked_since >= LEASE_SECONDS:
                    raise
                if not final:
                    return
                if current >= final_deadline:
                    raise
                time.sleep(min(.1, final_deadline - current))
            else:
                self.status_blocked_since = None
                return

    def publish(self, url):
        self.gate(self.phase, heartbeat=True)
        jobs = self.jobs()
        self.write_status({'session': self.key, 'phase': self.phase, 'url': url,
            'jobs': len(jobs), 'reason': self.reason,
            'installing_update': any(j['kind'] == 'app_update' and j['status'] == 'running' for j in jobs),
            'browser_request': self.store.get('desktop_browser', self.key)})

    @contextmanager
    def watch_heartbeats(self):
        """Keep observing the owning Windows process during blocking launcher calls."""
        done = threading.Event()
        thread = threading.Thread(target=self._watch_heartbeats,
                                  args=(done, self.last_committed), name='desktop-heartbeat', daemon=True)
        thread.start()
        try:
            yield
        finally:
            done.set()
            # A disconnected Windows mount can stall a read. Such a reader is
            # a daemon and checks done again before publishing any observation.
            thread.join(timeout=1)
            if not thread.is_alive():
                try:
                    if self.phase == 'stopped' or not _owner_record(self.pulse_path, self.key).get('stopping'):
                        self.pulse_path.unlink(missing_ok=True)
                except (OSError, MonitoringInterrupted):
                    # A leftover observation expires; never mask a launcher
                    # failure or discard an unreadable potential stop record.
                    pass

    def _watch_heartbeats(self, done, anchor):
        observed = self.started if self.heartbeat_observed is None else self.heartbeat_observed
        observed_wall = anchor if self.heartbeat_wall is None else self.heartbeat_wall
        previous, published = self.last_host_heartbeat, None
        while not done.is_set():
            request = read(self.root / 'request.json')
            closing = (isinstance(request, dict) and request.get('session') == self.key
                       and request.get('action') == 'confirm_close')
            beat = {} if closing else read(self.root / 'heartbeat.json')
            mono, wall = time.monotonic(), time.time()
            if done.is_set():
                return
            if closing:
                try:
                    atomic(self.pulse_path, {'session': self.key, 'stopping': True, 'reason': 'Window closed'})
                except OSError:
                    pass  # Existing proofs expire; never renew past a close.
                return
            if (mono - observed >= LEASE_SECONDS
                    or not -HOST_CLOCK_TOLERANCE <= wall - observed_wall < LEASE_SECONDS):
                return
            if isinstance(beat, dict) and beat.get('session') == self.key:
                stamp = beat.get('time')
                if (_timestamp(stamp) and (previous is None or stamp > previous)
                        and -HOST_CLOCK_TOLERANCE <= wall - stamp < LEASE_SECONDS):
                    previous = stamp
                    observed = mono - max(0, wall - stamp)
                    observed_wall = min(stamp, wall)
                    if published is None or mono - published >= min(5., LEASE_SECONDS / 4):
                        try:
                            atomic(self.pulse_path, {'session': self.key, 'anchor': anchor,
                                   'heartbeat': observed_wall, 'monotonic': observed})
                        except OSError:
                            pass  # The main loop still owns its normal lease.
                        else:
                            published = mono
            done.wait(min(1., LEASE_SECONDS / 6))

    def observe_heartbeat(self):
        """Only an advancing host pulse renews the local lease."""
        heartbeat = read(self.root / 'heartbeat.json')
        monotonic_now, wall_now = time.monotonic(), time.time()
        observed = self.started if self.heartbeat_observed is None else self.heartbeat_observed
        if monotonic_now - observed >= LEASE_SECONDS:
            pulse = _owner_record(self.pulse_path, self.key)
            if not _pulse_covers(pulse, self.last_committed):
                stamp = heartbeat.get('time') if isinstance(heartbeat, dict) and heartbeat.get('session') == self.key else None
                host_age = f'{wall_now - stamp:.1f}s' if _timestamp(stamp) else 'unavailable'
                last = 'last accepted pulse' if self.heartbeat_observed is not None else 'startup'
                self.disconnect_detail = (f'At disconnect, {monotonic_now - observed:.1f}s had passed since {last}. '
                                          f'Latest matching Windows heartbeat age was {host_age}.')
                return False
            self.heartbeat_observed = pulse['monotonic']
            self.heartbeat_wall = pulse['heartbeat']
            self.last_host_heartbeat = max(self.last_host_heartbeat or 0, self.heartbeat_wall)
        if isinstance(heartbeat, dict) and heartbeat.get('session') == self.key:
            seen = heartbeat.get('time')
            if _timestamp(seen) and (self.last_host_heartbeat is None or seen > self.last_host_heartbeat):
                age = wall_now - seen
                if -HOST_CLOCK_TOLERANCE <= age < LEASE_SECONDS:
                    self.last_host_heartbeat = seen
                    self.heartbeat_observed = monotonic_now - max(age, 0)
                    self.heartbeat_wall = min(seen, wall_now)
        return True

    def tick(self, url, ready):
        alive = self.observe_heartbeat()
        request = read(self.root / 'request.json')
        if not isinstance(request, dict):
            request = {}
        if not alive:
            closing = (request.get('session') == self.key and request.get('action') == 'confirm_close'
                       and request.get('nonce') != self.nonce)
            if not closing and not self.pending_stop_reason and self.disconnect_detail:
                try:
                    print(self.disconnect_detail, flush=True)
                except OSError:
                    pass  # A closed launcher log must not prevent cancellation.
            self.stop('Window closed' if closing else 'Desktop supervisor disconnected; stopping owned work')
        if request.get('session') == self.key and request.get('nonce') != self.nonce:
            action = request.get('action')
            if action == 'close' and self.phase != 'stopping':
                self.gate('confirming')
                if not self.jobs():
                    self.stop()
            elif action == 'confirm_close':
                self.stop()
            elif action == 'reopen' and self.phase == 'confirming':
                self.gate('ready' if ready else 'starting')
            self.nonce = request.get('nonce')
        if self.phase == 'starting' and ready:
            self.gate('ready')
        if self.phase == 'stopping':
            self.stop(self.reason)
        self.publish(url)
        return self.phase in {'stopping', 'stopped'} and not self.jobs()

    def record_contended_lease(self):
        """Bridge an observed, continuous Windows owner across a failed SQLite poll."""
        request = read(self.root / 'request.json')
        if isinstance(request, dict) and request.get('session') == self.key and request.get('action') == 'confirm_close':
            self.pending_stop_reason = self.pending_stop_reason or 'Window closed'
        if self.pending_stop_reason or self.phase in {'stopping', 'stopped'}:
            self.record_stop()
            return False
        current = time.time()
        if self.last_committed is None or not _timestamp(current):
            return False
        if current >= self.proven_until:
            return _pulse_covers(_owner_record(self.pulse_path, self.key), self.last_committed)
        if not self.observe_heartbeat() or self.heartbeat_observed is None or time.time() >= self.proven_until:
            return False
        # Publish the pulse's local age. A small Windows clock offset cannot
        # future-date the proof. Rereading the same pulse cannot renew it.
        seen = self.heartbeat_wall
        try:
            atomic(self.proof_path, {'session': self.key, 'recorded': self.last_committed, 'heartbeat': seen})
        except OSError:
            return False
        self.proven_until = seen + LEASE_SECONDS
        return True

    def poll(self, url, ready):
        """Keep the Windows session alive across brief control-database contention."""
        deadline = time.monotonic() + LEASE_SECONDS
        closing = False
        while True:
            try:
                return self.tick(url, ready)
            except (sqlite3.Error, MonitoringInterrupted) as exc:
                # Retain the launcher only while we can independently observe
                # the matching Windows owner or the initial retry window lasts.
                cause = exc.__cause__ if isinstance(exc, MonitoringInterrupted) else exc
                if not isinstance(cause, sqlite3.Error) or not transient_lock(cause):
                    raise
                ongoing = self.record_contended_lease()
                if self.pending_stop_reason and not closing:
                    # A close received during a long hold gets its own bounded
                    # polling window to commit the stop already signalled locally.
                    deadline = time.monotonic() + LEASE_SECONDS
                    closing = True
                remaining = deadline - time.monotonic()
                if remaining <= 0 and not ongoing:
                    raise
                time.sleep(.3 if ongoing else min(.3, remaining))

    def finish(self, url):
        if self.jobs():
            raise RuntimeError('Desktop shutdown cannot be confirmed while owned jobs remain active.')
        self.gate('stopped')
        self.publish(url)
        self.proof_path.unlink(missing_ok=True)

    def control_failure(self, url, error):
        # Do not claim zero jobs, a refreshed lease, or completed shutdown when
        # SQLite could not confirm it. This file does not control native workers.
        self.write_status({'session': self.key, 'phase': 'error', 'url': url,
            'jobs': None, 'shutdown_confirmed': False, 'control_error': str(error),
            'reason': self.pending_stop_reason or self.reason or 'Desktop control unavailable'})
