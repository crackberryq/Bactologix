"""Recommendations are capacity evidence, not permission to create OS swap."""
import copy
from pathlib import Path

import pytest
import yaml

from bactologix import swap_adviser as adviser
from test_native import workspace


MIB = 1024 ** 2
GIB = 1024 ** 3
HYBAS_10_SWAP = '44e60aa3795cd5940bea257e0a22f274c2898c88'
HYBAS_10_NO_SWAP = '0d334449d69fe2208b79affad2f5162ac801be05'


def database(tmp_path, size=98_605_320_723):
    folder = tmp_path / 'kraken'
    folder.mkdir()
    (folder / 'opts.k2d').write_bytes(b'opts')
    (folder / 'taxo.k2d').write_bytes(b'taxo')
    with (folder / 'hash.k2d').open('wb') as handle:
        handle.truncate(size - 8)  # Sparse planning fixture, not a real index.
    return folder


def host(swap=72 * 1024):
    return {'id': 'fresh-capacity', 'checked_at': 'fixture', 'cpu': {'available': 14},
            'memory': {'total_mb': 49 * 1024, 'available_mb': 49 * 1024,
                       'swap_mb': swap, 'swap_available_mb': swap,
                       'combined_total_mb': 49 * 1024 + swap,
                       'combined_available_mb': 49 * 1024 + swap}}


def storage(free=100 * GIB):
    return {'checked_at': 'fixture', 'errors': [], 'volumes': [
        {'id': 'linux:1', 'kind': 'linux', 'label': 'Workspace', 'available_bytes': free},
        {'id': 'windows:d', 'kind': 'windows_host', 'role': 'wsl_backing',
         'label': 'Windows backing storage', 'available_bytes': 100 * GIB},
        {'id': 'windows:e', 'kind': 'windows_host', 'role': 'wsl_swap',
         'label': 'Windows swap storage', 'available_bytes': 35 * GIB}]}


@pytest.fixture
def setup(workspace, tmp_path, monkeypatch):
    folder = database(tmp_path)
    config = yaml.safe_dump({'db_root': str(folder.parent), 'kraken_db_name': folder.name,
                             'run_kraken': True, 'kraken_mem_mb': 'auto'})
    workspace.put('configs', 'hybas', config)
    info, disks, calls = host(), storage(), []
    def inventory(store, refresh=False):
        assert store is workspace and refresh is True
        calls.append('hardware')
        return copy.deepcopy(info)
    class Probe:
        def __init__(self, store, run):
            assert store is workspace
            calls.append(copy.deepcopy(run))
        def sample(self):
            return copy.deepcopy(disks)
    monkeypatch.setattr(adviser.hardware, 'inventory', inventory)
    monkeypatch.setattr(adviser, 'CapacityProbe', Probe)
    return workspace, config, info, disks, calls, folder


def test_legacy_scheduler_produces_rounded_usable_allowance_without_mutation(setup, monkeypatch):
    workspace, config, info, disks, calls, folder = setup
    before = copy.deepcopy(workspace.all('configs'))
    def forbidden(*args, **kwargs):
        pytest.fail('Adviser must not read or recursively scan the database')
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'glob', forbidden)
    monkeypatch.setattr(Path, 'rglob', forbidden)
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert result['available'] and result['can_apply']
    assert result['inputs']['estimated_kraken_mb'] == 113868
    assert result['inputs']['calculated_swap_mb'] == 68608
    assert result['inputs']['usable_swap_mb'] == 71 * 1024
    assert result['proposal'] == {'memory_mb': 46080, 'swap_mb': 68608, 'allow_swap': True}
    assert '114,688 MB' in result['message']
    assert 'severe paging' in ' '.join(result['warnings'])
    assert result['binding']['hardware_id'] == 'fresh-capacity'
    assert workspace.all('configs') == before
    assert not workspace.jobs()
    assert calls[0] == 'hardware' and calls[1]['memory_mb'] == 46080


