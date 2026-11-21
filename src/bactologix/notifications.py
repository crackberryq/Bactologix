"""Durable local alerts and atomic desktop-delivery attempts.

This module does not contact a notification service, launch jobs, or install
updates. Desktop permission and rendering belong to the current browser.
"""
from __future__ import annotations

import hashlib
import json

from .catalog import PIPELINES
from .store import now
from . import update_checks

MAX_ITEMS = 200
CATEGORIES = ('jobs', 'review', 'updates')
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


def _get(db, kind, key, fallback=None):
    row = db.execute('SELECT body FROM items WHERE kind=? AND id=?', (kind, key)).fetchone()
    return json.loads(row[0]) if row else fallback


def _put(db, kind, key, value):
    db.execute('INSERT INTO items VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
               (kind, key, json.dumps(value)))


def _identity(value):
    return 'notice-' + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:32]


def settings(store):
    value = store.get('notification_state', 'preferences', {})
    return {key: value.get(key, True) is True for key in CATEGORIES} | {
        'auto_update_checks': update_checks.panel(store)['auto_check']}


def configure(store, payload):
    if not isinstance(payload, dict) or set(payload) != {*CATEGORIES, 'auto_update_checks'}:
        raise ValueError('Provide all notification preferences.')
    if any(type(value) is not bool for value in payload.values()):
        raise ValueError('Notification preferences must be on or off.')
    update_checks.configure(store, payload['auto_update_checks'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _put(db, 'notification_state', 'preferences', {key: payload[key] for key in CATEGORIES})
        for record in db.execute("SELECT id,body FROM items WHERE kind='notifications'").fetchall():
            row = json.loads(record['body'])
            if not payload.get(row.get('category'), False) and not row['read']:
                row['read'] = True
                _put(db, 'notifications', record['id'], row)
    return {'message': 'Notification preferences saved.', 'keep_modal': True}


def _job_event(job, runs):
    payload = job.get('payload') or {}
    rid = payload.get('run_id')
    run = runs.get(rid, {})
    tool = run.get('tool') or payload.get('tool')
    pipeline = PIPELINES.get(tool, {}).get('name', 'Bactologix')
    result = job['status']
    kind = job.get('kind')
    if kind == 'workflow':
        action = payload.get('action')
        label = {'dry-run': 'Validation', 'prepare': 'Tool preparation',
                 'check-environments': 'Environment check', 'run': 'Run', 'continue': 'Run'}.get(action, 'Workflow job')
        # The separate review alert is more informative than a finished-job
        # alert when the workflow intentionally pauses for a decision.
        awaiting = run.get('status') == 'awaiting_review' or (run.get('identification') or {}).get('state') == 'awaiting_review'
        if result == 'succeeded' and awaiting and action in {'run', 'continue'}:
            return None
    else:
        label = {'install': 'Pipeline installation', 'app_update': 'Application update',
                 'isolate_export': 'Isolate export', 'storage_cleanup': 'Storage cleanup',
                 'database_download': 'Database preparation', 'report_refresh': 'Report refresh',
                 'database_build': 'Database build', 'environments': 'Environment maintenance'}.get(kind, 'Job')
    outcome = {'succeeded': 'finished', 'failed': 'failed', 'cancelled': 'cancelled', 'interrupted': 'interrupted'}[result]
    body = (pipeline + '. ' if kind == 'workflow' else '') + (
        'Open Bactologix to review the recorded outcome.' if result == 'succeeded'
        else 'Open Bactologix to review the job log and next steps.')
    return {'category': 'jobs', 'title': label + ' ' + outcome, 'body': body,
            'target': {'kind': 'job', 'id': job['id']}}


def _event_candidates(jobs, runs, pauses, updates):
    indexed = {row['id']: row for row in runs}
    events = []
    for job in sorted(jobs, key=lambda row: (row.get('finished') or row.get('created') or '', row['id'])):
        if job.get('status') not in TERMINAL:
            continue
        event = _job_event(job, indexed)
        identity = ['job', job['id'], job['status'], job.get('finished')]
        events.append((_identity(identity), event, True))
    for job, safety in pauses:
        if not safety or safety.get('state') != 'paused':
            continue
        waiting = safety.get('pause_kind') == 'before_launch'
        unavailable = safety.get('reason') == 'monitor_unavailable'
        title = ('Waiting for storage check' if waiting and unavailable else
                 'Waiting for storage' if waiting else
                 'Paused — storage check unavailable' if unavailable else
                 'Paused — low disk space')
        body = ('No tool process has started for this command. ' if waiting else
                'Tool processes are suspended and still hold memory. ')
        body += ('Bactologix is retrying the storage check. Open the run to review the latest readings.' if unavailable else
                 'A monitored drive is below the storage safety threshold. Open the run to review drives and free space.')
        body += ' Choose Resume run once all drives pass.'
        event = {'category': 'jobs', 'event_type': 'disk_pause', 'title': title,
                 'body': body, 'target': {'kind': 'job', 'id': job['id']}}
        events.append((_identity(['disk_pause', job['id'], safety.get('generation')]), event, False))
    for run in runs:
        review = run.get('identification') or {}
        awaiting = (run.get('status') == 'awaiting_review' if 'status' in run
                    else review.get('state') == 'awaiting_review')
        if not awaiting or run.get('archived'):
            continue
        event = {'category': 'review', 'title': 'Run waiting for review',
                 'body': 'Open Bactologix to review identification and choose how to continue.',
                 'target': {'kind': 'run', 'id': run['id']}}
        attempts = [job for job in jobs if job.get('kind') == 'workflow'
                    and job.get('payload', {}).get('run_id') == run['id']
                    and job.get('payload', {}).get('action') in {'run', 'continue'}]
        attempt = max(attempts, key=lambda job: (job.get('created') or '', job['id']), default={}).get('id')
        events.append((_identity(['review', run['id'], review.get('evidence_sha256'), attempt]), event, False))
    for event in updates:
        if event.get('category') == 'updates':
            notice = {k: event[k] for k in ('category', 'title', 'body', 'target')}
            events.append((_identity(['update', event['id']]), notice, False))
    return events


def _panel_changes(db, events, prefs, timestamp):
    current = _get(db, 'notification_state', 'preferences', {})
    prefs.update({key: current.get(key, True) is True for key in CATEGORIES})
    first_visit = not _get(db, 'notification_state', 'initialized', False)
    notices = {key: json.loads(body) for key, body in db.execute(
        "SELECT id,body FROM items WHERE kind='notifications' ORDER BY rowid")}
    writes = {}
    live = set()
    for key, event, job_outcome in events:
        if not job_outcome:
            live.add(key)
        if event and key in notices:
            row = notices[key]
            if not row.get('active', True):
                row['active'] = True
                writes['notifications', key] = row
            continue
        # Dismissed, retention-pruned, and suppressed outcomes stay seen.
        if _get(db, 'notification_seen', key) or ('notification_seen', key) in writes:
            continue
        writes['notification_seen', key] = True
        if event:
            row = {**event, 'id': key, 'created': timestamp, 'active': True,
                   'read': (job_outcome and first_visit) or not prefs[event['category']],
                   'desktop_attempted': False}
            notices[key] = row
            writes['notifications', key] = row
    for key, row in notices.items():
        if (row['category'] in {'review', 'updates'} or row.get('event_type') == 'disk_pause') and key not in live and row.get('active', True):
            row.update(active=False, read=True)
            writes['notifications', key] = row
    if first_visit:
        writes['notification_state', 'initialized'] = True
    latest = list(reversed(notices))
    return [notices[key] for key in latest[:MAX_ITEMS]], writes, latest[MAX_ITEMS:]


def panel(store, jobs=None, runs=None):
    """Collect new saved outcomes and return a bounded notification center."""
    jobs = store.jobs() if jobs is None else jobs
    # Read the guard's public snapshot before taking the notification write
    # transaction. Polling this panel never probes a drive or controls a process.
    from . import disk_pause
    pauses = [(job, job['disk_safety'] if 'disk_safety' in job else disk_pause.snapshot(store, job['id'])) for job in jobs
              if job.get('kind') == 'workflow' and job.get('status') == 'running']
    if runs is None:
        from .runs import run_status
        archived = {row['id'] for row in store.all('run_visibility') if row.get('archived')}
        runs = [dict(row, archived=row['id'] in archived, status=run_status(row, jobs, False)) for row in store.all('runs')]
    prefs = settings(store)
    events = _event_candidates(jobs, runs, pauses, update_checks.candidates(store))
    timestamp = now()
    with store.connect() as db:
        db.execute('BEGIN')
        items, writes, pruned = _panel_changes(db, events, prefs, timestamp)
        if writes or pruned:
            # A claim, dismissal, or preference change may have committed since the
            # read. Recompute against the latest records under the writer lock.
            db.rollback()
            db.execute('BEGIN IMMEDIATE')
            items, writes, pruned = _panel_changes(db, events, prefs, timestamp)
            for (kind, key), value in writes.items():
                _put(db, kind, key, value)
            db.executemany("DELETE FROM items WHERE kind='notifications' AND id=?", ((key,) for key in pruned))
    eligible = [row for row in items if not row['read'] and row.get('active', True) and prefs[row['category']]]
    return {'items': items, 'unread': len(eligible), 'settings': prefs,
            'desktop_candidates': [row for row in eligible if not row['desktop_attempted']],
            'update_check': update_checks.panel(store)}


def _ids(payload, limit=MAX_ITEMS):
    ids = payload.get('ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= limit or any(not isinstance(key, str) for key in ids):
        raise ValueError(f'Select between one and {limit} notifications.')
    return list(dict.fromkeys(ids))


def mark_read(store, payload):
    all_items = payload.get('all') is True
    ids = None if all_items else _ids(payload)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if all_items:
            ids = [row[0] for row in db.execute("SELECT id FROM items WHERE kind='notifications'")]
        for key in ids:
            row = _get(db, 'notifications', key)
            if row and not row['read']:
                row['read'] = True
                _put(db, 'notifications', key, row)
    return {'keep_modal': True}


def clear(store, payload):
    """Remove the displayed alerts, leaving newer events and deduplication records alone."""
    ids = _ids(payload)
    with store.connect() as db:
        db.executemany("DELETE FROM items WHERE kind='notifications' AND id=?", ((key,) for key in ids))
    return {'message': 'Notifications cleared.', 'keep_modal': True}


def claim_desktop(store, payload):
    ids = _ids(payload, limit=3)
    result = []
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        saved = _get(db, 'notification_state', 'preferences', {})
        prefs = {key: saved.get(key, True) is True for key in CATEGORIES}
        for key in ids:
            row = _get(db, 'notifications', key)
            if (not row or row['read'] or not row.get('active', True)
                    or row['desktop_attempted'] or not prefs.get(row['category'])):
                continue
            row.update(desktop_attempted=True, desktop_attempted_at=now())
            _put(db, 'notifications', key, row)
            result.append(row)
    return {'notification_delivery': result, 'keep_modal': True}
