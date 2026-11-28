"""Persistent per-attempt storage control events beside the native job log."""
from datetime import datetime, timezone
import fcntl
import json
import os
import time


def append(store, jid, event, generation, **detail):
    path = store.log_path(jid).with_suffix('.storage-events.jsonl')
    row = dict(event=event, generation=generation, job_id=jid,
               at=datetime.now(timezone.utc).isoformat(), monotonic_s=time.monotonic(), **detail)
    with path.open('a', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(row, separators=(',', ':')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def read(store, jid):
    path = store.log_path(jid).with_suffix('.storage-events.jsonl')
    try:
        stream = path.open(encoding='utf-8')
    except FileNotFoundError:
        return []
    events = []
    with stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        for sequence, line in enumerate(stream, 1):
            try:
                events.append(dict(json.loads(line), sequence=sequence))
            except ValueError:
                events.append(dict(sequence=sequence, event='incomplete_record'))
    return events


def timeline(store, jid):
    events = read(store, jid)
    pauses, clicks = {}, {}
    last_resume = None
    for row in events:
        generation = row.get('generation')
        event = row['event']
        stamp = row.get('monotonic_s')
        if event == 'pause_confirmed':
            pauses[generation] = stamp
            if last_resume is not None:
                row['running_since_resume_s'] = round(stamp - last_resume, 3)
        elif event == 'resume_received':
            clicks[row['request_id']] = stamp
            if generation in pauses:
                row['paused_until_request_s'] = round(stamp - pauses[generation], 3)
        elif event == 'resume_confirmed':
            last_resume = stamp
            if row.get('request_id') in clicks:
                row['request_to_resume_s'] = round(stamp - clicks[row['request_id']], 3)
    return events
