"""GeneScope publication exports retain evidence origin across legacy recovery."""
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from bactologix import methods
from bactologix.report_evidence import identity as execution_identity
from bactologix.ui_backend import dispatch
from test_methods import run, rewrite
from test_native import workspace, installation, sample


A13 = 'b47b16c286051ec841e7cdb593350004eb028475'
A14 = 'af9502c3040258a69b1efc662a6f11b95e46b729'
A16 = 'f46cc9feeb30cd89b36876b7d341ed6750e19ce3'
SIZE_WARNING = 'Could not read micpscope/results.json: Methods evidence exceeds 8 MB: micpscope/results.json'
FALSE_SAMPLE_WARNING = 'GeneScope results are missing or belong to a different sample.'


@pytest.fixture
def genescope(workspace, run):
    changed = rewrite(run, {'threads': 12, 'memory_mb': 28672,
                            'reference_database': '/saved/application/database',
                            'reference_database_sha256': 'e' * 64})
    changed.update(tool='micpscope', commit=A14, cores=12, memory_mb=28672)
    workspace.put('runs', changed['id'], changed)
    return changed


def result_file(run, *, large=False, **overrides):
    # Match native serialization: the runtime metadata follows bulky results.
    document = {'schema_version': '1.3', 'sample_id': run['sample']['id'],
                'software_version': '0.1.0a14',
                'proteins': [{'protein_id': str(i), 'sequence': 'M' * 30000}
                             for i in range(300 if large else 1)],
                'provenance': {'python_version': '3.12.9', 'biopython_version': '1.85',
                               'input_sha256': 'a' * 64, 'created_utc': '2026-11-09T18:00:00Z'},
                'reference_search': {'tool_version': 'diamond version 2.1.11',
                                     'database': {'id': 'test-application', 'version': '1',
                                                  'checksums_sha256': 'e' * 64}}}
    document.update(overrides)
    path = Path(run['output'], 'micpscope/results.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


def complete(store, run):
    run['completed'] = '2026-11-09T18:30:00+00:00'
    job = store.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    store.update_job(job, status='succeeded')
    store.put('runs', run['id'], run)
    return job


def legacy_snapshot(store, run, job):
    receipt = {'schema': 1, 'run_id': run['id'], 'job_id': job,
               **{key: run[key] for key in ('completed', 'config_sha256', 'workflow_sha256', 'commit')},
               'identification': run.get('identification'), 'captured_at': run['completed'],
               'evidence': {'files': [], 'versions': {}, 'records': [], 'decisions': {},
                            'warnings': [SIZE_WARNING, FALSE_SAMPLE_WARNING]}}
    store.put('methods_evidence', run['id'], receipt)
    store.put('methods_attempt_evidence', job, receipt)
    return receipt


@pytest.mark.parametrize('commit', [A13, A14, A16])
def test_reviewed_sources_describe_configuration_without_claiming_execution(workspace, genescope, commit):
    genescope['commit'] = commit
    doc = methods.build(workspace, genescope)
    assert doc['state'] == 'Planned methods'
    assert not any('No verified Methods template' in warning for warning in doc['warnings'])
    assert 'DIAMOND' in doc['text'] and 'configured' in doc['text']
    assert '12 CPU cores and 28672 MB' in doc['text']
    settings = {row['setting']: row for row in doc['parameters']}
    assert settings['threads']['origin'] == 'Saved run'
    assert settings['reference_min_identity']['origin'] == 'Pinned workflow default'
    assert doc['execution'] == ['No per-step execution metadata is available for this export.']
    assert all(row['version'] == methods.UNKNOWN for row in doc['software'])


def test_no_reference_database_does_not_configure_diamond(workspace, genescope):
    changed = rewrite(genescope, {'threads': 12, 'memory_mb': 28672})
    doc = methods.build(workspace, changed)
    assert 'DIAMOND' not in '\n'.join(doc['paragraphs'])
    assert not any(row['tool'] == 'diamond' for row in doc['software'])


@pytest.mark.parametrize('change', ['unknown_commit', 'changed_workflow'])
def test_unreviewed_source_cannot_borrow_template(workspace, genescope, change):
    if change == 'unknown_commit':
        genescope['commit'] = 'f' * 40
    else:
        Path(genescope['folder'], 'workflow/Hybas.smk').write_text('changed source\n')
    doc = methods.build(workspace, genescope)
    assert any('No verified Methods template' in warning for warning in doc['warnings'])
    assert all(row['origin'] == 'Saved run' for row in doc['parameters'])
    assert 'Biopython' not in '\n'.join(doc['paragraphs'])
    assert 'DIAMOND' not in '\n'.join(doc['paragraphs'])


def test_large_native_output_collects_tail_versions_and_full_file_hash(genescope):
    path = result_file(genescope, large=True)
    assert path.stat().st_size > 8 * 1024 * 1024
    evidence = methods.collect(genescope)
    assert not evidence['warnings']
    assert evidence['versions']['biopython'] == '1.85'
    assert evidence['versions']['python'] == '3.12.9'
    assert evidence['versions']['diamond'] == 'diamond version 2.1.11'
    assert evidence['files'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(json.dumps(evidence)) < 10000
    assert 'proteins' not in evidence['metadata']
    assert 'snakemake' not in evidence['versions']
    assert not evidence['records'] and not evidence['decisions']


@pytest.mark.parametrize('state, expected', [('missing', 'results are missing'),
                                            ('malformed', 'Could not collect'),
                                            ('wrong_sample', 'sample identity')])
def test_collection_errors_do_not_cascade_to_false_sample_warning(genescope, state, expected):
    if state == 'malformed':
        result_file(genescope).write_text('{"schema_version":"1.3"')
    elif state == 'wrong_sample':
        result_file(genescope, sample_id='another-isolate')
    evidence = methods.collect(genescope)
    assert not evidence['versions']
    assert any(expected in warning for warning in evidence['warnings'])
    assert FALSE_SAMPLE_WARNING not in evidence['warnings']
    if state != 'wrong_sample':
        assert not any('different sample' in warning or 'does not match' in warning
                       for warning in evidence['warnings'])


@pytest.mark.parametrize('identity, expected', [
    ({'provenance': {'source_commit': 'f' * 40, 'biopython_version': '1.85'}}, 'does not match'),
    ({'provenance': {'config_sha256': 'f' * 64, 'biopython_version': '1.85'}}, 'does not match'),
    ({'bactologix': {'sample_id': 'another-isolate'}}, 'does not match'),
    ({'provenance': {'bactologix': {'workflow_sha256': 'f' * 64}}}, 'does not match'),
    ({'sample_id': 'unknown'}, 'no verified sample identity'),
    ({'run_id': 'another-run'}, 'different run'),
    ({'provenance': {'bactologix_run_id': 'another-run'}}, 'different run'),
    ({'reference_search': {'input_sha256': 'b' * 64}}, 'different protein input'),
    ({'reference_search': {'database': {'checksums_sha256': 'f' * 64}}}, 'database identity does not match'),
])
def test_identity_conflicts_are_not_discarded_with_bulk_results(genescope, identity, expected):
    result_file(genescope, **identity)
    evidence = methods.collect(genescope)
    assert not evidence['versions']
    assert any(expected in warning for warning in evidence['warnings'])


def test_legacy_ui_and_downloads_recover_separately_without_changing_history(workspace, genescope):
    result_file(genescope, large=True)
    job = complete(workspace, genescope)
    original = legacy_snapshot(workspace, genescope, job)
    doc = dispatch(workspace, 'inspect_methods', {'id': genescope['id']})['methods']
    assert doc['evidence'] == original['evidence']
    assert doc['supplemental_evidence']['status'] == 'unverified'
    assert 'at export' in doc['evidence_origin']
    assert FALSE_SAMPLE_WARNING not in doc['warnings']
    assert SIZE_WARNING not in doc['warnings']
    assert not any('Recorded software versions:' in paragraph for paragraph in doc['paragraphs'])
    versions = {row['tool']: row for row in doc['software']}
    assert versions['biopython']['version'] == '1.85'
    assert 'unverified' in versions['biopython']['scope']
    assert versions['snakemake']['version'] == methods.UNKNOWN
    assert doc['execution'] == ['No per-step execution metadata is available for this export.']
    artifact = dispatch(workspace, 'download_methods', {'id': genescope['id'], 'format': 'zip'})['download']
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(artifact['base64']))) as archive:
        provenance = json.loads(archive.read('methods-provenance.json'))
        assert provenance['evidence'] == original['evidence']
        assert provenance['supplemental_evidence']['original_completion_snapshot'] == original
        assert provenance['supplemental_evidence']['status'] == 'unverified'
        for filename in ('methods.txt', 'methods.md', 'methods.html'):
            content = archive.read(filename).decode()
            assert '1.85' in content and 'execution attribution unverified' in content
            assert FALSE_SAMPLE_WARNING not in content
    assert workspace.get('methods_evidence', genescope['id']) == original
    assert workspace.get('methods_attempt_evidence', job) == original


