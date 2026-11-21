"""Preserve existing outputs before execution, failing closed on any copy error.

Copies are independent (never hard links). Snapshots describe what existed
before an attempt, without inventing a producing tool or execution identity.
Incomplete snapshots are retained, including on cancellation or a full disk.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from .storage import _open
from .store import now

MAX_ENTRIES = 1000000
MAX_DEPTH = 96
CHUNK = 4 * 1024 * 1024


def _identity(info):
    return tuple(getattr(info, 'st_' + k) for k in ('dev', 'ino', 'mode', 'size', 'mtime_ns', 'ctime_ns'))


def _root(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Output preservation requires an absolute, scoped path.')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Output preservation root cannot be a filesystem link: ' + str(path))
    return path


def _scan(root, check_cancel, allowed_inputs=None):
    allowed_inputs = allowed_inputs or {}
    rows = []
    def visit(path, relative, ancestors=()):
        check_cancel()
        if len(rows) >= MAX_ENTRIES or len(ancestors) > MAX_DEPTH:
            raise ValueError('Output history inventory limit reached; execution was not started.')
        lexical = path.lstat()
        resolved = path.resolve(strict=True)
        input_reference = not resolved.is_relative_to(root) and stat.S_ISLNK(lexical.st_mode) and str(resolved) in allowed_inputs
        if not resolved.is_relative_to(root) and not input_reference:
            raise ValueError('An output link leaves the analysis folder; preserve that reference before rerunning: ' + str(path))
        with _open(resolved) as fd:
            info = os.fstat(fd)
            ident = (info.st_dev, info.st_ino)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Cannot preserve a special filesystem object: ' + str(path))
            row = {'relative': relative, 'source': str(resolved), 'identity': _identity(info),
                   'lexical_identity': _identity(lexical), 'directory': stat.S_ISDIR(info.st_mode)}
            if input_reference:
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('A staged input reference must target a regular file.')
                row['input_reference'] = allowed_inputs[str(resolved)]
            rows.append(row)
            if not row['directory']:
                return
            if ident in ancestors:
                raise ValueError('An output link cycle prevents preservation: ' + str(path))
            names = sorted(os.listdir(fd))
        for name in names:
            visit(path / name, relative + '/' + name if relative else name, ancestors + (ident,))
    visit(root, '')
    return rows


def _write_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def _copy_file(row, target, check_cancel):
    source = Path(row['source'])
    sha = hashlib.sha256()
    if shutil.disk_usage(target.parent).free < row['identity'][3] + 16 * 1024 * 1024:
        raise ValueError('Not enough space to preserve previous outputs. Transfer or explicitly remove reviewed data before retrying; no analysis was started.')
    with _open(source) as fd, os.fdopen(os.dup(fd), 'rb') as incoming, target.open('xb') as outgoing:
        if _identity(os.fstat(fd)) != row['identity']:
            raise ValueError('An output changed before preservation: ' + str(source))
        while block := incoming.read(CHUNK):
            check_cancel()
            outgoing.write(block)
            sha.update(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())
        if _identity(os.fstat(fd)) != row['identity']:
            raise ValueError('An output changed during preservation: ' + str(source))
    verify = hashlib.sha256()
    with target.open('rb') as handle:
        while block := handle.read(CHUNK):
            check_cancel()
            verify.update(block)
    if verify.digest() != sha.digest():
        raise ValueError('Output history copy failed verification: ' + str(target))
    return {'path': row['relative'], 'bytes': row['identity'][3], 'sha256': sha.hexdigest()}


def preserve(ctx, run):
    """Called before workflow mutation; a preservation failure prevents launch."""
    from .release_contract import approval
    review = approval(ctx.store, run.get('tool'), run.get('commit')) if run.get('tool') else None
    if review and review.get('output_retention'):
        # Caller verified frozen source and official contract. Archive per job
        # including retries, without duplicating unchanged large outputs.
        ctx.write('Output policy: the checked pipeline preserves replaced products in retained/ and keeps failed outputs. No automatic cleanup.')
        return None
    folder = _root(run['folder'])
    output = _root(run['output'])
    workspace_runs = _root(ctx.store.home / 'runs')
    if not folder.is_relative_to(workspace_runs) or not output.is_relative_to(workspace_runs):
        raise ValueError('Output history can preserve only registered workspace run folders.')
    if not output.exists():
        return None
    if not output.is_dir():
        raise ValueError('The registered analysis output is not a folder.')
    allowed_inputs = {str(Path(row['path']).resolve()): row for row in run.get('inputs', [])
                      if isinstance(row, dict) and isinstance(row.get('path'), str)}
    rows = _scan(output, ctx.check_cancel, allowed_inputs)
    if len(rows) == 1:
        return None
    job_id = ctx.job['id']
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
        raise ValueError('Invalid execution attempt identity for output history.')
    history = _root(folder / 'output-history')
    if history.is_relative_to(output):
        raise ValueError('Output history cannot be nested inside its source folder.')
    history.mkdir(exist_ok=True)
    destination = history / job_id
    destination.mkdir()
    manifest = {'schema': 1, 'created_at': now(), 'reason': 'preserved-before-attempt',
                'run_id': run['id'], 'job_id': job_id, 'source': str(output),
                'status': 'copying', 'completed': False,
                'attribution': 'Observed before this attempt; producing attempt is not inferred.'}
    _write_json(destination / 'started.json', manifest)
    ctx.write('Preserving existing tool outputs before this attempt: ' + str(destination))
    copies = destination / 'outputs'
    files, references = [], []
    try:
        for row in rows:
            ctx.check_cancel()
            target = copies / row['relative']
            if row['directory']:
                target.mkdir()
            elif 'input_reference' in row:
                # Original registered inputs are references, not generated products.
                target.symlink_to(row['source'])
                references.append({'path': row['relative'], 'target': row['source'],
                                   'recorded_identity': row['input_reference']})
            else:
                files.append(_copy_file(row, target, ctx.check_cancel))
        if _scan(output, ctx.check_cancel, allowed_inputs) != rows:
            raise ValueError('Output inventory changed during preservation; execution was not started.')
        manifest.update(status='completed', completed=True, completed_at=now(),
                        files=files, file_count=len(files), bytes=sum(r['bytes'] for r in files),
                        input_references=references, input_policy='Original inputs remain references; generated files are independent verified copies.')
        _write_json(destination / 'manifest.json', manifest)
    except BaseException as exc:
        try:
            _write_json(destination / 'failure.json', {'status': 'incomplete', 'error': str(exc), 'at': now()})
        except OSError:
            pass
        raise
    ctx.write('Output history verified. Previous files remain available in All tool outputs.')
    return manifest
