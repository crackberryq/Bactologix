"""Sample-scoped export inventory from one consistent workspace metadata snapshot.

This module plans locations; it neither follows file links nor copies content.
The export worker must still verify every source and destination while copying.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .runs import parse_config


_KINDS = (
    'samples', 'runs', 'run_visibility', 'followup_output_owner', 'reports',
    'report_refreshes', 'methods_evidence', 'methods_attempt_evidence', 'report_evidence', 'result_observations',
    'job_telemetry', 'job_usage_history', 'installs',
)
_JOB_KINDS = {'workflow', 'check', 'report_refresh'}
_HASH = re.compile(r'[0-9a-f]{64}')
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'{prefix}{number}' for prefix in ('COM', 'LPT') for number in range(1, 10))}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _path(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        return None
    path = Path(value)
    return path if path.is_absolute() and '..' not in path.parts else None


def _component(value):
    value = str(value)
    if (re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value) and not value.endswith('.')
            and value.split('.')[0].upper() not in _RESERVED):
        return value
    short = re.sub(r'[^A-Za-z0-9_-]', '_', value)[:48].strip('_') or 'item'
    return short + '-' + hashlib.sha256(value.encode()).hexdigest()[:12]


def _same_sample(left, right):
    return (isinstance(left, dict) and isinstance(right, dict)
            and left.get('id') == right.get('id')
            and _path(left.get('path')) is not None
            and _path(left.get('path')) == _path(right.get('path')))


def _snapshot(store):
    # The explicit read transaction also covers jobs and queue state. Store.all()
    # calls made separately would observe different workspace revisions.
    with store.connect() as db:
        db.execute('BEGIN')
        marks = ','.join('?' for _ in _KINDS)
        items = {(row['kind'], row['id']): json.loads(row['body']) for row in
                 db.execute(f'SELECT kind,id,body FROM items WHERE kind IN ({marks})', _KINDS)}
        jobs = [dict(row, payload=json.loads(row['payload'])) for row in db.execute(
            "SELECT j.*,COALESCE(q.held,0) AS held,COALESCE(q.position,j.rowid) AS queue_position "
            "FROM jobs j LEFT JOIN job_queue q ON q.job_id=j.id "
            "WHERE j.kind IN ('workflow','check','report_refresh') ORDER BY j.rowid DESC")]
    return items, jobs


def _links(runs):
    """Validate recorded lineage without inferring it from equal sample names."""
    edges, issues = [], []
    for rid, child in sorted(runs.items()):
        sample = child.get('sample', {})
        parent_id = child.get('parent')
        if parent_id:
            parent = runs.get(parent_id)
            parent_sample = parent.get('sample', {}) if parent else {}
            valid = (parent and parent_id != rid and sample.get('id') == parent_sample.get('id')
                     and (_same_sample(sample, parent_sample)
                          or _path(sample.get('path')) is not None
                          and _path(sample.get('path')) == _path(parent.get('output'))))
            if valid:
                edges.append({'source': parent_id, 'target': rid, 'relation': 'parent'})
            else:
                issues.append((rid, parent_id, 'Invalid or missing parent relationship for ' + rid + '.'))
        handoff = sample.get('micp_handoff')
        if isinstance(handoff, dict) and handoff.get('source_run'):
            source_id = handoff['source_run']
            source = runs.get(source_id)
            valid = (source and source_id != rid and source.get('tool') == 'hybas'
                     and child.get('tool') == 'micpscope'
                     and sample.get('id') == source.get('sample', {}).get('id')
                     and _path(sample.get('path')) is not None
                     and _path(sample.get('path')) == _path(source.get('output'))
                     and handoff.get('source_commit') == source.get('commit'))
            if valid:
                edges.append({'source': source_id, 'target': rid, 'relation': 'handoff'})
            else:
                issues.append((rid, source_id, 'Unverified GeneScope handoff for ' + rid + '; no relationship was inferred.'))
        aware = sample.get('aware_provenance')
        if not isinstance(aware, dict):
            continue
        bundle = _path(aware.get('bundle_path'))
        checksum = aware.get('bundle_sha256')
        manifest = _mapping(sample.get('manifest_source'))
        files = aware.get('files')
        verified = (bundle is not None and isinstance(checksum, str) and _HASH.fullmatch(checksum)
                    and _path(sample.get('path')) == bundle.parent
                    and _path(manifest.get('path')) == bundle and manifest.get('sha256') == checksum
                    and isinstance(files, list) and files and all(
                        isinstance(row, dict) and _path(row.get('path')) is not None
                        and _path(row['path']).is_relative_to(bundle.parent)
                        and isinstance(row.get('sha256'), str) and _HASH.fullmatch(row['sha256'])
                        for row in files))
        candidates, matches = [], []
        for source_id, source in runs.items():
            output = _path(source.get('output'))
            if (source_id != rid and source.get('tool') == 'hybas' and output
                    and bundle == output / 'reports/aware/manifest.json'
                    and aware.get('parent_sample') == source.get('sample', {}).get('id')):
                candidates.append(source_id)
                if verified and _mapping(source.get('aware')).get('bundle_sha256') == checksum:
                    matches.append(source_id)
        if len(matches) == 1:
            edges.append({'source': matches[0], 'target': rid, 'relation': 'aware_derived'})
        else:
            for source_id in candidates or [None]:
                issues.append((rid, source_id, 'AWARE ancestry for ' + rid + ' is ambiguous or unverified; no relationship was inferred.'))
    graph = {}
    for edge in edges:
        graph.setdefault(edge['source'], set()).add(edge['target'])
    def reaches(start, target):
        pending, seen = [start], set()
        while pending:
            node = pending.pop()
            if node == target:
                return True
            if node not in seen:
                seen.add(node)
                pending.extend(graph.get(node, ()))
        return False
    valid_edges = []
    for edge in edges:
        if reaches(edge['target'], edge['source']):
            issues.append((edge['target'], edge['source'], 'Cyclic lineage was excluded between ' + edge['source'] + ' and ' + edge['target'] + '.'))
        else:
            valid_edges.append(edge)
    return sorted(valid_edges, key=lambda row: (row['source'], row['target'], row['relation'])), issues


def build(store, sample_id):
    """Return the registered isolate's linked current files and saved history."""
    if not isinstance(sample_id, str):
        raise ValueError('Choose a registered sample.')
    items, all_jobs = _snapshot(store)
    sample = items.get(('samples', sample_id))
    if not isinstance(sample, dict) or sample.get('id') != sample_id or not _path(sample.get('path')):
        raise ValueError('Choose a registered sample with a recorded absolute input path.')
    all_runs = {key: body for (kind, key), body in items.items()
                if kind == 'runs' and isinstance(body, dict) and body.get('id') == key
                and isinstance(body.get('sample'), dict) and isinstance(body['sample'].get('id'), str)
                and _path(body['sample'].get('path')) is not None}
    edges, issues = _links(all_runs)
    selected = {rid for rid, row in all_runs.items() if _same_sample(row.get('sample'), sample)}
    # Include descendants of the selected sample, then their ancestors. An
    # ancestor discovered only for provenance does not pull in sibling isolates.
    changed = True
    while changed:
        before = set(selected)
        for edge in edges:
            if edge['source'] in selected:
                selected.add(edge['target'])
        changed = selected != before
    changed = True
    while changed:
        before = set(selected)
        for edge in edges:
            if edge['target'] in selected:
                selected.add(edge['source'])
        changed = selected != before
    edges = [edge for edge in edges if edge['source'] in selected and edge['target'] in selected]
    runs = [all_runs[rid] for rid in sorted(selected)]
    jobs = [job for job in all_jobs if isinstance(job.get('payload'), dict)
            and job['kind'] in _JOB_KINDS and job['payload'].get('run_id') in selected]
    job_ids = {job['id'] for job in jobs}
    warnings = {message for child, parent, message in issues if child in selected or parent in selected}
    missing, records, roots = set(), [], []
    home = Path(store.home)
    protected = [home / name for name in ('sources', 'controllers', 'envs', 'resources')]
    broad = [home, *(home / name for name in ('runs', 'logs', 'report-refreshes')), *protected]

    def root(value, relative, kind, label, required=True, file_only=False):
        source = _path(value)
        if source is None:
            warnings.add('An invalid or unrecorded path was excluded: ' + label + '.')
            if required:
                missing.add('Unrecorded location: ' + label)
            return
        if (any(path == source or path.is_relative_to(source) for path in broad)
                or not file_only and any(source.is_relative_to(path) for path in protected)):
            warnings.add('A broad workspace or shared software location was excluded: ' + str(source))
            if required:
                missing.add(str(source))
            return
        if file_only and source.is_dir():
            warnings.add('An expected file is a directory and was excluded: ' + str(source))
            missing.add(str(source))
            return
        roots.append({'source': str(source), 'relative': relative, 'kind': kind,
                      'label': label, 'required': bool(required)})
        if not source.exists():
            missing.add(str(source))

    def external_file(value, rid, label, required=True):
        path = _path(value)
        if path is None:
            root(value, 'inputs/external/unknown', 'external_input', label, required, True)
            return
        unique = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        root(str(path), 'inputs/external/' + _component(rid) + '/' + unique + '-' + _component(path.name),
             'external_input', label, required, True)

    root(sample['path'], 'inputs/' + _component(sample_id), 'inputs', 'Registered input folder')
    for row in sample.get('inputs', []) or []:
        if isinstance(row, dict):
            external_file(row.get('path'), 'registered', 'Registered external input')
    for field in ('micp_handoff', 'aware_provenance'):
        for row in _mapping(sample.get(field)).get('files', []) or []:
            if isinstance(row, dict):
                external_file(row.get('path'), 'registered', 'Registered ' + field + ' input')
    if _mapping(sample.get('manifest_source')).get('path'):
        external_file(sample['manifest_source']['path'], 'registered', 'Registered input manifest')
    for run in runs:
        rid, prefix = run['id'], 'analyses/' + _component(run['id'])
        root(run.get('folder'), prefix, 'run', rid + ' saved run')
        root(run.get('output'), prefix + '/external-output', 'output', rid + ' analysis output')
        for field in ('inputs', 'followup_sources'):
            for row in run.get(field, []) or []:
                if isinstance(row, dict):
                    external_file(row.get('path'), rid, rid + ' recorded ' + field)
        run_sample = run.get('sample', {})
        for field in ('micp_handoff', 'aware_provenance'):
            for row in _mapping(run_sample.get(field)).get('files', []) or []:
                if isinstance(row, dict):
                    external_file(row.get('path'), rid, rid + ' ' + field + ' input')
        manifest = run_sample.get('manifest_source')
        if isinstance(manifest, dict) and manifest.get('path'):
            external_file(manifest['path'], rid, rid + ' input manifest')
        if run.get('tool') == 'basis':
            try:
                native = parse_config(run['config']).get('storage', {}).get('native_results_directory')
                output = _path(run.get('output'))
                if native and _path(native) and output:
                    # BaSIS namespaces the configured shared store by the exact
                    # canonical isolate output path; never copy its shared root.
                    payload = _path(native) / hashlib.sha256(str(output).encode()).hexdigest()
                    if payload.exists():
                        root(str(payload), prefix + '/native-results', 'native_results', rid + ' native result payloads')
            except (KeyError, TypeError, AttributeError, ValueError):
                warnings.add('Native-result storage settings could not be inspected for ' + rid + '.')
        if run.get('run_type') == 'followup':
            owner = items.get(('followup_output_owner', run.get('parent')))
            if owner != rid:
                missing.add('Historical follow-up output snapshot for ' + rid + ' is not retained (current owner: ' + str(owner or 'unknown') + ').')
    refreshes = {}
    for (kind, key), body in sorted(items.items()):
        include = False
        if kind == 'samples':
            include = key == sample_id or any(_same_sample(body, run.get('sample')) for run in runs)
        elif kind in {'run_visibility', 'followup_output_owner'}:
            include = key in selected
        elif kind == 'methods_evidence':
            include = key in selected and isinstance(body, dict) and body.get('run_id') == key
        elif kind == 'methods_attempt_evidence':
            include = isinstance(body, dict) and body.get('run_id') in selected and body.get('job_id') == key
        elif kind in {'job_telemetry', 'job_usage_history'}:
            include = key in job_ids
        elif kind in {'report_evidence', 'result_observations'} and isinstance(body, dict):
            bound = _mapping(body.get('run')).get('id') if kind == 'report_evidence' else body.get('run_id')
            recorded_job = body.get('job_id') if kind == 'report_evidence' else body.get('id', key)
            include = bound in selected and recorded_job == key
            if not include and key in job_ids:
                warnings.add('Mismatched evidence metadata was excluded for job ' + key + '.')
        elif kind == 'report_refreshes' and isinstance(body, dict):
            include = body.get('run_id') in selected
            if include:
                refreshes[key] = body
        elif kind == 'reports' and isinstance(body, dict):
            include = body.get('run') in selected
            if include:
                path = _path(body.get('path'))
                root(body.get('path'), 'reports/' + _component(key) + '/' + _component(path.name if path else 'report.html'),
                     'report', 'Registered report ' + key, True, True)
            elif body.get('sample') == sample_id:
                warnings.add('A report with this sample label has no explicit linked run and was excluded.')
        elif kind == 'installs' and key in {run.get('installation') for run in runs} and isinstance(body, dict):
            include = True
            body = {name: body[name] for name in ('id', 'tool', 'version', 'source', 'controller', 'rule_cache') if name in body}
        if include:
            records.append({'kind': kind, 'id': key, 'body': body})
    for key, refresh in sorted(refreshes.items()):
        folder = home / 'report-refreshes' / _component(key)
        report = _path(refresh.get('path'))
        if report and report.is_relative_to(folder):
            root(str(folder), 'report-editions/' + _component(key), 'report_refresh', 'Report edition ' + key)
        else:
            external_file(refresh.get('path'), key, 'External report edition ' + key)
            warnings.add('Only the explicitly recorded report file is included for external report edition ' + key + '.')
    for job in jobs:
        path = store.log_path(job['id'])
        if path.exists() or job.get('started'):
            root(str(path), 'logs/' + _component(job['id']) + '.log', 'log', job['id'] + ' application log', False, True)
    # Longest coverage is selected first so a parent folder covers its nested
    # outputs once. References retain each requested logical location for an index.
    priority = {'inputs': 0, 'run': 1, 'output': 2, 'native_results': 3, 'report_refresh': 4,
                'report': 5, 'external_input': 6, 'log': 7}
    retained = []
    for row in sorted(roots, key=lambda row: (len(Path(row['source']).parts), row['source'], priority[row['kind']], row['relative'])):
        parent = next((old for old in retained if Path(row['source']).is_relative_to(Path(old['source']))), None)
        if parent:
            parent['required'] = parent['required'] or row['required']
            parent.setdefault('references', []).append({key: row[key] for key in ('source', 'relative', 'kind', 'label')})
        else:
            retained.append(row)
    for row in retained:
        if row['kind'] == 'report':
            warnings.add('Only the recorded file is included for ' + row['label'] + '; external linked assets are not inferred or copied.')
    warnings.add('Shared environments and reference databases are not copied; saved settings and recorded identities are retained.')
    warnings.add('This inventory preserves current files and saved records; overwritten historical file versions cannot be reconstructed.')
    document = {'schema_version': 1, 'sample': sample, 'runs': runs, 'edges': edges, 'jobs': jobs,
                'records': records, 'roots': retained, 'warnings': sorted(warnings), 'missing': sorted(missing)}
    document['signature'] = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return document