def test_recovery_attributes_only_content_observed_in_completed_attempt(workspace, genescope):
    path = result_file(genescope)
    job = complete(workspace, genescope)
    original = legacy_snapshot(workspace, genescope, job)
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    workspace.put('report_evidence', job, {
        'schema': 1, 'job_id': job, 'run': execution_identity(genescope),
        'before': {'files': {'micpscope/results.json': {'state': 'missing'}}},
        'after': {'files': {'micpscope/results.json': {'state': 'available', 'sha256': fingerprint}}}})
    doc = methods.build(workspace, genescope)
    assert doc['supplemental_evidence']['status'] == 'recovered'
    assert doc['supplemental_evidence']['attribution']['job_id'] == job
    assert doc['supplemental_evidence']['evidence']['files'][0]['sha256'] == fingerprint
    row = next(row for row in doc['software'] if row['tool'] == 'biopython')
    assert row['version'] == '1.85' and 'matched completed attempt' in row['scope']
    assert 'at export' in doc['evidence_origin']
    assert not doc['evidence']['records'] and not doc['evidence']['versions']
    assert workspace.get('methods_evidence', genescope['id']) == original
    # A later output substitution cannot borrow the matching historical receipt.
    result_file(genescope, software_version='99.9')
    changed = methods.build(workspace, genescope)
    assert changed['supplemental_evidence']['status'] == 'blocked'
    assert '99.9' not in changed['text'] and '1.85' not in changed['text']


