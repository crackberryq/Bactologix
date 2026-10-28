"""Result downloads preserve attempts and never infer completion from a file."""
import base64
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import results, telemetry
from bactologix.runs import create_run
from bactologix.store import now
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample


@pytest.fixture
def run(workspace, installation, sample):
    return create_run(workspace, 'hybas', sample, 'custom: preserve\n', label='Result fixture <script>')


def report(run, text='<html>software fixture</html>'):
    p = Path(run['output']) / 'reports/multiqc/multiqc_report.html'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def attempt(store, run, action='run', status='running'):
    jid = store.submit('workflow', {'run_id': run['id'], 'action': action})
    store.update_job(jid, status=status, started=now())
    return SimpleNamespace(store=store, job=store.job(jid), write=lambda _: None)


def finish(ctx, run, status='succeeded'):
    results.observe(ctx, run, 'after')
    ctx.store.update_job(ctx.job['id'], status=status, finished=now())


def test_draft_and_downloads_use_exact_frozen_settings(workspace, run):
    original = copy.deepcopy(run)
    workspace.put('configs', 'hybas', 'custom: different\n')
    d = dispatch(workspace, 'inspect_results', {'id': run['id']})['results']
    assert d['status'] == 'draft' and not d['latest_execution'] and not d['attempts']
    assert all(r['state'] == 'missing' for r in d['reports'])
    exported = dispatch(workspace, 'download_result', {'id': run['id'], 'format': 'config'})['download']
    assert exported['text'] == original['config']
    manifest = json.loads(results.download(workspace, run['id'], 'manifest')['text'])
    assert manifest['config_sha256'] == run['config_sha256']
    assert 'config' not in manifest and 'payload' not in json.dumps(manifest)
    assert workspace.get('runs', run['id']) == original and not workspace.jobs()


def test_legacy_report_does_not_imply_success_or_producer(workspace, run):
    path = report(run)
    job = attempt(workspace, run, status='failed')
    validation = attempt(workspace, run, 'dry-run', 'succeeded')
    d = results.inspect(workspace, run['id'])
    assert d['status'] == 'failed' and d['latest_execution']['id'] == job.job['id']
    assert [j['id'] for j in d['attempts']] == [validation.job['id'], job.job['id']]
    r = d['reports'][0]
    assert r['state'] == 'available' and r['producer'] == 'Not recorded' and not r['observation']
    assert all(not j['resource_record'] and j['log']['state'] == 'missing' for j in d['attempts'])
    artifact = results.download(workspace, run['id'], 'report', r['id'])
    assert base64.b64decode(artifact['base64']) == path.read_bytes()


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'cancelled', 'interrupted'])
def test_observation_keeps_attempt_status_and_changed_file_evidence(workspace, run, status):
    ctx = attempt(workspace, run)
    results.observe(ctx, run, 'before')
    path = report(run)
    finish(ctx, run, status)
    r = results.inspect(workspace, run['id'])['reports'][0]
    assert r['observation']['job_id'] == ctx.job['id']
    assert r['observation']['status'] == status and r['observation']['changed']
    assert r['producer'] == 'Not recorded'
    path.write_text('external replacement')
    assert results.inspect(workspace, run['id'])['reports'][0]['observation'] is None


def test_repeat_attempt_unchanged_reports_and_resource_history(workspace, run):
    report(run)
    first = attempt(workspace, run)
    results.observe(first, run, 'before')
    recorder = telemetry.Recorder(workspace, first.job)
    workspace.log_path(first.job['id']).write_text('first attempt\n')
    recorder.finish('failed')
    finish(first, run, 'failed')
    retained = copy.deepcopy(workspace.get('result_observations', first.job['id']))
    second = attempt(workspace, run)
    assert results.inspect(workspace, run['id'])['reports'][0]['observation'] is None
    results.observe(second, run, 'before')
    finish(second, run)
    d = results.inspect(workspace, run['id'])
    assert not d['reports'][0]['observation']['changed']
    assert d['reports'][0]['observation']['job_id'] == second.job['id']
    assert workspace.get('result_observations', first.job['id']) == retained
    assert d['attempts'][1]['resource_record'] and d['attempts'][1]['log']['state'] == 'available'
    assert d['attempts'][0]['log']['state'] == 'missing'


def test_observation_is_best_effort_and_only_for_execution(workspace, run, monkeypatch):
    ctx = attempt(workspace, run, 'dry-run')
    results.observe(ctx, run, 'before')
    assert not workspace.all('result_observations')
    ctx.job['payload']['action'] = 'run'
    monkeypatch.setattr(results, 'file_info', lambda *a: (_ for _ in ()).throw(OSError('fixture')))
    ctx.write = lambda text: (_ for _ in ()).throw(OSError('unwritable log'))
    results.observe(ctx, run, 'after')
    assert workspace.job(ctx.job['id'])['status'] == 'running'


def test_missing_before_observation_never_implies_report_changed(workspace, run):
    ctx = attempt(workspace, run)
    report(run)
    finish(ctx, run, 'failed')
    observation = results.inspect(workspace, run['id'])['reports'][0]['observation']
    assert observation['job_id'] == ctx.job['id'] and observation['changed'] is None


