"""HybAs v2 manifest registration and immutable run input records.

Schema validator vendored from Bactomics_HybAs workflow/lib/input_manifest.py
(MIT, same project). It validates declarations, not biological suitability.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .hybas_manifest_schema import load_manifest, validate_manifest
from .store import now


def paths(sample):
    return [Path(group[key]) for lib in sample['libraries'] for group in lib['read_groups']
            for key in ('r1', 'r2', 'reads') if key in group]


def review(path):
    from .runs import absolute_path, valid_id, snapshot_files
    path = absolute_path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Manifest exceeds 2 MB')
    validated = load_manifest(path)
    samples = validated['manifest']['samples']
    if len(samples) > 500:
        raise ValueError('Import at most 500 samples per manifest')
    seen = set()
    rows = []
    for sample, selection in zip(samples, validated['selections']):
        sid = valid_id(sample['sample_id'])
        if sid.casefold() in seen:
            raise ValueError('Sample IDs must also be unique ignoring case')
        seen.add(sid.casefold())
        for lib in sample['libraries']:
            if lib['platform'] == 'illumina':
                ordinary = lib['library_type'] == 'paired_end' and lib['orientation'] == 'fr'
                method = lib.get('preprocessing', 'fastp' if ordinary else None)
                if method is None or method == 'fastp' and not ordinary:
                    raise ValueError(sid + ': non-FR/mate-pair inputs require explicit pretrimmed or Nextera preprocessing')
        inputs = paths(sample)
        if any(not p.is_file() or p.stat().st_size == 0 for p in inputs):
            raise ValueError(sid + ': every declared FASTQ must be a readable, nonempty file')
        rows.append({'id': sid, 'path': str(path.parent), 'mode': selection['selected_mode'],
                     'input_manifest': deepcopy(sample), 'manifest_source': validated['source'],
                     'inputs': snapshot_files(inputs), 'selection': selection})
    token = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return {'rows': rows, 'token': token, 'path': str(path), 'notice':
            'Declarations and file availability checked. A compatible HybAs alpha and native dry-run are required; sequence content is checked during execution.'}


def commit(store, path, token):
    result = review(path)
    if result['token'] != token:
        raise ValueError('Manifest or input files changed. Preview again before importing.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = {json.loads(row[0])['id'].casefold() for row in db.execute("SELECT body FROM items WHERE kind='samples'")}
        if any(row['id'].casefold() in existing for row in result['rows']):
            raise ValueError('A sample ID is already registered. No samples were imported.')
        for row in result['rows']:
            row = {**row, 'created': now()}
            db.execute("INSERT INTO items VALUES ('samples',?,?)", (row['id'], json.dumps(row)))
    return result['rows']


def require_adapter(store, tool, installed):
    from .release_contract import approval
    row = approval(store, tool, installed['version']['commit']) if tool == 'hybas' else None
    if not row or row['configuration_schema'] != 'hybas-v2':
        raise ValueError('Manifest inputs require a reviewed HybAs v2 adapter. Select a compatible alpha in Install & update.')


def stage(sample, destination):
    from .runs import snapshot_files
    document = {'schema_version': 1, 'samples': [sample['input_manifest']]}
    validated = validate_manifest(document, base_dir=Path(sample['path']))
    files = paths(validated['manifest']['samples'][0])
    current = snapshot_files(files)
    if current != sample['inputs']:
        raise ValueError('Manifest inputs changed since registration. Register the changed data again.')
    destination.mkdir(parents=True)
    return current


def freeze(sample, folder):
    path = folder / 'inputs.manifest.json'
    document = {'schema_version': 1, 'samples': [sample['input_manifest']]}
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'source': sample['manifest_source']}


def verify(run):
    record = run.get('frozen_manifest')
    if run['sample'].get('input_manifest') and not record:
        raise ValueError('Saved manifest identity is missing. Create a new draft.')
    if record:
        path = Path(run['folder']) / 'inputs.manifest.json'
        if path.is_symlink() or record['path'] != str(path) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Saved input manifest changed. Create a new draft.')
