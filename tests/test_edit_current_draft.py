"""Editing a pre-execution draft keeps one run and distinguishes its validation attempts."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from bactologix import readiness, storage_cleanup
from bactologix.runs import check_run_files, create_run, dry_run_current, run_status, validate_launch
from bactologix.ui_backend import dispatch
from test_compute_memory import host
from test_native import installation, sample, workspace
from test_run_config import patch
from test_run_revisions import request


def failed_attempt(store, run, action):
    jid = store.submit('workflow', {'run_id': run['id'], 'action': action})
    assert store.claim()['id'] == jid
    store.log_path(jid).write_text('Native command failed in this attempt.\n')
    store.update_job(jid, status='failed', error='Native command failed')
    return jid


def test_failed_dry_run_edits_same_id_and_keeps_the_previous_attempt(workspace, installation, sample, monkeypatch):
    monkeypatch.setattr('bactologix.hardware.inventory', lambda *args, **kwargs: host())
    run = create_run(workspace, 'hybas', sample, 'kraken_decision_mode: aware\nrun_kraken: true\n', 12, 28672)
    jid = failed_attempt(workspace, run, 'dry-run')
    previous_log = workspace.log_path(jid).read_bytes()
    folder = Path(run['folder'])
    before_sheet = (folder / 'samples.tsv').read_bytes()
    defaults = workspace.get('configs', 'hybas')
    assert run_status(run, workspace.jobs(), False) == 'failed'
    editor = dispatch(workspace, 'edit_run_settings', {'id': run['id']})
    assert editor['edit_run']['can_edit_draft'] is True
    payload = request(workspace, run, patch(kraken_decision_mode='off', run_kraken=False))
    payload.update(cores=8, memory_mb=24576, swap_mb=8192)

    result = dispatch(workspace, 'update_run_draft', payload)

    saved, = workspace.all('runs')
    assert result['open_run'] == result['detail']['id'] == saved['id'] == run['id']
    for key in ('folder', 'output', 'sample', 'parent', 'created', 'inputs', 'input_layout',
                'installation', 'commit', 'workflow_sha256', 'rule_cache'):
        assert saved[key] == run[key]
    assert workspace.get('configs', 'hybas') == defaults
    assert saved['cores'] == 8 and saved['memory_mb'] == 24576
    cfg = yaml.safe_load(saved['config'])
    assert cfg['threads'] == 8 and cfg['run_kraken'] is False
    assert cfg['bactologix_compute']['swap_mb'] == result['detail']['swap_mb'] == 8192
    assert cfg['base_dir'] == str(folder / 'outputs') and cfg['sample_sheet'] == str(folder / 'samples.tsv')
    assert (folder / 'samples.tsv').read_bytes() == before_sheet
    assert (folder / 'config.yaml').read_text() == saved['config']
    assert (folder / 'submitted.yaml').read_text() == saved['submitted']
    assert saved['config_sha256'] == hashlib.sha256((folder / 'config.yaml').read_bytes()).hexdigest()
    assert json.loads((folder / 'bactologix-run.json').read_text())['config_sha256'] == saved['config_sha256']
    check_run_files(saved)
    assert workspace.job(jid)['status'] == 'failed' and workspace.log_path(jid).read_bytes() == previous_log
    prior, = saved['draft_edit_history']
    assert prior['job_ids'] == [jid] and prior['config_sha256'] == run['config_sha256']
    assert prior['config'] == run['config'] and prior['memory_mb'] == run['memory_mb']
    assert run_status(saved, workspace.jobs(), dry_run_current(workspace, saved)) == 'draft'
    assert storage_cleanup.preview_failed(workspace)['targets'] == []
    assert not result['detail']['readiness']['validated']
    assert validate_launch(workspace, saved['id'], 'dry-run')['config_sha256'] == saved['config_sha256']
    with pytest.raises(ValueError, match='dry-run'):
        validate_launch(workspace, saved['id'], 'run')
    # A failure on the edited settings still belongs to the current draft.
    later = failed_attempt(workspace, saved, 'dry-run')
    assert later != jid and run_status(saved, workspace.jobs(), False) == 'failed'


def test_saved_edit_invalidates_successful_validation_and_environment_inventory(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, '{}')
    run.update(dry_run_ok=True, validated='before-edit', validated_controller=run['controller'])
    workspace.put('runs', run['id'], run)
    workspace.put('run_environment_checks', run['id'], {
        'status': 'checked', 'checked_at': 'before-edit', 'rows': [], 'identity': readiness.identity(workspace, run)})
    payload = request(workspace, run)
    payload['memory_mb'] += 512
    result = dispatch(workspace, 'update_run_draft', payload)
    saved = workspace.get('runs', run['id'])
    assert not dry_run_current(workspace, saved)
    assert result['detail']['readiness']['environment_check_current'] is False


def test_stale_editor_cannot_overwrite_a_newer_draft(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, '{}')
    payload = request(workspace, run)
    updated = dict(payload, cores=8)
    dispatch(workspace, 'update_run_draft', updated)
    saved = workspace.get('runs', run['id'])
    folder = Path(saved['folder'])
    records = {name: (folder / name).read_bytes()
               for name in ('config.yaml', 'submitted.yaml', 'bactologix-run.json')}
    with pytest.raises(ValueError):
        dispatch(workspace, 'update_run_draft', dict(payload, cores=2))
    assert workspace.get('runs', run['id']) == saved and len(workspace.all('runs')) == 1
    assert all((folder / name).read_bytes() == content for name, content in records.items())


@pytest.mark.parametrize('action', ['run', 'continue'])
def test_execution_started_after_opening_the_editor_prevents_in_place_save(workspace, installation, sample, action):
    run = create_run(workspace, 'hybas', sample, '{}')
    payload = request(workspace, run)
    jid = failed_attempt(workspace, run, action)
    before = (Path(run['folder']) / 'config.yaml').read_bytes()
    assert dispatch(workspace, 'edit_run_settings', {'id': run['id']})['edit_run']['can_edit_draft'] is False
    with pytest.raises(ValueError):
        dispatch(workspace, 'update_run_draft', dict(payload, cores=2))
    assert workspace.get('runs', run['id']) == run
    assert (Path(run['folder']) / 'config.yaml').read_bytes() == before
    revision = dispatch(workspace, 'revise_run', request(workspace, run))
    assert workspace.get('runs', revision['open_run'])['parent'] == run['id']
    assert workspace.job(jid)['payload']['run_id'] == run['id']


def test_queued_validation_after_opening_editor_prevents_in_place_save(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, '{}')
    payload = request(workspace, run)
    job = workspace.submit('workflow', {'run_id': run['id'], 'action': 'dry-run'})
    with pytest.raises(ValueError):
        dispatch(workspace, 'update_run_draft', dict(payload, cores=3))
    assert workspace.get('runs', run['id']) == run and workspace.job(job)['status'] == 'queued'


def test_database_write_failure_preserves_the_previous_usable_draft(workspace, installation, sample):
    import sqlite3
    run = create_run(workspace, 'hybas', sample, '{}')
    folder = Path(run['folder'])
    records = {name: (folder / name).read_bytes()
               for name in ('config.yaml', 'submitted.yaml', 'bactologix-run.json')}
    payload = request(workspace, run)
    payload.update(cores=8, memory_mb=8192)
    with workspace.connect() as db:
        # Both INSERT ... ON CONFLICT and UPDATE must encounter the simulated write failure.
        for event in ('insert', 'update'):
            db.execute(f"""CREATE TRIGGER reject_run_{event} BEFORE {event} ON items
                           WHEN NEW.kind='runs' BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    with pytest.raises((sqlite3.DatabaseError, OSError, ValueError, RuntimeError)):
        dispatch(workspace, 'update_run_draft', payload)
    assert workspace.all('runs') == [run]
    assert all((folder / name).read_bytes() == content for name, content in records.items())
    check_run_files(run)
    with workspace.connect() as db:
        db.execute('DROP TRIGGER reject_run_insert')
        db.execute('DROP TRIGGER reject_run_update')
    assert dispatch(workspace, 'update_run_draft', payload)['open_run'] == run['id']


