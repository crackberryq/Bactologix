"""User-selected isolate locations, verified exports and portable history."""
from __future__ import annotations

import base64
from pathlib import Path

from .local import local_path


ACTIONS = frozenset({
    'isolate_details', 'open_isolate_folder', 'preview_isolate_export',
    'start_isolate_export', 'isolate_export_receipt', 'open_isolate_export',
    'download_isolate_history',
})


def _absolute_folder(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        return None
    path = Path(value)
    return path if path.is_absolute() and '..' not in path.parts else None


def _analysis_folder(store, doc, run):
    """Validate the recorded output, including explicitly shared BaSIS outputs."""
    path = _absolute_folder(run.get('output'))
    rid = run['id']
    folder = _absolute_folder(run.get('folder'))
    if Path(rid).name != rid or folder != Path(store.home) / 'runs' / rid:
        return path, 'The saved analysis location is not a managed run folder.'
    owner = run
    if run.get('run_type') == 'followup':
        owner = next((row for row in doc['runs'] if row['id'] == run.get('parent')), None)
        linked = any(edge['source'] == run.get('parent') and edge['target'] == rid
                     and edge['relation'] == 'parent' for edge in doc['edges'])
        if (not owner or not linked or run.get('tool') != 'basis' or owner.get('tool') != 'basis'
                or owner.get('run_type') == 'followup' or path != _absolute_folder(owner.get('output'))):
            return path, 'The shared analysis folder has no verified BaSIS parent relationship.'
    owner_folder = Path(store.home) / 'runs' / owner['id']
    sample_id = owner.get('sample', {}).get('id')
    if (not isinstance(sample_id, str) or Path(sample_id).name != sample_id
            or _absolute_folder(owner.get('folder')) != owner_folder or path is None
            or path != owner_folder / 'outputs' / sample_id
            or path == _absolute_folder(owner.get('sample', {}).get('path'))):
        return path, 'The saved output is not the recorded isolate analysis folder.'
    try:
        if any(part.is_symlink() for part in (path, *path.parents)):
            return path, 'The analysis folder contains a linked path and cannot be opened safely.'
        if not path.is_dir():
            return path, 'The analysis folder is missing or no longer a directory.'
    except OSError:
        return path, 'The analysis folder is currently inaccessible.'
    return path, None


def _analysis_choices(store, doc):
    visibility = {row['id']: row['body'] for row in doc['records']
                  if row['kind'] == 'run_visibility' and isinstance(row['body'], dict)}
    groups, warnings = {}, []
    for run in doc['runs']:
        path, problem = _analysis_folder(store, doc, run)
        location = str(path) if path is not None else ''
        # Missing locations are individual records, not one implied shared folder.
        key = location or ('unrecorded', run['id'])
        choice = groups.setdefault(key, {'path': location, 'run_id': run['id'], 'runs': [], 'available': False})
        choice['runs'].append({'id': run['id'], 'tool': run.get('tool'),
                              'label': run.get('label') or run['id'],
                              'archived': bool(visibility.get(run['id'], {}).get('archived'))})
        if problem:
            warnings.append(run['id'] + ': ' + problem)
        elif not choice['available']:
            choice.update(available=True, run_id=run['id'])
    if not doc['runs']:
        warnings.append('No saved analyses are linked to this isolate yet. Original inputs remain in their separate input folder.')
    elif not any(row['available'] for row in groups.values()):
        warnings.append('No linked analysis output folders are currently available. No input folder was opened.')
    return {'sample_id': doc['sample']['id'], 'folders': list(groups.values()), 'warnings': warnings}


def export_history(store):
    """Include queued/interrupted attempts even when copying never made a receipt."""
    from . import isolate_export
    rows = []
    for job in store.jobs():
        if job['kind'] != 'isolate_export':
            continue
        saved = store.get('isolate_export_receipts', job['id'])
        if saved:
            row = isolate_export.receipt(store, job['id'])
        else:
            plan = store.get('isolate_export_plans', job['payload'].get('plan_id'), {})
            row = {
                'id': job['id'], 'sample_id': plan.get('sample_id'),
                'destination': plan.get('destination', ''),
                'destination_kind': plan.get('destination_kind', 'folder'),
                'status': job['status'], 'verification': 'pending',
                'scope_status': plan.get('scope_status', 'unknown'),
                'copied_files': 0, 'copied_bytes': 0,
                'total_files': plan.get('file_count', 0), 'total_bytes': plan.get('total_bytes', 0),
                'warnings': ['No copy receipt is available for this attempt.'],
                'error': job.get('error'), 'cloud_verification': 'not-verified',
            }
        row = {**row, 'job_status': job['status'], 'created': job['created']}
        if row.get('status') in {'running', 'queued'} and job['status'] in {'failed', 'cancelled', 'interrupted'}:
            row.update(status=job['status'], verification='incomplete', error=job.get('error') or row.get('error'))
        rows.append(row)
        if len(rows) >= 30:
            break
    return rows


def dispatch(store, kind, payload, enqueue):
    from . import isolate_inventory, isolate_export, sample_history
    from .launcher import open_folder

    p = payload
    if kind == 'isolate_details':
        doc = isolate_inventory.build(store, p.get('id'))
        return {'isolate': {**doc, 'history': sample_history.build(store, p.get('id'))}, 'keep_modal': True}
    if kind == 'open_isolate_folder':
        doc = isolate_inventory.build(store, p.get('id'))
        if p.get('kind') == 'inputs':
            path = doc['sample']['path']
        elif p.get('kind') == 'analysis':
            choices = _analysis_choices(store, doc)
            available = [row for row in choices['folders'] if row['available']]
            if len(choices['folders']) != 1 or len(available) != 1 or choices['warnings']:
                return {'keep_modal': True, 'isolate_folder_choices': choices}
            path = available[0]['path']
        elif p.get('kind') == 'run':
            run = next((r for r in doc['runs'] if r['id'] == p.get('run_id')), None)
            if not run:
                raise ValueError('Choose an analysis linked to this isolate.')
            path, problem = _analysis_folder(store, doc, run)
            if problem:
                raise ValueError('Analysis folder unavailable: ' + problem)
        else:
            raise ValueError('Choose the analysis folders, input folder or a linked analysis folder.')
        try:
            folder = open_folder(Path(path))
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ValueError('The selected folder is no longer available. Refresh the isolate folders.') from exc
        return {'message': 'Requested the file manager on the computer running Bactologix: ' + folder, 'keep_modal': True}
    if kind == 'preview_isolate_export':
        destination = p.get('destination')
        if not isinstance(destination, str) or not destination.strip():
            raise ValueError('Choose an existing destination folder.')
        plan = isolate_export.preview(store, p.get('id'), str(local_path(destination)), p.get('destination_kind', 'folder'))
        return {'isolate_export_plan': plan, 'keep_modal': True}
    if kind == 'start_isolate_export':
        isolate_export.validate_plan(store, p.get('plan_id'), p.get('token'))
        jid = enqueue(store, 'isolate_export', {'plan_id': p.get('plan_id'), 'token': p.get('token')})
        return {'message': 'Isolate copy and verification queued. Original files are retained.', 'job_id': jid, 'navigate': 'jobs'}
    if kind == 'isolate_export_receipt':
        row = next((r for r in export_history(store) if r['id'] == p.get('id')), None)
        if row is None:
            row = isolate_export.receipt(store, p.get('id'))
        return {'isolate_export_receipt': row, 'keep_modal': True}
    if kind == 'open_isolate_export':
        row = isolate_export.receipt(store, p.get('id'))
        path = row.get('destination')
        if not path:
            raise ValueError('This export has no recorded destination.')
        folder = open_folder(Path(path))
        return {'message': 'Requested the export folder in the file manager: ' + folder, 'keep_modal': True}
    if kind == 'download_isolate_history':
        fmt = p.get('format')
        types = {'svg': 'image/svg+xml', 'pdf': 'application/pdf', 'json': 'application/json', 'html': 'text/html'}
        if fmt not in types:
            raise ValueError('Choose SVG, PDF, JSON or HTML for the sample history.')
        files = sample_history.bundle_files(store, p.get('id'))
        names = [name for name in files if name.endswith('.' + fmt)]
        if len(names) != 1:
            raise ValueError('The requested history document is unavailable.')
        content = files[names[0]]
        if isinstance(content, str):
            content = content.encode('utf-8')
        return {'download': {'name': names[0], 'type': types[fmt],
                             'base64': base64.b64encode(content).decode('ascii')}, 'keep_modal': True}
    raise ValueError('Unknown isolate action.')
