"""Versions come from the invoking interpreter, never an export-time probe."""
import copy
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from bactologix import methods_runtime as runtime
from bactologix.catalog import DATA
from bactologix.data import runtime_receipt as native
from bactologix.store import Store


@pytest.fixture
def attempt(tmp_path):
    store = Store(tmp_path / 'workspace with spaces')
    folder = store.home / 'runs/run-fixture'
    (folder / 'workflow').mkdir(parents=True)
    (folder / 'outputs/sample').mkdir(parents=True)
    config = 'threads: 1\n'
    run = {'id': 'run-fixture', 'tool': 'micpscope', 'sample': {'id': 'sample'},
           'folder': str(folder), 'output': str(folder / 'outputs/sample'),
           'controller': sys.prefix, 'last_controller': sys.prefix,
           'commit': 'a' * 40, 'config': config, 'config_sha256': hashlib.sha256(config.encode()).hexdigest(),
           'workflow_sha256': 'b' * 64, 'inputs': [{'path': '/original/sample.faa', 'sha256': 'c' * 64}],
           'cores': 1, 'memory_mb': 2048}
    store.put('runs', run['id'], run)
    jid = store.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    store.update_job(jid, status='running')
    return store, run, jid


@pytest.fixture
def fake_controller(tmp_path):
    root = tmp_path / 'fake controller modules'
    package = root / 'snakemake'
    (package / 'deployment').mkdir(parents=True)
    (package / '__init__.py').write_text('__version__ = "9.6.2-observed-fixture"\n')
    (package / 'deployment/__init__.py').write_text('')
    (package / 'deployment/conda.py').write_text('class Env:\n    def create(self, dryrun=False):\n        raise AssertionError("unprotected environment creation")\n')
    (package / 'exceptions.py').write_text('class WorkflowError(Exception): pass\n')
    (package / 'cli.py').write_text('''import os, signal
def main():
    assert not any(k.startswith('BACTOLOGIX_RUNTIME_') for k in os.environ), 'receipt context leaked to tools'
    mode = os.environ.get('FIXTURE_OUTCOME', '0')
    print('ACTUAL CLI INVOKED', flush=True)
    if mode == 'error': raise RuntimeError('fixture failure')
    if mode == 'interrupt': raise KeyboardInterrupt()
    if mode == 'kill': os.kill(os.getpid(), signal.SIGKILL)
    raise SystemExit(int(mode))
''')
    return root


def invoke(fake_controller, launch=None, outcome='0', **environment):
    env = {k: v for k, v in os.environ.items() if not k.startswith('BACTOLOGIX_RUNTIME_')}
    env.update(PYTHONPATH=str(fake_controller), FIXTURE_OUTCOME=outcome)
    env.update((launch or {}).get('environment', {}))
    env.update(environment)
    return subprocess.run([sys.executable, str(DATA / 'run_existing.py'), 'snakemake'],
                          env=env, capture_output=True, text=True, timeout=20)


def observe(attempt, fake_controller, outcome='0'):
    store, run, jid = attempt
    launch = runtime.prepare(store, run, store.job(jid))
    process = invoke(fake_controller, launch, outcome)
    receipt = runtime.finish(store, run, jid, launch, succeeded=process.returncode == 0)
    return launch, process, receipt


def test_actual_invocation_versions_are_bound_and_survive_environment_and_file_removal(attempt, fake_controller, monkeypatch):
    store, run, jid = attempt
    launch, process, receipt = observe(attempt, fake_controller)
    assert process.returncode == 0 and 'ACTUAL CLI INVOKED' in process.stdout
    assert receipt['status'] == 'succeeded'
    got = runtime.evidence(store, run, jid)
    assert got['versions'] == {'snakemake': '9.6.2-observed-fixture', 'controller_python': sys.version.split()[0]}
    assert got['status'] == 'succeeded' and got['exit_code'] == 0
    assert 'steps' not in got and 'python' not in got['versions']
    assert got['identity']['run']['inputs'] == run['inputs']
    store.update_job(jid, status='succeeded')
    shutil.rmtree(Path(launch['path']).parent)
    monkeypatch.setattr(native, 'read', lambda *a: pytest.fail('No file probe during export'))
    run['controller'] = '/a/newly/installed/controller'
    # Later validation/preparation changes last_controller, not this receipt.
    run['last_controller'] = '/a/replacement/runner/used/for/later/validation'
    assert runtime.evidence(store, run, jid) == got


