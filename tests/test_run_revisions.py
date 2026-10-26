"""Guided revisions preserve frozen provenance, original inputs and defaults."""
from pathlib import Path

import pytest
import yaml

from bactologix.runs import archive_runs, create_run, remove_sample
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample
from test_followup_adapter import screening
from test_run_config import patch


def request(store, run, changes=None):
    panel = dispatch(store, 'edit_run_settings', {'id': run['id']})
    return {'id': run['id'], 'signature': panel['edit_run']['signature'],
            'config': {'tool': run['tool'], 'text': panel['run_config']['text'], 'changes': changes or {}},
            'cores': run['cores'], 'memory_mb': run['memory_mb']}


def test_saved_run_inspection_has_actual_values(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, 'run_kraken: false\nkraken_decision_mode: off\ncustom: [one, two]\n')
    detail = dispatch(workspace, 'inspect_run', {'id': run['id']})['detail']
    fields = {f['name']: f for f in detail['settings']['fields'] if f['included']}
    assert fields['run_kraken']['value'] is False
    assert fields['custom']['value'] == ['one', 'two']
    assert detail['settings']['text'] == run['config']
    assert workspace.get('runs', run['id']) == run


def test_revision_uses_frozen_settings_preserves_parent_and_unregistered_inputs(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, 'run_kraken: true\nkraken_decision_mode: aware\ncustom: {keep: [false, 0]}\n')
    original = {p: p.read_bytes() for p in Path(run['folder']).rglob('*') if p.is_file()}
    defaults = 'run_busco: false\ncustom: changed global defaults\n'
    workspace.put('configs', 'hybas', defaults)
    remove_sample(workspace, sample['id'], sample)
    archive_runs(workspace, [run['id']], False)
    payload = request(workspace, run, patch(run_kraken=False, kraken_decision_mode='off', polish_mode='none'))
    assert payload['config']['text'] == run['config']
    payload.update(cores=3, memory_mb=12000)
    result = dispatch(workspace, 'revise_run', payload)
    child = workspace.get('runs', result['open_run'])
    cfg = yaml.safe_load(child['config'])
    assert child['parent'] == run['id'] and child['installation'] == run['installation']
    assert child['inputs'] == run['inputs'] and child['rule_cache'] == run['rule_cache']
    assert child['output'] != run['output'] and not child['dry_run_ok']
    assert cfg['threads'] == 3 and child['memory_mb'] == 12000
    assert cfg['kraken_decision_mode'] == 'off' and cfg['run_kraken'] is False
    assert cfg['custom'] == {'keep': [False, 0]} and cfg['db_root'] == yaml.safe_load(run['config'])['db_root']
    assert workspace.get('runs', run['id']) == run and workspace.get('configs', 'hybas') == defaults
    assert all(p.read_bytes() == content for p, content in original.items())
    assert not workspace.all('samples')
    assert result['detail']['settings']['text'] == child['config']


def test_basis_revision_preserves_nested_settings(screening):
    store, parent = screening
    payload = request(store, parent, patch(project='Revised software fixture', **{'modules.rgi.enabled': False}))
    result = dispatch(store, 'revise_run', payload)
    child = store.get('runs', result['open_run'])
    old, new = yaml.safe_load(parent['config']), yaml.safe_load(child['config'])
    assert new['project'] == 'Revised software fixture' and new['modules']['rgi']['enabled'] is False
    assert new['deep'] == old['deep'] and new['resources'] == old['resources']
    assert store.get('runs', parent['id']) == parent and child['parent'] == parent['id']


@pytest.mark.parametrize('change', ['signature', 'pipeline', 'budget', 'yaml', 'busy', 'archived', 'inputs', 'workflow'])
def test_revision_rejects_invalid_or_changed_sources(workspace, installation, sample, change):
    run = create_run(workspace, 'hybas', sample, '{}')
    payload = request(workspace, run)
    if change == 'signature': payload['signature'] = 'stale'
    elif change == 'pipeline': payload['config']['tool'] = 'basis'
    elif change == 'budget': payload['cores'] = 0
    elif change == 'yaml': payload['config']['text'] = 'broken: ['
    elif change == 'busy': workspace.submit('workflow', {'run_id': run['id'], 'action': 'dry-run'})
    elif change == 'archived': archive_runs(workspace, [run['id']])
    elif change == 'inputs': Path(run['inputs'][0]['path']).write_text('Changed software input\n')
    elif change == 'workflow': (Path(run['folder']) / 'workflow/Hybas.smk').write_text('Changed workflow\n')
    with pytest.raises((ValueError, yaml.YAMLError)):
        dispatch(workspace, 'revise_run', payload)
    assert len(workspace.all('runs')) == 1
    assert workspace.get('runs', run['id']) == run
