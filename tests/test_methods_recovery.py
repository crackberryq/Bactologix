"""Legacy collection failures receive supplements, never rewritten history."""
import copy
import hashlib

import pytest

from bactologix import methods_recovery, report_evidence
from test_native import workspace


@pytest.fixture
def case(workspace):
    config = 'threads: 12\n'
    run = {'id': 'run-legacy', 'tool': 'micpscope', 'sample': {'id': 'sample'},
           'commit': 'a' * 40, 'workflow_sha256': 'b' * 64,
           'config': config, 'config_sha256': hashlib.sha256(config.encode()).hexdigest(),
           'inputs': [{'path': '/saved/proteins.faa', 'sha256': 'c' * 64}],
           'completed': '2026-11-09T12:00:00+00:00'}
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(jid, status='succeeded')
    job = workspace.job(jid)
    receipt = {'schema': 1, 'run_id': run['id'], 'job_id': jid,
               'evidence': {'files': [], 'versions': {}, 'records': [], 'decisions': {},
                            'warnings': [methods_recovery.LEGACY_SIZE_WARNING,
                                         'GeneScope results are missing or belong to a different sample.']}}
    workspace.put('methods_evidence', run['id'], receipt)
    workspace.put('methods_attempt_evidence', jid, receipt)
    evidence = {'files': [{'path': methods_recovery.RESULTS, 'sha256': 'd' * 64, 'bytes': 9 * 1024 * 1024}],
                'metadata': {'sample_id': 'sample', 'software_version': '0.5.2',
                             'provenance': {'biopython_version': '1.86', 'input_sha256': 'c' * 64}},
                'versions': {'micpscope': '0.5.2', 'biopython': '1.86'}, 'warnings': []}
    return run, receipt, job, evidence


def observed(workspace, run, job, *, before=None, sha='d' * 64):
    receipt = {'schema': 1, 'job_id': job['id'], 'run': report_evidence.identity(run),
               'before': {'files': {methods_recovery.RESULTS: before or {'state': 'missing'}}},
               'after': {'files': {methods_recovery.RESULTS: {'state': 'available', 'sha256': sha}}}}
    workspace.put('report_evidence', job['id'], receipt)
    return receipt


def rows(workspace):
    with workspace.connect() as db:
        return [tuple(row) for row in db.execute('SELECT * FROM items ORDER BY kind,id')]


def test_recovery_is_content_bound_read_only_and_keeps_original_errors(workspace, case):
    run, receipt, job, evidence = case
    observed(workspace, run, job)
    original = copy.deepcopy((run, receipt, job, evidence))
    before = rows(workspace)
    result = methods_recovery.supplement(workspace, run, receipt, job, lambda _: evidence)
    assert result['status'] == 'recovered'
    assert result['attribution']['job_id'] == job['id']
    assert result['attribution']['attempt_status'] == 'succeeded'
    assert result['read_at']
    assert result['evidence']['versions'] == evidence['versions']
    assert 'sample identity' in result['evidence']['native_identity']['checks']
    assert 'not at completion' in ' '.join(result['warnings'])
    assert rows(workspace) == before and (run, receipt, job, evidence) == original
    result['evidence']['versions']['biopython'] = 'changed'
    assert evidence['versions']['biopython'] == '1.86'
    assert methods_recovery.LEGACY_SIZE_WARNING in workspace.get('methods_evidence', run['id'])['evidence']['warnings']


@pytest.mark.parametrize('change', ['files', 'versions', 'warning', 'tool'])
def test_recovery_is_limited_to_the_original_size_failure(workspace, case, change):
    run, receipt, job, evidence = case
    if change in ('files', 'versions'):
        receipt['evidence'][change] = evidence[change]
    elif change == 'warning':
        receipt['evidence']['warnings'] = ['Could not read micpscope/results.json: invalid JSON']
    else:
        run['tool'] = 'basis'
    def should_not_read(_):
        pytest.fail('Healthy or unrelated snapshots must not reread outputs.')
    assert methods_recovery.supplement(workspace, run, receipt, job, should_not_read) is None


@pytest.mark.parametrize('baseline', ['none', 'unchanged', 'no_job'])
def test_legacy_or_unchanged_content_does_not_acquire_execution_attribution(workspace, case, baseline):
    run, receipt, job, evidence = case
    if baseline == 'unchanged':
        observed(workspace, run, job, before={'state': 'available', 'sha256': 'd' * 64})
    result = methods_recovery.supplement(workspace, run, receipt, None if baseline == 'no_job' else job, lambda _: evidence)
    assert result['status'] == 'unverified'
    assert result['attribution']['job_id'] is None and result['attribution']['commit'] is None
    assert result['evidence']['versions'] == evidence['versions']
    assert 'current output metadata only' in ' '.join(result['warnings'])