@pytest.mark.parametrize('outcome,code,status', [('7', 7, 'failed'), ('error', 1, 'failed'), ('interrupt', -2, 'failed')])
def test_failure_exit_semantics_and_no_completed_methods_versions(attempt, fake_controller, outcome, code, status):
    store, run, jid = attempt
    _, process, receipt = observe(attempt, fake_controller, outcome)
    assert process.returncode == code
    assert receipt['status'] == status and not receipt['parent_succeeded']
    assert receipt['observations']['started']['versions']['snakemake'] == '9.6.2-observed-fixture'
    assert runtime.evidence(store, run, jid) is None


def test_no_context_keeps_original_wrapper_invocation(fake_controller):
    process = invoke(fake_controller, outcome='11')
    assert process.returncode == 11 and 'ACTUAL CLI INVOKED' in process.stdout
    assert 'runtime observation' not in process.stderr


def test_killed_controller_has_no_fabricated_completion(attempt, fake_controller):
    store, run, jid = attempt
    _, process, receipt = observe(attempt, fake_controller, 'kill')
    assert process.returncode == -9 and receipt['status'] == 'unavailable'
    assert 'started' in receipt['observations'] and 'finished' not in receipt['observations']
    assert runtime.evidence(store, run, jid) is None


@pytest.mark.parametrize('action', ['dry-run', 'prepare', 'check-environments'])
def test_nonexecution_actions_cannot_create_runtime_receipts(attempt, action):
    store, run, old_job = attempt
    store.update_job(old_job, status='succeeded')
    jid = store.submit('workflow', {'run_id': run['id'], 'action': action})
    store.update_job(jid, status='running')
    with pytest.raises(ValueError, match='execution attempt'):
        runtime.prepare(store, run, store.job(jid))
    assert not store.all(runtime.LAUNCH)


@pytest.mark.parametrize('change', ['nonce', 'hash', 'controller', 'linked_context', 'replaced_reserved_file'])
def test_bad_receipt_context_does_not_change_workflow_outcome(attempt, fake_controller, tmp_path, change):
    store, run, jid = attempt
    launch = runtime.prepare(store, run, store.job(jid))
    env = {}
    if change == 'nonce':
        env[native.ENV[1]] = 'incorrect'
    elif change == 'hash':
        env[native.ENV[2]] = 'incorrect'
    elif change == 'controller':
        path = Path(launch['path'])
        context = json.loads(path.read_bytes())
        context['controller'] = '/another/controller'
        path.write_bytes(native.encoded(context))
        env[native.ENV[2]] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif change == 'linked_context':
        other = tmp_path / 'context.json'
        other.symlink_to(launch['path'])
        env[native.ENV[0]] = str(other)
    else:
        path = Path(launch['path']).parent / 'started.json'
        path.rename(path.with_name('original-started.json'))
        path.write_text('')
    process = invoke(fake_controller, launch, **env)
    assert process.returncode == 0 and 'ACTUAL CLI INVOKED' in process.stdout
    receipt = runtime.finish(store, run, jid, launch, succeeded=True)
    assert receipt['status'] == 'unavailable'
    assert runtime.evidence(store, run, jid) is None


@pytest.mark.parametrize('change', ['nonce', 'job', 'run', 'config', 'source', 'inputs', 'producing_controller', 'status', 'versions'])
def test_saved_identity_conflicts_cannot_supply_versions(attempt, fake_controller, change):
    store, run, jid = attempt
    observe(attempt, fake_controller)
    if change in {'config', 'source', 'inputs'}:
        if change == 'config': run['config_sha256'] = 'f' * 64
        if change == 'source': run['commit'] = 'f' * 40
        if change == 'inputs': run['inputs'][0]['sha256'] = 'f' * 64
    else:
        receipt = store.get(runtime.RECEIPT, jid)
        start = receipt['observations']['started']
        if change == 'nonce': start['nonce'] = 'incorrect'
        if change == 'job': start['job_id'] = 'job-another'
        if change == 'run': receipt['run_id'] = 'run-another'
        if change == 'status': receipt['observations']['finished']['exit_code'] = 7
        if change == 'versions': start['versions']['snakemake'] = 'invented'
        if change == 'producing_controller': start['controller'] = '/another/controller'
        store.put(runtime.RECEIPT, jid, receipt)
    assert runtime.evidence(store, run, jid) is None
    assert runtime.evidence(store, run, jid, include_incomplete=True) is None


