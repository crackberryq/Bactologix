"""Only reviewed HybAs v2 sources may interpret the explicit swap policy."""
import copy
import hashlib
from pathlib import Path

import pytest

from bactologix import catalog, compute_memory, release_contract
from bactologix.runs import command, create_run, validate_launch
from bactologix.store import Store
from test_native import workspace, installation, sample


CAPABILITY = {'interface': 1, 'swap_policy': 'physical_ram_plus_explicit_swap_v1',
              'helper': 'workflow/lib/settings.py'}
CONFIG = {'bactologix_compute': {'swap_mb': 98304}}


def review(source):
    texts = {'Hybas.smk': 'rule all:\n    input: []\n',
             'environment.yaml': 'dependencies: [python=3.11, snakemake=9.27.1]\n',
             'workflow/lib/settings.py': '# Reviewed swap scheduler fixture\n'}
    hashes = {}
    for name, text in texts.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        hashes[name] = hashlib.sha256(text.encode()).hexdigest()
    return {'tool': 'hybas', 'repository': catalog.PIPELINES['hybas']['repo'], 'commit': 'e' * 40,
            'tag': 'v10.0.0-swap-fixture', 'channel': 'prerelease', 'adapter_interface': 1,
            'configuration_schema': 'hybas-v2',
            'entry_points': {'workflow': 'Hybas.smk', 'environment': 'environment.yaml'},
            'runner': {'python': '3.11', 'snakemake': '9.27.1'}, 'tested': True,
            'test_run': 'https://github.com/crackberryq/Bactologix/actions/runs/40906609530',
            'files_sha256': hashes, 'compute_memory': copy.deepcopy(CAPABILITY)}


def test_capability_requires_reviewed_helper_checksum(tmp_path):
    row = review(tmp_path / 'source')
    assert release_contract.reason('hybas', row) is None
    del row['files_sha256'][CAPABILITY['helper']]
    assert 'swap-scheduling' in release_contract.reason('hybas', row)


@pytest.mark.parametrize('value', [True, {}, {'interface': 2},
    dict(CAPABILITY, interface=True), dict(CAPABILITY, helper='other.py'),
    dict(CAPABILITY, swap_policy='unknown'), dict(CAPABILITY, extra='unsupported')])
def test_capability_contract_is_explicit_and_versioned(tmp_path, value):
    row = review(tmp_path / 'source')
    row['compute_memory'] = value
    assert release_contract.reason('hybas', row) is not None


def test_verified_source_required_not_just_capability_marker(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = tmp_path / 'source'
    row = review(source)
    store.put('release_approvals', 'hybas:' + row['commit'], row)
    compute_memory.require_adapter(store, 'hybas', row['commit'], CONFIG, source)
    (source / CAPABILITY['helper']).write_text('# Replaced source\n')
    with pytest.raises(ValueError, match='preflight failed for workflow/lib/settings.py'):
        compute_memory.require_adapter(store, 'hybas', row['commit'], CONFIG, source)


def test_old_v2_remains_approved_without_swap_and_rejects_opt_in(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = tmp_path / 'source'
    row = review(source)
    del row['compute_memory']
    store.put('release_approvals', 'hybas:' + row['commit'], row)
    assert release_contract.reason('hybas', row) is None
    assert catalog.supported('hybas', row['commit'], store)
    compute_memory.require_adapter(store, 'hybas', row['commit'], {}, source)
    compute_memory.require_adapter(store, 'hybas', row['commit'], {'bactologix_compute': {'swap_mb': 0}}, source)
    with pytest.raises(ValueError, match='Update HybAs.*new run draft'):
        compute_memory.require_adapter(store, 'hybas', row['commit'], CONFIG, source)
    assert store.get('release_approvals', 'hybas:' + row['commit']) == row


def test_v1_and_other_pipelines_keep_existing_behavior(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = tmp_path / 'source'
    row = review(source)
    row.pop('compute_memory')
    row['configuration_schema'] = 'hybas-v1'
    store.put('release_approvals', 'hybas:' + row['commit'], row)
    compute_memory.require_adapter(store, 'hybas', row['commit'], CONFIG, source)
    for tool in ('basis', 'micpscope'):
        compute_memory.require_adapter(store, tool, row['commit'], CONFIG, source)


def test_old_v2_draft_creation_refuses_before_staging(workspace, installation, sample, monkeypatch):
    monkeypatch.setattr(release_contract, 'approval', lambda *_: {'configuration_schema': 'hybas-v2'})
    with pytest.raises(ValueError, match='does not support the additional swap allowance'):
        create_run(workspace, 'hybas', sample, compute_memory.with_swap('{}', 98304), 12, 28672)
    assert workspace.all('runs') == []
    assert not list((workspace.home / 'runs').glob('run-*'))
    # Same approved older source remains usable with its original policy.
    run = create_run(workspace, 'hybas', sample, '{}', 12, 28672)
    assert compute_memory.policy(run)['swap_mb'] == 0


def test_saved_opt_in_old_v2_fails_validation_and_command_preview(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, compute_memory.with_swap('{}', 98304), 12, 28672)
    original = copy.deepcopy(run)
    # Emulate a saved run from a client that did not check the v2 capability.
    monkeypatch.setattr(release_contract, 'approval', lambda *_: {'configuration_schema': 'hybas-v2'})
    monkeypatch.setattr(release_contract, 'preflight', lambda *_: None)
    with pytest.raises(ValueError, match='Update HybAs.*new run draft'):
        validate_launch(workspace, run['id'], 'dry-run')
    with pytest.raises(ValueError, match='Update HybAs.*new run draft'):
        command(workspace, run, 'dry-run')
    assert workspace.get('runs', run['id']) == original
