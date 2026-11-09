"""Immutable invocation receipts, never retrospective environment probes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from datetime import datetime

from .data import runtime_receipt as native
from .report_evidence import identity as run_identity


LAUNCH = 'methods_runtime_launch'
RECEIPT = 'methods_runtime'


def _identity(run, job):
    return {'run': run_identity(run), 'controller': str(Path(run.get('last_controller') or run['controller']).absolute()),
            'action': job['payload']['action']}


def _job(store, run, jid):
    job = store.job(jid)
    if (not job or job['kind'] != 'workflow' or job['payload'].get('run_id') != run['id']
            or job['payload'].get('action') not in {'run', 'continue'}):
        raise ValueError('Runtime receipt is not bound to an execution attempt')
    return job


def _immutable(store, kind, jid, value):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT body FROM items WHERE kind=? AND id=?', (kind, jid)).fetchone()
        if old:
            if json.loads(old['body']) != value:
                raise ValueError('Runtime receipt for this attempt is already recorded')
        else:
            db.execute('INSERT INTO items VALUES (?,?,?)', (kind, jid, json.dumps(value)))
    return value


def prepare(store, run, job):
    """Reserve a unique, fixed destination before invoking the controller."""
    job = _job(store, run, job['id'])
    if job['status'] != 'running':
        raise ValueError('Runtime receipt requires a running execution attempt')
    if not all(re.fullmatch(r'(?:run|job)-[A-Za-z0-9_-]+', v) for v in (run['id'], job['id'])):
        raise ValueError('Invalid execution identity')
    folder = store.home / 'runs' / run['id']
    if Path(run['folder']) != folder:
        raise ValueError('Runtime receipt requires a managed run folder')
    root_fd = native.directory(folder)
    runtime_fd = attempt_fd = None
    try:
        try:
            os.mkdir('runtime', mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        runtime_fd = os.open('runtime', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        os.mkdir(job['id'], mode=0o700, dir_fd=runtime_fd)
        attempt_fd = os.open(job['id'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=runtime_fd)
        files = {}
        for name in ('started.json', 'finished.json'):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=attempt_fd)
            try:
                info = os.fstat(fd)
                files[name] = [info.st_dev, info.st_ino]
            finally:
                os.close(fd)
        identity = _identity(run, job)
        context = {'schema': 1, 'job_id': job['id'], 'run_id': run['id'], 'nonce': secrets.token_hex(32),
                   'controller': str(Path(identity['controller']).resolve()), 'identity_sha256': hashlib.sha256(native.encoded(identity)).hexdigest(),
                   'files': files}
        raw = native.encoded(context)
        fd = os.open('context.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=attempt_fd)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        for fd in (attempt_fd, runtime_fd, root_fd):
            if fd is not None:
                os.close(fd)
    path = folder / 'runtime' / job['id'] / 'context.json'
    launch = {'schema': 1, 'job_id': job['id'], 'run_id': run['id'], 'identity': identity,
              'context': context, 'context_sha256': hashlib.sha256(raw).hexdigest(), 'path': str(path)}
    _immutable(store, LAUNCH, job['id'], launch)
    return {**launch, 'environment': dict(zip(native.ENV, (str(path), context['nonce'], launch['context_sha256'])))}


def _validate(run, job, launch, started, finished):
    if (launch.get('schema') != 1 or launch.get('job_id') != job['id'] or launch.get('run_id') != run['id']
            or launch.get('identity') != _identity(run, job)):
        raise ValueError('Runtime receipt has different run or execution identity')
    context = launch['context']
    if (hashlib.sha256(native.encoded(context)).hexdigest() != launch['context_sha256']
            or hashlib.sha256(native.encoded(launch['identity'])).hexdigest() != context['identity_sha256']):
        raise ValueError('Runtime receipt context changed')
    for doc in (started, finished):
        if not isinstance(doc, dict) or any(doc.get(key) != expected for key, expected in
                {'schema': 1, 'job_id': job['id'], 'run_id': run['id'], 'nonce': context['nonce'],
                 'context_sha256': launch['context_sha256'], 'identity_sha256': context['identity_sha256']}.items()):
            raise ValueError('Controller observation does not match its reserved context')
    if (started.get('controller') != context['controller']
            or finished.get('started_sha256') != hashlib.sha256(native.encoded(started)).hexdigest()):
        raise ValueError('Controller observation changed')
    versions = started.get('versions', {})
    if (set(versions) != {'snakemake', 'controller_python'}
            or any(not isinstance(v, str) or not v or len(v) > 200 for v in versions.values())):
        raise ValueError('Actual controller versions are unavailable')
    start, end = datetime.fromisoformat(started['started_at']), datetime.fromisoformat(finished['finished_at'])
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError('Controller invocation timestamps are invalid')
    if finished.get('status') not in {'succeeded', 'failed', 'interrupted'} or type(finished.get('exit_code')) is not int:
        raise ValueError('Controller invocation result is invalid')
    if (finished['exit_code'] == 0) != (finished['status'] == 'succeeded'):
        raise ValueError('Controller invocation outcome conflicts with its exit code')
    return versions


def finish(store, run, jid, launch, *, succeeded):
    """Freeze the observed invocation, including missing/failed observations."""
    if launch is None:
        return None
    job = _job(store, run, jid)
    saved = store.get(LAUNCH, jid)
    if not saved or {k: v for k, v in launch.items() if k != 'environment'} != saved:
        raise ValueError('Runtime launch context changed')
    path = store.home / 'runs' / run['id'] / 'runtime' / jid
    if saved['path'] != str(path / 'context.json'):
        raise ValueError('Runtime receipt path changed')
    receipt = {'schema': 1, 'job_id': jid, 'run_id': run['id'], 'identity': saved['identity'],
               'status': 'unavailable', 'parent_succeeded': bool(succeeded), 'observations': {}, 'warnings': []}
    try:
        for name, key in (('started.json', 'started'), ('finished.json', 'finished')):
            doc, _, inode = native.read(path / name)
            if inode != saved['context']['files'][name]:
                raise ValueError('Reserved runtime receipt file changed')
            receipt['observations'][key] = doc
        started, finished = receipt['observations']['started'], receipt['observations']['finished']
        _validate(run, job, saved, started, finished)
        receipt['status'] = finished['status'] if succeeded else 'cancelled' if job.get('cancel') else 'failed'
        if succeeded and finished['status'] != 'succeeded':
            receipt['status'] = 'unavailable'
            receipt['warnings'].append('Controller observation conflicts with its parent command outcome.')
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        receipt['warnings'].append('Controller invocation evidence unavailable: ' + str(exc))
    return _immutable(store, RECEIPT, jid, receipt)


def evidence(store, run, job_id):
    """Return successful, bound evidence without reading current files/packages."""
    try:
        job = _job(store, run, job_id)
        if job['status'] not in {'running', 'succeeded'} or job.get('cancel'):
            return None
        receipt, launch = store.get(RECEIPT, job_id), store.get(LAUNCH, job_id)
        if (not receipt or not launch or receipt.get('status') != 'succeeded'
                or receipt.get('schema') != 1 or receipt.get('job_id') != job_id
                or receipt.get('run_id') != run['id'] or receipt.get('identity') != _identity(run, job)
                or receipt.get('parent_succeeded') is not True):
            return None
        started, finished = receipt['observations']['started'], receipt['observations']['finished']
        versions = _validate(run, job, launch, started, finished)
        if finished['status'] != 'succeeded':
            return None
        return {'schema': 1, 'job_id': job_id, 'run_id': run['id'], 'identity': copy.deepcopy(receipt['identity']),
                'versions': dict(versions),
                'scopes': {name: 'Observed in the actual workflow controller invocation; individual step execution is not established' for name in versions},
                'started_at': started['started_at'], 'finished_at': finished['finished_at'],
                'status': 'succeeded', 'exit_code': 0, 'executable': started['executable'],
                'context_sha256': launch['context_sha256']}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
