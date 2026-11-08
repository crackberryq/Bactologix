"""Compare native evidence with a run without inventing absent provenance."""
import hashlib
from pathlib import Path


def validate_native(run, doc):
    checks, missing = [], []
    expected_sample = run['sample']['id']

    def compare(label, actual, expected):
        if actual in (None, '', 'NA', 'unknown'):
            missing.append(label)
        elif expected in (None, '', 'NA', 'unknown'):
            missing.append(label + ' baseline')
        elif actual != expected:
            raise ValueError('Saved evidence ' + label + ' does not match the selected run.')
        else:
            checks.append(label)

    tool = run['tool']
    if isinstance(run.get('config'), str) and run.get('config_sha256'):
        compare('frozen configuration record', hashlib.sha256(run['config'].encode()).hexdigest(), run['config_sha256'])
    provenance = doc.get('provenance') or {}
    if not isinstance(provenance, dict):
        raise ValueError('Saved evidence provenance must be an object.')
    if tool == 'micpscope':
        compare('sample identity', doc.get('sample_id'), expected_sample)
    elif tool == 'hybas':
        summary = doc.get('summary') if doc.get('schema_version') == 'hybas-report-1' else doc
        compare('sample identity', summary.get('isolate'), expected_sample)
        rows = doc.get('tables', {}).get('workflow_provenance', [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Saved workflow provenance is malformed.')
        for row in rows:
            if row.get('field') == 'config_sha256':
                compare('configuration hash', row.get('value'), run.get('config_sha256'))
    else:
        samples = []
        for key in ('samples', 'records', 'deep_records'):
            rows = doc.get(key, [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('Saved BaSIS ' + key + ' are malformed.')
            samples.extend(row.get('sample_id') for row in rows)
        for key in ('input_validations', 'module_decisions'):
            rows = doc.get('run', {}).get(key, {})
            if not isinstance(rows, dict):
                raise ValueError('Saved BaSIS ' + key + ' are malformed.')
            samples.extend(rows)
        for sid in samples or [None]:
            compare('sample identity', sid, expected_sample)

    for key, expected in (('source_commit', run.get('commit')), ('workflow_commit', run.get('commit')),
                          ('config_sha256', run.get('config_sha256')), ('workflow_sha256', run.get('workflow_sha256'))):
        if key in provenance:
            compare(key, provenance[key], expected)
    # Native BaSIS run.run_id belongs to its own namespace, not the app's.
    for identity in (doc.get('bactologix'), provenance.get('bactologix')):
        if identity is None:
            continue
        if not isinstance(identity, dict):
            raise ValueError('Saved Bactologix identity must be an object.')
        for key, expected in (('run_id', run['id']), ('sample_id', expected_sample), ('commit', run.get('commit')),
                              ('config_sha256', run.get('config_sha256')), ('workflow_sha256', run.get('workflow_sha256'))):
            if key in identity:
                compare('Bactologix ' + key, identity[key], expected)

    by_suffix = {}
    for row in run.get('inputs', []):
        by_suffix.setdefault(Path(row['path']).suffix.lower(), []).append(row.get('sha256'))
    def input_hash(label, value, suffix):
        values = by_suffix.get(suffix, [])
        compare(label, value, values[0] if len(values) == 1 else None)
    if tool == 'micpscope':
        for key, suffix in (('input_sha256', '.faa'), ('assembly_sha256', '.fna')):
            if provenance.get(key):
                input_hash(key, provenance[key], suffix)
    elif tool == 'basis':
        validations = list(doc.get('run', {}).get('input_validations', {}).values())
        validations += [row.get('input_validation', {}) for row in doc.get('samples', [])]
        for validation in validations:
            if not isinstance(validation, dict):
                raise ValueError('Saved input validation is malformed.')
            hashes = validation.get('input_hashes', {})
            if not isinstance(hashes, dict):
                raise ValueError('Saved input hashes are malformed.')
            for key, suffix in (('proteins', '.faa'), ('assembly', '.fna'), ('gff', '.gff'), ('genbank', '.gbk')):
                if hashes.get(key):
                    input_hash(key + ' input hash', hashes[key], suffix)
    return {'checks': sorted(set(checks)), 'missing': sorted(set(missing))}
