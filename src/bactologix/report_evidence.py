"""Content observations associate evidence with attempts, not scientific validity."""
import hashlib
from pathlib import Path
from .store import now

LIMIT = 100 * 1024 * 1024


def paths(run):
    if run['tool'] == 'micpscope':
        return ['micpscope/results.json']
    if run['tool'] == 'hybas':
        return ['reports/hybas/results.json', 'metadata/run_summary.json']
    if run['tool'] == 'basis':
        return ['safety/deepsafe-report/evidence.json' if run.get('run_type') == 'followup' else 'safety/evidence.json']
    return []


def identity(run):
    result = {key: run.get(key) for key in ('id', 'tool', 'commit', 'config_sha256', 'workflow_sha256', 'inputs')}
    result['sample'] = run['sample']['id']
    result['reviewed_decisions'] = {
        'identification': {k: run.get('identification', {}).get(k) for k in ('decision_path', 'decision_sha256', 'selection')}}
    return result


def binding(store, run, relative, sha, jobs):
    expected = identity(run)
    known, matched = [], []
    for job in jobs:
        receipt = store.get('report_evidence', job['id'], {})
        if receipt.get('schema') != 1 or receipt.get('job_id') != job['id'] or receipt.get('run', {}).get('id') != run['id']:
            continue
        after = receipt.get('after', {}).get('files', {}).get(relative, {})
        if after.get('state') != 'available':
            continue
        known.append(receipt)
        if after.get('sha256') != sha:
            continue
        if receipt.get('run') != expected:
            raise ValueError('Saved evidence receipt has different run, source or configuration identity.')
        before = receipt.get('before', {}).get('files', {}).get(relative, {})
        # An unchanged legacy file does not acquire a new source attribution.
        if before.get('state') == 'missing' or before.get('state') == 'available' and before.get('sha256') != sha:
            matched.append(job)
    has_baseline = all(run.get(k) for k in ('commit', 'config_sha256', 'workflow_sha256'))
    if matched and has_baseline:
        job = matched[0]
        return {'state': 'recorded_attempt', 'label': 'Matched recorded execution attempt',
                'job_id': job['id'], 'attempt_status': job['status'], 'commit': run.get('commit')}
    if known and not any(r.get('after', {}).get('files', {}).get(relative, {}).get('sha256') == sha for r in known):
        raise ValueError('Saved evidence content no longer matches any recorded execution attempt.')
    # Legacy completion-only Methods snapshots cannot distinguish newly produced
    # evidence from a pre-existing unchanged file, so do not confer attribution.
    return {'state': 'unverified', 'label': 'Evidence attribution unverified', 'job_id': None,
            'attempt_status': 'unknown', 'commit': None}


def observe(ctx, run, phase):
    from .results import read_file
    try:
        receipt = ctx.store.get('report_evidence', ctx.job['id'], {
            'schema': 1, 'job_id': ctx.job['id'], 'run': identity(run)})
        files = {}
        for relative in paths(run):
            try:
                raw = read_file(Path(run['output']) / relative, LIMIT)
                files[relative] = {'state': 'available', 'sha256': hashlib.sha256(raw).hexdigest()}
            except FileNotFoundError:
                files[relative] = {'state': 'missing'}
            except (OSError, ValueError):
                files[relative] = {'state': 'unavailable'}
        receipt[phase] = {'at': now(), 'files': files}
        ctx.store.put('report_evidence', ctx.job['id'], receipt)
    except Exception:
        try:
            ctx.write('Report evidence content observation unavailable; attribution will remain unverified.')
        except Exception:
            pass
