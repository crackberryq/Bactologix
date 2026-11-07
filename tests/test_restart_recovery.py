"""Software-only interruption recovery; never launches pipeline analyses."""
import fcntl
import subprocess
import sys
from bactologix.store import Store
from bactologix.queue import reconcile_interrupted
from bactologix.ui_backend import dispatch, snapshot


def abandoned(tmp_path):
    store = Store(tmp_path)
    jid = store.submit('smoke', {'unchanged': True})
    store.claim()
    store.cancel(jid)
    store.put('job_telemetry', jid, {'id': jid, 'samples': 5})
    output = tmp_path / 'runs' / 'retained.txt'
    output.write_text('original result')
    store.log_path(jid).write_text('last observed output')
    return store, jid


def test_refresh_recovers_rebooted_job_and_pauses_waiting_work(tmp_path):
    store, jid = abandoned(tmp_path)
    waiting = store.submit('smoke', {'waiting': True})
    before = store.job(jid)
    view = snapshot(Store(tmp_path))
    after = store.job(jid)
    assert after['status'] == 'interrupted' and after['finished']
    assert after['payload'] == before['payload'] and after['started'] == before['started']
    assert after['cancel'] == 1  # retain the user's request in the audit record
    assert store.get('job_telemetry', jid) == {'id': jid, 'samples': 5}
    assert (tmp_path/'runs/retained.txt').read_text() == 'original result'
    assert store.log_path(jid).read_text() == 'last observed output'
    assert view['queue']['paused'] and view['queue']['recovered'] == [jid]
    assert store.job(waiting)['status'] == 'queued' and store.claim() is None
    assert reconcile_interrupted(store) == []
    assert store.job(jid)['finished'] == after['finished']


def test_stop_recovers_without_launching_worker(tmp_path, monkeypatch):
    store, jid = abandoned(tmp_path)
    monkeypatch.setattr('bactologix.ui_backend.launch_worker', lambda *_: (_ for _ in ()).throw(AssertionError('must not launch')))
    assert 'interrupted' in dispatch(store, 'cancel_job', {'id': jid})['message']
    assert store.job(jid)['status'] == 'interrupted'


def test_live_worker_lock_keeps_job_running_and_stop_pending(tmp_path):
    store, jid = abandoned(tmp_path)
    with (store.home/'worker.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert reconcile_interrupted(store) == []
        snapshot(store)
        assert 'Stop requested' in dispatch(store, 'cancel_job', {'id': jid})['message']
        assert store.job(jid)['status'] == 'running'
        assert not store.queue_snapshot()['paused']
    assert reconcile_interrupted(store) == [jid]


def test_surviving_child_retains_lock_after_parent_descriptor_closes(tmp_path):
    store, jid = abandoned(tmp_path)
    lock = (store.home/'worker.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    child = subprocess.Popen([sys.executable, '-c', 'import sys; print("ready", flush=True); sys.stdin.read()'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, pass_fds=(lock.fileno(),))
    try:
        assert child.stdout.readline().strip() == 'ready'
        lock.close()
        assert reconcile_interrupted(store) == []
        assert store.job(jid)['status'] == 'running'
    finally:
        lock.close()
        child.communicate('', timeout=10)
    assert reconcile_interrupted(store) == [jid]
