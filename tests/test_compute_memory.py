"""Swap is an explicit, frozen scheduler allowance, never a tool RAM setting."""
import copy
import json
from pathlib import Path

import pytest
import yaml

from bactologix import compute_memory as memory, hardware, preflight, run_config
from bactologix.runs import command, create_run, validate_launch
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample
from test_run_revisions import request


def host(swap=488 * 1024):
    return {'checked_at': '2026-11-10T22:00:00+00:00', 'cpu': {'available': 12},
            'memory': {'total_mb': 31 * 1024, 'available_mb': 30 * 1024,
                       'swap_mb': swap, 'swap_available_mb': swap,
                       'combined_total_mb': 31 * 1024 + swap,
                       'combined_available_mb': 30 * 1024 + swap}}


def record(swap=98304):
    return {'cores': 12, 'tool': 'hybas', 'memory_mb': 28672,
            'config': yaml.safe_dump({'bactologix_compute': {'swap_mb': swap},
                                     'kraken_mem_mb': 113868})}


def test_reported_workstation_has_capacity_for_explicit_allowance():
    run = record()
    before = copy.deepcopy(run)
    assert memory.policy(run) == {'ram_mb': 28672, 'swap_mb': 98304, 'scheduler_mb': 126976}
    assert memory.capacity_issues(run, host()) == []
    assert preflight.budget(run, host()) == []
    assert '126,976 MB scheduling budget' in memory.summary(run)
    assert run == before


def test_ram_only_policy_stays_unchanged_even_with_plenty_of_swap():
    run = record(0)
    assert memory.policy(run)['scheduler_mb'] == 28672
    assert memory.capacity_issues(run, host()) == []
    assert 'kraken_mem_mb' in ' '.join(preflight.budget(run, host()))
    run['config'] = '{}'
    assert memory.policy(run)['swap_mb'] == 0


@pytest.mark.parametrize('swap', [None, True, False, -1, 0.5, 98304.0, '98304', [], {}, 2_000_001])
def test_malformed_allowance_is_never_coerced(swap):
    with pytest.raises(ValueError, match='whole number'):
        memory.with_swap('{}', swap)
    with pytest.raises(ValueError, match='whole number'):
        memory.policy(record(swap))


def test_combined_budget_is_bounded_and_other_compute_settings_survive():
    with pytest.raises(ValueError, match='combined'):
        memory.policy(record(2_000_000))
    original = {'assembly_mem_mb': 8192, 'spades_memory_gb': 8, 'polishing_mem_mb': 4096,
                'bactologix_compute': {'gpu_mode': 'cpu'}, 'custom': [True, 0]}
    text = yaml.safe_dump(original)
    updated = yaml.safe_load(memory.with_swap(text, 98304))
    assert updated.pop('bactologix_compute') == {'gpu_mode': 'cpu', 'swap_mb': 98304}
    assert updated == {k: v for k, v in original.items() if k != 'bactologix_compute'}
    assert yaml.safe_load(text) == original
    assert memory.allowance(yaml.safe_load(memory.with_swap(memory.with_swap(text, 98304), 0))) == 0


@pytest.mark.parametrize('change', ['zero_swap', 'used_swap', 'unknown_swap', 'unknown_combined', 'restricted_combined', 'no_ram_reserve'])
def test_capacity_is_effective_free_capacity_with_reserves(change):
    info = host()
    if change == 'zero_swap': info = host(0)
    elif change == 'used_swap': info['memory']['swap_available_mb'] = 98304
    elif change == 'unknown_swap': info['memory']['swap_available_mb'] = None
    elif change == 'unknown_combined': info['memory']['combined_available_mb'] = None
    elif change == 'restricted_combined': info['memory']['combined_available_mb'] = 126976
    elif change == 'no_ram_reserve': info['memory']['total_mb'] = 28672
    assert memory.capacity_issues(record(), info)


