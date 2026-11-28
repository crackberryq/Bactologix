"""Restoring list visibility never unlocks, deletes or reruns saved work."""
from pathlib import Path

import pytest

from bactologix import queue, storage_cleanup
from bactologix.runs import command, create_run
from bactologix.ui_backend import dispatch, snapshot
from test_native import workspace, installation, sample


def files(root):
    """Include links and file identities so rewrites are detectable too."""
    return {
        str(path.relative_to(root)): (
            ('link', str(path.readlink())) if path.is_symlink()
            else ('file', path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        )
        for path in root.rglob('*') if path.is_file() or path.is_symlink()
    }


def retained_records(store):
    with store.connect() as db:
        return [tuple(row) for row in db.execute(
            "SELECT kind,id,body FROM items WHERE kind!='run_visibility' ORDER BY kind,id"
        )]


def test_restore_locked_run_preserves_outputs_provenance_locks_and_history(
        workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, '{}')
    folder = Path(run['folder'])
    # Only construct the command to locate its working directory; never run it.
    monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/inert-fixture/conda')
    argv = command(workspace, run, 'dry-run')
    native = Path(argv[argv.index('--directory') + 1])
    for path, content in {
        native / '.snakemake/locks/0.input.lock': b'fixture input lock\n',
        native / '.snakemake/locks/0.output.lock': b'fixture output lock\n',
        Path(run['output']) / 'intermediates/stage-one.txt': b'retained tool output\n',
        folder / 'methods-evidence.json': b'{"source": "completion-time fixture"}\n',
        folder / 'report-editions/one/report.html': b'<p>Retained report edition</p>\n',
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    job = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(job, status='failed', error='LockException: fixture workflow lock')
    log = workspace.log_path(job)
    log.write_bytes(b'LockException: fixture workflow lock\n')
    original_files = files(folder)
    original_inputs = files(Path(sample['path']))
    original_jobs = workspace.jobs()
    original_queue = workspace.queue_snapshot()

    def unexpected_launch(*args, **kwargs):
        pytest.fail('Changing run visibility must not enqueue or launch work')

    monkeypatch.setattr('bactologix.ui_backend.enqueue', unexpected_launch)
    monkeypatch.setattr('bactologix.engine.launch_worker', unexpected_launch)
    # Keep cleanup discovery inside the disposable test workspace.
    monkeypatch.setattr(storage_cleanup, '_other_workspaces', lambda _: ([], [], []))
    archived = dispatch(workspace, 'archive_runs', {'ids': [run['id']]})
    assert archived['archived_runs'] == [run['id']]
    assert workspace.get('run_visibility', run['id'])['archived']
    plan = storage_cleanup.preview(workspace, [{'kind': 'run', 'id': run['id']}])
    assert any('Native workflow locks remain' in reason for reason in plan['blocked'])
    assert plan['logical_bytes'] == 0 and plan['paths'] == []
    before_restore = retained_records(workspace)

    result = dispatch(workspace, 'restore_runs', {'ids': [run['id']]})

    assert result['restored_runs'] == [run['id']]
    assert not workspace.get('run_visibility', run['id'])['archived']
    assert workspace.get('run_visibility', run['id'])['reason'] == 'restored'
    assert workspace.get('runs', run['id']) == run
    assert retained_records(workspace) == before_restore
    assert files(folder) == original_files
    assert files(Path(sample['path'])) == original_inputs
    assert workspace.jobs() == original_jobs
    assert workspace.queue_snapshot() == original_queue
    assert log.read_bytes() == b'LockException: fixture workflow lock\n'
    assert not next(row for row in snapshot(workspace)['runs'] if row['id'] == run['id'])['archived']


@pytest.mark.parametrize('status', ['queued', 'running'])
@pytest.mark.parametrize('paused', [False, True])
def test_active_job_blocks_archive_but_not_visibility_restore(
        workspace, installation, sample, status, paused):
    run = create_run(workspace, 'hybas', sample, '{}')
    job = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(job, status=status)
    if paused:
        queue.change(workspace, 'pause', workspace.queue_snapshot()['revision'])
    original_jobs = workspace.jobs()
    original_queue = workspace.queue_snapshot()
    original_files = files(Path(run['folder']))

    with pytest.raises(ValueError, match='Queued or running jobs stay visible'):
        dispatch(workspace, 'archive_runs', {'ids': [run['id']]})
    assert workspace.get('run_visibility', run['id']) is None

    # Represent a recovered/imported archived record with an active job. Normal
    # queue insertion already prevents starting a job on an archived run.
    workspace.put('run_visibility', run['id'], {'id': run['id'], 'archived': True})
    before_restore = retained_records(workspace)
    result = dispatch(workspace, 'restore_runs', {'ids': [run['id']]})

    assert result['restored_runs'] == [run['id']]
    assert not workspace.get('run_visibility', run['id'])['archived']
    assert retained_records(workspace) == before_restore
    assert workspace.jobs() == original_jobs
    assert workspace.queue_snapshot() == original_queue
    assert workspace.get('runs', run['id']) == run
    assert files(Path(run['folder'])) == original_files
