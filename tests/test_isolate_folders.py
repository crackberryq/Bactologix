"""Opening analyses uses current isolate relationships without relocating files."""
import copy
from pathlib import Path
import shutil

import pytest

from bactologix.ui_backend import dispatch
from test_isolate_integration import isolate_fixture, original_bytes


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    value = isolate_fixture(tmp_path)
    value['opened'] = []
    monkeypatch.setattr('bactologix.launcher.open_folder',
                        lambda path: value['opened'].append(str(path)) or str(path))
    return value


def request(fixture, **payload):
    return dispatch(fixture['store'], 'open_isolate_folder',
                    {'id': fixture['sample']['id'], 'kind': 'analysis', **payload})


def remove_record(fixture, key):
    with fixture['store'].connect() as db:
        db.execute("DELETE FROM items WHERE kind='runs' AND id=?", (fixture[key]['id'],))


def test_multiple_analysis_folders_offer_only_linked_outputs(fixture):
    before = original_bytes(fixture)
    result = request(fixture, path=str(fixture['original']))
    choices = result['isolate_folder_choices']
    assert result['keep_modal'] is True
    assert choices['sample_id'] == fixture['sample']['id']
    assert {row['path'] for row in choices['folders']} == {
        fixture[key]['output'] for key in ('hybas', 'basis', 'genescope')}
    assert all(row['available'] for row in choices['folders'])
    assert {run['id'] for row in choices['folders'] for run in row['runs']} == {
        fixture[key]['id'] for key in ('hybas', 'basis', 'genescope')}
    basis = next(row for row in choices['folders'] if row['run_id'] == fixture['basis']['id'])
    assert basis['runs'] == [{'id': fixture['basis']['id'], 'tool': 'basis',
                              'label': fixture['basis']['label'], 'archived': True}]
    assert choices['warnings'] == []
    assert not fixture['opened']
    assert original_bytes(fixture) == before


def test_single_analysis_opens_output_without_touching_inputs(fixture):
    remove_record(fixture, 'basis')
    remove_record(fixture, 'genescope')
    before = original_bytes(fixture)
    result = request(fixture, path=str(fixture['original']), run_id=fixture['unrelated']['id'])
    assert fixture['opened'] == [fixture['hybas']['output']]
    assert result['keep_modal'] is True and 'isolate_folder_choices' not in result
    assert original_bytes(fixture) == before


def test_no_linked_analyses_does_not_open_original_inputs_or_create_folders(fixture):
    for key in ('hybas', 'basis', 'genescope'):
        remove_record(fixture, key)
    before = {str(path) for path in fixture['store'].home.rglob('*')}
    choices = request(fixture)['isolate_folder_choices']
    assert not choices['folders'] and any('No saved analyses' in text for text in choices['warnings'])
    assert not fixture['opened']
    assert {str(path) for path in fixture['store'].home.rglob('*')} == before


def test_shared_basis_followups_are_one_folder_and_can_auto_open(fixture):
    store = fixture['store']
    remove_record(fixture, 'genescope')
    for number in (1, 2):
        row = copy.deepcopy(fixture['basis'])
        rid = 'run-followup-' + str(number)
        row.update(id=rid, folder=str(store.home / 'runs' / rid),
                   run_type='followup', parent=fixture['basis']['id'], label='Follow-up ' + str(number))
        Path(row['folder']).mkdir()
        store.put('runs', rid, row)
    choices = request(fixture)['isolate_folder_choices']
    assert len(choices['folders']) == 2
    shared = next(row for row in choices['folders'] if row['path'] == fixture['basis']['output'])
    ids = {fixture['basis']['id'], 'run-followup-1', 'run-followup-2'}
    assert {row['id'] for row in shared['runs']} == ids
    assert shared['available']
    request(fixture, kind='run', run_id='run-followup-2', path=str(fixture['original']))
    assert fixture['opened'] == [fixture['basis']['output']]
    # A standalone screening plus its follow-ups still has exactly one output.
    remove_record(fixture, 'hybas')
    for rid in ids:
        row = store.get('runs', rid)
        row['sample'] = copy.deepcopy(fixture['sample'])
        if rid == fixture['basis']['id']:
            row['parent'] = None
        store.put('runs', rid, row)
    request(fixture)
    assert fixture['opened'] == [fixture['basis']['output']] * 2


