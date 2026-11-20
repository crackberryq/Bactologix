"""Deterministic I/O placement checks, not a workstation speed benchmark."""
from pathlib import Path

import pytest

from bactologix import databases, run_revisions
from bactologix.runs import create_run
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample


def test_repeated_configuration_reads_return_independent_fresh_values(monkeypatch):
    from bactologix import runs, ui_backend

    text = 'nested:\n  display_test: [one, two]\n'
    fields = ui_backend.configuration_fields('hybas', text)
    match = next(row for row in fields if row['name'] == 'nested.display_test')
    match['path'].clear()
    runs.parse_config(text)['nested']['display_test'].clear()
    with monkeypatch.context() as patch:
        patch.setattr(runs.yaml, 'safe_load', lambda *a: pytest.fail('Already parsed identical YAML'))
        patch.setattr(ui_backend.yaml, 'safe_dump', lambda *a, **kw: pytest.fail('Already formatted identical fields'))
        assert runs.parse_config(text)['nested']['display_test'] == ['one', 'two']
        again = ui_backend.configuration_fields('hybas', text)
        assert next(row for row in again if row['name'] == 'nested.display_test')['path'] == ['nested', 'display_test']
    edited = text.replace('two', 'three')
    assert runs.parse_config(edited)['nested']['display_test'] == ['one', 'three']
    assert 'three' in next(row['yaml'] for row in ui_backend.configuration_fields('hybas', edited)
                           if row['name'] == 'nested.display_test')


def test_editor_displays_saved_config_without_reading_raw_data(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, 'threads: 1\n')
    with monkeypatch.context() as patch:
        patch.setattr(run_revisions, 'check_run_files', lambda *a, **k: pytest.fail('Editor must not hash input data'))
        patch.setattr(run_revisions, 'tree_digest', lambda *a: pytest.fail('Editor must not hash the entire workflow'))
        panel = run_revisions.editor(workspace, run['id'])
    assert panel['run_config']['text'] == run['config']
    # Opening was observational only. A changed input still blocks the actual save.
    Path(run['inputs'][0]['path']).write_text('changed input\n')
    with pytest.raises(ValueError, match='Input changed'):
        dispatch(workspace, 'revise_run', {'id': run['id'],
                 'signature': panel['edit_run']['signature'],
                 'config': {'tool': 'hybas', 'text': panel['run_config']['text'], 'changes': []}})
    assert len(workspace.all('runs')) == 1


def test_editor_rejects_busy_or_archived_saved_records(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, '{}')
    workspace.put('run_visibility', run['id'], {'archived': True})
    with pytest.raises(ValueError, match='Archived'):
        run_revisions.editor(workspace, run['id'])
    workspace.put('run_visibility', run['id'], {'archived': False})
    workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    with pytest.raises(ValueError, match='queued or running'):
        run_revisions.editor(workspace, run['id'])


def test_scan_budget_includes_configured_inspection_and_discovery(workspace, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(databases.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(databases, 'MAX_SECONDS', 1)
    monkeypatch.setattr(databases, 'panel', lambda *a: {'rows': [], 'revision': 'fixture', 'lineage': ''})
    def roots(*args):
        assert args[-1] == 1.0
        clock[0] = 2.0
        return [tmp_path]
    monkeypatch.setattr(databases, '_roots', roots)
    monkeypatch.setattr(databases, '_children', lambda *a: pytest.fail('Discovery already exhausted the budget'))
    result = databases.scan(workspace, 'hybas')
    assert result['limited'] and result['visited'] == 0
    assert 'folder/time limit' in result['limits']
    assert 'in-progress operation may take longer' in result['scope']


def test_expired_scan_stops_before_inspecting_configured_resources(workspace, monkeypatch):
    monkeypatch.setattr(databases, 'MAX_SECONDS', 0)
    monkeypatch.setattr(databases, 'inspect', lambda *a: pytest.fail('No budget left for configured resources'))
    monkeypatch.setattr(databases, '_roots', lambda *a: pytest.fail('No budget left for discovery'))
    assert databases.scan(workspace, 'hybas')['limited']
