"""Invocation-only scheduler traces; no reconstruction from historical outputs."""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

import pytest

from bactologix.catalog import DATA
from bactologix.data import run_trace
from bactologix import methods_runtime as runtime
from test_methods_runtime import attempt, fake_controller, invoke


def event(observer, kind, **fields):
    record = logging.LogRecord('snakemake.logging', logging.INFO, '', 0, 'ignored', (), None)
    record.event = kind
    record.__dict__.update(fields)
    assert observer.filter(record) is True


def test_trace_records_declared_dependencies_and_status_without_sensitive_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    observer = run_trace.Observer()
    event(observer, 'JOB_INFO', jobid=2, rule_name='copy', input=['input.txt'], output=['stage.txt'],
          shellcmd='SECRET_COMMAND', conda_env='SECRET_ENV', wildcards={'secret': 'VALUE'})
    event(observer, 'JOB_FINISHED', job_id=2)
    event(observer, 'JOB_INFO', jobid=3, rule_name='report', input=['./stage.txt'], output=['report.txt'])
    event(observer, 'JOB_ERROR', jobid=3, rule_name='report', input=['stage.txt'], output=['report.txt'])
    trace = observer.snapshot()
    assert trace['coverage'] == 'captured'
    first, second = trace['jobs']
    assert first['status'] == 'completed' and second['status'] == 'failed'
    assert first['outputs'] == second['inputs'] == [str(tmp_path / 'stage.txt')]
    assert all(value not in json.dumps(trace) for value in ('SECRET_COMMAND', 'SECRET_ENV', 'VALUE', 'shellcmd', 'wildcards'))
    copied = run_trace.clean_trace(trace)
    copied['jobs'].clear()
    assert observer.snapshot()['jobs']


def test_ambiguous_file_labels_and_conflicting_jobs_are_not_guessed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    observer = run_trace.Observer()
    event(observer, 'JOB_INFO', jobid=1, rule_name='first',
          input=['data.txt (update)', 's3://bucket/data', '<TBD>', 'real.txt'], output=['out.txt'])
    assert observer.snapshot()['jobs'][0]['inputs'] == [str(tmp_path / 'real.txt')]
    event(observer, 'JOB_INFO', jobid=1, rule_name='second', input=[], output=['other.txt'])
    event(observer, 'JOB_FINISHED', job_id=1)
    trace = observer.snapshot()
    assert not trace['jobs'] and trace['coverage'] == 'partial'
    assert run_trace.WARNINGS['path'] in trace['warnings']
    assert run_trace.WARNINGS['conflict'] in trace['warnings']


def test_metadata_caps_are_explicit_and_never_suppress_logging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    observer = run_trace.Observer()
    for i in range(500):
        event(observer, 'JOB_INFO', jobid=i, rule_name='many', input=[('x' * 600) + str(i)], output=['out-' + str(i)])
        event(observer, 'JOB_FINISHED', job_id=i)
    trace = observer.snapshot()
    assert trace['truncated'] and trace['coverage'] == 'partial'
    assert len(trace['jobs']) <= run_trace.MAX_JOBS
    assert len(run_trace._encoded(trace)) <= run_trace.MAX_BYTES


def test_malformed_event_is_partial_and_retry_status_does_not_claim_full_history():
    observer = run_trace.Observer()
    event(observer, 'JOB_INFO', jobid=None, rule_name='bad', input=[], output=[])
    event(observer, 'GROUP_ERROR')
    event(observer, 'JOB_INFO', jobid=4, rule_name='retry', input=[], output=[])
    event(observer, 'JOB_ERROR', jobid=4, rule_name='retry', input=[], output=[])
    event(observer, 'JOB_INFO', jobid=4, rule_name='retry', input=[], output=[])
    event(observer, 'JOB_FINISHED', job_id=4)
    trace = observer.snapshot()
    assert trace['coverage'] == 'partial' and trace['jobs'][0]['status'] == 'completed'


@pytest.mark.parametrize('field,value', [('status', 'invented'), ('inputs', ['../outside']),
                                      ('status', []),
                                      ('reported_at', 'invalid'), ('finished_at', '2000-01-01T00:00:00+00:00')])