def test_edit_preserves_the_frozen_input_manifest(workspace, installation, tmp_path):
    from test_manifest_inputs import approve, registered
    approve(workspace, installation)
    sample, source_manifest, _ = registered(workspace, tmp_path)
    run = create_run(workspace, 'hybas', sample, 'polish_mode: auto\nthreads: auto')
    frozen = Path(run['frozen_manifest']['path'])
    contents = frozen.read_bytes()
    source_manifest.unlink()
    result = dispatch(workspace, 'update_run_draft', dict(request(workspace, run), memory_mb=18000))
    saved = workspace.get('runs', result['open_run'])
    assert saved['id'] == run['id']
    assert saved['frozen_manifest'] == run['frozen_manifest'] and frozen.read_bytes() == contents
    assert yaml.safe_load(saved['config'])['input_manifest'] == str(frozen)
    check_run_files(saved)


def test_reopen_recovers_an_edit_interrupted_between_file_writes(workspace, installation, sample, monkeypatch):
    import multiprocessing
    import os
    from bactologix import draft_edits
    from bactologix.store import Store
    run = create_run(workspace, 'hybas', sample, '{}')
    folder = Path(run['folder'])
    records = {name: (folder / name).read_bytes()
               for name in ('config.yaml', 'submitted.yaml', 'bactologix-run.json')}
    payload = dict(request(workspace, run), cores=8)
    write = draft_edits._write

    def interrupted_save():
        def exit_after_config(path, text):
            write(path, text)
            if path == folder / 'config.yaml':
                os._exit(19)
        monkeypatch.setattr(draft_edits, '_write', exit_after_config)
        dispatch(workspace, 'update_run_draft', payload)

    process = multiprocessing.get_context('fork').Process(target=interrupted_save)
    process.start()
    try:
        process.join(timeout=10)
        assert process.exitcode == 19
    finally:
        if process.is_alive():
            process.kill()
            process.join()
    assert (folder / 'config.yaml').read_bytes() != records['config.yaml']
    reopened = Store(workspace.home)
    assert reopened.get('runs', run['id']) == run
    assert all((folder / name).read_bytes() == content for name, content in records.items())
    check_run_files(run)
    assert dispatch(reopened, 'update_run_draft', payload)['open_run'] == run['id']


