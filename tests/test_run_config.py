"""Run-local settings travel from the editor to frozen native command inputs."""
import json
from pathlib import Path

import pytest
import yaml

from bactologix import run_config
from bactologix.runs import create_run, template
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample
from test_followup_adapter import screening


def patch(**values):
    return {json.dumps(name.split('.'), separators=(',', ':')): value for name, value in values.items()}


def test_editor_exposes_settings_without_materializing_catalog_defaults():
    text = 'run_kraken: true\ncustom: {number: 7, flags: [true, false]}\n'
    panel = run_config.editor('hybas', text)
    assert {'Kraken & AWARE', 'Assembly & polishing', 'Databases'} <= set(panel['groups'])
    fields = {f['name']: f for f in panel['fields']}
    assert fields['run_kraken']['kind'] == 'boolean'
    assert fields['polish_mode']['options'] == ['none', 'racon', 'medaka', 'tripolish']
    assert fields['threads']['managed']
    assert not fields['polish_mode']['included']
    assert fields['custom.flags']['kind'] == 'yaml'
    assert run_config.compile_config('hybas', text, {}) == text
    edited = yaml.safe_load(run_config.compile_config('hybas', text, patch(run_kraken=False, kraken_decision_mode='off')))
    assert edited['custom'] == {'number': 7, 'flags': [True, False]}
    assert 'polish_mode' not in edited


def test_run_configuration_is_frozen_and_saved_defaults_unchanged(workspace, installation, sample, monkeypatch):
    saved = 'run_kraken: true\nkraken_decision_mode: aware\nrun_busco: true\ncustom: {keep: [1, true]}\n'
    workspace.put('configs', 'hybas', saved)
    old = create_run(workspace, 'hybas', sample, saved)
    panel = dispatch(workspace, 'run_config_editor', {'tool': 'hybas'})['run_config']
    # A concurrent defaults change must not silently replace this dialog's configuration.
    workspace.put('configs', 'hybas', saved + 'later_default: true\n')
    changes = patch(run_kraken=False, kraken_decision_mode='off', polish_mode='none', run_busco=False, racon_rounds='0')
    monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/fixture/conda')
    result = dispatch(workspace, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']], 'cores': 3,
        'memory_mb': 12000, 'config': {'tool': 'hybas', 'text': panel['text'], 'changes': changes}})
    child = workspace.get('runs', result['open_run'])
    cfg = yaml.safe_load(child['config'])
    assert cfg['run_kraken'] is False and cfg['kraken_decision_mode'] == 'off'
    assert cfg['polish_mode'] == 'none' and cfg['racon_rounds'] == 0 and cfg['run_busco'] is False
    assert cfg['threads'] == 3 and cfg['custom'] == {'keep': [1, True]}
    assert 'later_default' not in cfg
    assert workspace.get('configs', 'hybas') == saved + 'later_default: true\n'
    assert workspace.get('runs', old['id']) == old
    assert str(Path(child['folder'], 'config.yaml')) in result['detail']['command']
    assert not child['dry_run_ok']


def test_basis_fields_and_nested_edits_keep_unrelated_options(screening):
    store, parent = screening
    cfg = yaml.safe_load(parent['config'])
    cfg['custom'] = {'literal.key': {'keep': 9}}
    text = yaml.safe_dump(cfg)
    fields = {f['name']: f for f in run_config.editor('basis', text)['fields']}
    assert 'Module: CARD / RGI' in {f['group'] for f in fields.values()}
    changes = patch(**{'modules.rgi.enabled': False, 'deep.maximum_candidates': '0', 'resources.policy': 'manual'})
    changes[json.dumps(['custom', 'literal.key', 'keep'], separators=(',', ':'))] = '10'
    result = run_config.compile_config('basis', text, changes)
    run_config.validate('basis', result)
    actual = yaml.safe_load(result)
    assert actual['modules']['rgi']['enabled'] is False
    assert actual['deep']['maximum_candidates'] == 0
    assert actual['custom']['literal.key']['keep'] == 10
    assert actual['deep']['aware'] == cfg['deep']['aware']
    assert yaml.safe_load(store.get('configs', 'basis', template('basis'))) == yaml.safe_load(template('basis'))


@pytest.mark.parametrize('changes', [patch(racon_rounds='1.5'), patch(run_kraken='false'), patch(threads='20'),
    patch(kraken_decision_mode='unknown'), patch(keep_percent='nan'), patch(keep_percent='')])
def test_editor_rejects_invalid_typed_changes(changes):
    with pytest.raises(ValueError):
        run_config.compile_config('hybas', template('hybas'), changes)


@pytest.mark.parametrize('values', [dict(run_kraken=False, kraken_decision_mode='aware'),
    dict(keep_percent=101), dict(assembly_mem_mb=4096, spades_memory_gb=8), dict(run_busco='false')])
def test_invalid_run_configuration_creates_no_drafts(workspace, installation, sample, values):
    with pytest.raises(ValueError):
        dispatch(workspace, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']], 'cores': 1,
            'memory_mb': 16000, 'config': {'tool': 'hybas', 'text': yaml.safe_dump(values)}})
    assert workspace.all('runs') == []


def test_wrong_pipeline_config_rejected_before_creation(workspace, installation, sample):
    with pytest.raises(ValueError, match='another pipeline'):
        dispatch(workspace, 'create_runs', {'tool': 'hybas', 'ids': [sample['id']], 'cores': 1,
            'memory_mb': 16000, 'config': {'tool': 'basis', 'text': template('basis')}})
    assert not workspace.all('runs')


def test_batch_uses_one_reviewed_configuration(workspace, installation, sample):
    from bactologix.runs import register_sample
    second = register_sample(workspace, 'second', sample['path'], sample['mode'])
    saved = 'run_kraken: true\nkraken_decision_mode: aware\n'
    workspace.put('configs', 'hybas', saved)
    result = dispatch(workspace, 'create_runs', {'tool': 'hybas', 'ids': [sample['id'], second['id']], 'cores': 2,
        'memory_mb': 16000, 'config': {'tool': 'hybas', 'text': saved,
                                     'changes': patch(run_kraken=False, kraken_decision_mode='off')}})
    assert '2 independent drafts' in result['message']
    assert len({r['output'] for r in workspace.all('runs')}) == 2
    assert all(yaml.safe_load(r['config'])['run_kraken'] is False for r in workspace.all('runs'))
    assert workspace.get('configs', 'hybas') == saved


def test_yaml_values_and_optional_overrides():
    text = 'custom: [original]\nkraken_mem_mb: 2000\n'
    changed = run_config.compile_config('hybas', text, patch(custom='["new", false, 0]', kraken_mem_mb=''))
    assert yaml.safe_load(changed) == {'custom': ['new', False, 0]}
    panel = run_config.editor('basis', 'deep: {aware: {tools: {example: {setting: 1}}}}')
    names = {f['name'] for f in panel['fields']}
    assert 'deep.aware.tools.example.setting' in names and 'deep.aware.tools' not in names
    empty = run_config.editor('basis', 'deep: {}')
    assert 'deep.enabled' in {f['name'] for f in empty['fields']}
    run_config.validate('basis', 'deep: false')
