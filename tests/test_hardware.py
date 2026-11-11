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
    paging = h.paging_limits(tmp_path, '')
    monkeypatch.setattr(h, 'paging_limits', lambda: paging)
    monkeypatch.setattr(h, 'gpu_inventory', lambda: {'cards': []})
    monkeypatch.setattr(h, '_read', lambda path: 'MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\nSwapTotal: 2097152 kB' if path == '/proc/meminfo' else '')
    info = h.detect(tmp_path)
    assert info['wsl'] and info['cpu']['available'] == 4
    assert info['memory'] == {'total_mb': 4096, 'available_mb': 2048, 'swap_mb': 2048,
                              'swap_available_mb': None, 'combined_total_mb': 6144,
                              'combined_available_mb': None}


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



def test_custom_per_pipeline_compute_defaults_and_cuda_policy(capacity):
    store, info = capacity
    store.put('runs','old',{'config':'preserved'})
    panel=h.panel(store)
    h.apply_custom(store,'hybas','3','4096','cpu',panel['revisions'],info['id'])
    cfg=yaml.safe_load(store.get('configs','hybas'))
    assert h.run_defaults(store,'hybas')['cores']==3
    assert cfg['bactologix_compute']['gpu_mode']=='cpu'
    assert h.workflow_environment(cfg)=={'CUDA_VISIBLE_DEVICES':''}
    assert h.workflow_environment({})=={}
    assert store.get('runs','old')=={'config':'preserved'}
    assert store.get('configs','basis') is None
    h.apply_preset(store,'medium',['hybas'],h.panel(store)['revisions'],info['id'])
    assert yaml.safe_load(store.get('configs','hybas'))['bactologix_compute']['gpu_mode']=='cpu'


@pytest.mark.parametrize('cpu,ram,mode,tool', [('17','4096','cpu','hybas'),('2','999999','cpu','hybas'),('1.5','4096','cpu','hybas'),('2','4096','native','micpscope'),('2','4096','force','basis')])
def test_invalid_custom_budgets_do_not_save(capacity,cpu,ram,mode,tool):
    store,info=capacity
    with pytest.raises(ValueError): h.apply_custom(store,tool,cpu,ram,mode,h.panel(store)['revisions'],info['id'])
    assert not store.all('resource_defaults')


def test_native_gpu_mode_never_overrides_existing_visibility():
    assert h.workflow_environment({'bactologix_compute':{'gpu_mode':'native'}})=={}
    with pytest.raises(ValueError): h.workflow_environment({'bactologix_compute':{'gpu_mode':'unsupported'}})


@pytest.mark.parametrize('tool', ['hybas', 'basis', 'micpscope'])
def test_swap_default_is_per_pipeline_and_preserves_compute_policy(capacity, tool):
    store, info = capacity
    store.put('configs', tool, 'bactologix_compute: {gpu_mode: cpu, swap_mb: 4096, custom: keep}\n')
    before_jobs = store.jobs()
    dispatch(store, 'save_compute_defaults', {'tool': tool, 'cores': 3, 'memory_mb': 4096,
        'gpu_mode': 'cpu', 'swap_mb': 98304, 'revisions': h.panel(store)['revisions'],
        'hardware_id': info['id']})
    cfg = yaml.safe_load(store.get('configs', tool))
    assert cfg['bactologix_compute'] == {'gpu_mode': 'cpu', 'swap_mb': 98304, 'custom': 'keep'}
    assert h.run_defaults(store, tool)['swap_mb'] == store.get('resource_defaults', tool)['swap_mb'] == 98304
    assert all(h.run_defaults(store, other)['swap_mb'] == 0 for other in h.PIPELINES if other != tool)
    assert store.jobs() == before_jobs  # Saving a preference neither allocates swap nor starts work.
    # Older clients can change CPU/RAM/GPU without erasing the saved allowance.
    h.apply_custom(store, tool, 2, 2048, 'cpu', h.panel(store)['revisions'], info['id'])
    assert h.run_defaults(store, tool)['swap_mb'] == 98304
    h.apply_custom(store, tool, 2, 2048, 'cpu', h.panel(store)['revisions'], info['id'], swap_mb=0)
    assert h.run_defaults(store, tool)['swap_mb'] == 0
    assert yaml.safe_load(store.get('configs', tool))['bactologix_compute']['custom'] == 'keep'


def test_presets_preserve_swap_without_inflating_tool_ram(capacity):
    store, info = capacity
    for tool, swap in [('hybas', 98304), ('basis', 4096), ('micpscope', 0)]:
        store.put('configs', tool, yaml.safe_dump({'bactologix_compute': {
            'gpu_mode': 'cpu', 'swap_mb': swap, 'custom': tool}}))
    h.apply_preset(store, 'medium', list(h.PIPELINES), h.panel(store)['revisions'], info['id'])
    ram = h.presets(info)[1]['memory_mb']
    for tool, swap in [('hybas', 98304), ('basis', 4096), ('micpscope', 0)]:
        cfg = yaml.safe_load(store.get('configs', tool))
        assert cfg['bactologix_compute'] == {'gpu_mode': 'cpu', 'swap_mb': swap, 'custom': tool}
        assert h.run_defaults(store, tool)['memory_mb'] == ram
        assert h.run_defaults(store, tool)['swap_mb'] == swap
        if tool == 'hybas':
            assert cfg['assembly_mem_mb'] == cfg['polishing_mem_mb'] == ram
            assert cfg['spades_memory_gb'] == ram // 1024
        else:
            assert cfg['memory_mb'] == ram


