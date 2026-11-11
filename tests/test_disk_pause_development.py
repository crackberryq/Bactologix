"""Dev storage monitoring follows the environment actually given to tools."""
from pathlib import Path
import threading

import pytest

from bactologix import channels, disk_capacity, disk_pause
from bactologix.engine import Context
from test_development_channel import dev


def test_dev_guard_checks_effective_channel_temporary_paths(dev, monkeypatch):
    checked = []

    class BeforeLaunch(Exception):
        pass

    class Guard:
        def __init__(self, *args, **kwargs):
            pass

        def checkpoint(self):
            pass

        def before_command(self, cwd, env):
            checked.append((cwd, dict(env)))
            raise BeforeLaunch

    monkeypatch.setattr(disk_pause, 'Guard', Guard)
    monkeypatch.delenv('BACTOLOGIX_DESKTOP_SESSION', raising=False)
    monkeypatch.setenv('TMPDIR', '/outside/parent-temp')
    jid = dev.submit('workflow', {'run_id': 'software-fixture', 'action': 'run'})
    dev.update_job(jid, status='running')
    context = Context(dev, dev.job(jid))
    try:
        with pytest.raises(BeforeLaunch):
            context.command(['unused-software-fixture'], cwd=dev.home,
                            extra_env={'TMPDIR': '/outside/request-temp'})
    finally:
        context.log.close()
    cwd, env = checked[0]
    assert cwd == dev.home
    assert env['TMPDIR'] == channels.environment(dev.home)['TMPDIR']
    assert Path(env['TMPDIR']).is_relative_to(dev.home)
    assert env['PYTHONNOUSERSITE'] == '1'


def test_initial_dev_checkpoint_ignores_unused_low_ambient_tmp(dev, tmp_path, monkeypatch):
    ambient = tmp_path / 'ambient-temp'
    ambient.mkdir()
    monkeypatch.setenv('TMPDIR', str(ambient))
    monkeypatch.delenv('BACTOLOGIX_DESKTOP_SESSION', raising=False)
    monkeypatch.setattr(disk_capacity, '_wsl_context', lambda: (False, ''))
    monkeypatch.setattr(disk_pause, 'POLL_SECONDS', .05)
    checked = []

    def capacity(path):
        checked.append(path)
        available = 2 if path == ambient else 40
        return path.stat().st_dev, available * disk_pause.GIB, 100 * disk_pause.GIB

    monkeypatch.setattr(disk_capacity.CapacityProbe, '_reading', staticmethod(capacity))
    unseeded = disk_capacity.CapacityProbe(dev, {}).sample()
    assert min(v['available_bytes'] for v in unseeded['volumes']) == 2 * disk_pause.GIB
    checked.clear()
    jid = dev.submit('workflow', {'run_id': 'software-fixture', 'action': 'run'})
    dev.update_job(jid, status='running')
    context = Context(dev, dev.job(jid))
    errors = []

    def initial_worker_checkpoint():
        try:
            context.check_cancel()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=initial_worker_checkpoint, daemon=True)
    thread.start()
    try:
        thread.join(3)
        assert not thread.is_alive(), 'Dev checked unused ambient TMPDIR before its first command'
        assert errors == []
        assert ambient not in checked
        assert dev.home / 'tmp' in checked
        assert disk_pause.snapshot(dev, jid)['state'] == 'monitoring'
    finally:
        if thread.is_alive():
            dev.cancel(jid)
            thread.join(3)
        context.disk_guard.finish('cancelled')
        context.log.close()