def test_interrupted_attempt_without_after_does_not_invent_observation(workspace, run):
    ctx = attempt(workspace, run)
    results.observe(ctx, run, 'before')
    report(run)
    workspace.update_job(ctx.job['id'], status='interrupted', finished=now())
    d = results.inspect(workspace, run['id'])
    assert d['status'] == 'interrupted' and d['reports'][0]['observation'] is None
    assert d['attempts'][0]['report_observation'] is None


def test_read_file_rejects_changed_content_and_bound(workspace, tmp_path, monkeypatch):
    p = tmp_path / 'changing'; p.write_text('original')
    with pytest.raises(ValueError, match='limit'): results.read_file(p, 2)
    real = results.os.fstat
    calls = []
    def changing(fd):
        calls.append(fd)
        if len(calls) == 3: p.write_text('replacement')
        return real(fd)
    monkeypatch.setattr(results.os, 'fstat', changing)
    with pytest.raises(ValueError, match='changed'): results.read_file(p, 100)


@pytest.mark.parametrize('kind', ['symlink', 'parent_link', 'fifo', 'directory', 'oversized', 'empty'])
def test_unsafe_unavailable_and_large_reports_are_not_downloaded(workspace, run, tmp_path, monkeypatch, kind):
    p = report(run)
    p.unlink()
    if kind == 'symlink': p.symlink_to(tmp_path / 'private')
    elif kind == 'parent_link':
        p.parent.rmdir(); p.parent.symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / p.name).write_text('private')
    elif kind == 'fifo': os.mkfifo(p)
    elif kind == 'directory': p.mkdir()
    elif kind == 'oversized':
        p.write_text('12345'); monkeypatch.setattr(results, 'REPORT_LIMIT', 4)
    else: p.touch()
    d = results.inspect(workspace, run['id'])
    assert d['reports'][0]['state'] != 'available'
    with pytest.raises(ValueError, match='unavailable'):
        results.download(workspace, run['id'], 'report', d['reports'][0]['id'])
    if kind in {'symlink', 'parent_link', 'fifo', 'directory'}:
        with pytest.raises((OSError, ValueError)): results.read_file(p, 100)


def test_report_download_revalidates_membership_and_disk_state(workspace, run, tmp_path):
    p = report(run)
    rid = results.inspect(workspace, run['id'])['reports'][0]['id']
    with pytest.raises(ValueError): results.download(workspace, run['id'], 'report', '/etc/passwd')
    p.unlink()
    with pytest.raises(ValueError): dispatch(workspace, 'open_result_report', {'id': run['id'], 'report_id': rid})
    for bad in (None, [], 'unknown'):
        with pytest.raises(ValueError): results.inspect(workspace, bad)
    with pytest.raises(ValueError): results.download(workspace, run['id'], 'unknown')


def test_shared_followup_cannot_download_another_runs_report(workspace, run):
    followup = dict(run, id='run-followup', run_type='followup', parent=run['id'])
    workspace.put('runs', followup['id'], followup)
    workspace.put('followup_output_owner', run['id'], 'run-other')
    d = results.inspect(workspace, followup['id'])
    assert d['shared_output_unavailable'] and not d['reports']
    with pytest.raises(ValueError): results.download(workspace, followup['id'], 'report', followup['id'] + '-Follow-up report')


def test_log_download_refuses_link_replacement(workspace, run, tmp_path):
    ctx = attempt(workspace, run, status='failed')
    private = tmp_path / 'private'; private.write_text('not a log')
    workspace.log_path(ctx.job['id']).symlink_to(private)
    assert results.inspect(workspace, run['id'])['attempts'][0]['log']['state'] == 'unavailable'
    with pytest.raises(OSError): dispatch(workspace, 'download_log', {'id': ctx.job['id']})


@pytest.mark.parametrize('fails', [False, True])
def test_real_command_records_report_observation_without_changing_result(workspace, run, monkeypatch, fails):
    """Execute only a tiny file-writing process through the real workflow command path."""
    import sys
    from bactologix import runs
    from bactologix.engine import Context
    ctx = attempt(workspace, run)
    real = Context(workspace, ctx.job)
    path = Path(run['output']) / 'reports/multiqc/multiqc_report.html'
    code = 'from pathlib import Path; p=Path(__import__("sys").argv[1]); p.parent.mkdir(parents=True,exist_ok=True); p.write_text("<html>fixture</html>"); raise SystemExit(' + ('1' if fails else '0') + ')'
    monkeypatch.setattr(runs, 'validate_launch', lambda *a, **k: copy.deepcopy(run))
    monkeypatch.setattr(runs, 'current_controller', lambda *a: run['controller'])
    monkeypatch.setattr(runs, 'command', lambda *a: [sys.executable, '-c', code, str(path)])
    try:
        if fails:
            with pytest.raises(RuntimeError): runs.execute_workflow(real)
        else:
            runs.execute_workflow(real)
        record = workspace.get('result_observations', ctx.job['id'])
        assert record['before']['files']['reports/multiqc/multiqc_report.html']['state'] == 'missing'
        assert record['after']['files']['reports/multiqc/multiqc_report.html']['state'] == 'available'
        assert bool(workspace.get('runs', run['id']).get('completed')) is not fails
    finally:
        real.log.close()
