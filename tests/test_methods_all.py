"""All-pipeline publication exports distinguish metadata from executed work."""
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from bactologix import methods
from bactologix.report_evidence import identity as report_identity
from test_methods import run, basis_run
from test_native import workspace, installation, sample


HYBAS_NEARBETA = 'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d'
HYBAS_DEV = '08c315479f5493ce0b1f44ba26da3c7f2e0d748b'
BASIS_NEARBETA = '6128b06279fec2f51015a76f798d0364a4b81602'


def finish(store, run, *, capture=True):
    run['completed'] = '2026-11-09T20:00:00+00:00'
    jid = store.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    store.update_job(jid, status='succeeded')
    store.put('runs', run['id'], run)
    if capture:
        methods.capture(store, run, jid)
    return jid


def hybas_output(run, **values):
    folder = Path(run['output'], 'metadata')
    folder.mkdir(exist_ok=True)
    doc = {'isolate': run['sample']['id'], 'workflow_version': 'fixture-version',
           'run_mode': run['sample']['mode'], 'executed_stages': ['medaka'],
           'skipped_stages': {}, 'final_source_stage': 'medaka'}
    doc.update(values)
    (folder / 'run_summary.json').write_text(json.dumps(doc))
    (folder / 'workflow_provenance.tsv').write_text(
        'field\tvalue\nconfig_sha256\t' + run['config_sha256'] + '\nsource_sha256\t' +
        hashlib.sha256(Path(run['folder'], 'workflow/Hybas.smk').read_bytes()).hexdigest() + '\n')
    (folder / 'tool_versions.tsv').write_text('tool\tversion\nsnakemake\t9.6.2\nunicycler\t0.5.1\n')
    return folder


