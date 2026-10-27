import copy
import json
from pathlib import Path

import pytest
import yaml

from bactologix import hardware as h
from bactologix.store import Store
from bactologix.ui_backend import dispatch


@pytest.fixture
def capacity(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    info = {'id': 'fixture', 'checked_at': '2026-10-27T12:00:00+00:00',
            'cpu': {'available': 16}, 'memory': {'total_mb': 32768, 'available_mb': 30000}}
    monkeypatch.setattr(h, 'inventory', lambda store, refresh=False: copy.deepcopy(info))
    return store, info


def test_preset_budgets_respect_capacity_and_system_reserve(capacity):
    _, info = capacity
    high, medium, low = h.presets(info)
    assert [p['cores'] for p in (high, medium, low)] == [16, 8, 4]
    assert high['memory_mb'] <= 30000
    assert high['memory_mb'] <= 32768 - high['reserve_mb']
    assert low['memory_mb'] <= medium['memory_mb'] <= high['memory_mb']
    info['cpu']['available'] = 1
    info['memory'] = {'total_mb': 2048, 'available_mb': 600}
    assert all(not p['available'] and p['cores'] == 1 for p in h.presets(info))
    info['memory'] = {'total_mb': None, 'available_mb': None}
    assert not any(p['available'] for p in h.presets(info))


def test_cgroup_ancestor_and_current_usage_limits(tmp_path):
    child = tmp_path / 'parent' / 'child'
    child.mkdir(parents=True)
    (tmp_path / 'parent/cpu.max').write_text('250000 100000')
    (child / 'cpu.max').write_text('max 100000')
    (tmp_path / 'parent/memory.max').write_text(str(8 * 1024**3))
    (tmp_path / 'parent/memory.current').write_text(str(6 * 1024**3))
    (child / 'memory.max').write_text(str(4 * 1024**3))
    (child / 'memory.current').write_text(str(1 * 1024**3))
    assert h.cgroup_limits(tmp_path, '0::/parent/child') == {'cores': 2, 'total_mb': 4096, 'available_mb': 2048}


def test_cgroup_v1_limits_and_unlimited_sentinel(tmp_path):
    for name in ('cpu,cpuacct', 'memory'):
        (tmp_path / name / 'group').mkdir(parents=True)
    (tmp_path / 'cpu,cpuacct/group/cpu.cfs_quota_us').write_text('50000')
    (tmp_path / 'cpu,cpuacct/group/cpu.cfs_period_us').write_text('100000')
    (tmp_path / 'memory/group/memory.limit_in_bytes').write_text(str(2**63 - 4096))
    assert h.cgroup_limits(tmp_path, '1:cpu,cpuacct:/group\n2:memory:/group') == {'cores': 1, 'total_mb': None, 'available_mb': None}


def test_wsl_detection_honors_affinity_and_memory_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(h.os, 'cpu_count', lambda: 32)
    monkeypatch.setattr(h.os, 'sched_getaffinity', lambda _: set(range(8)))
    monkeypatch.setattr(h.platform, 'release', lambda: 'microsoft-standard-WSL2')
    monkeypatch.setattr(h, 'cgroup_limits', lambda: {'cores': 4, 'total_mb': 4096, 'available_mb': 2048})
    monkeypatch.setattr(h, 'gpu_inventory', lambda: {'cards': []})
    monkeypatch.setattr(h, '_read', lambda path: 'MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\nSwapTotal: 2097152 kB' if path == '/proc/meminfo' else '')
    info = h.detect(tmp_path)
    assert info['wsl'] and info['cpu']['available'] == 4
    assert info['memory'] == {'total_mb': 4096, 'available_mb': 2048, 'swap_mb': 2048}


def test_cuda_driver_and_toolkit_are_distinct_and_optional(monkeypatch):
    monkeypatch.setattr(h.shutil, 'which', lambda name: '/bin/' + name if name in {'nvidia-smi', 'nvcc'} else None)
    def command(argv):
        if argv[0].endswith('nvcc'):
            return 'Cuda compilation tools, release 12.4, V12.4.131'
        return '<nvidia_smi_log><driver_version>570.1</driver_version><cuda_version>12.8</cuda_version><gpu><product_name>Fixture GPU</product_name><fb_memory_usage><total>8192 MiB</total><free>N/A</free></fb_memory_usage></gpu></nvidia_smi_log>'
    monkeypatch.setattr(h, '_command', command)
    result = h.gpu_inventory()
    assert result['cuda_driver_max'] == '12.8' and result['cuda_toolkit'] == '12.4'
    assert result['cards'][0]['memory_mb'] == 8192 and result['cards'][0]['free_mb'] is None
    monkeypatch.setattr(h, '_command', lambda _: '')
    result = h.gpu_inventory()
    assert result['cards'] == [] and result['status'] == 'GPU driver unavailable'


def test_preset_atomically_saves_yaml_and_run_budgets_preserving_runs(capacity):
    store, info = capacity
    store.put('configs', 'hybas', 'run_kraken: false\nkraken_decision_mode: off\ncustom: keep\nkraken_mem_mb: 99999\n')
    store.put('configs', 'basis', 'deep: {enabled: false}\nexecution: {gpus: 1, cores: 2, memory_mb: 4096}\n')
    store.put('runs', 'frozen', {'id': 'frozen', 'config': 'threads: 2', 'cores': 2})
    before = store.get('runs', 'frozen')
    panel = h.panel(store)
    dispatch(store, 'apply_resource_preset', {'preset': 'medium', 'tools': ['hybas', 'basis'],
                                            'revisions': panel['revisions'], 'hardware_id': info['id']})
    budget = h.presets(info)[1]
    hybas = yaml.safe_load(store.get('configs', 'hybas'))
    basis = yaml.safe_load(store.get('configs', 'basis'))
    assert hybas['threads'] == basis['threads'] == 8
    assert hybas['assembly_mem_mb'] == hybas['polishing_mem_mb'] == basis['memory_mb'] == budget['memory_mb']
    assert hybas['spades_memory_gb'] * 1024 <= budget['memory_mb']
    assert hybas['custom'] == 'keep' and hybas['run_kraken'] is False and hybas['kraken_mem_mb'] == 99999
    assert basis['execution'] == {'gpus': 1, 'cores': 8, 'memory_mb': budget['memory_mb']}
    assert basis['deep'] == {'enabled': False}
    assert store.get('runs', 'frozen') == before
    assert h.run_defaults(store, 'hybas')['memory_mb'] == budget['memory_mb']


def test_stale_or_invalid_preset_never_partially_saves(capacity):
    store, info = capacity
    panel = h.panel(store)
    store.put('configs', 'basis', 'custom: changed elsewhere\n')
    with pytest.raises(ValueError, match='defaults changed'):
        h.apply_preset(store, 'high', ['hybas', 'basis'], panel['revisions'], info['id'])
    assert not store.get('configs', 'hybas') and not store.all('resource_defaults')
    for name, tools, identity in [('extreme', ['hybas'], info['id']), ('high', ['other'], info['id']), ('high', ['hybas'], 'stale')]:
        with pytest.raises(ValueError):
            h.apply_preset(store, name, tools, panel['revisions'], identity)
    assert not store.all('resource_defaults')


def test_draft_creation_uses_saved_budgets_at_backend_boundary(capacity, monkeypatch):
    store, info = capacity
    panel = h.panel(store)
    h.apply_preset(store, 'low', ['hybas'], panel['revisions'], info['id'])
    store.put('samples', 'fixture', {'id': 'fixture'})
    calls = []
    def create(*args):
        calls.append(args)
        return {'id': 'run-fixture'}
    monkeypatch.setattr('bactologix.ui_backend.create_run', create)
    # Use two fixture samples to avoid the unrelated saved-run detail response.
    store.put('samples', 'fixture2', {'id': 'fixture2'})
    dispatch(store, 'create_runs', {'tool': 'hybas', 'ids': ['fixture', 'fixture2']})
    budget = h.presets(info)[2]
    assert len(calls) == 2 and calls[0][4:] == (budget['cores'], budget['memory_mb'])
    assert yaml.safe_load(calls[0][3])['threads'] == budget['cores']
