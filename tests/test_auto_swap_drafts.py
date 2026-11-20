from pathlib import Path

import pytest
import yaml

from bactologix import hardware, readiness, runs, swap_adviser
from bactologix.runs import create_run, parse_config
from bactologix.ui_backend import dispatch
from test_hardware import capacity
from test_native import installation, sample, workspace
from test_swap_adviser import host, storage


def test_opt_in_uses_config_revision_and_survives_preset(capacity):
    store, info = capacity
    initial = hardware.panel(store)['revisions']
    assert not hardware.run_defaults(store, 'hybas')['auto_swap']
    payload = {'tool': 'hybas', 'cores': 2, 'memory_mb': 4096, 'gpu_mode': 'cpu', 'swap_mb': 8201,
               'hardware_id': info['id'], 'revisions': initial, 'auto_swap': True}
    dispatch(store, 'save_compute_defaults', payload)
    saved = hardware.run_defaults(store, 'hybas')
    assert saved['auto_swap'] and saved['swap_mb'] == 8201
    with pytest.raises(ValueError, match='defaults changed'):
        dispatch(store, 'save_compute_defaults', dict(payload, auto_swap=False))
    hardware.apply_preset(store, 'medium', ['hybas'], hardware.panel(store)['revisions'], info['id'])
    assert hardware.run_defaults(store, 'hybas')['auto_swap']
    dispatch(store, 'save_compute_defaults', dict(payload, revisions=hardware.panel(store)['revisions'], auto_swap=False))
    saved = hardware.run_defaults(store, 'hybas')
    assert not saved['auto_swap'] and saved['swap_mb'] == 8201


@pytest.fixture
def draft(workspace, installation, sample, monkeypatch):
    folder = workspace.home / 'resources' / 'hybas' / 'db' / 'current'
    folder.mkdir(parents=True)
    for name in ('opts.k2d', 'taxo.k2d', 'hash.k2d'):
        (folder / name).write_bytes(b'index')
    config = {'db_root': 'db', 'kraken_db_name': 'current', 'kraken_mem_mb': 46011,
              'run_kraken': True, 'bactologix_compute': {'auto_swap': True, 'swap_mb': 25009}}
    workspace.put('configs', 'hybas', yaml.safe_dump(config))
    workspace.put('resource_defaults', 'hybas', {'cores': 2, 'memory_mb': 16000})
    info = host()
    monkeypatch.setattr(hardware, 'inventory', lambda *a, **kw: info)
    monkeypatch.setattr(hardware, 'compute_capacity', lambda: info)
    monkeypatch.setattr(swap_adviser, '_storage', lambda store, run: storage())
    return workspace, config, info, sample


@pytest.mark.parametrize('choices, expected', [
    ({}, 30011), ({'auto_swap': True, 'swap_mb': 0}, 30011),
    ({'auto_swap': False}, 25009), ({'swap_mb': 0}, 0), ({'swap_mb': 20001}, 20001),
])
def test_new_drafts_fix_a_numeric_allowance_with_explicit_override(draft, choices, expected):
    store, config, _, sample = draft
    result = dispatch(store, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']], **choices})
    run = store.get('runs', result['open_run'])
    for text in (run['submitted'], run['config'], (Path(run['folder']) / 'config.yaml').read_text()):
        assert parse_config(text)['bactologix_compute'] == {'swap_mb': expected}
    assert parse_config(run['config'])['db_root'] == str(store.home / 'resources/hybas/db')
    assert parse_config(store.get('configs', 'hybas')) == config


def test_each_save_remeasures_and_leaves_previous_run_intact(draft):
    store, _, info, sample = draft
    payload = {'tool': 'hybas', 'ids': [sample['id']]}
    first = store.get('runs', dispatch(store, 'create_runs', payload)['open_run'])
    info['memory']['swap_available_mb'] = 30010 + 1024
    with pytest.raises(ValueError, match='30,010 MB usable'):
        dispatch(store, 'create_runs', payload)
    assert store.all('runs') == [first]
    assert [p.name for p in (store.home / 'runs').iterdir()] == [first['id']]


def test_pinned_source_and_compiled_external_database_determine_swap(draft, installation, tmp_path, monkeypatch):
    store, config, info, sample = draft
    store.put('settings', 'active-hybas', 'different-installation')
    info['memory'].update(limit_bytes=52_627_500_000, total_mb=50189, available_mb=50189,
                          swap_available_mb=73726, combined_available_mb=123915)
    folder = tmp_path / 'external' / 'standard'
    folder.mkdir(parents=True)
    (folder / 'opts.k2d').write_bytes(b'opts')
    (folder / 'taxo.k2d').write_bytes(b'taxo')
    with (folder / 'hash.k2d').open('wb') as f:
        f.truncate(98_605_320_723 - 8)
    config.update(db_root=str(folder.parent), kraken_db_name=folder.name)
    del config['kraken_mem_mb']

    def review(_store, tool, commit):
        assert tool == 'hybas' and commit == installation['version']['commit']
        return {'configuration_schema': 'hybas-v2', 'compute_memory': {'interface': 1}}

    monkeypatch.setattr(swap_adviser, 'approval', review)
    run = create_run(store, 'hybas', sample, yaml.safe_dump(config), memory_mb=46080,
                     installation=installation['id'], auto_swap=True)
    assert run['commit'] == installation['version']['commit']
    assert parse_config(run['config'])['bactologix_compute'] == {'swap_mb': 71766}


def test_derived_sample_uses_resolved_off_without_storage_probe(draft, monkeypatch):
    store, cfg, _, sample = draft
    monkeypatch.setattr(swap_adviser, '_storage', lambda *a: pytest.fail('Kraken is disabled'))
    run = create_run(store, 'hybas', {**sample, 'aware_provenance': {'origin': 'fixture'}},
                     yaml.safe_dump(cfg), auto_swap=True)
    frozen = parse_config(run['config'])
    assert frozen['run_kraken'] is False and frozen['bactologix_compute'] == {'swap_mb': 0}


def test_first_draft_overview_reuses_creation_hash_and_only_probes_compute_capacity(draft, monkeypatch):
    store, _, info, sample = draft
    digest = runs.tree_digest
    frozen_checks, overview_checks, inventory_requests = [], [], []

    def checked_digest(folder, checks):
        checks.append(folder)
        return digest(folder)

    def inventory(store, refresh=False):
        inventory_requests.append(refresh)
        return info

    monkeypatch.setattr(runs, 'tree_digest', lambda folder: checked_digest(folder, frozen_checks))
    monkeypatch.setattr(readiness, 'tree_digest', lambda folder: checked_digest(folder, overview_checks))
    monkeypatch.setattr(hardware, 'inventory', inventory)
    result = dispatch(store, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']], 'auto_swap': True})
    run = result['detail']
    workflow = Path(run['folder']) / 'workflow'
    assert frozen_checks == [workflow]
    assert overview_checks == [] and inventory_requests == []
    pipeline = next(row for row in run['readiness']['rows'] if row['id'] == 'pipeline')
    assert pipeline['state'] == 'Setup recorded' and pipeline['checked_at'] == run['created']
    assert run['readiness']['validated'] is False

    # A new request must recheck even if the client asks for the first-view shortcut.
    (workflow / 'changed.txt').write_text('source changed after save')
    result = dispatch(store, 'inspect_run', {'id': run['id'], 'just_created': True})
    assert overview_checks == [workflow] and True in inventory_requests
    pipeline = next(row for row in result['detail']['readiness']['rows'] if row['id'] == 'pipeline')
    assert pipeline['level'] == 'error' and 'source changed' in pipeline['text']
