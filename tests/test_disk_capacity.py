import base64
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from bactologix import disk_capacity as capacity


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (False, ''))
    for key in ('TMPDIR', 'TEMP', 'TMP'):
        monkeypatch.delenv(key, raising=False)
    return SimpleNamespace(home=tmp_path, get=lambda *args: {})


def host(path='D:\\WSL_Ubuntu', available=100, role='wsl_backing'):
    return dict(id='windows:d', path=path, probe_path=path, label=role, role=role,
                kind='windows_host', available_bytes=available, total_bytes=1000)


def test_available_to_caller_not_total_free(local, monkeypatch):
    monkeypatch.setattr(capacity.os, 'statvfs', lambda p: SimpleNamespace(
        f_blocks=100, f_bavail=5, f_bfree=90, f_frsize=4096, f_flag=0))
    result = capacity.CapacityProbe(local, {'output': str(local.home / 'future')}).sample()
    assert not result['errors']
    assert all(v['available_bytes'] == 5 * 4096 for v in result['volumes'])
    assert len({v['id'] for v in result['volumes']}) == len(result['volumes'])
    assert not (local.home / 'future').exists()


def test_selected_temporary_directory_replaces_unused_system_tmp(local):
    scratch = local.home / 'scratch'
    scratch.mkdir()
    probe = capacity.CapacityProbe(local, {}, cwd=local.home, extra_env={'TMPDIR': str(scratch)})
    assert str(scratch) in probe.command_temporary
    assert '/tmp' not in probe.paths and '/tmp' not in probe.command_temporary
    assert '/var/tmp' not in probe.paths
    next_scratch = local.home / 'next'
    probe.include_command(extra_env={'TMPDIR': str(next_scratch), 'TEMP': '/unused'})
    assert str(scratch) not in probe.command_temporary
    assert '/unused' not in probe.command_temporary


def test_paths_from_command_configuration_and_followup(local):
    parent = local.home / 'parent'
    local.get = lambda *args: {'folder': str(parent)}
    run = {'folder': str(local.home / 'run'), 'run_type': 'followup', 'parent': 'parent',
           'output': str(local.home / 'output'), 'config': {'tool': {'scratch_dir': 'scratch'}}}
    probe = capacity.CapacityProbe(local, run, cwd=local.home)
    probe.include_paths({local.home / 'external': 'External results'})
    assert str(parent / 'followup-state') in probe.paths
    assert str(local.home / 'scratch') in probe.paths
    assert str(local.home / 'external') in probe.paths
    assert not parent.exists()
    assert not probe.sample()['errors']


def test_unidentified_followup_and_invalid_temp_are_errors(local):
    probe = capacity.CapacityProbe(local, {'run_type': 'followup', 'config': {'tmpdir': 12}})
    assert len(probe.sample()['errors']) == 2


def test_readonly_storage_is_not_reported_safe(local, monkeypatch):
    monkeypatch.setattr(capacity.os, 'statvfs', lambda p: SimpleNamespace(
        f_blocks=100, f_bavail=90, f_frsize=4096, f_flag=1))
    assert any('read-only' in e for e in capacity.CapacityProbe(local, {}).sample()['errors'])


def test_disappearing_output_directory_is_error(local):
    output = local.home / 'outputs'
    output.mkdir()
    probe = capacity.CapacityProbe(local, {'output': str(output)})
    assert not probe.sample()['errors']
    output.rmdir()
    assert any('unavailable' in e for e in probe.sample()['errors'])


def test_new_output_becomes_tracked_anchor(local):
    output = local.home / 'outputs'
    probe = capacity.CapacityProbe(local, {'output': str(output)})
    assert not probe.sample()['errors']
    output.mkdir()
    assert not probe.sample()['errors']
    output.rmdir()
    assert probe.sample()['errors']


def test_changed_device_is_error(local, monkeypatch):
    probe = capacity.CapacityProbe(local, {})
    assert not probe.sample()['errors']
    original = probe._reading
    monkeypatch.setattr(probe, '_reading', lambda p: (original(p)[0] + 1, 10, 100))
    assert any('filesystem changed' in e for e in probe.sample()['errors'])


def test_symlink_output_probes_target(local):
    target = local.home / 'actual'
    target.mkdir()
    link = local.home / 'link'
    link.symlink_to(target, target_is_directory=True)
    probe = capacity.CapacityProbe(local, {'output': str(link)})
    assert not probe.sample()['errors']
    assert probe.anchors[str(link)][0] == target


def test_windows_full_host_is_not_hidden_by_linux_capacity(local, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu-22.04'))
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [])
    monkeypatch.setattr(capacity, '_windows_query', lambda *args: {'volumes': [host(available=0)], 'errors': []})
    result = capacity.CapacityProbe(local, {}).sample()
    assert not result['errors']
    assert [v['available_bytes'] for v in result['volumes'] if v['kind'] == 'windows_host'] == [0]


def test_custom_mount_mapping_has_fresh_capacity_without_repeated_powershell(local, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu'))
    mount = local.home / 'custom' / 'd'
    backing = mount / 'WSL_Ubuntu'
    backing.mkdir(parents=True)
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [('D:\\', mount)])
    calls = []
    monkeypatch.setattr(capacity, '_windows_query', lambda *args: calls.append(args) or {'volumes': [host()], 'errors': []})
    probe = capacity.CapacityProbe(local, {})
    assert not probe.sample()['errors']
    original = probe._reading
    monkeypatch.setattr(probe, '_reading', lambda p: (original(p)[0], 17, 1000))
    result = probe.sample()
    assert not result['errors']
    assert len(calls) == 1
    assert next(v for v in result['volumes'] if v['kind'] == 'windows_host')['available_bytes'] == 17


