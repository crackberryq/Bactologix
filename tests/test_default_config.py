"""Defaults use shared controls without rewriting history or newer selections."""
from pathlib import Path

import pytest
import yaml

from bactologix import default_config, run_config
from bactologix.runs import create_run, template
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample
from test_run_config import patch


def panel(store, tool='hybas'):
    return dispatch(store, 'default_config_editor', {'tool': tool})['default_config']


def payload(editor, changes=None, **extra):
    return {k: editor[k] for k in ('tool', 'text', 'revision')} | {'changes': changes or {}} | extra


def test_read_check_and_download_do_not_save(workspace):
    text = 'custom: {literal.key: [false, 0, keep]}\n'
    workspace.put('configs', 'hybas', text)
    editor = panel(workspace)
    changes = patch(run_kraken=False, kraken_decision_mode='off')
    result = dispatch(workspace, 'default_config_editor', payload(editor, changes, validate=True, download=True, view='yaml'))
    cfg = yaml.safe_load(result['download']['text'])
    assert cfg['custom'] == {'literal.key': [False, 0, 'keep']}
    assert cfg['run_kraken'] is False and cfg['kraken_decision_mode'] == 'off'
    assert 'polish_mode' not in cfg
    assert result['default_config']['revision'] == editor['revision']
    assert workspace.get('configs', 'hybas') == text


def test_saved_defaults_apply_to_future_runs_only(workspace, installation, sample):
    text = 'run_kraken: true\nkraken_decision_mode: aware\ncustom: {keep: [false, 0]}\n'
    workspace.put('configs', 'hybas', text)
    parent = create_run(workspace, 'hybas', sample, text)
    original_files = {p: p.read_bytes() for p in Path(parent['folder']).rglob('*') if p.is_file()}
    editor = panel(workspace)
    result = dispatch(workspace, 'save_default_config', payload(editor, patch(run_kraken=False, kraken_decision_mode='off')))
    assert result['default_config_saved'] and result['default_config']['revision'] != editor['revision']
    new_text = workspace.get('configs', 'hybas')
    child = create_run(workspace, 'hybas', sample, new_text)
    cfg = yaml.safe_load(child['config'])
    assert cfg['run_kraken'] is False and cfg['kraken_decision_mode'] == 'off'
    assert cfg['custom'] == {'keep': [False, 0]}
    assert workspace.get('runs', parent['id']) == parent
    assert all(p.read_bytes() == content for p, content in original_files.items())


def test_basis_nested_edits_and_custom_yaml_round_trip(workspace):
    editor = panel(workspace, 'basis')
    changed = dispatch(workspace, 'default_config_editor', payload(editor,
                       patch(**{'modules.rgi.enabled': False}), view='yaml'))['default_config']
    changed['text'] += '\ncustom_defaults: {literal.key: [false, 0]}\n'
    result = dispatch(workspace, 'save_default_config', payload(changed))
    saved = yaml.safe_load(workspace.get('configs', 'basis'))
    before = yaml.safe_load(template('basis'))
    assert saved['modules']['rgi']['enabled'] is False
    assert saved['deep'] == before['deep'] and saved['resources'] == before['resources']
    assert saved['custom_defaults'] == {'literal.key': [False, 0]}
    assert result['default_config']['text'] == workspace.get('configs', 'basis')
    assert workspace.get('configs', 'hybas') is None


@pytest.mark.parametrize('concurrent', [False, True])
def test_stale_save_cannot_overwrite_new_database_defaults(workspace, monkeypatch, concurrent):
    editor = panel(workspace)
    newer = editor['text'] + '\ncustom_new_database_selection: /software/fixture\n'
    if concurrent:
        compile_config = run_config.compile_config
        def competing_save(*args, **kwargs):
            result = compile_config(*args, **kwargs)
            workspace.put('configs', 'hybas', newer)
            return result
        monkeypatch.setattr(default_config.run_config, 'compile_config', competing_save)
    else:
        workspace.put('configs', 'hybas', newer)
    with pytest.raises(ValueError, match='Saved defaults changed'):
        dispatch(workspace, 'save_default_config', payload(editor, patch(run_multiqc=False)))
    assert workspace.get('configs', 'hybas') == newer


@pytest.mark.parametrize('extra', [dict(text='broken: ['), dict(revision='stale'), dict(view='review'),
                                 dict(changes=patch(run_busco='false')), dict(text='run_busco: nope\n')])
def test_invalid_edits_do_not_replace_defaults(workspace, extra):
    editor = panel(workspace)
    with pytest.raises((ValueError, yaml.YAMLError)):
        dispatch(workspace, 'save_default_config', payload(editor, **extra))
    assert workspace.get('configs', 'hybas') is None