@pytest.mark.parametrize('outcome,job_status,expected,exit_code', [
    ('7', 'failed', 'failed', 7),
    ('kill', 'interrupted', 'interrupted', None),
    ('0', 'cancelled', 'cancelled', 0),
])
def test_explicit_incomplete_view_retains_observed_version_without_claiming_success(
        attempt, fake_controller, outcome, job_status, expected, exit_code):
    store, run, jid = attempt
    observe(attempt, fake_controller, outcome)
    store.update_job(jid, status=job_status)
    observed = runtime.evidence(store, run, jid, include_incomplete=True)
    assert observed['versions']['snakemake'] == '9.6.2-observed-fixture'
    assert observed['status'] == expected and observed['exit_code'] == exit_code
    assert (observed['finished_at'] is None) == (outcome == 'kill')
    assert 'successful completion and individual step execution are not established' in observed['scopes']['snakemake']
    assert runtime.evidence(store, run, jid) is None


def test_legacy_run_does_not_infer_runtime_from_installed_version(attempt, monkeypatch):
    store, run, jid = attempt
    store.update_job(jid, status='succeeded')
    monkeypatch.setattr(native, 'read', lambda *a: pytest.fail('No retrospective version probe'))
    assert runtime.evidence(store, run, jid, include_incomplete=True) is None


def test_incomplete_version_rejects_conflicting_finish_and_changed_start(attempt, fake_controller):
    store, run, jid = attempt
    observe(attempt, fake_controller, '7')
    store.update_job(jid, status='failed')
    saved = store.get(runtime.RECEIPT, jid)
    changed = copy.deepcopy(saved)
    changed['observations']['finished']['nonce'] = 'foreign attempt'
    store.put(runtime.RECEIPT, jid, changed)
    assert runtime.evidence(store, run, jid, include_incomplete=True) is None
    # A missing finish permits only a separately valid, bound start record.
    changed = copy.deepcopy(saved)
    del changed['observations']['finished']
    changed['observations']['started']['context_sha256'] = 'f' * 64
    store.put(runtime.RECEIPT, jid, changed)
    assert runtime.evidence(store, run, jid, include_incomplete=True) is None


def test_start_only_versions_require_matching_capture_hash(attempt, fake_controller):
    store, run, jid = attempt
    observe(attempt, fake_controller, 'kill')
    store.update_job(jid, status='interrupted')
    saved = store.get(runtime.RECEIPT, jid)
    assert runtime.evidence(store, run, jid, include_incomplete=True)['versions']['snakemake']
    changed = copy.deepcopy(saved)
    changed['observations']['started']['versions']['snakemake'] = 'invented'
    store.put(runtime.RECEIPT, jid, changed)
    assert runtime.evidence(store, run, jid, include_incomplete=True) is None
    del saved['observation_sha256']
    store.put(runtime.RECEIPT, jid, saved)
    assert runtime.evidence(store, run, jid, include_incomplete=True) is None


def test_legacy_completed_runtime_receipt_remains_readable_without_new_capture_hashes(attempt, fake_controller):
    store, run, jid = attempt
    observe(attempt, fake_controller)
    saved = store.get(runtime.RECEIPT, jid)
    del saved['observation_sha256']
    store.put(runtime.RECEIPT, jid, saved)
    assert runtime.evidence(store, run, jid)['versions']['snakemake'] == '9.6.2-observed-fixture'