def test_unmapped_windows_volume_is_resampled_not_cached(local, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu'))
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [])
    calls = []
    def query(payload, mounts):
        calls.append(payload['operation'])
        return {'volumes': [host(available=100 if len(calls) == 1 else 0)], 'errors': []}
    monkeypatch.setattr(capacity, '_windows_query', query)
    probe = capacity.CapacityProbe(local, {})
    probe.sample()
    result = probe.sample()
    assert calls == ['discover', 'sample']
    assert next(v for v in result['volumes'] if v['kind'] == 'windows_host')['available_bytes'] == 0


@pytest.mark.parametrize('failure', [OSError('interop unavailable'), ValueError('bad response'), subprocess.TimeoutExpired('ps', 3)])
def test_windows_failures_remain_errors_and_are_retried(local, monkeypatch, failure):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu'))
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [])
    calls = []
    def fail(*args):
        calls.append(args)
        raise failure
    monkeypatch.setattr(capacity, '_windows_query', fail)
    probe = capacity.CapacityProbe(local, {})
    assert probe.sample()['errors']
    assert probe.sample()['errors']
    assert len(calls) == 2


def test_missing_wsl_identity_is_explicit_error(local, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, ''))
    assert any('identity' in e for e in capacity.CapacityProbe(local, {}).sample()['errors'])


def test_discovery_requires_backing_and_does_not_cache_error(local, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu'))
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [])
    monkeypatch.setattr(capacity, '_windows_query', lambda *args: {'volumes': [host(role='wsl_swap')], 'errors': []})
    probe = capacity.CapacityProbe(local, {})
    assert probe.sample()['errors']
    assert probe.host_targets is None


def test_missing_fallback_volume_is_error(local, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu'))
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [])
    responses = iter([{'volumes': [host()], 'errors': []}, {'volumes': [], 'errors': []}])
    monkeypatch.setattr(capacity, '_windows_query', lambda *args: next(responses))
    probe = capacity.CapacityProbe(local, {})
    probe.sample()
    assert probe.sample()['errors']


def test_windows_shared_volume_uses_minimum_reading_not_sum(local, monkeypatch):
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu'))
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [])
    monkeypatch.setattr(capacity, '_windows_query', lambda *args: {'volumes': [
        host(available=100), host(path='D:\\swap.vhdx', available=90, role='wsl_swap')], 'errors': []})
    result = capacity.CapacityProbe(local, {}).sample()
    hosts = [v for v in result['volumes'] if v['kind'] == 'windows_host']
    assert len(hosts) == 1 and hosts[0]['available_bytes'] == 90
    assert len(hosts[0]['paths']) == 2


def test_mount_parser_custom_roots_spaces_and_bind_subtree(monkeypatch):
    lines = '1 0 0:1 / /windows/d rw - 9p D:\\ rw,aname=drvfs;path=D:\\;uid=1000\n'
    lines += '2 0 0:2 /data /custom/my\\040files rw - drvfs E:\\ rw\n'
    monkeypatch.setattr(Path, 'read_text', lambda *args: lines)
    assert capacity._drvfs_mounts() == [('D:\\', Path('/windows/d')), ('E:\\data', Path('/custom/my files'))]


def test_windows_path_mapping_supports_extended_unc_and_longest_match():
    mounts = [('D:\\', Path('/windows/d')), ('D:\\data', Path('/specific')),
              ('\\\\server\\share\\', Path('/network'))]
    assert capacity._mapped_path('\\\\?\\D:\\data\\run', mounts) == Path('/specific/run')
    assert capacity._mapped_path('D:\\database\\run', mounts) == Path('/windows/d/database/run')
    assert capacity._mapped_path('\\\\?\\UNC\\server\\share\\run', mounts) == Path('/network/run')
    assert capacity._mapped_path('F:\\missing', mounts) is None


def test_windows_query_is_bounded_and_payload_is_data(monkeypatch):
    payload = {'operation': 'discover', 'distribution': "Ubuntu'; throw 'oops"}
    monkeypatch.setattr(capacity, '_powershell', lambda mounts: 'powershell.exe')
    def run(args, **kwargs):
        assert kwargs['timeout'] == capacity.WINDOWS_TIMEOUT
        assert kwargs.get('shell') is None
        script = base64.b64decode(args[-1]).decode('utf-16-le')
        assert payload['distribution'] not in script
        assert base64.b64encode(json.dumps(payload).encode()).decode() in script
        return SimpleNamespace(returncode=0, stdout=(capacity._PREFIX + json.dumps({'volumes': [host()], 'errors': []})).encode())
    monkeypatch.setattr(capacity.subprocess, 'run', run)
    assert capacity._windows_query(payload, [])['volumes'][0]['available_bytes'] == 100


@pytest.mark.parametrize('response', ['', '{}', '{"volumes":{},"errors":[]}', '{"volumes":[{}],"errors":[]}',
                                    '{"volumes":[],"errors":[12]}'])
def test_windows_query_rejects_unverifiable_responses(monkeypatch, response):
    monkeypatch.setattr(capacity, '_powershell', lambda mounts: 'powershell.exe')
    monkeypatch.setattr(capacity.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=(capacity._PREFIX + response).encode()))
    with pytest.raises(ValueError):
        capacity._windows_query({'operation': 'discover'}, [])
