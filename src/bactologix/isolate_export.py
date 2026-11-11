"""Reviewed streaming isolate copies with durable local verification receipts.

Originals are never removed. Plans are metadata observations, not filesystem
snapshots. Source identities are rechecked; destination files are read and hashed.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
from urllib.parse import quote

from .storage import _open
from .store import identifier, now

MAX_FILES = 100000
MAX_DEPTH = 96
MAX_SCAN_SECONDS = 30
MAX_GENERATED_BYTES = 128 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
PREVIEW_FILES = 200
RESERVE_BYTES = 16 * 1024 * 1024
PLANS = 'isolate_export_plans'
RECEIPTS = 'isolate_export_receipts'
NOTICE = ('Original files are retained. Local destination contents are read back and verified with SHA256. '
          'A local copy does not confirm a SharePoint upload. References and software environments are '
          'recorded by identity rather than copied. Native reports and frozen settings retain their original '
          'content; use the package index and path map when original absolute links are unavailable.')


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _absolute(value):
    if not isinstance(value, (str, Path)) or '\x00' in str(value):
        raise ValueError('Choose a valid absolute folder path.')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Choose an absolute folder path without parent traversal.')
    return path


def _relative(value):
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value:
        raise ValueError('An export path is not portable.')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('An export path escapes the package.')
    for part in path.parts:
        if (re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((' ', '.'))
                or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)):
            raise ValueError('Rename this non-portable source name before export: ' + value)
    return path


def _inside(path, root):
    return path == root or root in path.parents


def _stat(info):
    return {key: getattr(info, 'st_' + key) for key in ('dev', 'ino', 'mode', 'size', 'mtime_ns', 'ctime_ns')}


def _destination(path):
    path = _absolute(path)
    with _open(path) as fd:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Export destination must be an existing folder.')
    return path, {'dev': info.st_dev, 'ino': info.st_ino}


def _idle(store, own_job=None):
    active = [j for j in store.jobs() if j['status'] in {'queued', 'running'} and j['id'] != own_job]
    if active:
        raise ValueError('Finish or cancel queued/running jobs before exporting an isolate.')


def _inventory(store, sample_id):
    from .isolate_inventory import build
    return build(store, sample_id)


def _scan(inventory, store):
    """Freeze a bounded tree inventory, resolving only explicitly scoped links."""
    roots, allowed = inventory.get('roots', []), []
    for root in roots:
        source = _absolute(root['source'])
        _relative(root['relative'])
        if source == Path('/') or _inside(store.home, source):
            raise ValueError('An isolate export cannot include the workspace or one of its ancestor folders.')
        try:
            resolved = source.resolve(strict=True)
        except FileNotFoundError:
            continue
        if resolved == Path('/') or _inside(store.home, resolved):
            raise ValueError('An export source resolves to the workspace or one of its ancestor folders.')
        allowed.append(resolved)
    files, directories, missing = [], [], list(inventory.get('missing', []))
    destinations = {}
    started, entries = time.monotonic(), 0

    def visit(source, relative, ancestors=()):
        nonlocal entries
        entries += 1
        if entries > MAX_FILES or len(ancestors) > MAX_DEPTH or time.monotonic() - started > MAX_SCAN_SECONDS:
            raise ValueError('Export inventory exceeds the bounded scan limit; reduce the selected source scope.')
        relative = str(_relative(relative))
        collision = destinations.get(relative.casefold())
        if collision is not None:
            if collision == str(source):
                return
            raise ValueError('Export paths would collide on a Windows or USB destination: ' + relative)
        destinations[relative.casefold()] = str(source)
        try:
            lexical = source.lstat()
            resolved = source.resolve(strict=True)
        except FileNotFoundError:
            missing.append('Missing source: ' + str(source))
            return
        except RuntimeError as exc:
            raise ValueError('A source link cycle prevents export: ' + str(source)) from exc
        if not any(_inside(resolved, root) for root in allowed):
            raise ValueError('A source link leaves the reviewed isolate scope: ' + str(source))
        with _open(resolved) as fd:
            info = os.fstat(fd)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('Special filesystem objects cannot be exported: ' + str(source))
            identity = (info.st_dev, info.st_ino)
            row = {'source': str(source), 'resolved_source': str(resolved), 'relative': relative,
                   'identity': _stat(info), 'lexical_identity': _stat(lexical),
                   'linked': source != resolved or stat.S_ISLNK(lexical.st_mode)}
            if stat.S_ISDIR(info.st_mode):
                if identity in ancestors:
                    raise ValueError('A source directory cycle prevents export: ' + str(source))
                directories.append(row)
                names = sorted(os.listdir(fd))
            else:
                row['bytes'] = info.st_size
                files.append(row)
                return
        for name in names:
            visit(source / name, relative + '/' + name, ancestors + (identity,))

    for root in sorted(roots, key=lambda r: (len(PurePosixPath(r['relative']).parts), r['relative'])):
        visit(_absolute(root['source']), root['relative'])
    copies, aliases, canonical = [], [], {}
    for row in sorted(files, key=lambda r: (r['linked'], r['relative'])):
        key = row['resolved_source']
        if key in canonical:
            aliases.append({**row, 'target_relative': canonical[key]['relative']})
        else:
            canonical[key] = row
            copies.append(row)
    return {'files': sorted(copies, key=lambda r: r['relative']), 'aliases': sorted(aliases, key=lambda r: r['relative']),
            'directories': sorted(directories, key=lambda r: r['relative']), 'missing': sorted(set(missing))}


def _plan_token(plan):
    return _digest({k: v for k, v in plan.items() if k not in {'token', 'status', 'consumed_by'}})


def _historical_sources(inventory):
    """Enumerate explicit saved file identities without probing undeclared paths."""
    def collection(owner, field):
        value = owner.get(field) or []
        return value if isinstance(value, list) else [value]

    def sample_sources(sample):
        if not isinstance(sample, dict):
            return
        for expected in collection(sample, 'inputs'):
            yield 'sample.inputs', expected
        for field in ('micp_handoff', 'aware_provenance'):
            evidence = sample.get(field)
            if not isinstance(evidence, dict):
                continue
            for expected in collection(evidence, 'files'):
                yield 'sample.' + field + '.files', expected
            if field == 'aware_provenance' and (evidence.get('bundle_path') or evidence.get('bundle_sha256')):
                yield 'sample.aware_provenance.bundle', {'path': evidence.get('bundle_path'), 'sha256': evidence.get('bundle_sha256')}
        if sample.get('manifest_source'):
            yield 'sample.manifest_source', sample['manifest_source']

    for name, expected in sample_sources(inventory.get('sample')):
        yield None, name, expected
    for run in inventory.get('runs', []):
        for field in ('inputs', 'followup_sources'):
            for expected in collection(run, field):
                yield run['id'], field, expected
        for name, expected in sample_sources(run.get('sample')):
            yield run['id'], name, expected


def _input_checks(inventory, files, pending=False, aliases=()):
    """Separate current-copy integrity from every retained historical source identity."""
    by_path = {}
    for row in files:
        for key in ('source', 'resolved_source'):
            if row.get(key):
                by_path[row[key]] = row
    by_relative = {row['relative']: row for row in files}
    for alias in aliases:
        if alias['target_relative'] in by_relative:
            by_path[alias['source']] = by_relative[alias['target_relative']]
    checks = []
    for run_id, source_collection, expected in _historical_sources(inventory):
        if not isinstance(expected, dict):
            expected = {'path': expected if isinstance(expected, str) else None}
        path, digest = expected.get('path'), expected.get('sha256')
        current = by_path.get(path) if isinstance(path, str) else None
        if not current:
            status = 'missing'
        elif not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            status = 'not-recorded'
        elif pending:
            status = 'pending'
        else:
            status = 'matched' if digest == current.get('sha256') else 'mismatch'
        checks.append({'run_id': run_id, 'source_collection': source_collection, 'path': path, 'status': status,
                       'expected_sha256': digest, 'copied_sha256': current.get('sha256') if current else None,
                       'relative': current.get('relative') if current else None})
    return checks


def _input_notes(checks):
    notes = []
    for row in checks:
        detail = {'missing': 'input file is unavailable', 'not-recorded': 'no valid input content checksum was recorded',
                  'mismatch': 'the copied current file does not match the saved historical checksum'}.get(row['status'])
        if detail:
            owner = str(row['run_id']) if row['run_id'] is not None else 'registered sample'
            notes.append('Historical input identity unavailable for ' + owner + ' [' + row['source_collection'] + ']: ' + str(row['path']) + ' — ' + detail + '.')
    return notes


def _public(plan):
    return {k: plan[k] for k in ('id', 'token', 'sample_id', 'created', 'destination', 'destination_kind',
                                'file_count', 'total_bytes', 'missing', 'warnings', 'blocked', 'can_export',
                                'scope_status', 'free_bytes', 'notice', 'alias_count', 'logical_bytes', 'input_verification')} | {
        'files': [{k: row[k] for k in ('source', 'relative', 'bytes', 'linked')} for row in plan['files'][:PREVIEW_FILES]],
        'omitted_preview_files': max(0, len(plan['files']) - PREVIEW_FILES),
        'aliases': [{k: r[k] for k in ('source', 'relative', 'target_relative', 'bytes')} for r in plan['aliases'][:PREVIEW_FILES]],
        'roots': plan['inventory'].get('roots', []), 'run_count': len(plan['inventory'].get('runs', [])),
    }


def preview(store, sample_id, destination, destination_kind='folder'):
    """Review exact scope without writing to the destination or copying data."""
    if destination_kind not in {'folder', 'sharepoint_sync'}:
        raise ValueError('Choose a local/USB folder or a SharePoint-synced folder.')
    from .storage_guard import activity
    with activity(store):
        parent, parent_identity = _destination(destination)
        inventory = _inventory(store, sample_id)
        key = identifier('export')
        sample_name = re.sub(r'[^A-Za-z0-9._-]+', '-', str(sample_id)).strip('.-')[:64] or 'isolate'
        target = parent / (sample_name + '-' + key)
        blocked = []
        if _inside(parent, store.home):
            blocked.append('Choose a destination outside the active workspace.')
        for root in inventory.get('roots', []):
            source = _absolute(root['source']).resolve()
            if _inside(parent, source) or _inside(source, target):
                blocked.append('Destination overlaps an isolate source: ' + str(source))
        try:
            _idle(store)
        except ValueError as exc:
            blocked.append(str(exc))
        try:
            scan = _scan(inventory, store)
        except (OSError, ValueError) as exc:
            scan = {'files': [], 'aliases': [], 'directories': [], 'missing': list(inventory.get('missing', []))}
            blocked.append(str(exc))
        total = sum(row['bytes'] for row in scan['files'])
        free = shutil.disk_usage(parent).free
        if total + RESERVE_BYTES > free:
            blocked.append('Destination has insufficient free space for the reviewed files and export metadata.')
        warnings = list(inventory.get('warnings', []))
        input_checks = _input_checks(inventory, scan['files'], pending=True, aliases=scan['aliases'])
        input_notes = _input_notes(input_checks)
        warnings.extend(input_notes)
        if any(row['status'] == 'pending' for row in input_checks):
            warnings.append('Saved input checksums will be compared with the copied files. Any historical mismatch will be reported as partial scope; a verified current copy does not reconstruct changed original inputs.')
        if scan['aliases']:
            warnings.append('Repeated staged input links are stored once. The manifest and index map each original alias to its portable file; this is an evidence package, not an automatically relocated runnable workspace.')
        if destination_kind == 'sharepoint_sync':
            warnings.append('This verifies the local synced folder only. Cloud upload and cloud integrity are not verified.')
        if not scan['files']:
            warnings.append('No existing source files were found; only available records and generated documents can be exported.')
        plan = {'id': key, 'sample_id': sample_id, 'created': now(), 'destination': str(target),
                'destination_parent': str(parent), 'destination_parent_identity': parent_identity,
                'destination_kind': destination_kind, 'inventory': inventory,
                **scan, 'file_count': len(scan['files']), 'total_bytes': total, 'free_bytes': free,
                'alias_count': len(scan['aliases']), 'logical_bytes': total + sum(r['bytes'] for r in scan['aliases']),
                'input_verification': input_checks,
                'warnings': warnings, 'blocked': sorted(set(blocked)), 'can_export': not blocked,
                'scope_status': 'partial' if scan['missing'] or input_notes else 'complete', 'notice': NOTICE}
        plan['token'] = _plan_token(plan)
        store.put(PLANS, key, plan)
        return _public(plan)


def validate_plan(store, plan_id, token, own_job=None):
    plan = store.get(PLANS, plan_id)
    if not isinstance(plan, dict) or not isinstance(token, str) or token != plan.get('token') or token != _plan_token(plan):
        raise ValueError('Export preview changed or expired. Review the isolate export again.')
    if not plan.get('can_export') or plan.get('blocked'):
        raise ValueError('Resolve the blocked export preview before copying files.')
    if plan.get('consumed_by') and plan['consumed_by'] != own_job:
        raise ValueError('This export preview was already used. Create a fresh preview for another copy.')
    parent, identity = _destination(plan['destination_parent'])
    if identity != plan['destination_parent_identity']:
        raise ValueError('The destination folder was replaced. Review the export again.')
    target = _absolute(plan['destination'])
    if target.parent != parent or target.exists() or target.is_symlink():
        raise ValueError('The export destination already exists or changed. No files were overwritten.')
    _idle(store, own_job)
    current = _inventory(store, plan['sample_id'])
    if current.get('signature') != plan['inventory'].get('signature'):
        raise ValueError('The isolate records changed. Review the export again.')
    scan = _scan(current, store)
    if any(scan[key] != plan[key] for key in ('files', 'aliases', 'directories', 'missing')):
        raise ValueError('The source files changed after review. Review the export again.')
    if shutil.disk_usage(parent).free < plan['total_bytes'] + RESERVE_BYTES:
        raise ValueError('Destination free space changed; there is not enough room for this export.')
    return plan


def receipt(store, key):
    value = store.get(RECEIPTS, key)
    if not isinstance(value, dict):
        raise ValueError('Export receipt was not found.')
    return value


@contextmanager
def _parent_fd(root_fd, relative, create=False):
    parts = _relative(relative).parts
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _mkdir(root_fd, relative):
    with _parent_fd(root_fd, relative + '/placeholder', create=True):
        pass


def _write_bytes(root_fd, relative, data):
    if isinstance(data, str):
        data = data.encode()
    if not isinstance(data, bytes) or len(data) > MAX_GENERATED_BYTES:
        raise ValueError('Generated export document exceeds the supported size.')
    with _parent_fd(root_fd, relative, create=True) as (fd, name):
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(out)
    expected = hashlib.sha256(data).hexdigest()
    _verify(root_fd, relative, expected, len(data))
    return {'relative': relative, 'bytes': len(data), 'sha256': expected, 'kind': 'generated'}


def _verify(root_fd, relative, expected, size, check=lambda: None):
    with _parent_fd(root_fd, relative) as (fd, name):
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(source)
            if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                raise ValueError('Destination verification failed: ' + relative)
            digest = hashlib.sha256()
            while True:
                check()
                chunk = os.read(source, CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
            if digest.hexdigest() != expected or _stat(os.fstat(source)) != _stat(info):
                raise ValueError('Destination verification failed: ' + relative)
        finally:
            os.close(source)


def _copy(root_fd, row, check, progress=lambda size: None):
    source = _absolute(row['source'])
    if _stat(source.lstat()) != row['lexical_identity'] or str(source.resolve(strict=True)) != row['resolved_source']:
        raise ValueError('Source changed during export: ' + str(source))
    with _open(Path(row['resolved_source'])) as input_fd:
        if _stat(os.fstat(input_fd)) != row['identity']:
            raise ValueError('Source changed during export: ' + str(source))
        with _parent_fd(root_fd, row['relative'], create=True) as (fd, name):
            output_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            digest, written = hashlib.sha256(), 0
            try:
                while True:
                    check()
                    chunk = os.read(input_fd, CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        count = os.write(output_fd, view)
                        view = view[count:]
                    written += len(chunk)
                    progress(len(chunk))
                os.fsync(output_fd)
            finally:
                os.close(output_fd)
        if written != row['bytes'] or _stat(os.fstat(input_fd)) != row['identity'] or _stat(source.lstat()) != row['lexical_identity']:
            raise ValueError('Source changed while copying: ' + str(source))
    expected = digest.hexdigest()
    _verify(root_fd, row['relative'], expected, written, check)
    return {'relative': row['relative'], 'source': row['source'], 'resolved_source': row['resolved_source'],
            'bytes': written, 'sha256': expected, 'kind': 'source', 'materialized_link': row['linked']}


def _generated(store, inventory, sample_id, warnings):
    yield 'records/isolate-inventory.json', _json(inventory)
    yield 'README.txt', (NOTICE + '\n\nThis is a portable evidence archive, not a relocated runnable Bactologix workspace. '
                        'Repeated staged input links are stored once; manifest.json maps aliases to exported files. '
                        'Open index.html to browse the package. Original path text is retained for provenance. '
                        'Only EXPORT-COMPLETE.json establishes that this copy attempt passed local verification. '
                        'A partial scope can be verified but still has unavailable historical files; read the missing list.\n')
    from . import methods
    for run in inventory.get('runs', []):
        rid = str(_relative(run['id']))
        if '/' in rid:
            raise ValueError('Invalid run identifier in export metadata.')
        try:
            value = methods.download(store, run, 'zip')
            yield 'methods/' + rid + '/reproducibility.zip', base64.b64decode(value['base64'], validate=True)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            message = 'Methods unavailable for ' + rid + ': ' + str(exc)
            warnings.append(message)
            yield 'methods/' + rid + '/UNAVAILABLE.txt', message + '\n'
    from . import sample_history
    for name, data in sample_history.bundle_files(store, sample_id).items():
        yield 'history/' + str(_relative(name)), data


def _index(plan, files, warnings):
    esc = html.escape
    groups = []
    for title, prefix in [('Sample history', 'history/'), ('Methods and citations', 'methods/'), ('Reports', None)]:
        rows = [f for f in files if f['relative'].startswith(prefix)] if prefix else [f for f in files if f['relative'].lower().endswith(('.html', '.pdf')) and not f['relative'].startswith(('history/', 'methods/'))]
        groups.append('<section><h2>' + title + '</h2>' + ('<ul>' + ''.join('<li><a href="' + quote(f['relative'], safe='/') + '">' + esc(f['relative']) + '</a></li>' for f in rows) + '</ul>' if rows else '<p>No files recorded.</p>') + '</section>')
    rows = ''.join('<tr><td><a href="' + quote(f['relative'], safe='/') + '">' + esc(f['relative']) + '</a></td><td>' + str(f['bytes']) + '</td></tr>' for f in files)
    aliases = ''.join('<li>' + esc(row['relative']) + ' → <a href="' + quote(row['target_relative'], safe='/') + '">' + esc(row['target_relative']) + '</a></li>' for row in plan['aliases'])
    notices = list(dict.fromkeys([*warnings, *plan['missing']]))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'">'
            '<title>Isolate export — ' + esc(plan['sample_id']) + '</title><style>body{font:16px/1.6 system-ui;margin:0;background:#f3f7f7;color:#18363d}main{max-width:1100px;margin:auto;padding:40px 24px}header,section,details{background:white;border:1px solid #d4e2e2;border-radius:12px;padding:24px;margin-bottom:20px}h1{margin:0}h2{font-size:20px}a{color:#08726e;overflow-wrap:anywhere}td{padding:8px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}small{color:#516970}.notice{border-left:5px solid #bc872c}@media print{body{background:white}main{padding:0}details{display:block}}</style></head><body><main><header><small>BACTOLOGIX · PORTABLE ISOLATE PACKAGE</small><h1>' + esc(plan['sample_id']) + '</h1><p>Reviewed scope: <strong>' + esc(plan['scope_status']) + '</strong>. See EXPORT-COMPLETE.json and manifest.json for final verification.</p><p>' + esc(NOTICE) + '</p></header>'
            + ('<section class="notice"><h2>Review notes</h2><ul>' + ''.join('<li>' + esc(n) + '</li>' for n in notices) + '</ul></section>' if notices else '')
            + ''.join(groups) + ('<details><summary>Repeated input links — stored once</summary><ul>' + aliases + '</ul></details>' if aliases else '')
            + '<details><summary>All exported files and recorded locations</summary><p><a href="manifest.json">Manifest and source path map</a> · <a href="SHA256SUMS">Checksums</a> · <a href="records/isolate-inventory.json">Recorded run metadata</a></p><table><thead><tr><th>File</th><th>Bytes</th></tr></thead><tbody>' + rows + '</tbody></table></details><p>Powered by GenoMix Human Link.</p></main></body></html>')


def _verify_target(plan, root_fd):
    parent, identity = _destination(plan['destination_parent'])
    if identity != plan['destination_parent_identity']:
        raise ValueError('The destination folder moved or was replaced during export.')
    with _open(Path(plan['destination'])) as fd:
        original, current = os.fstat(root_fd), os.fstat(fd)
        if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('The export package folder was replaced during export.')


def execute(ctx):
    """Copy one reviewed plan; retain a partial directory on interruption."""
    from .storage_guard import exclusive
    from .engine import Cancelled
    store, jid = ctx.store, ctx.job['id']
    if store.get(RECEIPTS, jid):
        raise ValueError('This export attempt already has a receipt. Review a new destination for another attempt.')
    record = {'id': jid, 'job_id': jid, 'plan_id': ctx.job['payload'].get('plan_id'), 'created': now(),
              'status': 'running', 'verification': 'pending', 'cloud_verification': 'not-verified',
              'copied_files': 0, 'copied_bytes': 0, 'total_files': 0, 'total_bytes': 0, 'warnings': []}
    store.put(RECEIPTS, jid, record)
    root_fd = None
    try:
        with exclusive(store):
            ctx.check_cancel()
            plan = validate_plan(store, ctx.job['payload'].get('plan_id'), ctx.job['payload'].get('token'), jid)
            record.update(sample_id=plan['sample_id'], destination=plan['destination'], destination_kind=plan['destination_kind'],
                          scope_status=plan['scope_status'], total_files=plan['file_count'], total_bytes=plan['total_bytes'],
                          alias_count=plan['alias_count'], warnings=list(plan['warnings']), missing=list(plan['missing']))
            store.put(RECEIPTS, jid, record)
            with _open(Path(plan['destination_parent'])) as parent_fd:
                actual = os.fstat(parent_fd)
                if {'dev': actual.st_dev, 'ino': actual.st_ino} != plan['destination_parent_identity']:
                    raise ValueError('Destination changed before export started.')
                name = Path(plan['destination']).name
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                root_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            plan['consumed_by'] = jid
            store.put(PLANS, plan['id'], plan)
            ctx.write('Copying reviewed isolate files to ' + plan['destination'])
            for row in plan['directories']:
                _mkdir(root_fd, row['relative'])
            verified = []
            last_progress = time.monotonic()
            def progress(size):
                nonlocal last_progress
                record['copied_bytes'] += size
                if time.monotonic() - last_progress >= 1:
                    store.put(RECEIPTS, jid, record)
                    last_progress = time.monotonic()
            for row in plan['files']:
                ctx.check_cancel()
                record['current_file'] = row['relative']
                verified.append(_copy(root_fd, row, ctx.check_cancel, progress))
                record['copied_files'] += 1
                store.put(RECEIPTS, jid, record)
            record['input_verification'] = _input_checks(plan['inventory'], verified, aliases=plan['aliases'])
            input_notes = _input_notes(record['input_verification'])
            record['warnings'] = list(dict.fromkeys(record['warnings'] + input_notes))
            record['missing'] = sorted(set(record['missing'] + input_notes))
            for relative, data in _generated(store, plan['inventory'], plan['sample_id'], record['warnings']):
                ctx.check_cancel()
                verified.append(_write_bytes(root_fd, relative, data))
            current = _inventory(store, plan['sample_id'])
            if current.get('signature') != plan['inventory'].get('signature'):
                raise ValueError('Isolate records changed during export; no completion marker was written.')
            scan = _scan(current, store)
            if any(scan[key] != plan[key] for key in ('files', 'aliases', 'directories', 'missing')):
                raise ValueError('Source files changed during export; no completion marker was written.')
            unavailable = [warning for warning in record['warnings'] if warning.startswith('Methods unavailable for ')]
            record['missing'] = sorted(set(record['missing'] + unavailable))
            if record['missing']:
                record['scope_status'] = 'partial'
            verified.append(_write_bytes(root_fd, 'index.html', _index({**plan, 'scope_status': record['scope_status'], 'missing': record['missing']}, verified, record['warnings'])))
            aliases = [{k: r[k] for k in ('source', 'resolved_source', 'relative', 'target_relative', 'bytes')} for r in plan['aliases']]
            source_map = [{'source': r['source'], 'relative': r['relative'], 'target_relative': r['relative']} for r in plan['files']] + aliases
            manifest = {'schema_version': 'bactologix-isolate-export-v1', 'sample_id': plan['sample_id'],
                        'created': now(), 'plan_id': plan['id'], 'job_id': jid, 'scope_status': record['scope_status'],
                        'verification': 'verified-local', 'cloud_verification': 'not-verified',
                        'notice': NOTICE, 'missing': record['missing'], 'warnings': record['warnings'],
                        'inventory_signature': current.get('signature'), 'files': verified,
                        'aliases': aliases, 'source_map': source_map, 'input_verification': record['input_verification']}
            manifest_row = _write_bytes(root_fd, 'manifest.json', _json(manifest))
            verified.append(manifest_row)
            checksum_row = _write_bytes(root_fd, 'SHA256SUMS', ''.join(f"{row['sha256']}  {row['relative']}\n" for row in verified))
            ctx.check_cancel()
            for row in verified + [checksum_row]:
                _verify(root_fd, row['relative'], row['sha256'], row['bytes'], ctx.check_cancel)
            final_inventory = _inventory(store, plan['sample_id'])
            final_scan = _scan(final_inventory, store)
            if (final_inventory.get('signature') != plan['inventory'].get('signature')
                    or any(final_scan[key] != plan[key] for key in ('files', 'aliases', 'directories', 'missing'))):
                raise ValueError('Source files or records changed during verification; the package is incomplete.')
            _verify_target(plan, root_fd)
            ctx.check_cancel()
            marker = {'schema_version': 'bactologix-isolate-export-completion-v1', 'job_id': jid,
                      'verified_at': now(), 'scope_status': record['scope_status'], 'verification': 'verified-local',
                      'missing': record['missing'],
                      'cloud_verification': 'not-verified', 'manifest_sha256': manifest_row['sha256'],
                      'checksums_sha256': checksum_row['sha256']}
            _write_bytes(root_fd, 'EXPORT-COMPLETE.json', _json(marker))
            os.fsync(root_fd)
            record.update(status='completed', verification='verified-local', finished=now(),
                          manifest_sha256=manifest_row['sha256'], index=str(Path(plan['destination']) / 'index.html'))
            record.pop('current_file', None)
            store.put(RECEIPTS, jid, record)
            ctx.write('Local copy verified. Original files retained. Cloud upload was not verified.')
            return record
    except BaseException as exc:
        if root_fd is not None:
            try:
                os.unlink('EXPORT-COMPLETE.json', dir_fd=root_fd)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                record['warnings'].append('Could not remove the completion marker after failure: ' + str(cleanup_error) + '. Treat this export attempt as incomplete.')
        record.update(status='cancelled' if isinstance(exc, Cancelled) else 'failed', verification='incomplete',
                      error=str(exc), finished=now())
        store.put(RECEIPTS, jid, record)
        raise
    finally:
        if root_fd is not None:
            os.close(root_fd)
