"""Database-path propagation and immutable run revisions; software fixtures only."""
import copy
from pathlib import Path

import pytest
import yaml

from bactologix import database_runs, databases
from bactologix.runs import create_run, command, validate_launch
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample
from test_databases import busco, files, kraken
from test_followup_adapter import screening


def save(store, tool, changes):
    return databases.save(store, tool, changes, databases.panel(store, tool)['revision'])


def reviewed(store, run, keys):
    plan = database_runs.preview(store, [run['id']])
    return {'ids': [run['id']], 'selections': {run['id']: keys}, 'signature': plan['signature']}


def test_apply_saved_busco_updates_command_preserves_parent_and_options(workspace, installation, sample, tmp_path, monkeypatch):
    old = kraken(tmp_path / 'old root/standard')
    parent = create_run(workspace, 'hybas', sample, yaml.safe_dump({
        'db_root': str(old.parent), 'busco_lineage': 'bacteria_odb10',
        'run_busco': True, 'run_kraken': False, 'polish_mode': 'none',
        'custom': {'keep': [1, True]}, 'busco_download_lineages': ['bacteria_odb10']}), 3, 12000)
    parent.update(dry_run_ok=True, completed='software fixture')
    workspace.put('runs', parent['id'], parent)
    before = copy.deepcopy(parent)
    workspace.put('installs', installation['id'], dict(installation, rule_cache=str(tmp_path / 'later-cache')))
    target = busco(tmp_path / 'new disk/bacteria_odb12')
    save(workspace, 'hybas', {'busco': str(target), 'kraken': str(kraken(tmp_path / 'new kraken'))})
    monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/fixture/conda')
    response = dispatch(workspace, 'apply_run_databases', reviewed(workspace, parent, ['busco']))
    child = workspace.get('runs', response['open_run'])
    cfg = yaml.safe_load(child['config'])
    assert databases._hybas_paths(workspace, cfg)['busco'].resolve() == target
    assert databases._hybas_paths(workspace, cfg)['kraken'].resolve() == old
    assert cfg['run_busco'] and not cfg['run_kraken'] and cfg['polish_mode'] == 'none'
    assert cfg['custom'] == {'keep': [1, True]}
    assert cfg['busco_download_lineages'] == ['bacteria_odb12', 'bacteria_odb10']
    assert child['cores'] == 3 and child['memory_mb'] == 12000
    assert child['rule_cache'] == parent['rule_cache']
    assert child['inputs'] == parent['inputs'] and child['commit'] == parent['commit']
    assert child['parent'] == parent['id'] and child['output'] != parent['output']
    assert not child['dry_run_ok'] and 'completed' not in child
    assert workspace.get('runs', parent['id']) == before
    assert Path(parent['folder'], 'config.yaml').read_text() == before['config']
    argv = command(workspace, child, 'dry-run')
    assert argv[argv.index('--configfile') + 1] == str(Path(child['folder'], 'config.yaml'))
    assert child['id'] in response['detail']['command']
    assert next(p for p in response['detail']['database_paths'] if p['id'] == 'busco')['path'] == str(target)
    with pytest.raises(ValueError, match='dry-run'):
        validate_launch(workspace, child['id'], 'run')


def test_basis_apply_changes_resource_fields_only(screening, tmp_path):
    store, base = screening
    cfg = yaml.safe_load(base['config'])
    cfg['modules'] = {'rgi': {'enabled': False, 'database': '/old/card.json', 'metadata': '/old/metadata', 'database_version': 'old', 'threshold': 0.73}, 'vfdb': {'enabled': False}}
    cfg['resources']['policy'] = 'manual'
    cfg['custom_note'] = 'keep this run setting'
    parent = create_run(store, 'basis', base['sample'], yaml.safe_dump(cfg))
    target = files(tmp_path / 'saved CARD', ['card.json'])
    save(store, 'basis', {'rgi': str(target)})
    child, = database_runs.apply(store, **reviewed(store, parent, ['rgi']))
    actual = yaml.safe_load(child['config'])
    assert actual['modules']['rgi']['database'] == str(target / 'card.json')
    assert actual['modules']['rgi'].get('metadata') is None
    assert actual['modules']['rgi']['threshold'] == 0.73
    assert actual['modules']['rgi']['enabled'] is False
    assert actual['resources']['policy'] == 'manual'
    assert actual['custom_note'] == cfg['custom_note']
    assert actual['modules']['vfdb'] == cfg['modules']['vfdb']
    assert store.get('runs', parent['id']) == parent


def test_batch_apply_and_unchanged_resource_rejected(workspace, installation, sample, tmp_path):
    parents = [create_run(workspace, 'hybas', sample, 'custom: ' + str(n)) for n in range(2)]
    target = busco(tmp_path / 'saved/bacteria_odb10')
    save(workspace, 'hybas', {'busco': str(target)})
    plan = database_runs.preview(workspace, [r['id'] for r in parents])
    children = database_runs.apply(workspace, [r['id'] for r in parents], {r['id']: ['busco'] for r in parents}, plan['signature'])
    assert len(children) == 2
    assert {yaml.safe_load(c['config'])['custom'] for c in children} == {0, 1}
    assert {c['parent'] for c in children} == {p['id'] for p in parents}
    plan = database_runs.preview(workspace, [children[0]['id']])
    assert not next(r for r in plan['runs'][0]['rows'] if r['id'] == 'busco')['changed']
    with pytest.raises(ValueError, match='changed, valid'):
        database_runs.apply(workspace, **reviewed(workspace, children[0], ['busco']))


@pytest.mark.parametrize('change', ['settings', 'database_removed', 'input', 'workflow', 'queued', 'added_input'])
def test_apply_revalidates_review_and_original_run(workspace, installation, sample, tmp_path, change):
    parent = create_run(workspace, 'hybas', sample, 'run_busco: true')
    target = busco(tmp_path / 'saved/bacteria_odb10')
    save(workspace, 'hybas', {'busco': str(target)})
    args = reviewed(workspace, parent, ['busco'])
    if change == 'settings':
        workspace.put('configs', 'hybas', workspace.get('configs', 'hybas') + '\ncustom: changed\n')
    elif change == 'database_removed':
        (target / 'dataset.cfg').unlink()
    elif change == 'input':
        Path(sample['path'], 'raw/illumina/sample_R1.fastq').write_text('changed')
    elif change == 'workflow':
        Path(parent['folder'], 'workflow/Hybas.smk').write_text('changed')
    elif change == 'queued':
        workspace.submit('workflow', {'run_id': parent['id'], 'action': 'dry-run'})
    else:
        Path(sample['path'], 'raw/illumina/extra_R1.fastq').write_text('@fixture\nACGT\n+\nIIII\n')
    with pytest.raises(ValueError):
        database_runs.apply(workspace, **args)
    assert len(workspace.all('runs')) == 1


def test_missing_saved_database_shown_and_cannot_be_applied(workspace, installation, sample):
    parent = create_run(workspace, 'hybas', sample, 'run_busco: true')
    plan = database_runs.preview(workspace, [parent['id']])
    row = next(r for r in plan['runs'][0]['rows'] if r['id'] == 'busco')
    assert not row['valid'] and 'does not exist' in row['error']
    with pytest.raises(ValueError, match='changed, valid'):
        database_runs.apply(workspace, **reviewed(workspace, parent, ['busco']))
    assert len(workspace.all('runs')) == 1
