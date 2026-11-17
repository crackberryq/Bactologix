"""Local desktop supervisor protocol. Never terminate a WSL distribution.

The Windows owner controls a dedicated browser profile. Jobs carry the session
identity in SQLite; shared workers use the existing cooperative cancellation.
"""
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from .store import CONTROL_BACKOFF, CONTROL_TIMEOUT, transient_lock


ENV = 'BACTOLOGIX_DESKTOP_SESSION'
LEASE_SECONDS = 30


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
    return bool(state['owner']) and session_value_stopped(state['session'])


def session_stopped(store, owner):
    if not owner:
        return False
    def read(db):
        row = db.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (owner,)).fetchone()
        return json.loads(row[0]) if row else None
    session = store.control_read(read)
    return session_value_stopped(session)


def session_value_stopped(session):
    if session is not None and (not isinstance(session, dict) or
            not isinstance(session.get('last_seen', 0), (int, float)) or
            isinstance(session.get('last_seen', 0), bool) or
            not math.isfinite(session.get('last_seen', 0))):
        from .supervision import MonitoringInterrupted
        raise MonitoringInterrupted('Desktop session control metadata is invalid.')
    return (not session or session.get('cancelled') is True or session.get('phase') in {'stopping', 'stopped'}
            or time.time() - session.get('last_seen', 0) > LEASE_SECONDS)


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
        self.started = time.time()
        self.nonce = None
        self.phase = 'starting'
        self.reason = ''
        self.pending_stop_reason = None
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
                    if row:
                        session_value_stopped(previous)  # Validate timestamp shape before any renewal.
                    terminal = previous.get('cancelled') is True or previous.get('phase') in {'stopping', 'stopped'}
                    expired = bool(row) and time.time() - previous.get('last_seen', 0) > LEASE_SECONDS
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
                self.phase, self.reason = value['phase'], value['reason']
                if cancelled:
                    self.pending_stop_reason = self.reason
                return
            except sqlite3.Error as exc:
                if not transient_lock(exc) or attempt >= len(CONTROL_BACKOFF):
                    raise
                time.sleep(CONTROL_BACKOFF[attempt])

    def stop(self, reason='Window closed'):
        # Same SQLite write boundary used by submit: after gating, a racing UI
        # cannot sneak in another job between cancellation and server shutdown.
        self.pending_stop_reason = self.pending_stop_reason or self.reason or reason
        self.gate('stopping')

    def publish(self, url):
        self.gate(self.phase, heartbeat=True)
        jobs = self.jobs()
        atomic(self.root / 'status.json', {'session': self.key, 'phase': self.phase, 'url': url,
            'jobs': len(jobs), 'reason': self.reason,
            'installing_update': any(j['kind'] == 'app_update' and j['status'] == 'running' for j in jobs),
            'browser_request': self.store.get('desktop_browser', self.key)})

    def tick(self, url, ready):
        heartbeat = read(self.root / 'heartbeat.json')
        alive = heartbeat.get('session') == self.key and time.time() - heartbeat.get('time', 0) < LEASE_SECONDS
        if not alive and time.time() - self.started > LEASE_SECONDS:
            self.stop('Desktop supervisor disconnected; stopping owned work')
        request = read(self.root / 'request.json')
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

    def finish(self, url):
        if self.jobs():
            raise RuntimeError('Desktop shutdown cannot be confirmed while owned jobs remain active.')
        self.gate('stopped')
        self.publish(url)

    def control_failure(self, url, error):
        # Do not claim zero jobs, a refreshed lease, or completed shutdown when
        # SQLite could not confirm it. This file does not control native workers.
        atomic(self.root / 'status.json', {'session': self.key, 'phase': 'error', 'url': url,
            'jobs': None, 'shutdown_confirmed': False, 'control_error': str(error),
            'reason': self.pending_stop_reason or self.reason or 'Desktop control unavailable'})
