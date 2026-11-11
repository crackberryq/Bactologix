"""Real harmless writers verify pause ownership survives an abrupt worker exit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

fcntl = pytest.importorskip('fcntl', reason='Native suspension uses POSIX worker locks')

from bactologix.queue import reconcile_interrupted
from bactologix.store import Store


WRITER = r'''
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil

role, directory, lock_fd, detached = sys.argv[1:]
directory = Path(directory)
identity = {'pid': os.getpid(), 'created': psutil.Process().create_time()}
(directory / (role + '.identity.json')).write_text(json.dumps(identity))
if role == 'root':
    subprocess.Popen([sys.executable, __file__, 'child', str(directory), lock_fd, detached],
                     pass_fds=(int(lock_fd),), start_new_session=detached == 'yes')
with (directory / (role + '.bytes')).open('ab', buffering=0) as output:
    while True:
        output.write(b'x')
        time.sleep(.02)
'''


WORKER = r'''
import fcntl
from pathlib import Path
import sys
from bactologix import disk_pause
from bactologix.engine import Context
from bactologix.store import Store, now

home, jid, writer, directory, detached = sys.argv[1:]
directory = Path(directory)
class SyntheticCapacity:
    def __init__(self, *args, **kwargs):
        pass
    def include_command(self, cwd=None, extra_env=None):
        pass
    def sample(self):
        available = (9 if (directory / 'low-space').exists() else 40) * 1024**3
        return {'volumes': [{'id': 'fixture', 'label': 'Software fixture disk',
                    'path': str(directory), 'available_bytes': available,
                    'total_bytes': 100 * 1024**3, 'kind': 'linux'}],
                'errors': [], 'checked_at': now()}

disk_pause.CapacityProbe = SyntheticCapacity
store = Store(home)
with (store.home / 'worker.lock').open('a+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    context = Context(store, store.job(jid), lock_fd=lock.fileno())
    context.command([sys.executable, writer, 'root', str(directory), str(lock.fileno()), detached])
'''


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for the software-only worker fixture')


def identity(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def owned_process(record):
    if not record:
        return None
    try:
        process = psutil.Process(record['pid'])
        if process.create_time() != record['created'] or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except psutil.NoSuchProcess:
        return None


def bytes_written(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


@pytest.mark.skipif(sys.platform != 'linux', reason='POSIX process suspension is tested on Linux')
@pytest.mark.parametrize('detached', ['no', 'yes'])
def test_killed_paused_worker_reaps_owned_writers_and_releases_lock(tmp_path, detached):
    """A SIGKILL must not leave paused descendants holding the worker lease forever."""
    store = Store(tmp_path / 'workspace')
    run_folder = store.home / 'runs' / 'run-fixture'
    output = run_folder / 'outputs'
    output.mkdir(parents=True)
    store.put('runs', 'run-fixture', {
        'id': 'run-fixture', 'tool': 'hybas', 'folder': str(run_folder),
        'output': str(output), 'config': '{}', 'cores': 1, 'memory_mb': 512,
    })
    jid = store.submit('workflow', {'run_id': 'run-fixture', 'action': 'run'})
    store.update_job(jid, status='running')
    writer = tmp_path / 'writer.py'
    writer.write_text(WRITER)
    env = dict(os.environ)
    env.pop('BACTOLOGIX_DESKTOP_SESSION', None)
    env['PYTHONPATH'] = str(Path(__file__).parents[1] / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    with (tmp_path / 'worker.out').open('w+') as log:
        worker = subprocess.Popen([sys.executable, '-u', '-c', WORKER, str(store.home), jid,
                                   str(writer), str(tmp_path), detached], env=env,
                                  stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        unrelated = subprocess.Popen([sys.executable, str(writer), 'unrelated', str(tmp_path), '-1', 'yes'],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        records, watchers = [], []
        try:
            def started():
                if worker.poll() is not None:
                    log.seek(0)
                    raise AssertionError('Worker fixture exited early:\n' + log.read())
                return all(bytes_written(tmp_path / (role + '.bytes')) >= 3
                           for role in ('root', 'child', 'unrelated'))

            wait_for(started)
            records = [identity(tmp_path / (role + '.identity.json')) for role in ('root', 'child')]
            assert all(records)
            (tmp_path / 'low-space').touch()
            wait_for(lambda: store.get('disk_safety', jid, {}).get('state') == 'paused')
            for record in records:
                process = owned_process(record)
                assert process is not None and process.status() == psutil.STATUS_STOPPED
            before = [bytes_written(tmp_path / (role + '.bytes')) for role in ('root', 'child')]
            unrelated_before = bytes_written(tmp_path / 'unrelated.bytes')
            time.sleep(.3)
            assert [bytes_written(tmp_path / (role + '.bytes')) for role in ('root', 'child')] == before
            with (store.home / 'worker.lock').open('a+') as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert reconcile_interrupted(store) == []
            assert store.job(jid)['status'] == 'running'
            watchers = [{'pid': process.pid, 'created': process.create_time()}
                        for process in psutil.Process(worker.pid).children()
                        if '--watch' in process.cmdline()]
            assert len(watchers) == 1

            worker.kill()
            worker.wait(timeout=5)
            wait_for(lambda: all(owned_process(record) is None for record in records))
            wait_for(lambda: all(owned_process(record) is None for record in watchers))
            assert [bytes_written(tmp_path / (role + '.bytes')) for role in ('root', 'child')] == before
            assert unrelated.poll() is None
            assert bytes_written(tmp_path / 'unrelated.bytes') > unrelated_before

            def lock_released():
                with (store.home / 'worker.lock').open('a+') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return True
                    except BlockingIOError:
                        return False

            wait_for(lock_released)
            assert reconcile_interrupted(store) == [jid]
            assert store.job(jid)['status'] == 'interrupted'
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
            for role in ('root', 'child'):
                record = identity(tmp_path / (role + '.identity.json'))
                process = owned_process(record)
                if process is not None:
                    process.kill()
            for record in watchers:
                process = owned_process(record)
                if process is not None:
                    process.kill()
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=5)
