"""Real sample-free subprocess checks; no user workstation or analysis claims."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from bactologix import workstation as w
from bactologix.catalog import PIPELINES
from bactologix.engine import Context, Cancelled
from bactologix.store import Store, now
from bactologix.ui_backend import dispatch


def check_job(store, payload=None):
    jid = store.submit('workstation', payload or w.request(store))
    store.update_job(jid, status='running', started=now())
    return jid


def execute(store, jid):
    ctx = Context(store, store.job(jid))
    try:
        w.execute_check(ctx)
    finally:
        ctx.observe('finish', 'succeeded')
        ctx.log.close()


def test_real_app_only_checks_keep_history_and_preserve_workspace(tmp_path):
    store = Store(tmp_path / 'workspace with spaces')
    store.put('configs', 'hybas', 'custom: unchanged')
    sentinel = store.home / 'resources' / 'keep.txt'
    sentinel.write_text('retained')
    jid = check_job(store)
    execute(store, jid)
    store.update_job(jid, status='succeeded', finished=now())
    result = w.inspect(store, jid)
    rows = {r['id']: r for r in result['rows']}
    assert all(rows[key]['state'] == 'Passed' for key in ('queue', 'storage', 'process', 'cancellation'))
    assert rows['runner']['state'] == 'Not verified'
    assert not result['partial'] and not result['observations']
    assert store.get('processes', jid)['pid'] is None
    assert store.get('configs', 'hybas') == 'custom: unchanged'
    assert sentinel.read_text() == 'retained' and not store.all('samples') and not store.all('runs')
    assert not list(store.home.glob('workstation-*'))
    second = check_job(store)
    assert w.history(store)[0]['id'] == second
    assert w.inspect(store, jid) == result


def test_download_and_user_observations_are_separate_and_dated(tmp_path):
    store = Store(tmp_path)
    jid = check_job(store)
    store.put('workstation_checks', jid, {'id': jid, 'created': now(), 'finished': now(),
        'rows': [{'id': 'fixture', 'label': '<script>bad</script>', 'state': 'Failed',
                  'detail': 'Permission denied <img src=x>', 'checked_at': now()}], 'observations': {}})
    with pytest.raises(ValueError, match='finish'):
        w.record_observation(store, jid, 'browser', True)
    store.update_job(jid, status='failed')
    with pytest.raises(ValueError, match='before confirming'):
        w.record_observation(store, jid, 'report', True)
    report = w.artifact(store, jid, 'report')
    html = base64.b64decode(report['base64']).decode()
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert not w.inspect(store, jid)['observations']
    result = w.record_observation(store, jid, 'report', True)
    assert result['observations']['report']['source'] == 'user-reported'
    result = w.record_observation(store, jid, 'report', False)
    assert result['observations']['report']['confirmed'] is False
    data = json.loads(w.artifact(store, jid, 'download')['text'])
    assert data['job_status'] == 'failed' and data['partial']
    assert 'download' not in data['observations']
    w.record_observation(store, jid, 'download', True)
    assert w.inspect(store, jid)['observations']['download']['confirmed']
    for key, value in [('unknown', True), ('browser', 'yes'), ([], True)]:
        with pytest.raises(ValueError):
            w.record_observation(store, jid, key, value)
    with pytest.raises(ValueError):
        w.inspect(store, 'job-missing')


@pytest.mark.parametrize('error', [PermissionError(13, 'Permission denied'), OSError(28, 'No space left on device')])
def test_storage_failure_retains_other_check_evidence(tmp_path, monkeypatch, error):
    store = Store(tmp_path)
    jid = check_job(store)
    def denied(*args, **kwargs):
        raise error
    monkeypatch.setattr(w.tempfile, 'TemporaryDirectory', denied)
    with pytest.raises(RuntimeError, match='checks failed'):
        execute(store, jid)
    record = w.inspect(store, jid)
    rows = {r['id']: r for r in record['rows']}
    assert rows['storage']['state'] == 'Failed'
    assert rows['process']['state'] == rows['cancellation']['state'] == 'Passed'
    assert record['finished']


def test_user_cancellation_is_not_a_pass(tmp_path):
    store = Store(tmp_path)
    jid = check_job(store)
    ctx = Context(store, store.job(jid))
    thread = threading.Thread(target=lambda: (time.sleep(.4), store.cancel(jid)))
    thread.start()
    try:
        with pytest.raises(Cancelled):
            w.execute_check(ctx)
    finally:
        thread.join()
        ctx.observe('finish', 'cancelled')
        ctx.log.close()
    store.update_job(jid, status='cancelled')
    result = w.inspect(store, jid)
    assert result['partial'] and not result.get('finished')
    assert not any(r['id'] == 'cancellation' and r['state'] == 'Passed' for r in result['rows'])
    assert store.get('processes', jid)['pid'] is None


def test_probe_timeout_reaps_child_and_keeps_status_distinct(tmp_path):
    store = Store(tmp_path)
    jid = check_job(store)
    ctx = Context(store, store.job(jid))
    try:
        with pytest.raises(TimeoutError, match='time limit'):
            ctx.command([sys.executable, '-c', 'import time; time.sleep(100)'], timeout=.2)
        assert store.get('processes', jid)['pid'] is None
    finally:
        ctx.observe('finish', 'failed')
        ctx.log.close()


def runner_fixture(store, tmp_path):
    snakemake = pytest.importorskip('snakemake')
    source = tmp_path / 'runner-source'
    definition = source / PIPELINES['hybas']['environment']
    definition.parent.mkdir(parents=True)
    definition.write_text(f'dependencies: [python={sys.version_info.major}.{sys.version_info.minor}, snakemake={snakemake.__version__}]')
    wrapper = tmp_path / 'software-conda-fixture'
    wrapper.write_text(f'#!{sys.executable}\nimport os,sys\nargs=sys.argv[5:]\nexe={str(Path(sys.executable).parent)!r}+"/"+args[0]\nos.execv(exe,[exe,*args[1:]])\n')
    wrapper.chmod(0o755)
    store.put('settings', 'conda', str(wrapper))
    item = {'id': 'test-runner', 'ready': True, 'tool': 'hybas', 'source': str(source),
            'controller': str(Path(sys.executable).parent.parent),
            'version': {'commit': PIPELINES['hybas']['commit']}}
    store.put('installs', item['id'], item)
    return item


def test_real_native_software_workflow_in_existing_python_environment(tmp_path):
    store = Store(tmp_path / 'workspace')
    item = runner_fixture(store, tmp_path)
    before = copy.deepcopy(item)
    jid = check_job(store, w.request(store, item['id']))
    execute(store, jid)
    result = w.inspect(store, jid)
    assert next(r for r in result['rows'] if r['id'] == 'runner')['state'] == 'Passed'
    assert store.get('installs', item['id']) == before
    assert not store.all('samples') and not store.all('runs')
    log = store.log_path(jid).read_text()
    assert 'Controller verified' in log and '1 of 1 steps' in log
    assert '--use-conda' not in log


def test_runner_selection_is_frozen_and_revalidated(tmp_path):
    store = Store(tmp_path / 'workspace')
    item = runner_fixture(store, tmp_path)
    jid = check_job(store, w.request(store, item['id']))
    store.put('installs', item['id'], {**item, 'controller': '/changed'})
    with pytest.raises(RuntimeError, match='checks failed'):
        execute(store, jid)
    row = next(r for r in w.inspect(store, jid)['rows'] if r['id'] == 'runner')
    assert row['state'] == 'Failed' and 'changed' in row['detail']
    assert 'snakemake --cores' not in store.log_path(jid).read_text()


def test_explicit_dispatch_and_duplicate_check_guard(tmp_path, monkeypatch):
    store = Store(tmp_path)
    launches = []
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda s: launches.append(s.home))
    before = store.jobs()
    assert w.history(store) == [] and store.jobs() == before
    dispatch(store, 'check_workstation', {})
    assert len(launches) == 1 and len(store.jobs()) == 1
    with pytest.raises(ValueError, match='already queued'):
        dispatch(store, 'check_workstation', {})
    jid = store.jobs()[0]['id']
    store.cancel(jid)
    assert w.inspect(store, jid)['partial']
    with pytest.raises(ValueError, match='stop'):
        w.artifact(store, jid, 'download')
    with pytest.raises(ValueError):
        dispatch(store, 'check_workstation', {'installation': '/untrusted'})


def test_detached_worker_recovers_interrupted_check_without_rewriting_history(tmp_path):
    store = Store(tmp_path)
    old = check_job(store)
    saved = {'id': old, 'rows': [{'id': 'queue', 'state': 'Passed'}], 'created': now()}
    store.put('workstation_checks', old, saved)
    result = subprocess.run([sys.executable, '-m', 'bactologix.worker', '--home', str(store.home)], timeout=30, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert store.job(old)['status'] == 'interrupted'
    assert store.get('workstation_checks', old) == saved
    from bactologix.queue import change
    assert store.queue_snapshot()['paused']
    change(store, 'resume', store.queue_snapshot()['revision'])
    new = store.submit('workstation', w.request(store))
    subprocess.run([sys.executable, '-m', 'bactologix.worker', '--home', str(store.home)], timeout=30, check=True)
    assert store.job(new)['status'] == 'succeeded'
    assert w.inspect(store, old)['partial']
    assert not w.inspect(store, new)['partial']


def test_user_stop_takes_precedence_over_probe_timeout(tmp_path):
    store = Store(tmp_path)
    jid = check_job(store)
    ctx = Context(store, store.job(jid))
    thread = threading.Thread(target=lambda: (time.sleep(.4), store.cancel(jid)))
    thread.start()
    try:
        with pytest.raises(Cancelled):
            ctx.command([sys.executable, '-c',
                         'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(100)'],
                        timeout=1)
        assert store.get('processes', jid)['pid'] is None
    finally:
        thread.join()
        ctx.observe('finish', 'cancelled')
        ctx.log.close()