def test_allowance_is_managed_in_form_and_invalid_yaml_is_rejected():
    text = memory.with_swap('{}', 98304)
    fields = {row['name']: row for row in run_config.editor('hybas', text)['fields']}
    assert fields['bactologix_compute.swap_mb']['managed']
    key = json.dumps(['bactologix_compute', 'swap_mb'], separators=(',', ':'))
    with pytest.raises(ValueError, match='editable'):
        run_config.compile_config('hybas', text, {key: '131072'})
    with pytest.raises(ValueError, match='whole number'):
        run_config.validate('hybas', 'bactologix_compute: {swap_mb: 1.5}')


def test_frozen_policy_changes_scheduler_only(workspace, installation, sample, monkeypatch):
    config = 'assembly_mem_mb: 8192\nspades_memory_gb: 8\npolishing_mem_mb: 4096\nkraken_mem_mb: 113868\n'
    run = create_run(workspace, 'hybas', sample, memory.with_swap(config, 98304), 12, 28672)
    monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/fixture/conda')
    before = copy.deepcopy(run)
    argv = command(workspace, run, 'dry-run')
    assert 'mem_mb=126976' in argv and 'kraken_jobs=1' in argv and '--dry-run' in argv
    assert '--notemp' in argv and '--keep-incomplete' in argv
    frozen = yaml.safe_load(Path(run['folder'], 'config.yaml').read_text())
    assert frozen['assembly_mem_mb'] == 8192 and frozen['spades_memory_gb'] == 8
    assert frozen['polishing_mem_mb'] == 4096 and frozen['kraken_mem_mb'] == 113868
    assert run['memory_mb'] == 28672 and run == before


def test_capacity_is_rechecked_at_queue_and_worker_but_not_environment_setup(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, memory.with_swap('{}', 98304), 12, 28672)
    current = host()
    refreshes = []
    def inventory(store, refresh=False):
        refreshes.append(refresh)
        return copy.deepcopy(current)
    monkeypatch.setattr(hardware, 'inventory', inventory)
    validate_launch(workspace, run['id'], 'dry-run')
    current['memory']['swap_available_mb'] = 0
    with pytest.raises(ValueError, match='Pre-run memory checks'):
        validate_launch(workspace, run['id'], 'dry-run', check_busy=False)
    assert refreshes == [True, True]
    for action in ('prepare', 'check-environments'):
        preflight.assert_launch(run, action, store=workspace)
    assert refreshes == [True, True]
    run['dry_run_ok'] = True
    workspace.put('runs', run['id'], run)
    with pytest.raises(ValueError, match='Pre-run memory checks'):
        validate_launch(workspace, run['id'], 'run')
    assert refreshes == [True, True, True]


def test_guided_revision_freezes_swap_and_preserves_parent(workspace, installation, sample, monkeypatch):
    monkeypatch.setattr(hardware, 'inventory', lambda store, refresh=False: host())
    parent = create_run(workspace, 'hybas', sample, 'assembly_mem_mb: 8192\nspades_memory_gb: 8\n', 12, 28672)
    original = {p: p.read_bytes() for p in Path(parent['folder']).rglob('*') if p.is_file()}
    payload = request(workspace, parent)
    payload['swap_mb'] = 98304
    result = dispatch(workspace, 'revise_run', payload)
    child = workspace.get('runs', result['open_run'])
    assert memory.policy(child)['swap_mb'] == result['detail']['swap_mb'] == 98304
    assert memory.policy(parent)['swap_mb'] == 0
    assert child['config_sha256'] != parent['config_sha256']
    assert child['memory_mb'] == parent['memory_mb'] == 28672
    assert not child['dry_run_ok']
    assert workspace.get('runs', parent['id']) == parent
    assert all(path.read_bytes() == data for path, data in original.items())
    # The frozen allowance survives revision by an older client with no new field.
    inherited = dispatch(workspace, 'revise_run', request(workspace, child))
    grandchild = workspace.get('runs', inherited['open_run'])
    assert memory.policy(grandchild)['swap_mb'] == 98304
    disabled = request(workspace, grandchild)
    disabled['swap_mb'] = 0
    fourth = dispatch(workspace, 'revise_run', disabled)
    assert memory.policy(workspace.get('runs', fourth['open_run']))['swap_mb'] == 0


