"""Review settings cannot silently activate an unsupported frozen native source."""
import hashlib
import json

import pytest

from bactologix import medaka_review, run_config
from bactologix.runs import create_run, template
from test_native import workspace, installation, sample


def test_old_editor_does_not_advertise_review_controls():
    fields = {row['name']: row for row in run_config.editor('hybas', template('hybas'))['fields']}
    assert 'medaka_model' in fields
    assert 'scientific suitability' in fields['medaka_model']['help']
    assert not medaka_review.FIELDS.intersection(fields)


@pytest.mark.parametrize('values', [
    {'medaka_model_review_acknowledged': 'true'},
    {'medaka_model_review_reason': ['approval']},
    {'medaka_model_review_reason': 'x' * 4097},
])
def test_invalid_review_types_fail(values):
    import yaml
    with pytest.raises(ValueError, match='medaka_model_review'):
        run_config.validate('hybas', yaml.safe_dump(values))


@pytest.mark.parametrize('values', [{'medaka_model_review_acknowledged': True},
                                  {'medaka_model_review_acknowledged': False},
                                  {'medaka_model_review_reason': ''}])
def test_old_source_rejects_supplied_review_fields_before_creating_draft(workspace, installation, sample, values):
    import yaml
    with pytest.raises(ValueError, match='older frozen workflow'):
        create_run(workspace, 'hybas', sample, yaml.safe_dump(values))
    assert not workspace.all('runs')


def reviewed_source(tmp_path, monkeypatch):
    doc = {'contract': 'hybas-medaka-model-review', 'version': 1,
           'helper': medaka_review.CONTRACT['helper'],
           'preflight_rule': medaka_review.CONTRACT['preflight_rule'],
           'configuration_fields': sorted(medaka_review.FIELDS | {'medaka_model'}),
           'automatic_fallback': False,
           'inspection': 'all_raw_ont_reads_before_analysis_and_all_filtered_reads_at_medaka_execution',
           'scientific_suitability': 'not_assessed'}
    files = {medaka_review.CONTRACT['capability']: json.dumps(doc),
             medaka_review.CONTRACT['helper']: 'reviewed helper fixture', 'Hybas.smk': 'reviewed workflow fixture'}
    files.update({name: 'reviewed dependency fixture' for name in medaka_review.REQUIRED_FILES if name not in files})
    for name, content in files.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    row = {'medaka_model_review': medaka_review.CONTRACT,
           'files_sha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}}
    monkeypatch.setattr('bactologix.release_contract.approval', lambda *args: row)
    return row


def test_review_contract_checks_all_execution_files(tmp_path, monkeypatch):
    row = reviewed_source(tmp_path, monkeypatch)
    cfg = {'medaka_model_review_acknowledged': True, 'medaka_model_review_reason': 'Explicit experiment'}
    medaka_review.require_adapter(None, 'hybas', 'fixture', cfg, tmp_path)
    for name in tuple(row['files_sha256']):
        path = tmp_path / name; previous = path.read_bytes(); path.write_bytes(previous + b'changed')
        with pytest.raises(ValueError, match='source identity changed'):
            medaka_review.require_adapter(None, 'hybas', 'fixture', cfg, tmp_path)
        path.write_bytes(previous)


def test_marker_alone_cannot_authorize_new_controls(tmp_path, monkeypatch):
    row = reviewed_source(tmp_path, monkeypatch)
    row.pop('medaka_model_review')
    with pytest.raises(ValueError, match='no reviewed model-selection contract'):
        medaka_review.require_adapter(None, 'hybas', 'fixture', {'medaka_model_review_reason': ''}, tmp_path)


def test_old_configuration_remains_usable_without_review_fields():
    medaka_review.require_adapter(None, 'hybas', 'old', {'medaka_model': 'user-specified'}, None)


def test_review_controls_only_in_verified_unsaved_editor(workspace, installation, monkeypatch):
    from pathlib import Path
    import yaml
    text = template('hybas')
    workspace.put('configs', 'hybas', text)
    assert medaka_review.editor_text(workspace, 'hybas', text) == text
    reviewed_source(Path(installation['source']), monkeypatch)
    edited = medaka_review.editor_text(workspace, 'hybas', text)
    cfg = yaml.safe_load(edited)
    assert cfg['medaka_model_review_acknowledged'] is False
    assert cfg['medaka_model_review_reason'] == ''
    assert cfg['medaka_model_reviewed_model'] == ''
    fields = {row['name']: row for row in run_config.editor('hybas', edited)['fields']}
    assert fields['medaka_model_review_acknowledged']['group'] == 'Assembly & polishing'
    assert fields['medaka_model_review_reason']['group'] == 'Assembly & polishing'
    assert fields['medaka_model_reviewed_model']['managed']
    assert workspace.get('configs', 'hybas') == text


def test_revision_editor_checks_frozen_source_not_active_source(workspace, installation, tmp_path, monkeypatch):
    from pathlib import Path
    reviewed_source(Path(installation['source']), monkeypatch)
    run = {'commit': installation['version']['commit'], 'folder': str(tmp_path / 'old-run')}
    text = template('hybas')
    assert medaka_review.editor_text(workspace, 'hybas', text, run=run) == text


def test_model_change_invalidates_inherited_ack_and_new_ack_binds_exact_choice():
    import yaml
    from test_run_config import patch
    cfg = {'medaka_model': 'old-model', 'medaka_model_review_acknowledged': True,
           'medaka_model_review_reason': 'old evidence', 'medaka_model_reviewed_model': 'old-model'}
    text = yaml.safe_dump(cfg)
    changed = run_config.compile_config('hybas', text, patch(medaka_model='new-model'))
    result = yaml.safe_load(changed)
    assert result['medaka_model_review_acknowledged'] is False
    assert result['medaka_model_review_reason'] == result['medaka_model_reviewed_model'] == ''
    reviewed = yaml.safe_load(run_config.compile_config('hybas', changed,
        patch(medaka_model_review_acknowledged=True, medaka_model_review_reason='reviewed new model')))
    assert reviewed['medaka_model_reviewed_model'] == 'new-model'
    assert reviewed['medaka_model_review_acknowledged'] is True
    assert yaml.safe_load(text) == cfg


def test_source_symlink_escape_rejected_even_with_matching_hash(tmp_path, monkeypatch):
    row = reviewed_source(tmp_path / 'source', monkeypatch)
    path = tmp_path / 'source' / medaka_review.CONTRACT['helper']
    outside = tmp_path / 'elsewhere.py'; outside.write_bytes(path.read_bytes())
    path.unlink(); path.symlink_to(outside)
    with pytest.raises(ValueError, match='source identity changed'):
        medaka_review.require_adapter(None, 'hybas', 'fixture', {'medaka_model_review_acknowledged': False}, tmp_path / 'source')