def test_retained_trace_validation_rejects_malformed_observations(field, value):
    observer = run_trace.Observer()
    event(observer, 'JOB_INFO', jobid=1, rule_name='rule', input=[], output=[])
    event(observer, 'JOB_FINISHED', job_id=1)
    trace = observer.snapshot()
    trace['jobs'][0][field] = value
    with pytest.raises(ValueError):
        run_trace.clean_trace(trace)


@pytest.mark.parametrize('field,value', [('coverage', []), ('schema', True), ('warnings', [{}])])
def test_retained_trace_header_rejects_invalid_scalar_types(field, value):
    trace = run_trace.Observer().snapshot()
    trace[field] = value
    with pytest.raises(ValueError):
        run_trace.clean_trace(trace)


def test_ambiguity_limit_cannot_reintroduce_an_omitted_identity(monkeypatch):
    monkeypatch.setattr(run_trace, 'MAX_JOBS', 2)
    observer = run_trace.Observer()
    for jobid in range(3):
        event(observer, 'JOB_INFO', jobid=jobid, rule_name='first', input=[], output=[])
        event(observer, 'JOB_INFO', jobid=jobid, rule_name='conflict', input=[], output=[])
    event(observer, 'JOB_INFO', jobid=2, rule_name='conflict', input=[], output=[])
    event(observer, 'JOB_FINISHED', job_id=2)
    trace = observer.snapshot()
    assert trace['jobs'] == [] and trace['truncated'] and trace['coverage'] == 'partial'


def test_readonly_filter_survives_handler_reset_and_is_frozen_in_receipt(attempt, fake_controller):
    store, run, jid = attempt
    (fake_controller / 'snakemake/logging.py').write_text('import logging\nlogger=logging.getLogger("snakemake.logging")\n')
    (fake_controller / 'snakemake/cli.py').write_text('''from .logging import logger
import logging
def main():
    logger.handlers.clear()  # Snakemake 9.27 replaces handlers during setup.
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.NullHandler())
    logger.info('job', extra=dict(event='JOB_INFO', jobid=8, rule_name='copy', input=['input.txt'], output=['out.txt'], shellcmd='SECRET'))
    logger.info('finished', extra=dict(event='JOB_FINISHED', job_id=8))
    raise SystemExit(0)
''')
    launch = runtime.prepare(store, run, store.job(jid))
    process = invoke(fake_controller, launch)
    assert process.returncode == 0, process.stderr
    receipt = runtime.finish(store, run, jid, launch, succeeded=True)
    trace = receipt['observations']['finished']['rule_trace']
    assert trace['jobs'][0]['rule'] == 'copy' and trace['jobs'][0]['status'] == 'completed'
    assert 'SECRET' not in json.dumps(trace)


def test_native_sample_free_rule_chain_and_noop_have_distinct_attempt_traces(attempt, tmp_path):
    pytest.importorskip('snakemake')
    store, run, jid = attempt
    work = tmp_path / 'scheduler trace with spaces'
    work.mkdir()
    (work / 'Snakefile').write_text('''rule all:
    input: "report.txt"
rule first:
    output: "stage.txt"
    shell: "printf fixture > {output}"
rule report:
    input: "stage.txt"
    output: "report.txt"
    shell: "cp {input} {output}"
''')
    def execute(job_id):
        launch = runtime.prepare(store, run, store.job(job_id))
        env = dict(os.environ, **launch['environment'])
        result = subprocess.run([sys.executable, str(DATA / 'run_existing.py'), 'snakemake',
                                 '--cores', '1', '--nolock', '--snakefile', str(work / 'Snakefile')],
                                cwd=work, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        receipt = runtime.finish(store, run, job_id, launch, succeeded=True)
        return receipt['observations']['finished']['rule_trace']
    first = execute(jid)
    observed = {j['rule']: j for j in first['jobs']}
    assert {'first', 'report', 'all'} <= set(observed)
    assert all(j['status'] == 'completed' for j in observed.values())
    assert observed['first']['outputs'] == observed['report']['inputs'] == [str(work / 'stage.txt')]
    store.update_job(jid, status='succeeded')
    next_id = store.submit('workflow', {'run_id': run['id'], 'action': 'continue'})
    store.update_job(next_id, status='running')
    second = execute(next_id)
    assert second['jobs'] == []
    assert first['jobs'], 'A later no-op must not overwrite the earlier invocation trace.'
