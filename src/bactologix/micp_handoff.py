"""Explicit HybAs-to-MICP drafts with pinned reference and input provenance."""
import hashlib
import json
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def database_identity(path, expected=None):
    root = Path(path)
    receipt = json.loads((root / 'completed.json').read_text())
    identity = digest(root / 'checksums.json')
    if receipt.get('status') != 'completed' or receipt.get('checksums_sha256') != identity:
        raise ValueError('Reference database completion receipt does not match checksums')
    if expected and expected != identity:
        raise ValueError('Reference database changed; create a new MICP Scope draft')
    checksums = json.loads((root / 'checksums.json').read_text())
    if not isinstance(checksums, dict) or not {'proteins.faa', 'proteins.dmnd', 'reference-manifest.json', 'diamond-version.txt'} <= checksums.keys():
        raise ValueError('Reference database is missing required checksums')
    for name, checksum in checksums.items():
        if Path(name).name != name or name in {'.', '..'}:
            raise ValueError('Unsafe database filename')
        if digest(root / name) != checksum:
            raise ValueError('Reference database checksum mismatch: ' + name)
    if json.loads((root / 'reference-manifest.json').read_text()).get('schema_version') != '1.0':
        raise ValueError('Unsupported reference database schema')
    return identity


def create(store, source_id):
    from .runs import create_run, template, parse_config, run_status, dry_run_current, check_run_files
    source = store.get('runs', source_id)
    if not source or source['tool'] != 'hybas' or run_status(source, store.jobs(), dry_run_current(store, source)) != 'completed':
        raise ValueError('Choose a completed HybAs AWARE run')
    check_run_files(source)
    text = store.get('configs', 'micpscope', template('micpscope'))
    if not parse_config(text).get('reference_database'):
        raise ValueError('Select a completed reference database in MICP Scope resources before creating this draft')
    output, sid = Path(source['output']), source['sample']['id']
    files = [output / 'annotation' / (sid + ext) for ext in ('.faa', '.gff')]
    files.append(output / 'work/final/assembly.final.fasta')
    for path in files:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('HybAs handoff requires completed Prokka annotation and final assembly: ' + str(path))
    handoff = dict(source_run=source['id'], source_commit=source['commit'],
                   files=[dict(path=str(p.resolve()), sha256=digest(p)) for p in files])
    sample = dict(source['sample'], path=source['output'], micp_handoff=handoff)
    # Cross-pipeline provenance is separate from same-pipeline result inheritance.
    return create_run(store, 'micpscope', sample, text, source['cores'], source['memory_mb'],
                      label=source['label'] + ' · MICP Scope')