def test_one_available_folder_does_not_hide_other_missing_analyses(fixture):
    for key in ('basis', 'genescope'):
        shutil.rmtree(fixture[key]['output'])
    choices = request(fixture)['isolate_folder_choices']
    assert len(choices['folders']) == 3
    assert [row['path'] for row in choices['folders'] if row['available']] == [fixture['hybas']['output']]
    assert choices['warnings'] and not fixture['opened']


def test_missing_output_is_unavailable_without_falling_back_to_run_folder(fixture):
    for key in ('hybas', 'basis', 'genescope'):
        shutil.rmtree(fixture[key]['output'])
    choices = request(fixture)['isolate_folder_choices']
    assert len(choices['folders']) == 3 and not any(row['available'] for row in choices['folders'])
    assert any('No linked analysis output folders' in text for text in choices['warnings'])
    assert not fixture['opened']
    with pytest.raises(ValueError, match='missing'):
        request(fixture, kind='run', run_id=fixture['hybas']['id'])
    assert Path(fixture['hybas']['folder']).is_dir() and not fixture['opened']


@pytest.mark.parametrize('change', ['deleted_record', 'changed_sample', 'missing_output'])
def test_selection_rechecks_current_relationship_and_availability(fixture, change):
    choices = request(fixture)['isolate_folder_choices']
    selected = next(row for row in choices['folders'] if row['run_id'] == fixture['genescope']['id'])
    if change == 'deleted_record':
        remove_record(fixture, 'genescope')
    elif change == 'changed_sample':
        row = copy.deepcopy(fixture['genescope'])
        row['sample'] = copy.deepcopy(fixture['unrelated']['sample'])
        fixture['store'].put('runs', row['id'], row)
    else:
        shutil.rmtree(fixture['genescope']['output'])
    with pytest.raises(ValueError, match='linked|unavailable'):
        request(fixture, kind='run', run_id=selected['run_id'], path=selected['path'])
    assert not fixture['opened']


@pytest.mark.parametrize('location', ['absent', 'relative', 'inputs', 'input_child', 'run_folder',
                                    'workspace_runs', 'shared_source', 'other_run', 'file', 'linked'])
def test_malformed_saved_outputs_cannot_open_input_or_unrelated_folders(fixture, location):
    row = copy.deepcopy(fixture['hybas'])
    targets = {'absent': None, 'relative': 'outputs/isolate-07', 'inputs': str(fixture['original']),
               'input_child': str(fixture['original'] / 'child'), 'run_folder': row['folder'],
               'workspace_runs': str(fixture['store'].home / 'runs'),
               'shared_source': str(fixture['store'].home / 'sources'),
               'other_run': fixture['unrelated']['output']}
    if location in {'file', 'linked'}:
        shutil.rmtree(row['output'])
        path = Path(row['output'])
        if location == 'file':
            path.write_text('not a directory')
        else:
            path.symlink_to(fixture['original'], target_is_directory=True)
    else:
        row['output'] = targets[location]
    fixture['store'].put('runs', row['id'], row)
    with pytest.raises(ValueError, match='unavailable'):
        request(fixture, kind='run', run_id=row['id'], path=str(fixture['original']))
    assert not fixture['opened']


def test_explicit_input_folder_remains_available(fixture):
    request(fixture, kind='inputs', path='/ignored')
    assert fixture['opened'] == [str(fixture['original'])]


def test_disappearance_during_file_manager_request_has_clear_error(fixture, monkeypatch):
    remove_record(fixture, 'basis')
    remove_record(fixture, 'genescope')
    def disappeared(path):
        raise FileNotFoundError(str(path))
    monkeypatch.setattr('bactologix.launcher.open_folder', disappeared)
    with pytest.raises(ValueError, match='no longer available'):
        request(fixture)
