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


def panel(store, jobs=None, runs=None):
    """Collect new saved outcomes and return a bounded notification center."""
    jobs = store.jobs() if jobs is None else jobs
    if runs is None:
        from .runs import run_status
        archived = {row['id'] for row in store.all('run_visibility') if row.get('archived')}
        runs = [dict(row, archived=row['id'] in archived, status=run_status(row, jobs, False)) for row in store.all('runs')]
    indexed = {row['id']: row for row in runs}
    prefs = settings(store)
    updates = update_checks.candidates(store)
    timestamp = now()
    live = set()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Another tab may have changed category preferences since the panel's
        # initial read. Event creation and its unread status share this lock.
        current = _get(db, 'notification_state', 'preferences', {})
        prefs.update({key: current.get(key, True) is True for key in CATEGORIES})
        first_visit = not _get(db, 'notification_state', 'initialized', False)

        def add(identity, event, historical=False):
            key = _identity(identity)
            existing = _get(db, 'notifications', key)
            if existing:
                if not existing.get('active', True):
                    existing['active'] = True
                    _put(db, 'notifications', key, existing)
                return key
            # Tombstones prevent dismissed/retention-pruned events from being
            # recreated when the same workspace is polled or reopened.
            if _get(db, 'notification_seen', key):
                return key
            record = {**event, 'id': key, 'created': timestamp,
                      'read': historical or not prefs[event['category']],
                      'active': True, 'desktop_attempted': False}
            _put(db, 'notifications', key, record)
            _put(db, 'notification_seen', key, True)
            return key

        for job in sorted(jobs, key=lambda row: (row.get('finished') or row.get('created') or '', row['id'])):
            if job.get('status') not in TERMINAL:
                continue
            event = _job_event(job, indexed)
            identity = ['job', job['id'], job['status'], job.get('finished')]
            if event:
                add(identity, event, historical=first_visit)
            else:
                _put(db, 'notification_seen', _identity(identity), True)
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
            live.add(add(['review', run['id'], review.get('evidence_sha256'), attempt], event))
        for event in updates:
            if event.get('category') == 'updates':
                live.add(add(['update', event['id']], {k: event[k] for k in ('category', 'title', 'body', 'target')}))
        records = db.execute("SELECT id,body FROM items WHERE kind='notifications' ORDER BY rowid DESC").fetchall()
        for record in records:
            row = json.loads(record['body'])
            if row['category'] in {'review', 'updates'} and row['id'] not in live and row.get('active', True):
                row.update(active=False, read=True)
                _put(db, 'notifications', row['id'], row)
        for record in records[MAX_ITEMS:]:
            db.execute("DELETE FROM items WHERE kind='notifications' AND id=?", (record['id'],))
        _put(db, 'notification_state', 'initialized', True)
        items = [json.loads(row[0]) for row in db.execute(
            "SELECT body FROM items WHERE kind='notifications' ORDER BY rowid DESC LIMIT ?", (MAX_ITEMS,))]
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
