"""Linux-visible paging capacity; no swap devices or kernel settings are changed."""
from pathlib import Path

import pytest

from bactologix import hardware as h


MIB = 1024 ** 2
GIB = 1024 * MIB


def controls(folder, values):
    folder.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        (folder / name).write_text(str(value))


def memory(root, membership='0::/parent/child', **mem):
    base = {'SwapTotal': 488 * 1024, 'SwapFree': 488 * 1024}
    base.update(mem)
    return h.paging_memory(base, 31 * 1024, 30 * 1024, h.paging_limits(root, membership))


def test_wsl_large_active_swap_keeps_ram_and_swap_separate(tmp_path, monkeypatch):
    cg = tmp_path / 'cgroup'
    cg.mkdir()
    raw = ('MemTotal: 32505856 kB\nMemAvailable: 31457280 kB\n'
           'SwapTotal: 511705088 kB\nSwapFree: 511705088 kB\n')
    original_read = h._read
    monkeypatch.setattr(h, '_read', lambda path: raw if path == '/proc/meminfo' else original_read(path))
    monkeypatch.setattr(h.platform, 'release', lambda: 'microsoft-standard-WSL2')
    ram_limits, paging_limits = h.cgroup_limits(cg, ''), h.paging_limits(cg, '')
    monkeypatch.setattr(h, 'cgroup_limits', lambda: ram_limits)
    monkeypatch.setattr(h, 'paging_limits', lambda: paging_limits)
    monkeypatch.setattr(h, 'gpu_inventory', lambda: {})
    info = h.detect(tmp_path)
    assert info['wsl']
    assert info['memory'] == {'total_mb': 31744, 'available_mb': 30720,
                              'swap_mb': 499712, 'swap_available_mb': 499712,
                              'combined_total_mb': 531456, 'combined_available_mb': 530432}
    # Existing automatic tool/RAM defaults must not grow to include swap.
    assert h.presets(info)[0]['memory_mb'] < 30720


@pytest.mark.parametrize('total,free,expected', [(0, None, 0), (8192, 0, 0), (8192, 2048, 2048),
                                                (8192, 9999, 8192), (8192, None, None), (None, 8192, None)])
def test_available_swap_uses_free_not_total_and_unknowns_remain_unknown(tmp_path, total, free, expected):
    result = memory(tmp_path, SwapTotal=total, SwapFree=free)
    assert result['swap_available_mb'] == expected
    assert result['combined_available_mb'] == (30720 + expected if expected is not None else None)


def test_v2_swap_ancestor_headroom_is_the_minimum(tmp_path):
    controls(tmp_path / 'parent', {'memory.swap.max': 20 * GIB, 'memory.swap.current': 18 * GIB})
    controls(tmp_path / 'parent/child', {'memory.swap.max': 4 * GIB, 'memory.swap.current': GIB})
    result = memory(tmp_path)
    assert result['swap_mb'] == 488 * 1024  # Host total remains a separate observation.
    assert result['swap_available_mb'] == 2048
    assert result['combined_total_mb'] == (31 + 4) * 1024
    assert result['combined_available_mb'] == (30 + 2) * 1024


def test_v2_namespaced_root_limit_applies_without_visible_parent(tmp_path):
    controls(tmp_path, {'memory.swap.max': 3 * GIB, 'memory.swap.current': GIB})
    assert memory(tmp_path, '0::/')['swap_available_mb'] == 2048


@pytest.mark.parametrize('limit,usage', [('0', None), ('0', 'invalid'), (GIB, 2 * GIB)])
def test_zero_or_exceeded_swap_limit_authorizes_no_additional_swap(tmp_path, limit, usage):
    values = {'memory.swap.max': limit}
    if usage is not None:
        values['memory.swap.current'] = usage
    controls(tmp_path / 'parent', values)
    assert memory(tmp_path)['swap_available_mb'] == 0


