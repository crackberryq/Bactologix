"""Identity-checked local workflow suspension and worker-loss cleanup."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import psutil

MAX_MEMBERS = 8192
STOPPED = {psutil.STATUS_STOPPED, psutil.STATUS_TRACING_STOP}

class SuspensionError(RuntimeError):
    pass

class OwnedTree:
    def __init__(self, pid, created=None):
        self.members, self.stopped_by_us = {}, set()
        root = psutil.Process(pid)
        if created is not None and root.create_time() != created:
            raise SuspensionError('The workflow process identity changed.')
        self.root_identity = self.remember(root)

    def remember(self, process):
        identity = (process.pid, process.create_time())
        if len(self.members) >= MAX_MEMBERS and identity not in self.members:
            raise SuspensionError('The workflow process tree exceeds the safe monitoring limit.')
        self.members[identity] = process
        return identity

    def alive(self, process, identity):
        try:
            return process.is_running() and process.create_time() == identity[1] and process.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
        except psutil.NoSuchProcess:
            return False

    def refresh(self):
        for identity, process in list(self.members.items()):
            if not self.alive(process, identity):
                continue
            try:
                for child in process.children(recursive=True):
                    self.remember(child)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                raise SuspensionError('Cannot inspect an owned workflow process.') from exc
        return {key: process for key, process in self.members.items() if self.alive(process, key)}

    def freeze(self):
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            members = self.refresh()
            for identity, process in members.items():
                try:
                    if process.status() not in STOPPED:
                        process.send_signal(signal.SIGSTOP)
                        self.stopped_by_us.add(identity)
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error as exc:
                    raise SuspensionError('Cannot suspend every owned process; stopping this job.') from exc
            time.sleep(.02)
            current = self.refresh()
            try:
                if set(current) == set(members) and all(p.status() in STOPPED for p in current.values()):
                    return
            except psutil.NoSuchProcess:
                continue
        raise SuspensionError('Owned processes did not acknowledge suspension; stopping this job.')

    def resume(self):
        for identity in reversed(list(self.stopped_by_us)):
            process = self.members.get(identity)
            if process is not None and self.alive(process, identity):
                try:
                    process.send_signal(signal.SIGCONT)
                except psutil.NoSuchProcess:
                    pass
        self.stopped_by_us.clear()

    def signal(self, sig):
        try:
            self.refresh()
        except (psutil.Error, SuspensionError):
            pass
        errors = []
        for identity, process in list(self.members.items()):
            try:
                if self.alive(process, identity):
                    process.send_signal(sig)
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                errors.append(str(exc))
        if errors:
            raise SuspensionError('Could not signal an owned workflow process: ' + '; '.join(errors))

    def stop(self):
        # Queue interruption before continuing an OS-suspended process.
        self.signal(signal.SIGINT)
        self.resume()

    def kill(self):
        self.signal(signal.SIGKILL)

class OwnerWatch:
    """Independent EOF watcher; native children never inherit its pipe writer."""
    def __init__(self, tree):
        self.tree, self.sent = tree, set()
        read_fd, self.write_fd = os.pipe()
        os.set_inheritable(self.write_fd, False)
        env = os.environ.copy()
        root = str(Path(__file__).resolve().parent.parent)
        env['PYTHONPATH'] = root + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        try:
            self.process = subprocess.Popen([sys.executable, '-m', 'bactologix.disk_process', '--watch', str(read_fd)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(read_fd,), close_fds=True, start_new_session=True, env=env)
        except BaseException:
            os.close(self.write_fd)
            raise
        finally:
            os.close(read_fd)
        os.set_blocking(self.write_fd, False)
        self.publish()

    def publish(self):
        if self.process.poll() is not None:
            raise SuspensionError('The independent storage safety monitor stopped unexpectedly.')
        pending = [key for key in self.tree.members if key not in self.sent]
        for start in range(0, len(pending), 80):
            batch = pending[start:start + 80]
            data = (json.dumps({'root': self.tree.root_identity, 'members': batch}, separators=(',', ':')) + '\n').encode()
            if len(data) > 4096:
                raise SuspensionError('The ownership record exceeds the safe pipe limit.')
            try:
                written = os.write(self.write_fd, data)
            except (BlockingIOError, BrokenPipeError):
                raise SuspensionError('The independent storage safety monitor is not receiving updates.') from None
            if written != len(data):
                raise SuspensionError('The storage safety monitor received an incomplete ownership record.')
            self.sent.update(batch)

    def close(self, clean=False):
        if self.write_fd is None:
            return
        if clean:
            try:
                os.write(self.write_fd, b'{"disarm":true}\n')
            except OSError:
                pass
        os.close(self.write_fd)
        self.write_fd = None
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # Do not kill a watcher while it may be stopping owned work.


def watch(fd):
    root, members, disarmed = None, set(), False
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        while True:
            line = stream.readline(8193)
            if not line or len(line) > 8192:
                break
            try:
                value = json.loads(line)
                if value.get('disarm'):
                    disarmed = True
                    break
                root = value['root']
                members.update(tuple(item) for item in value['members'])
                if len(members) > MAX_MEMBERS:
                    break
            except (ValueError, TypeError, KeyError, AttributeError):
                break
    if disarmed or root is None:
        return
    tree = None
    try:
        tree = OwnedTree(int(root[0]), float(root[1]))
    except (ValueError, TypeError, IndexError, psutil.Error, SuspensionError):
        pass
    if tree is None:
        tree = object.__new__(OwnedTree)
        tree.members, tree.stopped_by_us = {}, set()
    for item in members:
        try:
            pid, created = int(item[0]), float(item[1])
            process = psutil.Process(pid)
            if process.create_time() == created:
                tree.remember(process)
        except (ValueError, TypeError, IndexError, psutil.Error, SuspensionError):
            continue
    try:
        tree.freeze()
    except (psutil.Error, SuspensionError):
        pass
    try:
        tree.kill()
    except (psutil.Error, SuspensionError):
        pass

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--watch':
        watch(int(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == '--exec':
        # The native command only starts after the watchdog knows its identity.
        gate = int(sys.argv[2])
        ready = os.read(gate, 1)
        os.close(gate)
        if ready == b'1':
            os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