@pytest.fixture
def hybas10_setup(setup, monkeypatch):
    workspace, _, info, *_ = setup
    monkeypatch.setattr(workspace, 'channel', 'dev')
    workspace.put('installs', 'current-hybas', {'version': {'commit': HYBAS_10_SWAP}})
    workspace.put('settings', 'active-hybas', 'current-hybas')
    # This host's exact ceiling resolves to 42,102 native MB. Prematurely
    # truncating to MiB would resolve to 42,101 MB instead.
    memory = info['memory']
    limit = 52_627_500_000
    total = limit // MIB
    memory.update(limit_bytes=limit, total_mb=total, available_mb=total,
                  combined_total_mb=total + memory['swap_mb'],
                  combined_available_mb=total + memory['swap_available_mb'])
    return setup


def test_hybas10_swap_covers_reported_kraken_reservation(hybas10_setup):
    workspace, *_ = hybas10_setup
    result = adviser.recommend(workspace, 'hybas', 46080)
    inputs = result['inputs']
    assert result['can_apply'], result['message']
    assert inputs['estimated_kraken_mb'] == inputs['required_budget_mb'] == 113868
    assert inputs['ram_budget_mb'] == 46080
    assert inputs['effective_ram_mb'] == 42102
    assert inputs['usable_swap_mb'] == inputs['calculated_swap_mb'] == 72704
    assert inputs['effective_budget_mb'] == 114806
    assert result['proposal'] == {'memory_mb': 46080, 'swap_mb': 72704, 'allow_swap': True}


def test_hybas10_requirement_between_native_and_chosen_ram_needs_swap(hybas10_setup):
    workspace, config, _, _, _, folder = hybas10_setup
    with (folder / 'hash.k2d').open('wb') as handle:
        handle.truncate(100 * MIB)
    cfg = yaml.safe_load(config)
    cfg['kraken_mem_mb'] = 44000
    result = adviser.recommend(workspace, 'hybas', 46080, config=yaml.safe_dump(cfg))
    assert result['can_apply']
    assert result['inputs']['required_budget_mb'] == 44000
    assert result['proposal']['swap_mb'] == 2048


def test_hybas10_refuses_a_recommendation_one_mb_past_usable_swap(hybas10_setup):
    workspace, _, info, *_ = hybas10_setup
    info['memory']['swap_available_mb'] -= 1
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert result['inputs']['usable_swap_mb'] == 72703
    assert result['inputs']['calculated_swap_mb'] == 72704
    assert not result['can_apply'] and result['proposal'] is None


@pytest.mark.parametrize('total_setting,chosen,effective,swap', [
    ('auto', 41984, 41984, 72704),
    (41984, 46080, 41984, 72704),
    (46080, 46080, 46080, 68608),
])
def test_hybas10_honors_selected_and_native_ram_limits(hybas10_setup, total_setting, chosen, effective, swap):
    workspace, config, *_ = hybas10_setup
    cfg = yaml.safe_load(config)
    cfg['total_mem_mb'] = total_setting
    result = adviser.recommend(workspace, 'hybas', chosen, config=yaml.safe_dump(cfg))
    assert result['can_apply'], result['message']
    assert result['inputs']['effective_ram_mb'] == effective
    assert result['proposal']['swap_mb'] == swap


def test_hybas10_uses_frozen_source_when_active_pipeline_changes(hybas10_setup):
    workspace, config, *_ = hybas10_setup
    frozen = {'id': 'run-frozen', 'tool': 'hybas', 'commit': HYBAS_10_SWAP,
              'folder': str(workspace.home), 'memory_mb': 46080, 'config': config}
    workspace.put('runs', frozen['id'], frozen)
    workspace.put('installs', 'current-hybas', {'version': {'commit': HYBAS_10_NO_SWAP}})
    old_draft = adviser.recommend(workspace, 'hybas', 46080)
    assert not old_draft['can_apply'] and old_draft['proposal'] is None
    saved = adviser.recommend(workspace, 'hybas', 46080, run_id=frozen['id'])
    assert saved['can_apply'] and saved['proposal']['swap_mb'] == 72704
    assert workspace.get('runs', frozen['id']) == frozen


