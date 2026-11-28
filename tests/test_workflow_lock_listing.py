from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import runs, workflow_unlock
from bactologix.ui_backend import snapshot
from test_native import installation, sample, workspace


def test_jobs_lists_live_lock_even_after_clearing_interrupted_history(workspace, installation, sample):
    run = runs.create_run(workspace, 'hybas', sample, '{}')
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(jid, status='interrupted')
    workspace.clear_job_history()
    locks = Path(run['folder']) / 'workflow/.snakemake/locks'
    locks.mkdir(parents=True)
    lock = locks / '0.input.lock'
    lock.write_text('preserved')
    assert snapshot(workspace)['workflow_locks'] == [{'run_id': run['id']}]
    assert lock.read_text() == 'preserved'
    lock.unlink()
    assert snapshot(workspace)['workflow_locks'] == []


def test_shared_followup_lock_list_is_readonly_and_uses_one_probe(tmp_path, monkeypatch):
    store = SimpleNamespace(home=tmp_path)
    run = {'id': 'screen', 'folder': str(tmp_path / 'runs/screen'), 'tool': 'basis'}
    children = [dict(run, id=rid, folder=str(tmp_path / 'runs' / rid), parent='screen',
                     run_type='followup') for rid in ('first', 'second', 'unused')]
    records = [*children, run]
    state = Path(run['folder']) / 'followup-state'
    def listed(jobs=(), archived=()):
        return workflow_unlock.review_list(store, records, jobs, archived)
    assert listed() == []
    assert not state.exists()
    lock_dir = state / '.snakemake/locks'
    lock_dir.mkdir(parents=True)
    (lock_dir / '0.output.lock').touch()
    probes = []
    original = workflow_unlock._lock_status
    def probe(directory):
        probes.append(directory)
        return original(directory)
    monkeypatch.setattr(workflow_unlock, '_lock_status', probe)
    monkeypatch.setattr(workflow_unlock, 'validate_launch', lambda *_: pytest.fail('unexpected preflight'))
    monkeypatch.setattr(workflow_unlock, 'check_processes', lambda *_: pytest.fail('unexpected process scan'))
    monkeypatch.setattr(Path, 'read_bytes', lambda *_: pytest.fail('unexpected file read'))
    job = {'kind': 'workflow', 'status': 'failed', 'payload': {'run_id': 'second'}}
    assert listed([job]) == [{'run_id': 'second'}]
    assert probes == [state, Path(run['folder']) / 'workflow']
    assert listed([job], archived={'second'}) == [{'run_id': 'first'}]
    for kind in ('workflow', 'workflow_unlock'):
        for status in ('queued', 'running'):
            assert listed([dict(job, status=status, kind=kind)]) == []


def test_redirected_lock_directory_cannot_advertise_an_unlock_action(tmp_path):
    store = SimpleNamespace(home=tmp_path)
    folder = tmp_path / 'runs/test-run'
    folder.mkdir(parents=True)
    external = tmp_path / 'external'
    locks = external / '.snakemake/locks'
    locks.mkdir(parents=True)
    (locks / '0.output.lock').touch()
    (folder / 'workflow').symlink_to(external, target_is_directory=True)
    rows = workflow_unlock.review_list(store, [{'id': 'test-run', 'folder': str(folder)}], [], set())
    assert len(rows) == 1 and rows[0]['run_id'] == 'test-run'
    assert 'Could not inspect workflow locks' in rows[0]['issue']
