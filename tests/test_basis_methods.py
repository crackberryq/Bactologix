"""BaSIS Methods accepts metadata without importing large findings payloads."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from bactologix import basis_methods, metadata_stream


@pytest.fixture
def run(tmp_path):
    cfg = 'threads: 2\n'
    return {'id': 'run-selected', 'tool': 'basis', 'sample': {'id': 'isolate'},
            'output': str(tmp_path / 'outputs'), 'config': cfg,
            'config_sha256': hashlib.sha256(cfg.encode()).hexdigest(),
            'workflow_sha256': 'b' * 64, 'commit': 'c' * 40,
            'inputs': [{'path': '/recorded/isolate.faa', 'sha256': 'd' * 64}]}


def bundle():
    return {'schema_version': 'basis-report-1',
            'run': {'run_id': 'native-namespace', 'version': '0.4.0a6',
                    'completeness': 'completed_requested_scope', 'report_view': 'screening',
                    'input_validations': {'isolate': {'input_hashes': {'proteins': 'd' * 64}}}},
            'samples': [{'sample_id': 'isolate'}],
            'records': [{'sample_id': 'isolate', 'module': 'amrfinder', 'execution_status': 'completed',
                         'tool_version': '4.0.3', 'database_version': '2026-01-01.1',
                         'module_decision': {'requested': True, 'action': 'run', 'scope_incomplete': False}}],
            'deep_records': []}


def write(run, doc):
    rel = 'safety/deepsafe-report/evidence.json' if run.get('run_type') == 'followup' else 'safety/evidence.json'
    p = Path(run['output']) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding='utf-8')
    return p


def test_large_report_discards_findings_and_hashes_complete_file(run):
    doc = bundle()
    doc['records'][0]['findings'] = [{'sequence': 'A' * 30000} for _ in range(300)]
    doc['run']['finished_at'] = '2026-11-09T12:00:00Z'
    path = write(run, doc)
    assert path.stat().st_size > 8 * 1024 * 1024
    result = basis_methods.collect(run)
    assert not result['warnings']
    assert result['versions'] == {'basis': '0.4.0a6', 'amrfinder': '4.0.3'}
    assert result['files'] == [{'path': 'safety/evidence.json',
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                               'bytes': path.stat().st_size}]
    assert len(json.dumps(result)) < 5000
    assert 'findings' not in result['metadata']['records'][0]
    assert 'protein' in ' '.join(result['identity']['checks'])


def test_partial_completed_scope_is_preserved(run):
    doc = bundle()
    doc['run'].update(completeness='incomplete', incomplete_modules=['isolate:amrfinder'])
    doc['records'][0].update(scope_incomplete=True, reason='unassessed input')
    doc['records'][0]['module_decision']['scope_incomplete'] = True
    write(run, doc)
    result = basis_methods.collect(run)
    assert not result['warnings']
    assert result['native_run']['completeness'] == 'incomplete'
    assert result['records'][0]['scope_incomplete']
    assert result['records'][0]['module_decision']['scope_incomplete']
    assert result['records'][0]['reason'] == 'unassessed input'


@pytest.mark.parametrize('location,field,value', [
    ('sample', 'sample_id', 'another'),
    ('record', 'sample_id', 'unknown'),
    ('provenance', 'source_commit', 'f' * 40),
    ('provenance', 'config_sha256', 'f' * 64),
    ('bactologix', 'workflow_sha256', 'f' * 64),
    ('bactologix', 'run_id', 'another'),
    ('input', 'proteins', 'f' * 64),
    ('record_input', 'proteins', 'f' * 64),
])
def test_known_identity_conflicts_do_not_supply_versions(run, location, field, value):
    doc = bundle()
    target = {'sample': doc['samples'][0], 'record': doc['records'][0],
              'provenance': doc.setdefault('provenance', {}),
              'bactologix': doc.setdefault('bactologix', {}),
              'input': doc['run']['input_validations']['isolate']['input_hashes'],
              'record_input': doc['records'][0].setdefault('input_hashes', {})}[location]
    target[field] = value
    write(run, doc)
    result = basis_methods.collect(run)
    assert not result['records'] and not result['versions'] and not result['files']
    assert result['warnings']


def test_native_run_id_is_a_separate_namespace(run):
    doc = bundle()
    doc['run']['run_id'] = 'unrelated-native-run-id'
    write(run, doc)
    assert not basis_methods.collect(run)['warnings']


@pytest.mark.parametrize('followup,view', [(False, 'deep'), (True, 'screening'), (True, 'combined')])
def test_wrong_report_view_is_rejected(run, followup, view):
    if followup:
        run['run_type'] = 'followup'
    doc = bundle()
    doc['run']['report_view'] = view
    write(run, doc)
    result = basis_methods.collect(run)
    assert not result['records']
    assert 'report view' in result['warnings'][0]


def test_followup_retains_inherited_screening_identity_but_exports_only_deep_records(run):
    run['run_type'] = 'followup'
    doc = bundle()
    doc['run'].update(report_view='deep', options={'deep': True}, screening_version='0.4.0a2')
    doc['deep_records'] = [{'sample_id': 'isolate', 'module': 'deepsafe', 'execution_status': 'completed',
                            'complete': False, 'upstream_incomplete': [],
                            'completion_summary': {'selected_execution': 'incomplete', 'deferred_candidates': 1},
                            'candidates': [{'sequence': 'DO NOT EXPORT', 'analyses': {}}]}]
    write(run, doc)
    result = basis_methods.collect(run)
    assert not result['warnings']
    assert [row['module'] for row in result['records']] == ['deepsafe']
    assert 'amrfinder' not in result['versions']
    assert result['metadata']['records'][0]['module'] == 'amrfinder'
    assert result['records'][0]['complete'] is False
    assert 'DO NOT EXPORT' not in json.dumps(result)


@pytest.mark.parametrize('change', ['duplicate_module', 'wrong_schema', 'malformed_decision', 'string_boolean', 'wrong_record_list', 'hidden_deep'])
def test_invalid_record_structure_is_rejected(run, change):
    doc = bundle()
    if change == 'duplicate_module':
        doc['records'].append(copy.deepcopy(doc['records'][0]))
    elif change == 'wrong_schema':
        doc['schema_version'] = 'unsupported'
    elif change == 'malformed_decision':
        doc['records'][0]['module_decision'] = []
    elif change == 'string_boolean':
        doc['records'][0]['scope_incomplete'] = 'false'
    elif change == 'wrong_record_list':
        doc['deep_records'] = doc.pop('records')
        doc['records'] = []
    else:
        doc['deep_records'] = [{'sample_id': 'isolate', 'module': 'deepsafe', 'execution_status': 'completed'}]
    write(run, doc)
    result = basis_methods.collect(run)
    assert not result['versions'] and result['warnings']


@pytest.mark.parametrize('tail', [' trailing', '\n{}', ',"sample_id":"duplicate"}'])
def test_complete_json_is_validated_after_metadata(run, tail):
    path = write(run, bundle())
    content = path.read_text()
    path.write_text((content[:-1] if tail.startswith(',') else content) + tail)
    result = basis_methods.collect(run)
    if tail.startswith(','):
        # Duplicate unrelated root identity still must be rejected.
        path.write_text(content[:-1] + ',"schema_version":"basis-report-1"}')
        result = basis_methods.collect(run)
    assert not result['versions'] and result['warnings']


def test_missing_and_linked_evidence_are_distinct(run, tmp_path):
    missing = basis_methods.collect(run)
    assert 'is missing' in missing['warnings'][0]
    p = write(run, bundle())
    target = tmp_path / 'outside.json'
    p.rename(target)
    p.symlink_to(target)
    linked = basis_methods.collect(run)
    assert not linked['files'] and 'is missing' not in linked['warnings'][0]


@pytest.mark.parametrize('limit,value,needle', [('MAX_FILE_BYTES', 10, 'file size'),
                                              ('MAX_METADATA_BYTES', 10, 'retained metadata'),
                                              ('MAX_DEPTH', 2, 'nesting'),
                                              ('MAX_TOKEN_BYTES', 5, 'scalar token'),
                                              ('MAX_KEY_BYTES', 10, 'object key')])
def test_streaming_limits_apply_before_materializing_payload(run, monkeypatch, limit, value, needle):
    write(run, bundle())
    monkeypatch.setattr(metadata_stream, limit, value)
    result = basis_methods.collect(run)
    assert not result['versions']
    assert needle in result['warnings'][0]


def test_changed_file_is_not_a_stable_metadata_snapshot(run, monkeypatch):
    p = write(run, bundle())
    original = metadata_stream._project
    def replace(reader, fields):
        result = original(reader, fields)
        p.write_text(p.read_text() + ' ')
        return result
    monkeypatch.setattr(metadata_stream, '_project', replace)
    result = basis_methods.collect(run)
    assert not result['versions'] and 'changed' in result['warnings'][0]


def test_projection_preserves_empty_and_malformed_containers_for_validation(run):
    path = write(run, {'samples': [None, {}, {'sample_id': 'sample'}], 'records': 3})
    value, fingerprint = metadata_stream.read(Path(run['output']), 'safety/evidence.json',
        {('samples', '*', 'sample_id'), ('records', '*', 'module')})
    assert value == {'samples': [None, {}, {'sample_id': 'sample'}], 'records': 3}
    assert fingerprint['bytes'] == path.stat().st_size


def test_native_source_fingerprints_are_kept_without_following_paths(run):
    doc = bundle()
    doc['run']['evidence_sources'] = [{'module': 'amrfinder', 'path': '/outside/not-readable.json', 'sha256': 'a' * 64}]
    doc['run']['report_inputs'] = {'config': {'path': '/outside/config.json', 'sha256': 'b' * 64}}
    write(run, doc)
    result = basis_methods.collect(run)
    assert not result['warnings']
    assert result['native_run']['evidence_sources'] == doc['run']['evidence_sources']
    assert result['native_run']['report_inputs'] == doc['run']['report_inputs']
    assert result['versions'] == {'basis': '0.4.0a6', 'amrfinder': '4.0.3'}


def test_duplicate_keys_in_discarded_bulk_are_still_rejected(run):
    path = write(run, bundle())
    path.write_text(path.read_text()[:-1] + ',"findings":[{"sequence":"a","sequence":"b"}]}')
    result = basis_methods.collect(run)
    assert not result['versions']
    assert 'Duplicate key' in result['warnings'][0]


@pytest.mark.parametrize('relative', ['../private.json', '/etc/passwd'])
def test_projection_cannot_read_outside_root(run, relative):
    with pytest.raises(ValueError, match='Invalid Methods evidence path'):
        metadata_stream.read(run['output'], relative, {('schema_version',)})


def test_nonregular_evidence_cannot_block_reader(run):
    path = Path(run['output']) / 'safety/evidence.json'
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    result = basis_methods.collect(run)
    assert not result['versions'] and 'not a regular file' in result['warnings'][0]
