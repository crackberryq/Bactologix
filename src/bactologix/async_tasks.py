"""Bounded background observations for the workspace UI, never authorization.

Tasks are deliberately independent of the workflow queue. Cancelling or timing
out discards a result; it cannot interrupt a filesystem call already in progress.
Such threads retain their capacity slot until they actually return.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid

ACTIONS = frozenset({'scan_databases', 'check_database', 'edit_run_settings',
                     'factory_reset_preview', 'database_redownload_preview', 'recommend_swap'})
MAX_ACTIVE_PER_WORKSPACE = 2
MAX_ACTIVE = 8
MAX_TASKS = 128
MAX_TASKS_PER_WORKSPACE = 32
TASK_TIMEOUT = 30
# Reset inventories and protected-link discovery each have a 60s traversal
# budget. Keep normal checks short without discarding a valid reset preview.
TASK_TIMEOUTS = {'factory_reset_preview': 180}
RESULT_TTL = 300
MAX_PAYLOAD_BYTES = 65536
MAX_RESULT_BYTES = 4 * 1024 * 1024
_LOCK = threading.Lock()
_TASKS = {}
_ACTIVE = {}


def _workspace(store):
    return (str(store.home), store.channel)


def _text(value, label):
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ValueError('Invalid background task ' + label + '.')
    return value


def _binding(store, kind, payload):
    """Cheap saved-record identity only; full checks remain in save/launch."""
    if kind == 'edit_run_settings':
        rid = payload.get('id')
        run = store.get('runs', rid)
        value = [run, store.get('run_visibility', rid),
                 store.get('installs', run.get('installation')) if run else None,
                 [(j['id'], j['status']) for j in store.jobs()
                  if j['kind'] == 'workflow' and j['payload'].get('run_id') == rid
                  and j['status'] in {'queued', 'running'}]]
    elif kind == 'factory_reset_preview':
        value = [store.all(name) for name in ('runs', 'run_visibility', 'configs', 'installs', 'database_locations')]
    else:
        tool = payload.get('tool')
        value = [store.get('configs', tool), store.get('database_locations', tool),
                 store.get('settings', 'active-' + str(tool))]
        if kind == 'recommend_swap' and payload.get('run_id'):
            value += [store.get('runs', payload['run_id']), store.get('run_visibility', payload['run_id'])]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _describe(task):
    result = {k: task[k] for k in ('token', 'request_id', 'kind', 'status')}
    if task.get('message'):
        result['message'] = task['message']
    if task['status'] == 'completed':
        result['result'] = copy.deepcopy(task['result'])
    return result


def _invalidate(task, status, message):
    task.update(status=status, message=message, finished=time.monotonic())
    task.pop('result', None)


def _expire():
    timestamp = time.monotonic()
    for token, task in list(_TASKS.items()):
        if task['status'] == 'running' and timestamp - task['started'] >= TASK_TIMEOUTS.get(task['kind'], TASK_TIMEOUT):
            _invalidate(task, 'timed_out', 'This check took too long. Its result will not be used; a filesystem operation may still be finishing.')
        if not task['active'] and timestamp - task.get('finished', task['started']) >= RESULT_TTL:
            del _TASKS[token]


def submit(store, owner, request_id, kind, payload):
    """Return immediately; owner is a server-generated browser-session token."""
    _text(owner, 'owner')
    _text(request_id, 'request ID')
    if kind not in ACTIONS or not isinstance(payload, dict):
        raise ValueError('This action cannot run as a background observation.')
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError('Background task request is too large.')
    payload = json.loads(encoded)
    workspace = _workspace(store)
    with _LOCK:
        _expire()
        for task in _TASKS.values():
            if (task['workspace'], task['owner'], task['request_id']) == (workspace, owner, request_id):
                if task['kind'] != kind or task['payload'] != payload:
                    raise ValueError('This request ID already belongs to a different task.')
                return _describe(task)
        if _ACTIVE.get(workspace, 0) >= MAX_ACTIVE_PER_WORKSPACE or sum(_ACTIVE.values()) >= MAX_ACTIVE:
            raise ValueError('Background checks are still finishing. Wait for one to finish before starting another.')
        # Evict only finished records; active work is never hidden to free a slot.
        while (len(_TASKS) >= MAX_TASKS or
               sum(t['workspace'] == workspace for t in _TASKS.values()) >= MAX_TASKS_PER_WORKSPACE):
            candidates = [t for t in _TASKS.values() if not t['active'] and
                          (t['workspace'] == workspace if sum(x['workspace'] == workspace for x in _TASKS.values()) >= MAX_TASKS_PER_WORKSPACE else True)]
            if not candidates:
                raise ValueError('Background task capacity is full. Try again after a check finishes.')
            del _TASKS[min(candidates, key=lambda t: t.get('finished', t['started']))['token']]
        for previous in _TASKS.values():
            if (previous['workspace'], previous['owner'], previous['kind']) == (workspace, owner, kind):
                _invalidate(previous, 'superseded', 'A newer request replaced this result.')
        token = uuid.uuid4().hex
        task = dict(token=token, request_id=request_id, kind=kind, payload=payload,
                    owner=owner, workspace=workspace, status='running', active=True,
                    started=time.monotonic())
        _TASKS[token] = task
        _ACTIVE[workspace] = _ACTIVE.get(workspace, 0) + 1
        try:
            threading.Thread(target=_run, args=(store, token), name='bactologix-ui-check', daemon=True).start()
        except BaseException:
            task['active'] = False
            _ACTIVE[workspace] -= 1
            if not _ACTIVE[workspace]:
                del _ACTIVE[workspace]
            _invalidate(task, 'failed', 'The background check could not start.')
            raise
        return _describe(task)


def _run(store, token):
    with _LOCK:
        task = _TASKS[token]
        kind, payload = task['kind'], copy.deepcopy(task['payload'])
    try:
        binding = _binding(store, kind, payload)
        from .ui_backend import dispatch
        result = dispatch(store, kind, payload)
        if len(json.dumps(result).encode()) > MAX_RESULT_BYTES:
            raise ValueError('The check result is too large. Choose a narrower folder or selection.')
        current = _binding(store, kind, payload)
        with _LOCK:
            _expire()
            if task['status'] == 'running':
                if binding != current:
                    _invalidate(task, 'stale', 'Saved settings changed during this check. Request it again.')
                else:
                    task.update(status='completed', result=result, binding=binding, finished=time.monotonic())
    except Exception as exc:
        with _LOCK:
            if task['status'] == 'running':
                _invalidate(task, 'failed', str(exc))
    finally:
        with _LOCK:
            task['active'] = False
            _ACTIVE[task['workspace']] -= 1
            if not _ACTIVE[task['workspace']]:
                del _ACTIVE[task['workspace']]


def _owned(store, owner, token):
    _text(owner, 'owner')
    _text(token, 'token')
    task = _TASKS.get(token)
    if not task or task['workspace'] != _workspace(store) or task['owner'] != owner:
        raise ValueError('This background check expired or belongs to another session. Request it again.')
    return task


def poll(store, owner, token):
    with _LOCK:
        _expire()
        task = _owned(store, owner, token)
        if task['status'] != 'completed':
            return _describe(task)
        kind, payload, binding = task['kind'], copy.deepcopy(task['payload']), task['binding']
    current = _binding(store, kind, payload)
    with _LOCK:
        _expire()
        task = _owned(store, owner, token)
        if task['status'] == 'completed' and current != binding:
            _invalidate(task, 'stale', 'Saved settings changed after this check. Request it again.')
        return _describe(task)


def cancel(store, owner, token):
    with _LOCK:
        _expire()
        task = _owned(store, owner, token)
        _invalidate(task, 'cancelled', 'Check cancelled. Any result from work still finishing will be discarded.')
        return _describe(task)
