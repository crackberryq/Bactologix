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
        elif p.get('kind') == 'run':
            run = next((r for r in doc['runs'] if r['id'] == p.get('run_id')), None)
            if not run:
                raise ValueError('Choose an analysis linked to this isolate.')
            path = run.get('output') or run['folder']
        else:
            raise ValueError('Choose the input folder or a linked analysis folder.')
        folder = open_folder(Path(path))
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
