"""Content identity is checked before execution, without rereading inputs on UI polls."""
import copy
import hashlib
import os
from pathlib import Path

import pytest

from test_native import workspace, installation, sample
from bactologix import runs, readiness, manifest_inputs


def replace_preserving_metadata(path):
    stat = path.stat()
    data = path.read_bytes()
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_input_hashes_block_same_size_same_mtime_execution(workspace, installation, sample):
    run = runs.create_run(workspace, 'hybas', sample, 'threads: 1\n')
    source = Path(run['inputs'][0]['path'])
    assert run['inputs'][0]['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    run['dry_run_ok'] = True
    workspace.put('runs', run['id'], run)
    replace_preserving_metadata(source)
    runs.check_inputs(run, full=False)
    with pytest.raises(ValueError, match='SHA-256 content mismatch'):
        runs.validate_launch(workspace, run['id'], 'run')


def test_quick_readiness_does_not_reread_reads(workspace, installation, sample, monkeypatch):
    run = runs.create_run(workspace, 'hybas', sample, 'threads: 1\n')
    def unexpected(path):
        pytest.fail('UI readiness must not hash sequencing inputs')
    monkeypatch.setattr(runs, '_fingerprint', unexpected)
    panel = readiness.panel(workspace, run)
    row = next(row for row in panel['rows'] if row['id'] == 'inputs')
    assert row['state'] == 'Unchanged'
    assert 'quick check does not reread input content' in row['text']


def test_legacy_snapshots_remain_explicitly_unverified(workspace, installation, sample):
    parent = runs.create_run(workspace, 'hybas', sample, 'threads: 1\n')
    for row in parent['inputs']:
        row.pop('sha256')
    workspace.put('runs', parent['id'], parent)
    saved = copy.deepcopy(parent)
    runs.check_inputs(parent)
    assert 'original content identity unverified' in runs.input_identity(parent)
    child = runs.clone_runs(workspace, parent['id'], strategies=['none'])[0]
    assert all(row['sha256'] for row in child['inputs'])
    assert workspace.get('runs', parent['id']) == saved
    assert runs.matching_inputs(parent['inputs'], child['inputs'])


def test_replacement_during_streaming_hash_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'reads.fastq'
    path.write_bytes(b'AAAA')
    original = runs.hashlib.sha256
    class ReplacingHash:
        def __init__(self): self.digest = original()
        def update(self, block):
            self.digest.update(block)
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(b'BBBB')
            replacement.replace(path)
        def hexdigest(self): return self.digest.hexdigest()
    monkeypatch.setattr(runs.hashlib, 'sha256', ReplacingHash)
    with pytest.raises(ValueError, match='changed while its content'):
        runs.snapshot_files([path])


def test_legacy_manifest_registration_can_make_new_content_verified_draft(tmp_path):
    from test_manifest_inputs import manifest
    path, _ = manifest(tmp_path)
    sample = manifest_inputs.review(path)['rows'][0]
    for row in sample['inputs']:
        row.pop('sha256')
    original = copy.deepcopy(sample)
    current = manifest_inputs.stage(sample, tmp_path / 'staged')
    assert current[0]['sha256']
    assert sample == original


def test_malformed_content_identity_is_never_downgraded_to_metadata(tmp_path):
    path = tmp_path / 'reads.fastq'
    path.write_bytes(b'AAAA')
    record = runs.snapshot_files([path])[0]
    record['sha256'] = ''
    with pytest.raises(ValueError, match='content identity is invalid'):
        runs.check_inputs({'inputs': [record]}, full=False)


def test_methods_export_keeps_hashes_and_legacy_limits_without_rereading(workspace, installation, sample, monkeypatch):
    from bactologix import methods
    run = runs.create_run(workspace, 'hybas', sample, 'threads: 1\n')
    monkeypatch.setattr(runs, '_fingerprint', lambda path: pytest.fail('Export must use recorded provenance'))
    doc = methods.build(workspace, run)
    assert doc['input_snapshots'] == run['inputs']
    assert 'SHA-256 content fingerprints' in doc['text']
    assert 'Export does not recheck current input contents' in doc['text']
    for row in run['inputs']:
        row.pop('sha256')
    doc = methods.build(workspace, run)
    assert 'original content identity unverified' in doc['text']