def test_saved_run_and_unsaved_override_are_read_only_and_use_exact_paths(setup, tmp_path):
    workspace, config, *_ = setup
    run = {'id': 'run-saved', 'tool': 'hybas', 'folder': str(tmp_path / 'run'),
           'output': str(tmp_path / 'run/outputs'), 'memory_mb': 8192, 'config': config}
    workspace.put('runs', run['id'], run)
    saved = adviser.recommend(workspace, 'hybas', 46080, run_id=run['id'])
    smaller = tmp_path / 'small'
    smaller.mkdir()
    for name in ('hash.k2d', 'opts.k2d', 'taxo.k2d'):
        (smaller / name).write_bytes(b'fixture')
    cfg = yaml.safe_load(config)
    cfg['kraken_db_name'] = smaller.name
    override = yaml.safe_dump(cfg)
    result = adviser.recommend(workspace, 'hybas', 46080, run_id=run['id'], config=override)
    assert result['can_apply'] and result['proposal']['swap_mb'] == 0
    assert result['binding']['config_sha256'] != saved['binding']['config_sha256']
    assert result['database']['resolved_path'] == str(smaller)
    assert workspace.get('runs', run['id']) == run


def test_default_relative_database_location_matches_new_draft_binding(setup):
    workspace, _, _, _, _, folder = setup
    alias = workspace.home / 'resources/hybas/db/standard'
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(folder, target_is_directory=True)
    workspace.put('configs', 'hybas', 'db_root: db\nkraken_db_name: standard\n')
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert result['can_apply']
    assert result['database']['selected_path'] == str(alias)
    assert result['database']['resolved_path'] == str(folder)


def test_higher_explicit_kraken_request_is_respected(setup):
    workspace, config, *_ = setup
    cfg = yaml.safe_load(config)
    cfg['kraken_mem_mb'] = 116000
    result = adviser.recommend(workspace, 'hybas', 46080, config=yaml.safe_dump(cfg))
    assert result['inputs']['required_budget_mb'] == 116000
    assert result['proposal']['swap_mb'] == 70656
    assert result['inputs']['configured_kraken_mb'] == 116000


@pytest.mark.parametrize('change', ['no_swap', 'swap_in_use', 'unknown_swap', 'unknown_combined',
                                  'container_limit', 'ram_overcommit', 'ram_in_use'])
def test_insufficient_or_unknown_capacity_never_offers_applicable_values(setup, change):
    workspace, _, info, _, _, _ = setup
    if change == 'no_swap':
        info.update(host(0))
    elif change == 'swap_in_use':
        info['memory']['swap_available_mb'] = 68 * 1024 - 1
    elif change == 'unknown_swap':
        info['memory']['swap_available_mb'] = None
    elif change == 'unknown_combined':
        info['memory']['combined_available_mb'] = None
    elif change == 'container_limit':
        info['memory']['combined_total_mb'] = 110000
    elif change == 'ram_overcommit':
        info['memory']['total_mb'] = 46080
    else:
        info['memory']['available_mb'] = 46080
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert result['available'] and not result['can_apply']
    assert result['proposal'] is None and result['problems']
    if change == 'no_swap':
        assert result['inputs']['additional_usable_swap_needed_mb'] == 68 * 1024
        assert result['inputs']['additional_swap_disk_headroom_mb'] == 88 * 1024
        assert 'Container limits' in ' '.join(result['warnings'])


@pytest.mark.parametrize('free', [0, 10 * GIB, 15 * GIB, 20 * GIB - 1])
@pytest.mark.parametrize('volume', [0, 1, 2])
def test_linux_backing_and_swap_volume_warning_headroom_all_matter(setup, free, volume):
    workspace, _, _, disks, _, _ = setup
    disks['volumes'][volume]['available_bytes'] = free
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert not result['can_apply'] and result['proposal'] is None
    assert result['storage']['thresholds'] == {
        'pause_bytes': 10 * GIB, 'resume_bytes': 15 * GIB, 'warning_bytes': 20 * GIB}
    assert 'at least 20 GiB' in ' '.join(result['problems'])