def test_create_payload_is_run_local_and_does_not_change_defaults(workspace, installation, sample, monkeypatch):
    monkeypatch.setattr(hardware, 'inventory', lambda store, refresh=False: host())
    defaults = 'assembly_mem_mb: 8192\nspades_memory_gb: 8\n'
    workspace.put('configs', 'hybas', defaults)
    response = dispatch(workspace, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']],
        'cores': 12, 'memory_mb': 28672, 'swap_mb': 98304})
    run = workspace.get('runs', response['open_run'])
    assert memory.policy(run)['scheduler_mb'] == 126976
    assert response['detail']['swap_mb'] == 98304
    assert workspace.get('configs', 'hybas') == defaults
    payload = request(workspace, run)
    assert dispatch(workspace, 'edit_run_settings', {'id': run['id']})['edit_run']['swap_mb'] == 98304
    payload['swap_mb'] = 1.5
    with pytest.raises(ValueError, match='whole number'):
        dispatch(workspace, 'revise_run', payload)
    assert len(workspace.all('runs')) == 1


def test_saved_pipeline_allowance_freezes_into_new_draft_and_never_rewrites_old_run(workspace, installation, sample, monkeypatch):
    info = host() | {'id': 'fixture'}
    monkeypatch.setattr(hardware, 'inventory', lambda store, refresh=False: info)
    workspace.put('configs', 'hybas', 'assembly_mem_mb: 8192\nspades_memory_gb: 8\n')
    parent = create_run(workspace, 'hybas', sample, workspace.get('configs', 'hybas'), 12, 28672)
    parent_files = {p: p.read_bytes() for p in Path(parent['folder']).rglob('*') if p.is_file()}
    dispatch(workspace, 'save_compute_defaults', {'tool': 'hybas', 'cores': 12, 'memory_mb': 28672,
        'gpu_mode': 'cpu', 'swap_mb': 98304, 'revisions': hardware.panel(workspace)['revisions'],
        'hardware_id': info['id']})
    saved_config = workspace.get('configs', 'hybas')
    response = dispatch(workspace, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']]})
    child = workspace.get('runs', response['open_run'])
    assert memory.policy(child) == {'ram_mb': 28672, 'swap_mb': 98304, 'scheduler_mb': 126976}
    frozen = yaml.safe_load(Path(child['folder'], 'config.yaml').read_text())
    assert frozen['bactologix_compute'] == {'gpu_mode': 'cpu', 'swap_mb': 98304}
    assert frozen['assembly_mem_mb'] == 28672 and frozen['spades_memory_gb'] == 28
    # A run-specific disable does not change the pipeline's saved preference.
    disabled = dispatch(workspace, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']], 'swap_mb': 0})
    assert memory.policy(workspace.get('runs', disabled['open_run']))['swap_mb'] == 0
    assert workspace.get('configs', 'hybas') == saved_config
    # Revising the old run retains its own RAM-only policy despite the new default.
    revised = dispatch(workspace, 'revise_run', request(workspace, parent))
    assert memory.policy(workspace.get('runs', revised['open_run']))['swap_mb'] == 0
    assert workspace.get('runs', parent['id']) == parent
    assert all(path.read_bytes() == data for path, data in parent_files.items())


def test_explicit_refresh_checks_rechecks_recovered_capacity(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, memory.with_swap('{}', 98304), 12, 28672)
    actual = host(0)
    cached = host(0)
    refreshes = []
    def inventory(store, refresh=False):
        nonlocal cached
        refreshes.append(refresh)
        if refresh:
            cached = copy.deepcopy(actual)
        return copy.deepcopy(cached)
    monkeypatch.setattr(hardware, 'inventory', inventory)
    first = dispatch(workspace, 'inspect_run', {'id': run['id']})['detail']
    row = next(item for item in first['readiness']['rows'] if item['id'] == 'resources')
    assert row['level'] == 'error'
    actual = host()
    second = dispatch(workspace, 'inspect_run', {'id': run['id']})['detail']
    row = next(item for item in second['readiness']['rows'] if item['id'] == 'resources')
    assert row['level'] == 'info'
    assert refreshes.count(True) == 2
