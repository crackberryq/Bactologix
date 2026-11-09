"""Exercise confirmed cleanup through the real serialized background worker."""
import fcntl
import subprocess
import sys
import time

from bactologix import storage_cleanup as cleanup
from bactologix.store import Store


def test_cleanup_waits_for_worker_lease_then_deletes_only_reviewed_run(tmp_path, monkeypatch):
    # Process ownership has separate fixture tests. This worker test uses a
    # controlled process inventory so host/container /proc restrictions cannot
    # turn a serialization test into a host-permission test.
    children = []
    monkeypatch.setattr(cleanup, '_check_processes', lambda paths: None)
    def launch(store):
        code = '''
import sys
from bactologix import storage_cleanup
from bactologix.store import Store
from bactologix.worker import work
storage_cleanup._check_processes = lambda paths: None
work(Store(sys.argv[1]))
'''
        children.append(subprocess.Popen([sys.executable, '-c', code, str(store.home)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    monkeypatch.setattr('bactologix.engine.launch_worker', launch)
    store = Store(tmp_path / 'workspace')
    original = tmp_path / 'original-input.fastq'
    original.write_text('original input fixture')
    transferred = tmp_path / 'transferred-result.txt'
    transferred.write_text('verified copy fixture')
    folder = store.home / 'runs' / 'run-cleanup'
    output = folder / 'outputs'
    output.mkdir(parents=True)
    (output / 'result.txt').write_text('saved result fixture')
    (folder / 'input.fastq').symlink_to(original)
    store.put('runs', 'run-cleanup', {'id': 'run-cleanup', 'tool': 'hybas',
        'folder': str(folder), 'output': str(output), 'label': 'Software cleanup fixture',
        'config': '{}', 'inputs': [], 'sample': {'id': 'sample'}})
    plan = cleanup.preview(store, [{'kind': 'run', 'id': 'run-cleanup'}])
    assert plan['blocked'] == []
    with (store.home / 'worker.lock').open('a+') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        jid = cleanup.request(store, plan['id'], plan['signature'], 'DELETE', True)
        time.sleep(.4)
        assert store.job(jid)['status'] == 'queued'
        assert (output / 'result.txt').exists()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and store.job(jid)['status'] in {'queued', 'running'}:
        time.sleep(.1)
    job = store.job(jid)
    assert job['status'] == 'succeeded', store.log_path(jid).read_text()
    assert not folder.exists() and store.get('runs', 'run-cleanup') is None
    assert original.read_text() == 'original input fixture'
    assert transferred.read_text() == 'verified copy fixture'
    receipt = cleanup.receipt(store, jid)
    assert receipt['status'] == 'completed'
    assert store.get('deleted_runs', 'run-cleanup')['cleanup'] == jid
    for child in children:
        _, errors = child.communicate(timeout=10)
        assert child.returncode == 0, errors