@pytest.mark.parametrize('swap', [None, True, False, -1, 0.5, 98304.0, '98304', [], {}, 2_000_001, 1_999_999])
def test_invalid_default_swap_never_saves(capacity, swap):
    store, info = capacity
    original = 'bactologix_compute: {gpu_mode: native, swap_mb: 4096, custom: keep}\n'
    store.put('configs', 'hybas', original)
    with pytest.raises(ValueError, match='whole number|combined'):
        dispatch(store, 'save_compute_defaults', {'tool': 'hybas', 'cores': 2, 'memory_mb': 4096,
            'gpu_mode': 'cpu', 'swap_mb': swap, 'revisions': h.panel(store)['revisions'],
            'hardware_id': info['id']})
    assert store.get('configs', 'hybas') == original
    assert not store.all('resource_defaults')


def test_preset_combined_cap_rolls_back_all_pipelines(capacity):
    store, info = capacity
    store.put('configs', 'basis', 'bactologix_compute: {swap_mb: 1990000}\n')
    before = store.get('configs', 'basis')
    with pytest.raises(ValueError, match='combined'):
        h.apply_preset(store, 'high', ['hybas', 'basis'], h.panel(store)['revisions'], info['id'])
    assert store.get('configs', 'hybas') is None
    assert store.get('configs', 'basis') == before
    assert not store.all('resource_defaults')


@pytest.mark.parametrize('kind', ['save_config', 'save_default_config'])
def test_yaml_swap_save_and_removal_stay_in_sync_with_compute_defaults(capacity, kind):
    store, info = capacity
    h.apply_custom(store, 'hybas', 2, 4096, 'cpu', h.panel(store)['revisions'], info['id'], swap_mb=98304)
    for text, expected in [('bactologix_compute: {swap_mb: 8192, gpu_mode: native}\n', 8192),
                           ('bactologix_compute: {}\n', 0), ('custom: keep\n', 0)]:
        editor = dispatch(store, 'default_config_editor', {'tool': 'hybas'})['default_config']
        dispatch(store, kind, {'tool': 'hybas', 'text': text, 'revision': editor['revision']})
        assert h.run_defaults(store, 'hybas')['swap_mb'] == expected
        assert h.panel(store)['defaults']['hybas']['swap_mb'] == expected
        assert store.get('resource_defaults', 'hybas')['swap_mb'] == expected
        assert h.run_defaults(store, 'hybas')['gpu_mode'] == 'native'
    before = store.get('configs', 'hybas'), store.get('resource_defaults', 'hybas')
    editor = dispatch(store, 'default_config_editor', {'tool': 'hybas'})['default_config']
    with pytest.raises(ValueError, match='combined'):
        dispatch(store, kind, {'tool': 'hybas', 'text': 'bactologix_compute: {swap_mb: 2000000}\n',
                              'revision': editor['revision']})
    assert (store.get('configs', 'hybas'), store.get('resource_defaults', 'hybas')) == before


def test_yaml_policy_is_authoritative_over_legacy_cached_metadata(capacity):
    store, _ = capacity
    store.put('resource_defaults', 'hybas', {'cores': 2, 'memory_mb': 4096, 'swap_mb': 98304, 'gpu_mode': 'cpu'})
    store.put('configs', 'hybas', 'bactologix_compute: {swap_mb: 8192, gpu_mode: native}\n')
    values = h.run_defaults(store, 'hybas')
    assert values['swap_mb'] == 8192 and values['gpu_mode'] == 'native'


@pytest.mark.parametrize('tool', ['hybas', 'basis', 'micpscope'])
def test_backend_drafts_inherit_pipeline_swap_unless_explicitly_overridden(capacity, monkeypatch, tool):
    store, info = capacity
    h.apply_custom(store, tool, 2, 4096, 'cpu', h.panel(store)['revisions'], info['id'], swap_mb=98304)
    original = store.get('configs', tool)
    for sid in ('one', 'two'):
        store.put('samples', sid, {'id': sid})
    calls = []
    monkeypatch.setattr('bactologix.ui_backend.create_run', lambda *args: calls.append(args) or {'id': 'fixture'})
    for override, expected in [({}, 98304), ({'swap_mb': 4096}, 4096), ({'swap_mb': 0}, 0)]:
        calls.clear()
        dispatch(store, 'create_runs', {'tool': tool, 'ids': ['one', 'two'], **override})
        assert len(calls) == 2
        assert all(yaml.safe_load(call[3])['bactologix_compute']['swap_mb'] == expected for call in calls)
        assert all(call[4:] == (2, 4096) for call in calls)
        assert store.get('configs', tool) == original