@pytest.mark.parametrize('limit,usage', [('bad', '0'), ('', '0'), ('-5', '0'),
                                      (GIB, None), (GIB, ''), (GIB, '-1'), (GIB, 'bad')])
def test_malformed_or_unreadable_swap_capacity_fails_closed(tmp_path, limit, usage):
    values = {'memory.swap.max': limit}
    if usage is not None:
        values['memory.swap.current'] = usage
    controls(tmp_path / 'parent', values)
    result = memory(tmp_path)
    assert result['swap_available_mb'] is None
    assert result['combined_available_mb'] is None


def test_permission_failure_is_not_treated_as_absent_limit(tmp_path, monkeypatch):
    path = tmp_path / 'parent/memory.swap.max'
    controls(path.parent, {path.name: GIB})
    original = Path.read_text
    def read(self, *args, **kwargs):
        if self == path:
            raise PermissionError('fixture unreadable limit')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    assert memory(tmp_path)['swap_available_mb'] is None


def test_unlimited_swap_does_not_require_a_current_counter(tmp_path):
    controls(tmp_path / 'parent', {'memory.swap.max': 'max'})
    assert memory(tmp_path)['swap_available_mb'] == 488 * 1024


def test_v1_memsw_is_a_combined_limit_not_added_to_ram(tmp_path):
    controls(tmp_path / 'memory/parent', {'memory.limit_in_bytes': 4 * GIB,
                                         'memory.usage_in_bytes': GIB,
                                         'memory.memsw.limit_in_bytes': 10 * GIB,
                                         'memory.memsw.usage_in_bytes': 7 * GIB})
    controls(tmp_path / 'memory/parent/child', {'memory.limit_in_bytes': 3 * GIB,
                                               'memory.usage_in_bytes': GIB,
                                               'memory.memsw.limit_in_bytes': 8 * GIB,
                                               'memory.memsw.usage_in_bytes': 2 * GIB})
    result = memory(tmp_path, '2:memory:/parent/child')
    assert result['combined_total_mb'] == 8192
    assert result['combined_available_mb'] == 3072
    # The ancestor has only 3GiB combined headroom; do not advertise more free swap.
    assert result['swap_available_mb'] == 3072


@pytest.mark.parametrize('limit', [str(2 ** 63 - 4096), '-1'])
def test_v1_unlimited_sentinel_does_not_require_usage(tmp_path, limit):
    controls(tmp_path / 'memory/parent', {'memory.memsw.limit_in_bytes': limit})
    assert memory(tmp_path, '2:memory:/parent')['combined_available_mb'] == 518 * 1024


@pytest.mark.parametrize('values', [
    {'memory.memsw.limit_in_bytes': 'bad'},
    {'memory.memsw.limit_in_bytes': GIB},
    {'memory.memsw.limit_in_bytes': GIB, 'memory.memsw.usage_in_bytes': 'bad'},
])
def test_unknown_v1_memsw_capacity_cannot_authorize_swap(tmp_path, values):
    controls(tmp_path / 'memory/parent', values)
    result = memory(tmp_path, '2:memory:/parent')
    assert result['combined_available_mb'] is None
    assert result['swap_available_mb'] is None


def test_finite_ram_limit_with_unknown_usage_makes_combined_capacity_unknown(tmp_path):
    controls(tmp_path / 'parent', {'memory.max': 4 * GIB, 'memory.swap.max': 'max'})
    result = memory(tmp_path)
    assert result['combined_total_mb'] == 492 * 1024
    assert result['combined_available_mb'] is None


def test_sub_mib_and_exhausted_v1_limits_are_not_unlimited(tmp_path):
    controls(tmp_path / 'memory/parent', {'memory.memsw.limit_in_bytes': MIB - 1,
                                         'memory.memsw.usage_in_bytes': 2 * MIB})
    result = memory(tmp_path, '2:memory:/parent')
    assert result['combined_total_mb'] == 0
    assert result['combined_available_mb'] == result['swap_available_mb'] == 0