def basis_output(run, *, rows=None, large=False, view='screening', **overrides):
    rows = rows if rows is not None else [
        {'sample_id': run['sample']['id'], 'module': 'amrfinder',
         'execution_status': 'completed', 'tool_version': '4.0.3',
         'database_version': '2026-01-01.1', 'complete': True,
         'module_decision': {'requested': True, 'scope_incomplete': False}}]
    doc = {'schema_version': 'basis-report-1',
           'run': {'version': '0.4.0a6', 'run_id': 'native-separate-namespace',
                   'completeness': 'completed_requested_scope', 'report_view': view},
           'samples': [{'sample_id': run['sample']['id']}],
           'records': rows, 'deep_records': []}
    doc.update(overrides)
    if large:
        doc['records'][0]['findings'] = [{'sequence': 'A' * 30000} for _ in range(300)]
    rel = 'safety/deepsafe-report/evidence.json' if run.get('run_type') == 'followup' else 'safety/evidence.json'
    path = Path(run['output'], rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    return path, doc


def failed_size_snapshot(store, run, jid):
    rel = 'safety/deepsafe-report/evidence.json' if run.get('run_type') == 'followup' else 'safety/evidence.json'
    receipt = {'schema': 1, 'run_id': run['id'], 'job_id': jid,
               **{k: run[k] for k in ('completed', 'config_sha256', 'workflow_sha256', 'commit')},
               'captured_at': run['completed'], 'identification': run.get('identification'),
               'evidence': {'files': [], 'versions': {}, 'records': [], 'decisions': {},
                            'warnings': ['Could not read ' + rel + ': Methods evidence exceeds 8 MB: ' + rel]}}
    store.put('methods_evidence', run['id'], receipt)
    store.put('methods_attempt_evidence', jid, receipt)
    return receipt


@pytest.mark.parametrize('tool', ['hybas', 'basis'])
def test_unknown_source_keeps_native_versions_without_borrowing_configured_methods(workspace, run, monkeypatch, tool):
    if tool == 'basis':
        run = basis_run(run, monkeypatch)
        basis_output(run)
        expected = {'basis': '0.4.0a6', 'amrfinder': '4.0.3'}
    else:
        hybas_output(run)
        expected = {'snakemake': '9.6.2', 'unicycler': '0.5.1'}
    run['commit'] = 'f' * 40
    finish(workspace, run)
    doc = methods.build(workspace, run)
    versions = {row['tool']: row['version'] for row in doc['software']}
    assert all(versions[name] == version for name, version in expected.items())
    assert all(row['origin'] == 'Saved run' for row in doc['parameters'])
    assert any('No source-matched workflow description' in note for note in doc['warnings'])
    assert not any('was configured with Unicycler' in text or 'requested modules were' in text for text in doc['paragraphs'])


def test_hybas_environment_inventory_is_visible_without_execution_claims(workspace, run):
    hybas_output(run)
    finish(workspace, run)
    doc = methods.build(workspace, run)
    row = next(row for row in doc['software'] if row['tool'] == 'unicycler')
    assert row['version'] == '0.5.1'
    assert 'environment package' in row['scope'] and 'execution not established' in row['scope']
    assert 'unicycler 0.5.1' not in '\n'.join(doc['paragraphs'])


def test_legacy_hybas_snapshot_without_version_scopes_stays_inventory(workspace, run):
    hybas_output(run)
    jid = finish(workspace, run)
    receipt = workspace.get('methods_evidence', run['id'])
    receipt['evidence'].pop('version_scopes')
    workspace.put('methods_evidence', run['id'], receipt)
    workspace.put('methods_attempt_evidence', jid, receipt)
    doc = methods.build(workspace, run)
    measured = [row for row in doc['software'] if row['version'] != methods.UNKNOWN]
    assert {row['tool'] for row in measured} == {'snakemake', 'unicycler'}
    assert all('environment package' in row['scope'] and 'execution not established' in row['scope'] for row in measured)
    assert 'Recorded software versions:' not in '\n'.join(doc['paragraphs'])
    assert workspace.get('methods_evidence', run['id']) == receipt
    assert workspace.get('methods_attempt_evidence', jid) == receipt


def test_historical_hybas_identity_conflict_cannot_supply_displayed_metadata(workspace, run):
    hybas_output(run, medaka_model_effective='conflicting-effective-model')
    jid = finish(workspace, run)
    receipt = workspace.get('methods_evidence', run['id'])
    receipt['evidence'].pop('version_scopes')
    receipt['evidence']['warnings'] = ['HybAs summary sample identity does not match this run.']
    receipt['evidence']['versions']['unicycler'] = '99.0-conflicting-inventory'
    receipt['evidence']['database_hashes'] = [{'database_type': 'kraken2',
        'path': '/conflicting-database', 'sha256': 'a' * 64}]
    workspace.put('methods_evidence', run['id'], receipt)
    workspace.put('methods_attempt_evidence', jid, receipt)
    doc = methods.build(workspace, run)
    assert all(row['version'] == methods.UNKNOWN for row in doc['software'])
    assert not doc['databases']
    assert not any('retained native record' in paragraph for paragraph in doc['paragraphs'])
    for value in ('99.0-conflicting-inventory', 'conflicting-effective-model', '/conflicting-database'):
        assert value not in doc['text']
    assert any('sample identity does not match' in warning for warning in doc['warnings'])
    assert doc['evidence'] == receipt['evidence']
    artifact = methods.download(workspace, run, 'zip')
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(artifact['base64']))) as archive:
        provenance = json.loads(archive.read('methods-provenance.json'))
        assert provenance['evidence'] == receipt['evidence']
    assert workspace.get('methods_evidence', run['id']) == receipt
    assert workspace.get('methods_attempt_evidence', jid) == receipt


@pytest.mark.parametrize('effective', ['recorded-model', None])
def test_hybas_never_substitutes_requested_medaka_model_for_effective_model(workspace, run, effective):
    hybas_output(run, medaka_model='requested-model', medaka_model_requested='requested-model',
                 medaka_model_effective=effective, medaka_model_sha256='a' * 64 if effective else None)
    finish(workspace, run)
    doc = methods.build(workspace, run)
    record = next(p for p in doc['paragraphs'] if 'retained native record' in p)
    assert 'requested-model' not in record
    assert 'effective Medaka model: ' + (effective or methods.UNKNOWN) in record
    assert 'requested-model' in '\n'.join(doc['execution'])


def test_reviewed_regular_hybas_template_remains_available(workspace, run):
    run['commit'] = HYBAS_NEARBETA
    doc = methods.build(workspace, run)
    assert 'assembly quality assessment with QUAST' in doc['text']
    assert not any('No source-matched workflow description' in note for note in doc['warnings'])