@pytest.mark.parametrize('conflict', ['sample', 'source', 'configuration', 'input', 'job', 'nested_job', 'run'])
def test_native_identity_conflicts_block_versions(workspace, case, conflict):
    run, receipt, job, evidence = case
    observed(workspace, run, job)
    metadata = evidence['metadata']
    if conflict == 'sample':
        metadata['sample_id'] = 'different-sample'
    elif conflict in ('source', 'configuration', 'input'):
        key = {'source': 'source_commit', 'configuration': 'config_sha256', 'input': 'input_sha256'}[conflict]
        metadata['provenance'][key] = 'f' * (40 if conflict == 'source' else 64)
    elif conflict == 'job':
        metadata['bactologix'] = {'job_id': 'another-attempt'}
    elif conflict == 'nested_job':
        metadata['provenance']['bactologix'] = {'job_id': 'another-attempt'}
    else:
        metadata['bactologix'] = {'run_id': 'another-run'}
    result = methods_recovery.supplement(workspace, run, receipt, job, lambda _: evidence)
    assert result['status'] == 'blocked' and not result['evidence']


@pytest.mark.parametrize('conflict', ['checksum', 'saved_identity'])
def test_content_receipt_conflicts_block_recovery(workspace, case, conflict):
    run, receipt, job, evidence = case
    observation = observed(workspace, run, job, sha='e' * 64 if conflict == 'checksum' else 'd' * 64)
    if conflict == 'saved_identity':
        observation['run']['commit'] = 'f' * 40
        workspace.put('report_evidence', job['id'], observation)
    result = methods_recovery.supplement(workspace, run, receipt, job, lambda _: evidence)
    assert result['status'] == 'blocked' and not result['evidence']
    assert ('content no longer matches' if conflict == 'checksum' else 'different run, source or configuration') in ' '.join(result['warnings'])


def test_a_matching_older_attempt_cannot_bind_current_recovery(workspace, case):
    run, receipt, old_job, evidence = case
    observed(workspace, run, old_job)
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'continue'})
    workspace.update_job(jid, status='succeeded')
    latest = workspace.job(jid)
    receipt['job_id'] = latest['id']
    result = methods_recovery.supplement(workspace, run, receipt, latest, lambda _: evidence)
    assert result['status'] == 'unverified' and result['attribution']['job_id'] is None


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'running'])
def test_unsuccessful_latest_attempt_is_blocked_before_collection(workspace, case, status):
    run, receipt, job, _ = case
    job['status'] = status
    def should_not_read(_):
        pytest.fail('An unsuccessful attempt must not trigger completed metadata recovery.')
    result = methods_recovery.supplement(workspace, run, receipt, job, should_not_read)
    assert result['status'] == 'blocked' and result['read_at'] is None and not result['evidence']


@pytest.mark.parametrize('failure', ['exception', 'missing_metadata', 'missing_fingerprint', 'missing_sample'])
def test_failed_collection_never_exposes_versions(workspace, case, failure):
    run, receipt, job, evidence = case
    if failure == 'missing_metadata':
        evidence.pop('metadata')
        evidence['warnings'] = ['Current result is unavailable.']
    elif failure == 'missing_fingerprint':
        evidence['files'] = []
    elif failure == 'missing_sample':
        evidence['metadata'].pop('sample_id')
    def collect(_):
        if failure == 'exception':
            raise ValueError('Malformed current JSON')
        return evidence
    result = methods_recovery.supplement(workspace, run, receipt, job, collect)
    assert result['status'] == ('blocked' if failure == 'missing_sample' else 'unavailable')
    assert not result['evidence']
    if failure == 'missing_metadata':
        assert 'Current result is unavailable.' in result['warnings']


def test_recovery_does_not_create_stage_execution(workspace, case):
    run, receipt, job, evidence = case
    observed(workspace, run, job)
    evidence['records'] = [{'module': 'invented', 'execution_status': 'completed'}]
    evidence['decisions'] = {'executed_stages': ['invented']}
    result = methods_recovery.supplement(workspace, run, receipt, job, lambda _: evidence)
    assert 'records' not in result['evidence'] and 'decisions' not in result['evidence']


@pytest.mark.parametrize('location,key', [
    ('root', 'run_id'), ('root', 'job_id'),
    ('root', 'bactologix_run_id'), ('root', 'bactologix_job_id'),
    ('provenance', 'run_id'), ('provenance', 'job_id'),
    ('provenance', 'bactologix_run_id'), ('provenance', 'bactologix_job_id'),
    ('bactologix', 'run_id'), ('bactologix', 'job_id'),
    ('provenance.bactologix', 'run_id'), ('provenance.bactologix', 'job_id'),
])
def test_every_projected_run_and_job_alias_can_block_recovery(workspace, case, location, key):
    run, receipt, job, evidence = case
    observed(workspace, run, job)
    declared = evidence['metadata']
    if location != 'root':
        for component in location.split('.'):
            declared = declared.setdefault(component, {})
    declared[key] = 'different-identity'
    result = methods_recovery.supplement(workspace, run, receipt, job, lambda _: evidence)
    assert result['status'] == 'blocked' and not result['evidence']


def test_agreeing_identity_aliases_do_not_replace_content_attribution(workspace, case):
    run, receipt, job, evidence = case
    for declared in (evidence['metadata'], evidence['metadata']['provenance']):
        declared.update(run_id=run['id'], job_id=job['id'],
                        bactologix_run_id=run['id'], bactologix_job_id=job['id'])
        declared['bactologix'] = {'run_id': run['id'], 'job_id': job['id']}
    result = methods_recovery.supplement(workspace, run, receipt, job, lambda _: evidence)
    assert result['status'] == 'unverified' and result['attribution']['job_id'] is None