@pytest.mark.parametrize('legacy', [False, True], ids=['new-revision', 'pre-upgrade-revision'])
def test_independent_revision_keeps_its_frozen_settings_when_parent_draft_is_edited(
        workspace, installation, sample, legacy):
    parent = create_run(workspace, 'hybas', sample, 'polish_mode: none\n', cores=6)
    parent_job = failed_attempt(workspace, parent, 'dry-run')
    revision = dispatch(workspace, 'revise_run', dict(request(workspace, parent), cores=8))
    child = workspace.get('runs', revision['open_run'])
    child_folder = Path(child['folder'])
    assert child['parent_draft_generation'] == 0
    if legacy:
        child.pop('parent_draft_generation')
        workspace.put('runs', child['id'], child)
        (child_folder / 'bactologix-run.json').write_text(json.dumps(child, indent=2) + '\n')
    frozen = {path: path.read_bytes() for path in child_folder.rglob('*') if path.is_file()}

    editor = dispatch(workspace, 'edit_run_settings', {'id': parent['id']})
    assert editor['edit_run']['can_edit_draft'] is True
    result = dispatch(workspace, 'update_run_draft', dict(request(workspace, parent), cores=10))

    assert result['open_run'] == parent['id'] and len(workspace.all('runs')) == 2
    saved = workspace.get('runs', parent['id'])
    assert saved['cores'] == 10 and saved['draft_edit_history'][0]['job_ids'] == [parent_job]
    assert saved['draft_edit_history'][0]['config_sha256'] == parent['config_sha256']
    assert workspace.get('runs', child['id']) == child
    assert child['parent'] == parent['id'] and child['cores'] == 8
    assert all(path.read_bytes() == data for path, data in frozen.items())
    check_run_files(child)
    next_revision = dispatch(workspace, 'revise_run', request(workspace, saved))
    assert workspace.get('runs', next_revision['open_run'])['parent_draft_generation'] == saved['draft_generation']
