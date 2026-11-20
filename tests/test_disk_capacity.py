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


@pytest.fixture
def desktop_storage(local, monkeypatch):
    key = 'ab' * 16
    directory = local.home / 'sessions' / key
    directory.mkdir(parents=True)
    monkeypatch.setattr(capacity, '_wsl_context', lambda: (True, 'Ubuntu-22.04'))
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [])
    monkeypatch.setenv(capacity.STORAGE_SESSION_DIR, str(directory))
    def interop_disabled(payload, mounts):
        raise OSError(8, 'Exec format error', '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
    monkeypatch.setattr(capacity, '_windows_query', interop_disabled)
    def publish(**fields):
        record = dict(schema=1, session=key, distribution='Ubuntu-22.04',
                      time=capacity.time.time(), swap_required=False, volumes=[host()], errors=[])
        record.update(fields)
        path = directory / 'windows-storage.json'
        path.write_text(json.dumps(record), encoding='utf-8-sig')
        return path
    return publish


def test_desktop_supplies_current_backing_and_separate_swap_without_interop(local, desktop_storage):
    backing = host(available=80)
    swap = host(path=r'F:\wsl-swap', role='wsl_swap', available=0)
    swap['id'] = 'windows:f'
    desktop_storage(swap_required=True, volumes=[backing, swap])
    probe = capacity.CapacityProbe(local, {})
    sample = probe.sample()
    assert not sample['errors']
    hosts = {r['id']: r['available_bytes'] for r in sample['volumes'] if r['kind'] == 'windows_host'}
    assert hosts == {'windows:d': 80, 'windows:f': 0}
    swap['available_bytes'] = 40
    desktop_storage(swap_required=True, volumes=[backing, swap])
    assert next(r for r in probe.sample()['volumes'] if r['id'] == 'windows:f')['available_bytes'] == 40


@pytest.mark.parametrize('fields', [
    {'session': 'cd' * 16}, {'distribution': 'Other'}, {'schema': True},
    {'time': 0}, {'time': float('nan')}, {'time': float('inf')},
    {'swap_required': True}, {'volumes': []}, {'swap_required': None},
    {'errors': ['Swap configuration is inaccessible.']},
    {'volumes': [dict(host(), available_bytes=-1)]},
    {'volumes': [dict(host(), id='linux:1')]},
    {'volumes': [dict(host(), probe_path='relative')]},
])
def test_unverified_desktop_storage_and_interop_failure_keep_guard_closed(local, desktop_storage, fields):
    desktop_storage(**fields)
    errors = capacity.CapacityProbe(local, {}).sample()['errors']
    assert errors and any('Exec format error' in e for e in errors)


def test_desktop_reading_keeps_lowest_verified_value(local, desktop_storage, monkeypatch):
    mount = local.home / 'custom' / 'd'
    (mount / 'WSL_Ubuntu').mkdir(parents=True)
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: [('D:\\', mount)])
    probe = capacity.CapacityProbe(local, {})
    original = probe._reading
    def read(target):
        if target == mount / 'WSL_Ubuntu':
            return 10, 30, 1000
        return original(target)
    monkeypatch.setattr(probe, '_reading', read)
    for available, expected in [(80, 30), (5, 5)]:
        desktop_storage(volumes=[host(available=available)])
        result = probe.sample()
        assert not result['errors']
        observed = next(r for r in result['volumes'] if r['kind'] == 'windows_host')
        assert observed['available_bytes'] == expected


def test_partial_desktop_observation_preserves_low_capacity_when_interop_recovers(local, desktop_storage, monkeypatch):
    desktop_storage(volumes=[host(available=0)], errors=['Swap lookup failed.'])
    monkeypatch.setattr(capacity, '_windows_query',
                        lambda *args: {'volumes': [host(available=100)], 'errors': []})
    result = capacity.CapacityProbe(local, {}).sample()
    assert not result['errors']
    assert next(r for r in result['volumes'] if r['kind'] == 'windows_host')['available_bytes'] == 0


def test_swap_configuration_cannot_remove_monitored_host_volume_mid_run(local, desktop_storage):
    desktop_storage(swap_required=True, volumes=[host(), dict(host(path=r'E:\swap', role='wsl_swap'), id='windows:e')])
    probe = capacity.CapacityProbe(local, {})
    assert not probe.sample()['errors']
    desktop_storage(swap_required=False, volumes=[host()])
    assert any('changed during monitoring' in error for error in probe.sample()['errors'])


def test_stale_or_missing_desktop_reading_can_recover_via_normal_interop(local, desktop_storage, monkeypatch):
    path = desktop_storage(time=0)
    monkeypatch.setattr(capacity, '_windows_query', lambda *args: {'volumes': [host()], 'errors': []})
    for existing in (True, False):
        if not existing:
            path.unlink()
        result = capacity.CapacityProbe(local, {}).sample()
        assert not result['errors'] and 'windows_sampled_at' not in result
        assert any(r['role'] == 'wsl_backing' for r in result['volumes'] if r['kind'] == 'windows_host')


