"""New report editions from frozen evidence; no workflow or database execution."""
import hashlib
import json
from pathlib import Path
from .store import now
from .catalog import supported
from .release_contract import preflight

LIMIT = 100 * 1024 * 1024


def digest(path):
    from .results import read_file
    raw = read_file(path, LIMIT)
    return raw, hashlib.sha256(raw).hexdigest()


def renderer_identity(source, tool):
    root = Path(source) / ('src/micpscope' if tool == 'micpscope' else 'basis')
    if not (root / 'report.py').is_file():
        raise ValueError('This installed version has no supported saved-data renderer. Update this pipeline first.')
    hashes = {}
    for path in sorted(root.rglob('*.py')):
        hashes[str(path.relative_to(root))] = digest(path)[1]
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def inspect(store, rid, own_job=None):
    run = store.get('runs', rid)
    if not run:
        raise ValueError('Choose an existing run.')
    plan = {'run_id': rid, 'available': False, 'mode': 'unavailable', 'reasons': [], 'notes': [],
            'analysis_version': run.get('version', run.get('commit', 'Not recorded')),
            'analysis_commit': run.get('commit'), 'tool': run['tool']}
    if run['tool'] not in {'micpscope', 'basis'}:
        plan['reasons'].append('HybAs/MultiQC does not yet have a supported saved-data refresh adapter. The original report remains available.')
        return plan
    if run.get('run_type') == 'followup' and store.get('followup_output_owner', run.get('parent')) != rid:
        plan['reasons'].append('This follow-up no longer owns the shared evidence. Open the current owning run.')
        return plan
    for job in store.jobs():
        if job['id'] != own_job and job['status'] in {'queued', 'running'} and job['kind'] in {'workflow', 'report_refresh'}:
            other = store.get('runs', job['payload'].get('run_id'))
            if other and Path(other['output']).resolve() == Path(run['output']).resolve():
                plan['reasons'].append('Finish queued or running work sharing these outputs before refreshing the report.')
                return plan
    tool = run['tool']
    item = store.get('installs', store.get('settings', 'active-' + tool) or '')
    if not item or not item.get('ready') or not item.get('controller'):
        plan['reasons'].append('Select a ready installed pipeline version in Install & update first.')
        return plan
    plan.update(installation=item['id'], renderer_version=item['version'].get('ref'), renderer_commit=item['version']['commit'],
                source=item['source'], controller=item['controller'])
    if not supported(tool, plan['renderer_commit'], store):
        plan['reasons'].append('The selected renderer has no reviewed compatibility contract.')
        return plan
    relative = 'micpscope/results.json' if tool == 'micpscope' else ('safety/deepsafe-report/evidence.json' if run.get('run_type') == 'followup' else 'safety/evidence.json')
    evidence = Path(run['output']) / relative
    plan['evidence'] = str(evidence)
    try:
        raw, sha = digest(evidence)
        doc = json.loads(raw)
        if tool == 'micpscope':
            if not isinstance(doc, dict) or doc.get('schema_version') != '1.3' or not isinstance(doc.get('proteins'), list) or not isinstance(doc.get('provenance'), dict):
                raise ValueError('Unsupported GeneScope results schema; no report was changed.')
            plan['analysis_version'] = doc.get('software_version', plan['analysis_version'])
            if not doc.get('context', {}).get('features'):
                plan['notes'].append('No saved gene coordinates: the genome explorer cannot show gene locations. Adding coordinates requires a separate analysis step.')
            if not doc.get('reference_search'):
                plan['notes'].append('No saved database search: refreshing does not apply the currently selected database.')
        else:
            if not isinstance(doc, dict) or doc.get('schema_version') != 'basis-report-1' or not isinstance(doc.get('run'), dict) or any(not isinstance(doc.get(k), list) for k in ('samples', 'records', 'deep_records')):
                raise ValueError('Unsupported BaSIS evidence schema; no report was changed.')
            plan['analysis_version'] = doc['run'].get('version', plan['analysis_version'])
            plan['notes'].append('Tree and genome panels use saved evidence and available artifacts. Refresh cannot generate a missing tree or restore missing source files.')
        preflight(store, tool, plan['renderer_commit'], item['source'])
        plan.update(evidence_sha256=sha, renderer_sha256=renderer_identity(item['source'], tool), available=True, mode='report-only')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        plan['reasons'].append('Saved-data refresh unavailable: ' + str(exc))
    plan['notes'].append('No new database search, threshold change, prediction or native analysis will run. Reporting summaries may be recalculated from saved evidence.')
    return plan


def request(store, rid, evidence_sha256, renderer_sha256):
    plan = inspect(store, rid)
    if not plan['available']:
        raise ValueError(' '.join(plan['reasons']))
    if plan['evidence_sha256'] != evidence_sha256 or plan['renderer_sha256'] != renderer_sha256:
        raise ValueError('Saved data or renderer changed. Review the refresh preview again.')
    return plan


def execute(ctx):
    from .engine import find_conda
    plan = ctx.job['payload']
    current = inspect(ctx.store, plan['run_id'], ctx.job['id'])
    keys = ('evidence_sha256', 'renderer_sha256', 'renderer_commit', 'installation', 'source', 'controller')
    if not current['available'] or any(current.get(k) != plan.get(k) for k in keys):
        raise ValueError('Evidence or renderer changed since preview. Review and request a new report refresh.')
    run = ctx.store.get('runs', plan['run_id'])
    folder = ctx.store.home / 'report-refreshes' / ctx.job['id']
    folder.mkdir(parents=True, exist_ok=False)
    raw, sha = digest(plan['evidence'])
    if sha != plan['evidence_sha256']:
        raise ValueError('Evidence changed before the saved snapshot was captured.')
    snapshot = folder / 'source-evidence.json'
    snapshot.write_bytes(raw)
    plan_file = folder / 'refresh-plan.json'
    plan_file.write_text(json.dumps(plan, indent=2))
    ctx.write('Report-only refresh. Original evidence, analyses and report remain unchanged.')
    script = Path(__file__).parent / 'data' / 'render_saved_report.py'
    ctx.command([find_conda(ctx.store), 'run', '--no-capture-output', '-p', plan['controller'], 'python', '-B', str(script),
                 str(snapshot), str(plan_file), str(folder / 'edition')], cwd=folder)
    if digest(plan['evidence'])[1] != sha or renderer_identity(plan['source'], plan['tool']) != plan['renderer_sha256']:
        raise ValueError('Source data or renderer changed while rendering. Output retained for diagnostics, not registered as a completed edition.')
    output = folder / 'edition' / 'report.html'
    if not output.is_file() or not output.stat().st_size:
        raise ValueError('Renderer did not produce a report.')
    ctx.check_cancel()
    record = {**plan, 'id': ctx.job['id'], 'created_at': now(), 'report_only': True, 'source_unchanged': True,
              'report_sha256': digest(output)[1], 'path': str(output)}
    (folder / 'provenance.json').write_text(json.dumps(record, indent=2) + '\n')
    ctx.store.put('report_refreshes', ctx.job['id'], record)
    ctx.store.put('reports', ctx.job['id'], {'id': ctx.job['id'], 'name': 'Refreshed report · ' + str(plan['renderer_version']),
                  'path': str(output), 'sample': run['sample']['id'], 'run': run['id'], 'run_status': 'Report-only',
                  'refresh': True, 'analysis_version': plan['analysis_version'], 'renderer_version': plan['renderer_version'],
                  'producing_job': ctx.job['id']})
    ctx.write('New report edition saved: ' + str(output))