@pytest.mark.parametrize('valid', [True, False])
def test_rule_trace_is_bound_to_captured_invocation_and_cannot_replace_versions(attempt, fake_controller, valid):
    store, run, jid = attempt
    launch = runtime.prepare(store, run, store.job(jid))
    assert invoke(fake_controller, launch).returncode == 0
    path = Path(launch['path']).with_name('finished.json')
    finished = json.loads(path.read_bytes())
    trace = {'schema': 1, 'observer': 'snakemake-structured-log', 'coverage': 'captured',
             'truncated': False, 'jobs': [], 'warnings': []}
    if not valid:
        trace['observer'] = 'unbound external output'
    finished['rule_trace'] = trace
    path.write_bytes(native.encoded(finished))
    runtime.finish(store, run, jid, launch, succeeded=True)
    observed = runtime.evidence(store, run, jid)
    assert observed['versions']['snakemake'] == '9.6.2-observed-fixture'
    if valid:
        assert observed['rule_trace'] == trace
    else:
        assert 'rule_trace' not in observed and 'rule data flow is unavailable' in observed['warnings'][0]
    saved = store.get(runtime.RECEIPT, jid)
    saved['observations']['finished']['rule_trace']['coverage'] = 'partial'
    store.put(runtime.RECEIPT, jid, saved)
    assert runtime.evidence(store, run, jid) is None, 'Changed trace must not bypass its capture hash'


@pytest.mark.parametrize('state', ['failed', 'cancelled'])
def test_failed_or_cancelled_job_never_promotes_even_successful_child(attempt, fake_controller, state):
    store, run, jid = attempt
    observe(attempt, fake_controller)
    store.update_job(jid, status=state)
    assert runtime.evidence(store, run, jid) is None


def test_receipts_and_destinations_cannot_be_replaced(attempt, fake_controller):
    store, run, jid = attempt
    launch, _, receipt = observe(attempt, fake_controller)
    assert runtime.finish(store, run, jid, launch, succeeded=True) == receipt
    with pytest.raises((ValueError, FileExistsError)):
        runtime.prepare(store, run, store.job(jid))
    before = Path(launch['path']).with_name('started.json').read_bytes()
    process = invoke(fake_controller, launch)
    assert process.returncode == 0
    assert Path(launch['path']).with_name('started.json').read_bytes() == before
    with pytest.raises(ValueError, match='already recorded'):
        runtime.finish(store, run, jid, launch, succeeded=False)
    assert store.get(runtime.RECEIPT, jid) == receipt


@pytest.mark.parametrize('contents', [b'', b'{', b'{"schema":1,"schema":1}', b'[]', b'x' * (native.LIMIT + 1)])
def test_missing_or_malformed_child_receipt_is_nonfatal(attempt, fake_controller, contents):
    store, run, jid = attempt
    launch = runtime.prepare(store, run, store.job(jid))
    process = invoke(fake_controller, launch)
    assert process.returncode == 0
    Path(launch['path']).with_name('finished.json').write_bytes(contents)
    receipt = runtime.finish(store, run, jid, launch, succeeded=True)
    assert receipt['status'] == 'unavailable' and receipt['warnings']
    assert runtime.evidence(store, run, jid) is None


def test_linked_runtime_directory_is_not_followed(attempt, tmp_path):
    store, run, jid = attempt
    target = tmp_path / 'outside'
    target.mkdir()
    Path(run['folder'], 'runtime').symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        runtime.prepare(store, run, store.job(jid))
    assert list(target.iterdir()) == []


@pytest.mark.parametrize('pipeline', ['hybas', 'basis', 'micpscope'])
def test_real_sample_free_snakemake_invocation(attempt, tmp_path, pipeline):
    snakemake = pytest.importorskip('snakemake')
    store, run, jid = attempt
    run['tool'] = pipeline
    launch = runtime.prepare(store, run, store.job(jid))
    snakefile = tmp_path / 'Empty.smk'
    snakefile.write_text('rule all:\n    input: []\n')
    working = tmp_path / 'native-work'
    working.mkdir()
    env = dict(os.environ, **launch['environment'])
    process = subprocess.run([sys.executable, str(DATA / 'run_existing.py'), 'snakemake',
                              '--snakefile', str(snakefile), '--directory', str(working), '--cores', '1'],
                             env=env, capture_output=True, text=True, timeout=40)
    assert process.returncode == 0, process.stdout + process.stderr
    runtime.finish(store, run, jid, launch, succeeded=True)
    assert runtime.evidence(store, run, jid)['versions']['snakemake'] == snakemake.__version__