@pytest.mark.parametrize('assembler', ['flye', 'spades'])
def test_dev_hybas_template_does_not_apply_unicycler_defaults(workspace, run, assembler):
    run['commit'] = HYBAS_DEV
    hybas_output(run, assembler=assembler, input_strategy='recorded-strategy', long_read_platform='ont')
    finish(workspace, run)
    doc = methods.build(workspace, run)
    prose = '\n'.join(doc['paragraphs'])
    assert 'experimental HybAs 10' in prose
    assert 'Unicycler' not in prose
    assert 'recorded assembler: ' + assembler in prose
    assert all(row['origin'] == 'Saved run' for row in doc['parameters'])


def test_current_basis_source_uses_reviewed_scope_default(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch)
    run['commit'] = BASIS_NEARBETA
    doc = methods.build(workspace, run)
    setting = next(row for row in doc['parameters'] if row['setting'] == 'deep.aware.scope')
    assert setting['value'] == 'amrfinder-review' and setting['origin'] == 'Pinned workflow default'
    assert 'scope amrfinder-review' in doc['text']
    assert not any('No source-matched workflow description' in note for note in doc['warnings'])


@pytest.mark.parametrize('partial', [
    {'scope_incomplete': True}, {'module_decision': {'requested': True, 'scope_incomplete': True}},
    {'complete': False}, {'upstream_incomplete': ['input-stage']},
])
def test_completed_basis_process_cannot_become_complete_assessment_scope(workspace, run, monkeypatch, partial):
    run = basis_run(run, monkeypatch)
    row = {'sample_id': run['sample']['id'], 'module': 'amrfinder', 'execution_status': 'completed',
           'tool_version': '4.0.3', 'complete': True}
    row.update(partial)
    basis_output(run, rows=[row])
    finish(workspace, run)
    doc = methods.build(workspace, run)
    completion = next(p for p in doc['paragraphs'] if 'retained module records' in p)
    assert 'completion of no modules' in completion
    assert 'AMRFinderPlus' in '\n'.join(doc['execution'])


@pytest.mark.parametrize('snapshot', ['completion', 'legacy', 'recovered'])
def test_basis_native_run_incompleteness_overrides_successful_module_status(workspace, run, monkeypatch, snapshot):
    run = basis_run(run, monkeypatch)
    path, native = basis_output(run)
    native['run'].update(completeness='incomplete',
                         incomplete_modules=[run['sample']['id'] + ':amrfinder'])
    path.write_text(json.dumps(native))
    jid = finish(workspace, run, capture=snapshot == 'completion')
    if snapshot == 'recovered':
        failed_size_snapshot(workspace, run, jid)
    doc = methods.build(workspace, run)
    if snapshot == 'recovered':
        assert any('AMRFinderPlus: native execution status=completed; complete scope=not established' in line
                   for line in doc['execution'])
    else:
        assert not any('completion of AMRFinderPlus' in paragraph or 'modules complete: AMRFinderPlus' in paragraph
                       for paragraph in doc['paragraphs'])
        assert any('no modules' in paragraph for paragraph in doc['paragraphs'])


@pytest.mark.parametrize('summary,gap', [
    ({'selected_execution': 'incomplete', 'deferred_candidates': 0}, 'selected execution incomplete'),
    ({'selected_execution': 'completed', 'deferred_candidates': 1}, 'deferred candidates'),
])
def test_basis_completion_summary_cannot_be_promoted_to_complete_scope(workspace, run, monkeypatch, summary, gap):
    run = basis_run(run, monkeypatch, deep={'enabled': True})
    row = {'sample_id': run['sample']['id'], 'module': 'deepsafe',
           'execution_status': 'completed', 'complete': True,
           'upstream_incomplete': [], 'completion_summary': summary}
    basis_output(run, rows=[], view='combined', deep_records=[row])
    finish(workspace, run)
    doc = methods.build(workspace, run)
    assert any('completion of no modules' in paragraph for paragraph in doc['paragraphs'])
    assert any('Assessment scope for deepsafe: incomplete' in line and gap in line for line in doc['execution'])
    assert doc['evidence']['records'][0]['completion_summary'] == summary


def test_reused_basis_result_is_labelled_as_reused_work(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch)
    row = {'sample_id': run['sample']['id'], 'module': 'amrfinder', 'execution_status': 'completed',
           'tool_version': '4.0.3', 'complete': True, 'reused': True}
    basis_output(run, rows=[row])
    finish(workspace, run)
    doc = methods.build(workspace, run)
    assert 'reused' in '\n'.join(doc['execution']).lower()
    assert 'reused' in next(row['scope'].lower() for row in doc['software'] if row['tool'] == 'amrfinder')


