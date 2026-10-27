"""Restart only a verified, completed update belonging to this launcher."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

import pytest

from bactologix import __version__, restart
from bactologix.store import Store


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    store = Store(tmp_path)
    item = restart.register(store, 'http://127.0.0.1:8502')
    restart.mark_ready(store, item)
    monkeypatch.setenv('BACTOLOGIX_LAUNCH_ID', item['id'])
    job = store.submit('app_update', {'release': {'version': __version__}, 'launcher_id': item['id']})
    store.update_job(job, status='succeeded')
    state = {'job': job, 'version': __version__, 'launcher_id': item['id'], 'environment': sys.prefix,
             'pip_started_at': time.time(), 'status': 'installed'}
    store.put('settings', 'app-update', state)
    return store, item, state


def test_handoff_requires_fresh_import_verification(handoff, monkeypatch):
    store, item, state = handoff
    assert restart.current_launcher(store)['id'] == item['id']
    assert restart.pending(store, item) == state
    calls = []
    monkeypatch.setattr(restart.subprocess, 'run', lambda argv, **kw: calls.append((argv, kw)))
    assert restart.verify_and_mark(store, item, state)
    assert calls[0][0][:2] == [sys.executable, '-c'] and calls[0][1]['check']
    assert store.get('settings', 'app-update')['restart_status'] == 'restarting'
    restart.mark_ready(store, item)
    assert store.get('settings', 'app-update')['restart_status'] == 'ready'
    assert restart.pending(store, item) is None


@pytest.mark.parametrize('change', ['installing', 'failed', 'unfinished-job', 'wrong-launcher', 'wrong-environment', 'old', 'busy'])
def test_incomplete_unrelated_or_busy_updates_never_restart(handoff, change):
    store, item, state = handoff
    if change in {'installing', 'failed'}: state['status'] = change
    elif change == 'unfinished-job': store.update_job(state['job'], status='running')
    elif change == 'wrong-launcher': state['launcher_id'] = 'another-launcher'
    elif change == 'wrong-environment': state['environment'] = '/another-environment'
    elif change == 'old': state['pip_started_at'] = item['started_at'] - 1
    elif change == 'busy': store.submit('smoke', {})
    store.put('settings', 'app-update', state)
    assert restart.pending(store, item) is None


def test_verification_failure_keeps_old_server_and_prevents_retry_loop(handoff, monkeypatch):
    store, item, state = handoff
    def broken(*args, **kwargs): raise subprocess.CalledProcessError(1, ['python'])
    monkeypatch.setattr(restart.subprocess, 'run', broken)
    assert not restart.verify_and_mark(store, item, state)
    assert store.get('launchers', item['id'])['status'] == 'ready'
    assert restart.pending(store, item) is None


def test_static_identity_is_per_server_and_contains_no_workspace_paths(tmp_path):
    item = {'id': 'unique-launch', 'version': '1.2.3', 'environment': '/private/environment'}
    app = restart.make_app(tmp_path, item)
    assert app.is_file()
    assert json.loads((tmp_path / 'static/runtime.json').read_text()) == {'id': 'unique-launch', 'version': '1.2.3'}


def test_real_launcher_restarts_same_url_once_after_success(tmp_path):
    from bactologix.launcher import free_port, healthy
    from bactologix import __version__
    port = free_port('127.0.0.1')
    url = f'http://127.0.0.1:{port}'
    store = Store(tmp_path / 'workspace')
    store.put('configs', 'hybas', 'saved_fixture: preserve\n')
    def runtime():
        try:
            with build_opener(ProxyHandler({})).open(url + '/app/static/runtime.json', timeout=1) as response:
                return json.load(response)
        except (OSError, ValueError):
            return None
    def until(predicate, seconds=25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value: return value
            if proc.poll() is not None: pytest.fail(log.read_text())
            time.sleep(.1)
        pytest.fail(log.read_text())
    log = tmp_path / 'server.log'
    with log.open('w') as output:
        proc = subprocess.Popen([str(Path(sys.executable).with_name('bactologix')), '--home', str(store.home),
                                 '--host', '127.0.0.1', '--port', str(port), '--no-browser'],
                                stdout=output, stderr=subprocess.STDOUT)
        try:
            old = until(lambda: runtime() if any(x['status'] == 'ready' for x in store.all('launchers')) else None)
            job = store.submit('app_update', {'release': {'version': __version__}, 'launcher_id': old['id']})
            store.update_job(job, status='running')
            state = {'job': job, 'version': __version__, 'status': 'installed', 'launcher_id': old['id'],
                     'environment': sys.prefix, 'pip_started_at': time.time()}
            store.put('settings', 'app-update', state)
            time.sleep(.5)
            assert runtime()['id'] == old['id'], 'Do not stop a server while its update job is still running'
            store.update_job(job, status='succeeded')
            fresh = until(lambda: (r if (r := runtime()) and r['id'] != old['id'] else None))
            assert fresh['version'] == __version__ and healthy(url)
            until(lambda: store.get('settings', 'app-update').get('restart_status') == 'ready')
            time.sleep(.5)
            assert runtime()['id'] == fresh['id'], 'A completed handoff must not trigger a restart loop'
            assert store.get('configs', 'hybas') == 'saved_fixture: preserve\n'
            assert not store.get('launchers', old['id'])
        finally:
            proc.send_signal(signal.SIGTERM)
            try: proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
        assert not healthy(url)