def test_healthy_completion_keeps_original_versions_after_output_changes(workspace, genescope):
    result_file(genescope)
    job = complete(workspace, genescope)
    original = methods.capture(workspace, genescope, job)
    result_file(genescope, software_version='99.9', provenance={'biopython_version': '99.9'})
    doc = methods.build(workspace, genescope)
    assert doc['supplemental_evidence'] is None
    assert doc['evidence'] == original['evidence']
    assert '1.85' in doc['text'] and '99.9' not in doc['text']
    assert doc['evidence_origin'] == 'Completion-time snapshot'


def test_failed_retry_cannot_recover_completed_methods(workspace, genescope):
    result_file(genescope)
    job = complete(workspace, genescope)
    original = legacy_snapshot(workspace, genescope, job)
    retry = workspace.submit('workflow', {'run_id': genescope['id'], 'action': 'continue'})
    workspace.update_job(retry, status='failed')
    doc = methods.build(workspace, genescope)
    assert doc['state'] == 'Incomplete execution'
    assert doc['supplemental_evidence'] is None
    assert not doc['evidence']
    assert '1.85' not in doc['text']
    assert workspace.get('methods_attempt_evidence', job) == original


def test_legacy_output_without_snapshot_does_not_acquire_execution_attribution(workspace, genescope):
    result_file(genescope)
    complete(workspace, genescope)
    doc = methods.build(workspace, genescope)
    assert doc['evidence_origin'] == 'Legacy output metadata read at export'
    assert doc['supplemental_evidence'] is None
    assert 'Recorded software versions:' not in doc['text']
    assert all('execution attribution unverified' in row['scope'] for row in doc['software']
               if row['version'] != methods.UNKNOWN)
