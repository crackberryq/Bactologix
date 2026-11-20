"""Dev storage monitoring follows the environment actually given to tools."""
from pathlib import Path
import sys
import threading

import pytest

from bactologix import channels, disk_capacity, disk_pause
from bactologix.engine import Cancelled, Context
from test_development_channel import dev
from test_disk_capacity import desktop_storage, host, local
from test_disk_pause import background, wait_for


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


def test_running_job_rechecks_verified_drives_during_desktop_gap(dev, desktop_storage, tmp_path, monkeypatch):
    gib = disk_pause.GIB
    drive_d, drive_e = tmp_path / 'drive-D', tmp_path / 'drive-E'
    backing, swap = drive_d / 'WSL_Ubuntu', drive_e / 'WSL-Swap'
    backing.mkdir(parents=True)
    swap.mkdir(parents=True)
    mounts = [('D:\\', drive_d), ('E:\\', drive_e)]
    monkeypatch.setattr(disk_capacity, '_drvfs_mounts', lambda: list(mounts))
    monkeypatch.setattr(disk_pause, 'POLL_SECONDS', .05)
    monkeypatch.delenv('BACTOLOGIX_DESKTOP_SESSION', raising=False)
    free = {backing: 100 * gib, swap: 90 * gib}
    total = 200 * gib

    def read(path):
        return path.stat().st_dev, free.get(path, 150 * gib), total

    monkeypatch.setattr(disk_capacity.CapacityProbe, '_reading', staticmethod(read))
    report = desktop_storage(swap_required=True, volumes=[
        dict(host(available=free[backing]), volume_path='D:\\', total_bytes=total),
        dict(host(path=r'E:\WSL-Swap\swap.vhdx', available=free[swap], role='wsl_swap'),
             id='windows:e', probe_path=r'E:\WSL-Swap', volume_path='E:\\', total_bytes=total),
    ])
    jid = dev.submit('workflow', {'run_id': 'software-fixture', 'action': 'run'})
    dev.update_job(jid, status='running')
    context = Context(dev, dev.job(jid))
    progress = tmp_path / 'progress'
    code = ('import sys,time\nwith open(sys.argv[1], "ab", buffering=0) as f:\n'
            '    while True:\n        f.write(b"x")\n        time.sleep(.05)')
    thread, errors = background(context, [sys.executable, '-c', code, str(progress)])

    def state():
        return disk_pause.snapshot(dev, jid) or {}

    try:
        wait_for(lambda: progress.exists() and progress.stat().st_size > 0)
        report.unlink()  # Windows source stops reporting; interop is disabled in this fixture.
        free[backing] = 65 * gib
        wait_for(lambda: any(r.get('observed_by') == 'drvfs' and r['available_bytes'] == free[backing]
                             for r in state().get('volumes', [])))
        assert state()['state'] == 'monitoring'
        size = progress.stat().st_size
        wait_for(lambda: progress.stat().st_size > size)

        free[swap] = 15 * gib
        wait_for(lambda: state().get('state') == 'warning')
        size = progress.stat().st_size
        wait_for(lambda: progress.stat().st_size > size)

        mounts.pop()  # The verified swap volume disappears while the process is writing.
        wait_for(lambda: state().get('state') == 'paused')
        second = state()
        assert second['reason'] == 'monitor_unavailable' and not second['can_resume']
        with pytest.raises(ValueError, match='fresh check'):
            disk_pause.request_resume(dev, jid, second['generation'])
    finally:
        dev.cancel(jid)
        thread.join(5)
        context.disk_guard.finish('cancelled')
        context.log.close()
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], Cancelled)