@pytest.mark.parametrize('conflict', ['sample', 'source', 'report_view'])
def test_conflicting_basis_bundle_cannot_supply_software_or_execution(workspace, run, monkeypatch, conflict):
    run = basis_run(run, monkeypatch)
    path, doc = basis_output(run)
    if conflict == 'sample':
        doc['records'].append({'sample_id': 'another', 'module': 'rgi', 'execution_status': 'completed', 'tool_version': '6.0'})
    elif conflict == 'source':
        doc['provenance'] = {'source_commit': 'f' * 40}
    else:
        doc['run']['report_view'] = 'deep'
    path.write_text(json.dumps(doc))
    finish(workspace, run)
    result = methods.build(workspace, run)
    assert not result['evidence']['records'] and not result['evidence']['versions']
    assert all(row['version'] == methods.UNKNOWN for row in result['software'])
    assert not any('completion of AMRFinderPlus' in p for p in result['paragraphs'])
    assert any('Could not collect BaSIS' in warning for warning in result['warnings'])


def test_legacy_basis_records_remain_explicitly_retrospective(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch)
    basis_output(run)
    finish(workspace, run, capture=False)
    doc = methods.build(workspace, run)
    assert doc['evidence_origin'] == 'Legacy output metadata read at export'
    assert all('execution attribution unverified' in row['scope'] for row in doc['software'] if row['version'] != methods.UNKNOWN)
    assert 'Recorded software versions:' not in '\n'.join(doc['paragraphs'])
    assert any('execution attribution unverified' in p for p in doc['paragraphs'] if 'mark these modules complete' in p)


def test_large_basis_snapshot_recovers_without_rewriting_historical_evidence(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch)
    path, _ = basis_output(run, large=True)
    assert path.stat().st_size > 8 * 1024 * 1024
    jid = finish(workspace, run, capture=False)
    original = failed_size_snapshot(workspace, run, jid)
    doc = methods.build(workspace, run)
    supplement = doc['supplemental_evidence']
    assert supplement['status'] == 'unverified'
    assert supplement['evidence']['versions']['amrfinder'] == '4.0.3'
    assert supplement['evidence']['files'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert doc['evidence'] == original['evidence']
    assert not any('retained module records report completion' in p for p in doc['paragraphs'])
    assert any('execution attribution unverified' in line for line in doc['execution'])
    result = methods.download(workspace, run, 'zip')
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['base64']))) as archive:
        provenance = json.loads(archive.read('methods-provenance.json'))
        assert provenance['supplemental_evidence']['original_completion_snapshot'] == original
    assert workspace.get('methods_evidence', run['id']) == original
    assert workspace.get('methods_attempt_evidence', jid) == original


def test_current_basis_output_must_match_bound_attempt_before_recovery(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch)
    path, bundle = basis_output(run)
    jid = finish(workspace, run, capture=False)
    original = failed_size_snapshot(workspace, run, jid)
    workspace.put('report_evidence', jid, {'schema': 1, 'job_id': jid, 'run': report_identity(run),
        'before': {'files': {'safety/evidence.json': {'state': 'missing'}}},
        'after': {'files': {'safety/evidence.json': {'state': 'available', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}}})
    assert methods.build(workspace, run)['supplemental_evidence']['status'] == 'recovered'
    bundle['records'][0]['tool_version'] = '99.0'
    path.write_text(json.dumps(bundle))
    doc = methods.build(workspace, run)
    assert doc['supplemental_evidence']['status'] == 'blocked'
    assert '99.0' not in doc['text']
    assert workspace.get('methods_evidence', run['id']) == original


def test_database_fingerprints_are_available_in_human_readable_exports(workspace, run):
    hybas_output(run)
    path = Path(run['output']).parent / 'provenance/databases/database_hashes.tsv'
    path.parent.mkdir(parents=True)
    path.write_text('database_type\tpath\tsha256\nkraken2\t/recorded/db/hash.k2d\t' + 'a' * 64 + '\n')
    finish(workspace, run)
    doc = methods.build(workspace, run)
    assert any('a' * 64 in row and 'kraken2' in row for row in doc['databases'])
    for fmt in ('txt', 'html'):
        artifact = methods.download(workspace, run, fmt)
        assert 'a' * 64 in artifact['text'] and 'Database identities' in artifact['text']
