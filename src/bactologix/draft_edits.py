"""Change settings before analysis while retaining the configuration of every attempt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .runs import freeze_config, prepare_config
from .store import now


_FILES = ('config.yaml', 'submitted.yaml', 'bactologix-run.json')
_JOURNAL = '.pending-draft-edit.json'


def jobs_for(store, run):
    return [j for j in store.jobs() if j['payload'].get('run_id') == run['id']]


def available(store, run, jobs):
    if (run.get('run_type') == 'followup' or run.get('variant') or run.get('completed')
            or run.get('previous_completed') or run.get('identification') or run.get('aware')
            or run.get('last_action') in {'run', 'continue'}):
        return False
    for job in jobs:
        if job['status'] in {'queued', 'running'}:
            return False
        if job['kind'] != 'workflow':
            continue
        if job['status'] == 'interrupted':
            from .storage_cleanup import failed_job
            if not failed_job(store, job):
                return False
        action = job['payload'].get('action')
        if action not in {'dry-run', 'check-environments', 'prepare'}:
            if not (job['status'] == 'cancelled' and job['started'] is None):
                return False
    with store.connect() as db:
        # An independent revised draft has its own source, settings and outputs.
        # Only a child that consumes this parent's shared results constrains it.
        return db.execute("""SELECT 1 FROM items WHERE kind='runs' AND json_extract(body,'$.parent')=?
                             AND (json_type(body,'$.variant')='object' OR json_extract(body,'$.run_type')='followup'
                                  OR json_extract(body,'$.output')=?) LIMIT 1""",
                          (run['id'], run['output'])).fetchone() is None


def _folder(store, run):
    folder = store.home / 'runs' / run['id']
    if folder != Path(run['folder']) or folder.resolve() != folder or folder.parent != store.home / 'runs':
        raise ValueError('The saved run folder changed. Reopen the run from this workspace.')
    return folder


def _write(path, text):
    # Readers always see a complete file, even after an interrupted write.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_pending(store, db):
    """Under the SQLite write lock, undo file changes if the row never committed."""
    journal = store.home / _JOURNAL
    if not journal.exists():
        return
    record = json.loads(journal.read_text(encoding='utf-8'))
    row = db.execute("SELECT body FROM items WHERE kind='runs' AND id=?", (record['id'],)).fetchone()
    if row:
        run = json.loads(row[0])
        if run.get('draft_generation', 0) == record['generation']:
            folder = _folder(store, run)
            for name in _FILES:
                _write(folder / name, record['originals'][name])
    journal.unlink()


def recover(store):
    if (store.home / _JOURNAL).exists():
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _restore_pending(store, db)


def save(store, parent, text, cores, memory, expected):
    from .run_revisions import signature
    if expected != signature(parent):
        raise ValueError('This run changed while the editor was open. Reopen Edit settings.')
    folder = _folder(store, parent)
    # Bind only configuration fields. Keep the original source, manifests and input identities.
    installation = store.get('installs', parent['installation'])
    installed = dict(installation, source=str(folder / 'workflow'))
    cfg = prepare_config(store, parent['tool'], parent['sample'], text, memory, installed)
    frozen = freeze_config(cfg, parent['tool'], folder, parent.get('frozen_manifest'), cores, memory)
    try:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _restore_pending(store, db)
            current = store.get('runs', parent['id'])
            if not current or expected != signature(current):
                raise ValueError('This run changed while the editor was open. Reopen Edit settings.')
            jobs = jobs_for(store, current)
            if (not available(store, current, jobs)
                    or store.get('run_visibility', parent['id'], {}).get('archived')):
                raise ValueError('This run is no longer an editable draft. Reopen Edit settings.')
            from .runs import current_controller
            current_controller(store, current)
            previous_jobs = current.get('previous_draft_jobs', [])
            job_ids = [j['id'] for j in jobs if j['kind'] == 'workflow' and j['id'] not in previous_jobs]
            keys = ('config', 'submitted', 'config_sha256', 'cores', 'memory_mb', 'validated', 'validated_controller')
            prior = {key: current[key] for key in keys if key in current}
            generation = current.get('draft_generation', 0)
            prior.update(generation=generation, job_ids=job_ids, edited_at=now())
            updated = dict(current, config=frozen, submitted=text, config_sha256=hashlib.sha256(frozen.encode()).hexdigest(),
                           cores=cores, memory_mb=memory, dry_run_ok=False, draft_generation=generation + 1,
                           previous_draft_jobs=[*previous_jobs, *job_ids],
                           draft_edit_history=[*current.get('draft_edit_history', []), prior])
            for key in ('validated', 'validated_controller', 'last_controller', 'last_action'):
                updated.pop(key, None)
            if any((folder / name).is_symlink() for name in _FILES):
                raise ValueError('The saved settings files must not be symbolic links.')
            originals = {name: (folder / name).read_bytes().decode('utf-8') for name in _FILES}
            _write(store.home / _JOURNAL, json.dumps(dict(id=current['id'], generation=generation, originals=originals)))
            _write(folder / 'config.yaml', frozen)
            _write(folder / 'submitted.yaml', text)
            _write(folder / 'bactologix-run.json', json.dumps(updated, indent=2) + '\n')
            db.execute("UPDATE items SET body=? WHERE kind='runs' AND id=?", (json.dumps(updated), current['id']))
            db.execute("DELETE FROM items WHERE kind='run_environment_checks' AND id=?", (current['id'],))
        return updated
    finally:
        # A process death instead takes this path when the workspace next opens.
        recover(store)