@pytest.fixture
def mounted_windows_drives(local, desktop_storage, monkeypatch):
    mount_d, mount_e = local.home / 'D', local.home / 'E'
    backing = mount_d / 'WSL_Ubuntu'
    swap_dir = mount_e / 'WSL-Swap'
    backing.mkdir(parents=True)
    swap_dir.mkdir(parents=True)
    native = [
        dict(host(), volume_path='D:\\'),
        dict(host(path=r'E:\WSL-Swap\swap.vhdx', role='wsl_swap'), id='windows:e',
             probe_path=r'E:\WSL-Swap', volume_path='E:\\'),
    ]
    mounts = [('D:\\', mount_d), ('E:\\', mount_e)]
    monkeypatch.setattr(capacity, '_drvfs_mounts', lambda: mounts)
    probe = capacity.CapacityProbe(local, {})
    values = {backing: 80, swap_dir: 75}
    default_read = probe._reading

    def read(path):
        if path in values:
            return path.stat().st_dev, values[path], 1000
        return default_read(path)

    monkeypatch.setattr(probe, '_reading', read)

    def publish(**fields):
        return desktop_storage(swap_required=True, volumes=native, **fields)

    path = publish()
    assert not probe.sample()['errors']
    return SimpleNamespace(probe=probe, publish=publish, file=path, mounts=mounts,
                           native=native, values=values, backing=backing, swap=swap_dir)


@pytest.mark.parametrize('gap', ['missing', 'stale', 'slightly_future'])
def test_verified_mounts_measure_capacity_during_brief_desktop_gap(mounted_windows_drives, gap):
    setup = mounted_windows_drives
    if gap == 'missing':
        setup.file.unlink()
    else:
        stamp = capacity.time.time() + (1 if gap == 'slightly_future' else -10)
        setup.publish(time=stamp)
    setup.values.update({setup.backing: 50, setup.swap: 0})
    started = capacity.time.time()
    result = setup.probe.sample()
    assert not result['errors']
    assert started <= result['windows_sampled_at'] <= capacity.time.time()
    assert {r['id']: r['available_bytes'] for r in result['volumes']
            if r['kind'] == 'windows_host'} == {'windows:d': 50, 'windows:e': 0}


def test_aging_native_report_does_not_expire_independent_mounted_reads(mounted_windows_drives):
    setup = mounted_windows_drives
    stamp = capacity.time.time() - 4.5
    setup.publish(time=stamp)
    start = capacity.time.time()
    check = setup.probe.sample()
    assert not check['errors'] and check['windows_sampled_at'] >= start
    assert all(row['observed_by'] == 'drvfs' for row in check['volumes'] if row['kind'] == 'windows_host')
    setup.native[1]['available_bytes'] = 10
    setup.publish(time=stamp)
    limited = setup.probe.sample()
    assert limited['windows_sampled_at'] == stamp
    assert next(row for row in limited['volumes'] if row['id'] == 'windows:e')['available_bytes'] == 10


def test_mount_identity_must_be_renewed_by_windows(mounted_windows_drives, monkeypatch):
    setup = mounted_windows_drives
    setup.file.unlink()
    original = capacity.time.monotonic()
    monkeypatch.setattr(capacity.time, 'monotonic', lambda: original + capacity.DESKTOP_BINDING_SECONDS + 1)
    assert setup.probe.sample()['errors']


@pytest.mark.parametrize('change', ['mount_missing', 'directory_replaced', 'volume_changed', 'quota',
                                    'invalid_record', 'native_error', 'wrong_session', 'clock_jump'])
def test_a_previous_good_measurement_cannot_mask_lost_verification(mounted_windows_drives, change):
    setup = mounted_windows_drives
    if change == 'mount_missing':
        setup.mounts.pop()
        setup.file.unlink()
    elif change == 'directory_replaced':
        setup.backing.rename(setup.backing.with_name('original'))
        setup.backing.mkdir()
        setup.file.unlink()
    elif change == 'volume_changed':
        setup.native[1]['id'] = 'windows:replacement'
        setup.publish(time=capacity.time.time() - 10)
    elif change == 'quota':
        setup.native[1]['available_bytes'] = 4
        setup.publish()
        current = setup.probe.sample()
        assert next(r for r in current['volumes'] if r['id'] == 'windows:e')['available_bytes'] == 4
        setup.file.unlink()
    elif change == 'invalid_record':
        setup.file.write_text('{')
    elif change == 'native_error':
        setup.publish(errors=['Swap drive could not be identified.'])
    elif change == 'wrong_session':
        setup.publish(session='cd' * 16)
    else:
        setup.publish(time=capacity.time.time() + 60)
    assert setup.probe.sample()['errors']


@pytest.mark.parametrize('field, replacement', [
    ('probe_path', r'E:\replacement'), ('volume_path', 'F:\\'), ('total_bytes', 800),
    ('available_bytes', 0),
])
@pytest.mark.parametrize('offset', [-10, 1])
def test_untimely_native_evidence_cannot_be_overridden(mounted_windows_drives, field, replacement, offset):
    setup = mounted_windows_drives
    setup.native[1][field] = replacement
    setup.publish(time=capacity.time.time() + offset)
    result = setup.probe.sample()
    assert result['errors'] or (
        field == 'available_bytes' and
        any(row['id'] == 'windows:e' and row['available_bytes'] == 0 for row in result['volumes'])
    )
