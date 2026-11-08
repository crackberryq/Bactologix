"""Execution success cannot stand in for recorded assessment coverage."""
import json
from pathlib import Path

import pytest

from bactologix import assessment
from bactologix.runs import create_run
from bactologix.ui_backend import snapshot
from test_native import workspace, installation, sample


@pytest.fixture
def run(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, 'run_kraken: false\n')
    run.update(tool='basis', completed='2026-11-08T20:00:00+00:00')
    workspace.put('runs', run['id'], run)
    return run


def write(run, completeness='incomplete', status='not_assessed', **extra):
    document = {'schema_version': 'basis-report-1',
                'run': {'completeness': completeness, 'enabled_modules': ['icescreen']},
                'samples': [{'sample_id': run['sample']['id']}],
                'records': [{'sample_id': run['sample']['id'], 'module': 'icescreen',
                             'execution_status': status, 'reason': 'scope_unavailable',
                             'module_decision': {'requested': True}}], 'deep_records': []}
    document.update(extra)
    path = Path(run['output'], 'safety/evidence.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(document))
    return path


def test_successful_job_preserves_incomplete_assessment_on_run_and_report(workspace, run):
    write(run)
    Path(run['output'], 'safety/report.html').write_text('<html>saved report</html>')
    job = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(job, status='succeeded')
    data = snapshot(workspace)
    row = next(r for r in data['runs'] if r['id'] == run['id'])
    assert row['status'] == 'completed'
    assert row['assessment']['state'] == 'incomplete'
    assert any('scope_unavailable' in reason for reason in row['assessment']['reasons'])
    report = next(r for r in data['reports'] if r['name'] == 'Safety report')
    assert report['run_status'] == 'completed'
    assert report['assessment'] == row['assessment']


def test_native_complete_is_qualified_and_contradictory_record_overrides_it(workspace, run):
    write(run, 'completed_requested_scope', 'completed')
    value = assessment.summary(workspace, run)
    assert value['state'] == 'recorded' and value['label'] == 'Reported scope complete'
    assert 'unverified' in value['origin'] and 'scientific validity' in value['interpretation']
    write(run, 'completed_requested_scope', 'not_assessed')
    assert assessment.summary(workspace, run)['state'] == 'incomplete'


def test_wrong_sample_and_conflicting_provenance_never_supply_coverage(workspace, run):
    write(run, samples=[{'sample_id': 'another-sample'}])
    value = assessment.summary(workspace, run)
    assert value['state'] == 'unknown' and 'does not match' in value['reasons'][0]
    write(run, provenance={'bactologix': {'run_id': 'another-run'}})
    assert assessment.summary(workspace, run)['state'] == 'unknown'
    write(run, 'completed_requested_scope', 'completed', provenance={'bactologix': {'job_id': 'another-attempt'}})
    assert assessment.summary(workspace, run)['state'] == 'unknown'


def test_missing_requested_record_cannot_agree_with_native_complete_label(workspace, run):
    write(run, 'completed_requested_scope', 'completed', records=[])
    value = assessment.summary(workspace, run)
    assert value['state'] == 'incomplete'
    assert 'evidence is missing' in value['reasons'][0]


def test_missing_sample_identity_is_unknown_even_when_native_says_complete(workspace, run):
    write(run, 'completed_requested_scope', 'completed', samples=[], records=[{
        'module': 'icescreen', 'execution_status': 'completed'}])
    assert assessment.summary(workspace, run)['state'] == 'unknown'


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted', 'queued', 'running'])
def test_previous_scope_cannot_become_current_after_incomplete_attempt(workspace, run, status):
    write(run, 'completed_requested_scope', 'completed')
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(jid, status=status)
    value = assessment.summary(workspace, run)
    assert value['state'] == 'unknown'
    assert status in value['reasons'][0] and 'earlier attempt' in value['reasons'][0]


def test_missing_malformed_or_linked_evidence_is_explicitly_unknown(workspace, run, tmp_path):
    assert assessment.summary(workspace, run)['state'] == 'unknown'
    path = write(run)
    path.write_text('[]')
    assert assessment.summary(workspace, run)['state'] == 'unknown'
    path.unlink()
    target = tmp_path / 'external.json'
    target.write_text('{}')
    path.symlink_to(target)
    assert 'Linked' in assessment.summary(workspace, run)['reasons'][0]


def test_refreshed_report_keeps_edition_coverage_instead_of_current_outputs(workspace, run, tmp_path):
    write(run)
    path = tmp_path / 'edition.html'
    path.write_text('<html>edition</html>')
    retained = {'state': 'unknown', 'label': 'Legacy edition', 'reasons': ['Frozen edition evidence']}
    workspace.put('reports', 'edition', {'id': 'edition', 'run': run['id'], 'path': str(path),
                  'refresh': True, 'assessment': retained})
    assert next(r for r in snapshot(workspace)['reports'] if r['id'] == 'edition')['assessment'] == retained


def test_hybas_processing_pass_does_not_become_assessment_complete(workspace, run):
    run['tool'] = 'hybas'
    path = Path(run['output'], 'reports/hybas/results.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema_version': 'hybas-report-1', 'summary': {'isolate': run['sample']['id']},
                               'tables': {'qc_assessment': [{'overall': 'PASS'}]}}))
    value = assessment.summary(workspace, run)
    assert value['state'] == 'unknown' and 'not a scientific-quality verdict' in value['reasons'][0]
