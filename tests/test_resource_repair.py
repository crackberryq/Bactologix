"""Stale VFDB repairs create separate drafts and preserve original evidence."""
import copy
import hashlib
from pathlib import Path
import subprocess

import pytest
import yaml

from bactologix import resource_repair as repair
from bactologix.runs import create_run, archive_runs
from bactologix.store import Store
from test_release_contract import review


@pytest.fixture
def stale_run(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'BaSIS.smk').write_text('rule all:\n    input: []\n')
    (source / 'environment.yml').write_text('dependencies: [python=3.11, snakemake=9.6.2]\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                    'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    row = review()
    row.update(commit=commit, database_downloads=1,
               files_sha256={name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                             for name in ('BaSIS.smk', 'environment.yml')})
    store.put('release_approvals', 'basis:' + commit, row)
    controller = tmp_path / 'runner'
    (controller / 'bin').mkdir(parents=True)
    for name in ('python', 'snakemake'):
        path = controller / 'bin' / name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    installed = {'id': 'basis-fixture', 'tool': 'basis', 'source': str(source),
                 'controller': str(controller), 'ready': True, 'version': {'commit': commit, 'ref': 'fixture'}}
    store.put('installs', installed['id'], installed)
    store.put('settings', 'active-basis', installed['id'])
    monkeypatch.setattr('bactologix.maintenance.find_conda', lambda _: '/fixture/conda')
    folder = tmp_path / 'sample'
    (folder / 'annotation').mkdir(parents=True)
    for ext in ('fna', 'faa', 'gff'):
        (folder / 'annotation' / ('sample.' + ext)).write_text('Software-only input fixture\n')
    cfg = {'resources': {'directory': str(store.home / 'resources/basis'), 'policy': 'manual'},
           'modules': {'vfdb': {'enabled': False, 'database': str(tmp_path / 'old/vfdb_setA.dmnd'),
                               'reference_fasta': str(tmp_path / 'old/VFDB_setA_pro.normalized.fas'),
                               'metadata': str(tmp_path / 'old/resource-provenance.json'),
                               'database_version': 'old recorded identity', 'identity': 77, 'coverage': 83},
                       'rgi': {'enabled': True, 'database': '/unchanged/card.json'}},
           'deep': {'enabled': False}, 'bactologix_compute': {'swap_mb': 8192}}
    parent = create_run(store, 'basis', {'id': 'sample', 'path': str(folder)}, yaml.safe_dump(cfg),
                        cores=5, memory_mb=12000)
    return store, parent


def test_repair_creates_new_draft_preserving_frozen_parent_and_settings(stale_run):
    store, parent = stale_run
    original = {p: p.read_bytes() for p in Path(parent['folder']).rglob('*') if p.is_file()}
    defaults = 'custom: future defaults\n'
    store.put('configs', 'basis', defaults)
    plan = repair.preview(store, parent['id'])
    assert plan['available'] and 'other enabled resources' in plan['notice']
    assert len(plan['changes']) == 5
    child = repair.apply(store, parent['id'], plan['signature'])
    before, after = yaml.safe_load(parent['config']), yaml.safe_load(child['config'])
    assert child['parent'] == parent['id'] and child['output'] != parent['output']
    assert child['inputs'] == parent['inputs'] and child['rule_cache'] == parent['rule_cache']
    assert child['commit'] == parent['commit'] and not child['dry_run_ok']
    assert child['cores'] == 5 and child['memory_mb'] == 12000
    assert after['bactologix_compute'] == before['bactologix_compute']
    expected = copy.deepcopy(before['modules'])
    for field in ('database', 'reference_fasta', 'metadata', 'database_version'):
        expected['vfdb'].pop(field)
    assert after['modules'] == expected and after['deep'] == before['deep']
    assert after['resources'] == {**before['resources'], 'policy': 'auto'}
    assert store.get('runs', parent['id']) == parent and store.get('configs', 'basis') == defaults
    assert all(p.read_bytes() == content for p, content in original.items())
    assert not store.jobs() and not list(store.home.rglob('provenance.json'))
    assert not repair.preview(store, child['id'])['available']


@pytest.mark.parametrize('change', ['signature', 'workflow', 'configuration', 'input', 'queued', 'archived',
                                   'reappeared', 'installation', 'approval', 'controller'])
def test_repair_rechecks_preview_and_preserves_parent_on_rejection(stale_run, change):
    store, parent = stale_run
    plan = repair.preview(store, parent['id'])
    signature = plan['signature']
    if change == 'signature': signature = 'stale'
    elif change == 'workflow': (Path(parent['folder']) / 'workflow/BaSIS.smk').write_text('changed')
    elif change == 'configuration': (Path(parent['folder']) / 'config.yaml').write_text('changed')
    elif change == 'input': Path(parent['inputs'][0]['path']).write_text('changed')
    elif change == 'queued': store.submit('workflow', {'run_id': parent['id'], 'action': 'dry-run'})
    elif change == 'archived': archive_runs(store, [parent['id']])
    elif change == 'reappeared':
        path = Path(yaml.safe_load(parent['config'])['modules']['vfdb']['database'])
        path.parent.mkdir()
        path.write_text('not a verified database')
    elif change == 'installation':
        installed = store.get('installs', parent['installation'])
        installed['rule_cache'] = str(store.home / 'different-cache')
        store.put('installs', installed['id'], installed)
    elif change == 'approval': store.put('release_approvals', 'basis:' + parent['commit'], {})
    else:
        installed = store.get('installs', parent['installation'])
        assert repair.maintenance.download_panel_capability(installed, store)['available']
        (Path(parent['controller']) / 'bin/snakemake').unlink()
        display = repair.maintenance.download_panel_capability(installed, store)
        assert display['available'] and display['cached']
    with pytest.raises(ValueError):
        repair.apply(store, parent['id'], signature)
    assert len(store.all('runs')) == 1 and store.get('runs', parent['id']) == parent


def test_broken_link_is_reported_without_following_or_removing_it(stale_run):
    store, parent = stale_run
    path = Path(yaml.safe_load(parent['config'])['modules']['vfdb']['database'])
    path.parent.mkdir()
    path.symlink_to(path.parent / 'moved.dmnd')
    plan = repair.preview(store, parent['id'])
    assert plan['available'] and plan['fields'][0]['status'] == 'broken link'
    repair.apply(store, parent['id'], plan['signature'])
    assert path.is_symlink()


def test_present_files_are_not_claimed_verified_or_replaced(stale_run):
    store, parent = stale_run
    cfg = yaml.safe_load(parent['config'])
    for field in ('database', 'reference_fasta', 'metadata'):
        path = Path(cfg['modules']['vfdb'][field])
        path.parent.mkdir(exist_ok=True)
        path.write_text('unverified bytes')
    plan = repair.preview(store, parent['id'])
    assert not plan['available'] and 'not been verified' in plan['reason']
    assert all(row['status'] == 'present; identity not verified' for row in plan['fields'][:3])
    with pytest.raises(ValueError, match='not been verified'):
        repair.apply(store, parent['id'], plan['signature'])