@pytest.mark.parametrize('observer_failure', [False, True])
def test_worker_finalizes_receipt_before_completion_capture(attempt, fake_controller, monkeypatch, observer_failure):
    from bactologix import methods, runs
    from bactologix.engine import Context
    store, run, jid = attempt
    monkeypatch.setattr(runs, 'validate_launch', lambda *a, **kw: copy.deepcopy(run))
    monkeypatch.setattr('bactologix.variants.verify', lambda run: None)
    monkeypatch.setattr(runs, 'tree_digest', lambda path: run['workflow_sha256'])
    monkeypatch.setattr(runs, 'current_controller', lambda *a: sys.prefix)
    monkeypatch.setattr(runs, 'command', lambda *a: [sys.executable, str(DATA / 'run_existing.py'), 'snakemake'])
    monkeypatch.setenv('PYTHONPATH', str(fake_controller))
    if observer_failure:
        def unavailable(*args):
            raise OSError('fixture observer failure')
        monkeypatch.setattr(runtime, 'prepare', unavailable)
    captured = []
    monkeypatch.setattr(methods, 'capture', lambda store, saved, job: captured.append(runtime.evidence(store, saved, job)))
    ctx = Context(store, store.job(jid))
    try:
        runs.execute_workflow(ctx)
    finally:
        ctx.log.close()
    if observer_failure:
        assert captured == [None]
        assert 'fixture observer failure' in store.log_path(jid).read_text()
    else:
        assert captured[0]['versions']['snakemake'] == '9.6.2-observed-fixture'
    assert store.get('runs', run['id'])['completed']


@pytest.mark.parametrize('success', [True, False])
def test_methods_document_and_zip_preserve_actual_invocation_without_inventing_steps(attempt, fake_controller, success):
    from bactologix import methods
    store, run, jid = attempt
    _, process, saved = observe(attempt, fake_controller, '0' if success else '7')
    # A stale completed field cannot turn a failed retry into completed Methods.
    run['completed'] = saved['observations']['finished']['finished_at']
    if success:
        snapshot = methods.capture(store, run, jid)
        assert snapshot['runtime'] == runtime.evidence(store, run, jid)
    store.update_job(jid, status='succeeded' if success else 'failed')
    store.put('runs', run['id'], run)
    doc = methods.build(store, run)
    if success:
        receipt = runtime.evidence(store, run, jid)
        assert doc['runtime_evidence'] == receipt
        versions = {row['tool']: row for row in doc['software']}
        assert versions['snakemake']['version'] == '9.6.2-observed-fixture'
        assert versions['controller_python']['version'] == sys.version.split()[0]
        assert 'Snakemake 9.6.2-observed-fixture' in doc['text']
        assert 'Controller Python ' + sys.version.split()[0] in doc['text']
        assert 'No per-step execution metadata is available' in doc['text']
        assert 'individual steps require their own evidence' in doc['text']
        assert not doc['evidence']['records'] and not doc['evidence']['decisions']
    else:
        assert process.returncode == 7 and doc['state'] == 'Incomplete execution'
        assert doc['runtime_evidence']['status'] == 'failed'
        assert doc['runtime_evidence']['exit_code'] == 7
        assert '9.6.2-observed-fixture' in doc['text']
        assert 'outcome=failed' in doc['text']
        assert not doc['completed']
    artifact = methods.download(store, run, 'zip')
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(artifact['base64']))) as archive:
        provenance = json.loads(archive.read('methods-provenance.json'))
        assert provenance['runtime_evidence'] == doc['runtime_evidence']
        if success:
            exported = provenance['runtime_evidence']
            assert exported['job_id'] == jid and exported['run_id'] == run['id']
            assert exported['identity'] == saved['identity']
            assert exported['context_sha256'] == store.get(runtime.LAUNCH, jid)['context_sha256']
            for name in ('methods.txt', 'methods.md', 'methods.html'):
                text = archive.read(name).decode()
                assert '9.6.2-observed-fixture' in text and 'Controller Python' in text
                assert 'individual steps require their own evidence' in text
        else:
            exported = archive.read('methods.txt')
            assert b'9.6.2-observed-fixture' in exported and b'outcome=failed' in exported
    assert store.get(runtime.RECEIPT, jid) == saved
