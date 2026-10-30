"""Desktop ownership and shutdown tests; never invoke WSL or analysis tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest
from bactologix import desktop
from bactologix.store import Store


def session(tmp_path, store):
    key = uuid.uuid4().hex
    directory = tmp_path / key
    directory.mkdir()
    result = desktop.Session(store, directory, key)
    desktop.atomic(directory / 'heartbeat.json', {'session': key, 'time': time.time()})
    result.tick('http://127.0.0.1:8501', True)
    return result


def request(item, action, key=None):
    desktop.atomic(item.root / 'request.json', {'session': key or item.key, 'action': action, 'nonce': uuid.uuid4().hex})


def test_close_owns_only_its_jobs_and_blocks_racing_submissions(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    a, b = session(tmp_path, store), session(tmp_path, store)
    monkeypatch.setenv(desktop.ENV, a.key)
    ja = store.submit('smoke', {})
    monkeypatch.setenv(desktop.ENV, b.key)
    jb = store.submit('smoke', {})
    monkeypatch.delenv(desktop.ENV)
    independent = store.submit('smoke', {})
    request(a, 'close', key=b.key)
    assert not a.tick('http://127.0.0.1:8501', True)
    assert a.phase == 'ready'
    request(a, 'close')
    assert not a.tick('http://127.0.0.1:8501', True)
    assert a.phase == 'confirming'
    monkeypatch.setenv(desktop.ENV, a.key)
    with pytest.raises(ValueError, match='closing'):
        store.submit('smoke', {})
    request(a, 'reopen')
    a.tick('', True)
    assert a.phase == 'ready' and store.job(ja)['status'] == 'queued'
    request(a, 'confirm_close')
    assert a.tick('', True)
    assert store.job(ja)['status'] == 'cancelled'
    assert store.job(jb)['status'] == store.job(independent)['status'] == 'queued'


def test_lost_supervisor_cancels_only_owned_work(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    item = session(tmp_path, store)
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('smoke', {})
    item.started -= 100
    desktop.atomic(item.root / 'heartbeat.json', {'session': item.key, 'time': time.time() - 100})
    assert item.tick('', True)
    assert store.job(jid)['status'] == 'cancelled'
    assert desktop.owner_cancelled(store, jid)


def test_running_application_update_finishes_before_shutdown(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    item = session(tmp_path, store)
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('app_update', {})
    store.update_job(jid, status='running')
    request(item, 'confirm_close')
    assert not item.tick('', True)
    assert store.job(jid)['cancel'] == 0
    assert desktop.read(item.root / 'status.json')['installing_update']
    store.update_job(jid, status='succeeded')
    assert item.tick('', True)


def test_job_lease_detects_killed_launcher(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    item = session(tmp_path, store)
    monkeypatch.setenv(desktop.ENV, item.key)
    jid = store.submit('smoke', {})
    assert not desktop.owner_cancelled(store, jid)
    store.put('desktop_sessions', item.key, {'phase':'ready', 'last_seen':time.time()-100})
    assert desktop.owner_cancelled(store, jid)


def test_real_desktop_server_stops_cleanly_and_preserves_unrelated_process(tmp_path):
    home, control = tmp_path / 'workspace', tmp_path / 'control'
    control.mkdir()
    key = uuid.uuid4().hex
    desktop.atomic(control / 'heartbeat.json', {'session':key, 'time':time.time()})
    external = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    log = (tmp_path / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, '-m', 'bactologix.cli', '--home', str(home), '--host','127.0.0.1',
        '--desktop-control',str(control),'--desktop-session',key], stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            desktop.atomic(control / 'heartbeat.json', {'session':key,'time':time.time()})
            if desktop.read(control / 'status.json').get('phase') == 'ready': break
            assert process.poll() is None, (tmp_path / 'server.log').read_text()
            time.sleep(.1)
        else: pytest.fail((tmp_path / 'server.log').read_text())
        desktop.atomic(control / 'request.json', {'session':key,'action':'close','nonce':'close'})
        assert process.wait(timeout=15) == 0, (tmp_path / 'server.log').read_text()
        assert desktop.read(control / 'status.json')['phase'] == 'stopped'
        assert external.poll() is None
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=15)
        external.terminate(); external.wait(timeout=5); log.close()


def test_cancellation_reaps_owned_process_group_not_independent_work(tmp_path):
    store = Store(tmp_path / 'workspace')
    jid = store.submit('smoke', {})
    job = store.claim()
    marker = tmp_path / 'descendant.txt'
    child = "import time,signal; from pathlib import Path; signal.signal(signal.SIGINT,signal.SIG_IGN); p=Path(" + repr(str(marker)) + ");\nwhile True:\n p.write_text(str(time.time())); time.sleep(.05)"
    leader = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(child)+']); time.sleep(90)'
    program = '''import sys
from bactologix.store import Store
from bactologix.engine import Context, Cancelled
store = Store(sys.argv[1]); context = Context(store, store.job(sys.argv[2]))
try: context.command([sys.executable, '-c', sys.argv[3]])
except Cancelled: pass
finally: context.log.close()
'''
    external = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])
    worker = subprocess.Popen([sys.executable,'-c',program,str(store.home),jid,leader])
    try:
        deadline = time.monotonic()+15
        while not marker.exists() and time.monotonic()<deadline: time.sleep(.05)
        assert marker.exists()
        store.cancel(jid)
        assert worker.wait(timeout=15) == 0
        before = marker.read_text(); time.sleep(.3)
        assert marker.read_text() == before
        assert external.poll() is None
    finally:
        if worker.poll() is None: worker.kill(); worker.wait()
        external.terminate(); external.wait()


def test_owned_waiting_worker_exits_without_touching_other_worker_lock(tmp_path):
    import fcntl
    store = Store(tmp_path / 'workspace')
    item = session(tmp_path, store)
    with (store.home / 'worker.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        worker = subprocess.Popen([sys.executable, '-m', 'bactologix.worker', '--home', str(store.home), '--desktop-owner', item.key])
        try:
            time.sleep(.3)
            assert worker.poll() is None
            item.stop()
            assert worker.wait(timeout=5) == 0
            # The external worker lock is still held, unaffected by shutdown.
            with (store.home / 'worker.lock').open('a+') as probe:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if worker.poll() is None: worker.kill(); worker.wait()
