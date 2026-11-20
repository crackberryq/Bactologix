"""Recheck the identity of an existing desktop after WSL misses a lease."""
import json
import math
from pathlib import Path
import threading
import time

import psutil


def _stamp(value):
    return type(value) in (float, int) and math.isfinite(value)


def _request(directory, key):
    try:
        value = json.loads((directory / 'request.json').read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or value.get('session') != key:
        raise ValueError('Invalid desktop request')
    action = value.get('action')
    if action not in {'close', 'reopen', 'confirm_close'} or not isinstance(value.get('nonce'), str) or not value['nonce']:
        raise ValueError('Invalid desktop request')
    return action


def _read(directory, name):
    with (directory / name).open(encoding='utf-8-sig') as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def confirm(identity, key, after, now, lease):
    """Return a current host pulse, 'close', or None. Never called under SQLite.

    Windows creates a distinct ID and control directory for each launcher.
    The owning Linux supervisor must also still have its original PID and
    creation time. A current native storage witness checks the Windows
    launcher process handle before publication. Older installations can
    demonstrate liveness with a second advancing pulse from the same owner.
    """
    if not isinstance(identity, dict) or not _stamp(after):
        return None
    if not isinstance(identity.get('directory'), str):
        return None
    directory = Path(identity['directory'])
    pid, created = identity.get('pid'), identity.get('created')
    if not directory.is_absolute() or directory.name != key or type(pid) is not int or not _stamp(created):
        return None

    def supervisor_alive():
        try:
            process = psutil.Process(pid)
            return process.create_time() == created and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.Error, OSError):
            return False

    if not supervisor_alive():
        return None
    limit = min(3., lease)
    deadline = time.monotonic() + limit
    finished = threading.Event()
    result = []

    def fresh(record, max_age):
        stamp = record.get('time')
        return (record.get('session') == key and _stamp(stamp) and stamp > after
                and -2 <= now() - stamp < min(max_age, lease))

    def check():
        try:
            first = None
            while time.monotonic() < deadline:
                if _request(directory, key) in {'close', 'confirm_close'}:
                    result.append('close')
                    return
                beat = _read(directory, 'heartbeat.json')
                if not fresh(beat, 5):
                    return
                stamp = beat['time']
                if first is None:
                    first = stamp
                    try:
                        witness = _read(directory, 'windows-storage.json')
                    except (OSError, ValueError):
                        witness = {}
                    distribution = identity.get('distribution')
                    live = (type(witness.get('schema')) is int and witness['schema'] == 1 and fresh(witness, 6)
                            and (not distribution or witness.get('distribution') == distribution))
                else:
                    live = stamp > first
                if live:
                    if _request(directory, key) in {'close', 'confirm_close'}:
                        result.append('close')
                    elif fresh(beat, 5):
                        result.append(stamp)
                    return
                time.sleep(.1)
        except (OSError, ValueError):
            return
        finally:
            finished.set()

    # A Windows-mounted filesystem may itself stall. No late result from a
    # timed-out read can publish evidence or change session state.
    threading.Thread(target=check, name='desktop-reconnect', daemon=True).start()
    if not finished.wait(limit) or not result or not supervisor_alive():
        return None
    evidence = result[0]
    if evidence == 'close' or -2 <= now() - evidence < min(5., lease):
        return evidence
    return None
