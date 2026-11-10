"""On-demand, bounded browsing of live outputs and retained attempt snapshots.

Directory listings are observations, not claims about the producing tool or
scientific completeness. No file bytes are changed or automatically downloaded.
"""
import json
import os
from pathlib import Path
import stat
import time

PAGE_SIZE = 100
MAX_ENTRIES = 10000
MAX_SECONDS = 2.0
MAX_SNAPSHOTS = 200
MANIFEST_LIMIT = 2 * 1024 * 1024


def _run(store, rid):
    run = store.get('runs', rid) if isinstance(rid, str) else None
    if not run:
        raise ValueError('Choose an existing run.')
    return run


def _directory(path):
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Output browsing does not follow symbolic links.')
    if not path.is_dir():
        raise ValueError('This output folder is not available.')
    return path


def _same_output(left, right):
    return Path(left['output']).absolute() == Path(right['output']).absolute()


def snapshots(store, run):
    rows, partial = [], False
    deadline = time.monotonic() + MAX_SECONDS
    for owner in store.all('runs'):
        if not _same_output(owner, run):
            continue
        history = Path(owner['folder']) / 'output-history'
        if not history.exists():
            continue
        try:
            _directory(history)
            with os.scandir(history) as entries:
                for item in entries:
                    if len(rows) >= MAX_SNAPSHOTS or time.monotonic() > deadline:
                        partial = True
                        break
                    if item.is_symlink() or not item.is_dir(follow_symlinks=False):
                        continue
                    folder = Path(item.path)
                    manifest = folder / 'manifest.json'
                    info = {}
                    if not manifest.is_symlink() and manifest.is_file() and manifest.stat().st_size <= MANIFEST_LIMIT:
                        try:
                            raw = json.loads(manifest.read_text())
                            if isinstance(raw, dict):
                                info = raw
                        except (OSError, ValueError):
                            pass
                    rows.append({'id': owner['id'] + '/' + item.name,
                                 'run_id': owner['id'], 'attempt': item.name,
                                 'created_at': info.get('created_at', info.get('created', 'Not recorded')),
                                 'state': info.get('status', 'Manifest unavailable or status not recorded'),
                                 'reason': 'Preserved before an attempt; this does not identify the producing attempt.',
                                 'path': str(folder), 'available': (folder / 'outputs').is_dir() and not (folder / 'outputs').is_symlink()})
        except (OSError, ValueError):
            partial = True
        if partial and (len(rows) >= MAX_SNAPSHOTS or time.monotonic() > deadline):
            break
    rows.sort(key=lambda row: (str(row['created_at']), row['id']), reverse=True)
    return rows, partial


def root_for(store, rid, snapshot=''):
    run = _run(store, rid)
    if not snapshot:
        return _directory(run['output'])
    if not isinstance(snapshot, str) or len(snapshot.split('/')) != 2:
        raise ValueError('Choose a retained snapshot from this run.')
    owner_id, attempt = snapshot.split('/')
    if owner_id == '@run-files':
        owner = _run(store, attempt)
        if not _same_output(owner, run):
            raise ValueError('This run belongs to a different output folder.')
        return _directory(owner['folder'])
    if not attempt or attempt in {'.', '..'} or '\\' in attempt:
        raise ValueError('Choose a retained snapshot from this run.')
    owner = _run(store, owner_id)
    if not _same_output(owner, run):
        raise ValueError('This snapshot belongs to a different output folder.')
    return _directory(Path(owner['folder']) / 'output-history' / attempt / 'outputs')


def folder(store, rid, relative='', snapshot=''):
    if not isinstance(relative, str):
        raise ValueError('Choose an output folder.')
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or '\\' in relative:
        raise ValueError('Output folder must stay within the selected run.')
    root = root_for(store, rid, snapshot)
    return _directory(root / rel)


def inspect(store, rid, relative='', snapshot='', page=0):
    if isinstance(page, bool) or not isinstance(page, int) or page < 0 or page > MAX_ENTRIES // PAGE_SIZE:
        raise ValueError('Choose a valid output page.')
    run = _run(store, rid)
    archives, archives_partial = snapshots(store, run)
    run_folders = [{'id': '@run-files/' + owner['id'], 'run_id': owner['id']}
                   for owner in store.all('runs') if _same_output(owner, run)]
    unavailable = ''
    try:
        location = folder(store, rid, relative, snapshot)
        root = root_for(store, rid, snapshot)
    except (OSError, ValueError) as error:
        if relative or snapshot:
            raise
        # A missing live folder must not hide the retained copies or run files.
        location = root = Path(run['output'])
        unavailable = str(error)
    rows, partial = [], False
    deadline = time.monotonic() + MAX_SECONDS

    def entries_at():
        if unavailable:
            return
        with os.scandir(location) as entries:
            yield from entries

    try:
        for item in entries_at():
            if len(rows) >= MAX_ENTRIES or time.monotonic() > deadline:
                partial = True
                break
            try:
                details = item.stat(follow_symlinks=False)
                mode = details.st_mode
                kind = 'link' if stat.S_ISLNK(mode) else 'folder' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'special'
                rows.append({'name': item.name, 'relative': str(Path(item.path).relative_to(root)),
                             'kind': kind, 'bytes': details.st_size if kind == 'file' else None})
            except OSError:
                rows.append({'name': item.name, 'relative': str(Path(item.path).relative_to(root)), 'kind': 'unavailable', 'bytes': None})
    except OSError as error:
        partial = True
        unavailable = 'Directory could not be read: ' + str(error)
    rows.sort(key=lambda row: (row['kind'] != 'folder', row['name'].casefold(), row['name']))
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    return {'id': rid, 'snapshot': snapshot, 'relative': str(location.relative_to(root)) if location != root else '',
            'path': str(location), 'rows': rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE],
            'page': page, 'pages': pages, 'observed_entries': len(rows), 'partial': partial,
            'snapshots': archives, 'snapshots_partial': archives_partial, 'run_folders': run_folders,
            'unavailable': unavailable,
            'notice': 'All file types are listed, including tool outputs and intermediate files. Links are shown but not followed. '
                      'Live files may change during a job. Preserved snapshots describe files present before an attempt, not who produced them. '
                      'Use Open folder for the full directory when a listing reaches its limit.'}