def test_exact_warning_boundary_can_be_reviewed_but_is_not_a_disk_growth_guarantee(setup):
    workspace, _, _, disks, _, _ = setup
    for row in disks['volumes']:
        row['available_bytes'] = 20 * GIB
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert result['can_apply']
    assert 'not a prediction' in result['storage']['notice']


@pytest.mark.parametrize('change', ['probe_error', 'no_volumes', 'invalid_free'])
def test_unverified_disk_measurement_is_not_treated_as_unlimited(setup, change):
    workspace, _, _, disks, _, _ = setup
    if change == 'probe_error':
        disks['errors'].append('Cannot verify Windows backing storage')
    elif change == 'no_volumes':
        disks['volumes'].clear()
    else:
        disks['volumes'][0]['available_bytes'] = None
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert not result['can_apply'] and result['proposal'] is None


@pytest.mark.parametrize('change', ['missing_index', 'disabled', 'other_pipeline'])
def test_no_guess_or_host_probe_without_a_supported_database_estimate(setup, change):
    workspace, config, _, _, calls, folder = setup
    tool = 'hybas'
    if change == 'missing_index':
        (folder / 'taxo.k2d').unlink()
    elif change == 'disabled':
        config = 'run_kraken: false\n'
    else:
        tool = 'basis'
        config = '{}'
    result = adviser.recommend(workspace, tool, 46080, config=config)
    assert not result['available'] and not result['can_apply'] and result['proposal'] is None
    assert 'calculated_swap_mb' not in result['inputs']
    assert not calls


@pytest.mark.parametrize('value', [True, -1, 1.5, '1000', [], {}])
def test_invalid_explicit_tool_memory_is_not_replaced_with_a_guess(setup, value):
    workspace, config, *_ = setup
    cfg = yaml.safe_load(config)
    cfg['kraken_mem_mb'] = value
    with pytest.raises(ValueError, match='Kraken memory'):
        adviser.recommend(workspace, 'hybas', 46080, config=yaml.safe_dump(cfg))


def test_zero_swap_proposal_still_requires_usable_ram_and_storage(setup):
    workspace, config, info, _, _, folder = setup
    with (folder / 'hash.k2d').open('wb') as handle:
        handle.truncate(10 * MIB)
    info.update(host(0))
    result = adviser.recommend(workspace, 'hybas', 46080)
    assert result['can_apply'] and result['proposal']['swap_mb'] == 0
    info['memory']['available_mb'] = 46080
    assert not adviser.recommend(workspace, 'hybas', 46080)['can_apply']


def test_configuration_and_run_validation_remain_explicit(setup):
    workspace, config, *_ = setup
    with pytest.raises(ValueError, match='existing run'):
        adviser.recommend(workspace, 'hybas', 46080, run_id='missing')
    with pytest.raises(ValueError, match='whole number'):
        adviser.recommend(workspace, 'hybas', True)
    with pytest.raises(ValueError, match='whole number'):
        adviser.recommend(workspace, 'hybas', 46080,
                          config=config + '\nbactologix_compute: {swap_mb: "98304"}\n')


def test_dispatch_compiles_current_unsaved_editor_fields_before_sizing(setup):
    from bactologix.ui_backend import dispatch
    workspace, config, *_ = setup
    original = workspace.get('configs', 'hybas')
    payload = {'tool': 'hybas', 'memory_mb': 46080, 'config': {
        'tool': 'hybas', 'text': config, 'changes': {'["kraken_mem_mb"]': '116000'}}}
    result = dispatch(workspace, 'recommend_swap', payload)['swap_recommendation']
    assert result['inputs']['configured_kraken_mb'] == 116000
    assert result['proposal']['swap_mb'] == 70656
    assert workspace.get('configs', 'hybas') == original
    payload['config']['tool'] = 'basis'
    with pytest.raises(ValueError, match='another pipeline'):
        dispatch(workspace, 'recommend_swap', payload)
