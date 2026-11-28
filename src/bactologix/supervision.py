"""Control-plane diagnostics independent of the SQLite file being monitored."""
from __future__ import annotations
import json
import math
import os
import time
import uuid


class MonitoringInterrupted(RuntimeError):
    """The supervisor could not establish an execution result."""
    def __init__(self, message, *, cleanup_confirmed=True):
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed


def path(store, jid, kind='monitor'):
    return store.log_path(jid).with_suffix('.' + kind + '.json')


def save(store, jid, value, kind='monitor'):
    target = path(store, jid, kind)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot(store, jid, kind='monitor'):
    try:
        value = json.loads(path(store, jid, kind).read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Invalid control record shape')
        if kind == 'process':
            if type(value.get('cleanup_confirmed')) is not bool or not isinstance(value.get('identities'), list):
                raise ValueError('Incomplete native ownership record')
            if 'ownership_complete' in value and type(value['ownership_complete']) is not bool:
                raise ValueError('Invalid native ownership inspection result')
            if value['cleanup_confirmed'] and value.get('ownership_complete') is False:
                raise ValueError('Incomplete ownership cannot establish cleanup')
            if not value['cleanup_confirmed'] and not value['identities']:
                raise ValueError('Unconfirmed ownership record has no native identities')
            for identity in value['identities']:
                if (not isinstance(identity, dict) or type(identity.get('pid')) is not int or identity['pid'] <= 0 or
                        type(identity.get('created')) not in {float, int} or identity['created'] <= 0 or
                        not math.isfinite(identity['created'])):
                    raise ValueError('Invalid native process identity')
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        if kind != 'monitor':
            raise
        return {'id': jid, 'state': 'unavailable', 'cleanup_confirmed': False,
                'detail': 'Monitoring diagnostics are unavailable; native execution state is not established.'}


def remaining(identities):
    """Missing/reused PIDs are gone; access failure is not proof of exit."""
    import psutil
    live = []
    for identity in identities:
        try:
            process = psutil.Process(identity['pid'])
            if process.create_time() == identity['created'] and process.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
                live.append(identity)
        except psutil.NoSuchProcess:
            pass
    return live


def verify_exit(identities, timeout=3):
    deadline = time.monotonic() + timeout
    while True:
        try:
            if not remaining(identities):
                return True
        except Exception:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(.05)


def assert_recoverable(store, ids):
    """The OS lock plus recorded native identities must both permit recovery."""
    for jid in ids:
        try:
            record = snapshot(store, jid, 'process')
            if record and record.get('cleanup_confirmed') is not True:
                if record.get('ownership_complete') is False or not record.get('identities') or remaining(record['identities']):
                    raise MonitoringInterrupted('Native work is still present or its cleanup is unconfirmed; monitoring remains interrupted.', cleanup_confirmed=False)
        except MonitoringInterrupted:
            raise
        except Exception as exc:
            raise MonitoringInterrupted('Cannot verify recorded native process identities; recovery remains blocked.', cleanup_confirmed=False) from exc
