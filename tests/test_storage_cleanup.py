"""Deletion tests use only temporary owned fixtures; no user data is touched."""
import fcntl
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import storage_cleanup as cleanup
from bactologix.store import Store


def file(path, content=b'example result'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def saved_run(store, key='run-one', **changes):
    folder = store.home / 'runs' / key
    file(folder / 'outputs' / 'report.html')
    run = {'id': key, 'label': key, 'folder': str(folder), 'output': str(folder / 'outputs'),
           'sample': {'id': 'sample-one', 'path': str(store.home.parent / 'samples/sample-one')},
           'tool': 'hybas', 'config': '{}', 'inputs': [], 'completed': True, **changes}
    store.put('runs', key, run)
    return run


def sample(store, key='sample-one', **changes):
    root = store.home.parent / 'samples' / key
    raw = file(root / 'raw/nanopore/reads.fastq.gz', b'original reads')
    row = {'id': key, 'path': str(root), 'mode': 'ont_only', **changes}
    store.put('samples', key, row)
    return row, raw


def plan(store, *targets):
    return cleanup.preview(store, [{'kind': k, 'id': i} for k, i in targets])


@pytest.fixture(autouse=True)
def no_worker_launch(monkeypatch):
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda _: None)
    # Container supervisor shares UID but denies /proc inspection. Process guards
    # have separate controlled tests; filesystem tests use an idle process list.
    monkeypatch.setattr('psutil.process_iter', lambda: [])


def run_cleanup(store, preview, check_cancel=lambda: None):
    jid = cleanup.request(store, preview['id'], preview['signature'], 'DELETE', True)
    store.update_job(jid, status='running')
    ctx = SimpleNamespace(store=store, job=store.job(jid), check_cancel=check_cancel, write=lambda _: None, lock_fd=None)
    try:
        result = cleanup.execute(ctx)
    except BaseException:
        store.update_job(jid, status='failed')
        raise
    store.update_job(jid, status='succeeded')
    return result


def test_run_deletes_owned_files_and_editions_preserves_originals_and_receipt(tmp_path):
    store = Store(tmp_path / 'workspace')
    source, original = sample(store)
    row = saved_run(store, inputs=[{'path': str(original)}], sample=source)
    (Path(row['output']) / 'reads.fastq.gz').symlink_to(original)
    edition = file(store.home / 'report-refreshes/job-edition/edition/report.html')
    store.put('report_refreshes', 'job-edition', {'id': 'job-edition', 'run_id': row['id'], 'path': str(edition)})
    store.put('reports', 'job-edition', {'id': 'job-edition', 'run': row['id'], 'path': str(edition)})
    preview = plan(store, ('run', row['id']))
    assert not preview['blocked']
    assert sum(p['links'] for p in preview['paths']) == 1
    result = run_cleanup(store, preview)
    assert result['status'] == 'completed'
    assert not Path(row['folder']).exists() and not edition.exists()
    assert original.read_bytes() == b'original reads'
    assert store.get('samples', source['id']) == source
    assert store.get('runs', row['id']) is None
    assert store.get('reports', 'job-edition') is None
    assert store.get('deleted_runs', row['id'])['run'] == row
    receipt = cleanup.receipt(store, result['id'])
    assert receipt['deleted_paths'] == sorted([row['folder'], str(edition.parent.parent)])
    assert not receipt['retained_paths']


def test_registration_only_and_explicit_sample_files_are_different(tmp_path):
    store = Store(tmp_path / 'workspace')
    row, raw = sample(store)
    unrelated = file(Path(row['path']) / 'notes.txt', b'keep')
    preview = plan(store, ('sample_registration', row['id']))
    assert preview['logical_bytes'] == 0
    run_cleanup(store, preview)
    assert raw.exists() and unrelated.exists() and store.get('samples', row['id']) is None
    store.put('samples', row['id'], row)
    run_cleanup(store, plan(store, ('sample_files', row['id'])))
    assert not raw.exists() and unrelated.exists() and Path(row['path']).is_dir()


def test_originals_blocked_by_runs_and_other_registrations(tmp_path):
    store = Store(tmp_path / 'workspace')
    row, raw = sample(store)
    run = saved_run(store, inputs=[{'path': str(raw)}], sample=row)
    assert any('runs run-one' in reason for reason in plan(store, ('sample_files', row['id']))['blocked'])
    shared = {**row, 'id': 'other-sample'}
    store.put('samples', shared['id'], shared)
    preview = plan(store, ('run', run['id']), ('sample_files', row['id']))
    assert any('other-sample' in reason for reason in preview['blocked'])
    preview = plan(store, ('run', run['id']), ('sample_files', row['id']), ('sample_registration', shared['id']))
    assert not preview['blocked']
    run_cleanup(store, preview)
    assert not raw.exists()


def test_changed_preview_and_exact_confirmation_fail_without_deleting(tmp_path):
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    preview = plan(store, ('run', row['id']))
    for confirmation, acknowledged in [('delete', True), ('DELETE', False), ('DELETE', 1)]:
        with pytest.raises(ValueError, match='type DELETE'):
            cleanup.request(store, preview['id'], preview['signature'], confirmation, acknowledged)
    file(Path(row['output']) / 'new.txt')
    with pytest.raises(ValueError, match='changed since preview'):
        run_cleanup(store, preview)
    assert Path(row['folder']).exists() and not store.jobs()


@pytest.mark.parametrize('unsafe', ['symlink_root', 'symlink_parent', 'mount', 'native_lock', 'fifo'])
def test_unsafe_trees_are_blocked(tmp_path, monkeypatch, unsafe):
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    folder = Path(row['folder'])
    if unsafe == 'symlink_root':
        moved = tmp_path / 'moved'
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
    elif unsafe == 'symlink_parent':
        moved = tmp_path / 'moved'
        folder.parent.rename(moved)
        folder.parent.symlink_to(moved, target_is_directory=True)
    elif unsafe == 'mount':
        monkeypatch.setattr(cleanup, '_mounts', lambda: {folder / 'outputs'})
    elif unsafe == 'native_lock':
        file(folder / 'workflow/.snakemake/locks/1.input.lock')
    else:
        os.mkfifo(folder / 'pipe')
    preview = plan(store, ('run', row['id']))
    assert preview['blocked']
    with pytest.raises(ValueError):
        run_cleanup(store, preview)
    assert (folder / 'outputs/report.html').exists()


def test_failed_deletion_has_truthful_receipt_and_recovery(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    real_erase = cleanup._erase
    monkeypatch.setattr(cleanup, '_erase', lambda *args: (_ for _ in ()).throw(OSError('disk IO error')))
    with pytest.raises(OSError, match='disk IO error'):
        run_cleanup(store, plan(store, ('run', row['id'])))
    receipt = store.all('storage_cleanup_receipts')[0]
    assert receipt['status'] == 'partial' and receipt['records_retired']
    assert store.get('runs', row['id']) is None
    assert Path(receipt['operations'][0]['quarantine']).exists()
    with pytest.raises(ValueError, match='recovery'):
        store.submit('check', {})
    monkeypatch.setattr(cleanup, '_erase', real_erase)
    preview = plan(store, ('cleanup_remaining', receipt['id']))
    assert not preview['blocked']
    result = run_cleanup(store, preview)
    assert result['status'] == 'completed'
    assert not Path(receipt['operations'][0]['quarantine']).exists()


def test_journal_recovers_interruption_between_rename_and_receipt_update(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    real_stage = cleanup._stage
    def interrupted(operation):
        real_stage(operation)
        operation['state'] = 'planned'
        raise OSError('stopped after rename')
    monkeypatch.setattr(cleanup, '_stage', interrupted)
    with pytest.raises(OSError):
        run_cleanup(store, plan(store, ('run', row['id'])))
    receipt = store.all('storage_cleanup_receipts')[0]
    assert not receipt.get('records_retired')
    monkeypatch.setattr(cleanup, '_stage', real_stage)
    result = run_cleanup(store, plan(store, ('cleanup_remaining', receipt['id'])))
    assert result['status'] == 'completed' and store.get('runs', row['id']) is None


def test_other_workspace_worker_and_database_writer_prevent_deletion(tmp_path):
    store, other = Store(tmp_path / 'regular'), Store(tmp_path / 'dev')
    row = saved_run(store)
    preview = plan(store, ('run', row['id']))
    with (other.home / 'worker.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='surviving child'):
            run_cleanup(store, preview)
    assert Path(row['folder']).exists()
    with other.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='related workspace is changing'):
            run_cleanup(store, preview)
    assert Path(row['folder']).exists()


def test_new_files_after_staging_are_retained_for_review(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    real_erase = cleanup._erase
    def introduce_new_file(operation, check_cancel):
        file(Path(operation['quarantine']) / 'new-unreviewed.txt')
        return real_erase(operation, check_cancel)
    monkeypatch.setattr(cleanup, '_erase', introduce_new_file)
    with pytest.raises(ValueError, match='unreviewed file'):
        run_cleanup(store, plan(store, ('run', row['id'])))
    receipt = store.all('storage_cleanup_receipts')[0]
    assert (Path(receipt['operations'][0]['quarantine']) / 'new-unreviewed.txt').exists()
    assert receipt['status'] == 'partial'


@pytest.mark.parametrize('field', ['cwd', 'exe', 'open_files'])
def test_active_process_paths_block_cleanup(tmp_path, monkeypatch, field):
    root = tmp_path / 'selected'
    fields = {'pid': 999999, 'uids': lambda: SimpleNamespace(real=os.getuid(), effective=os.getuid(), saved=os.getuid()),
              'cwd': lambda: '/elsewhere', 'exe': lambda: '/elsewhere/python', 'open_files': lambda: []}
    fields[field] = (lambda: [SimpleNamespace(path=str(root / 'file'))]) if field == 'open_files' else lambda: str(root / 'file')
    monkeypatch.setattr('psutil.process_iter', lambda: [SimpleNamespace(**fields)])
    with pytest.raises(ValueError, match='using a selected cleanup path'):
        cleanup._check_processes([str(root)])


def test_inaccessible_same_user_process_fails_closed(tmp_path, monkeypatch):
    import psutil
    process = SimpleNamespace(pid=999999, uids=lambda: SimpleNamespace(real=os.getuid(), effective=os.getuid(), saved=os.getuid()),
                              cwd=lambda: (_ for _ in ()).throw(psutil.AccessDenied(999999)))
    monkeypatch.setattr('psutil.process_iter', lambda: [process])
    monkeypatch.setattr('bactologix.workflow_unlock.protected_session_helper', lambda *_: False)
    with pytest.raises(ValueError, match='Cannot inspect a user process'):
        cleanup._check_processes([str(tmp_path)])


def test_cancel_before_staging_keeps_files_and_can_recover(tmp_path):
    from bactologix.engine import Cancelled
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    def cancel():
        raise Cancelled('Stopped by user')
    with pytest.raises(Cancelled):
        run_cleanup(store, plan(store, ('run', row['id'])), cancel)
    receipt = store.all('storage_cleanup_receipts')[0]
    assert receipt['status'] == 'partial'
    assert Path(row['folder']).exists() and store.get('runs', row['id']) == row
    run_cleanup(store, plan(store, ('cleanup_remaining', receipt['id'])))
    assert not Path(row['folder']).exists()


def test_preview_entry_limit_is_fail_closed(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    monkeypatch.setattr(cleanup, 'MAX_ENTRIES', 1)
    preview = plan(store, ('run', row['id']))
    assert any('safe scan limit' in reason for reason in preview['blocked'])
    with pytest.raises(ValueError, match='blocked'):
        run_cleanup(store, preview)
    assert Path(row['folder']).exists()


def test_preview_manifests_are_bounded_and_completed_receipts_are_compact(tmp_path):
    store = Store(tmp_path / 'workspace')
    row = saved_run(store)
    for _ in range(5):
        preview = plan(store, ('run', row['id']))
    assert len(store.all('storage_cleanup_plans')) == 3
    result = run_cleanup(store, preview)
    assert store.get('storage_cleanup_plans', preview['id']) is None
    receipt = store.get('storage_cleanup_receipts', result['id'])
    assert 'manifest' not in receipt['operations'][0]['inventory']
    public = cleanup.receipt(store, result['id'])
    assert 'inventory' not in public['operations'][0]


@pytest.mark.parametrize('other_workspace', [False, True])
def test_workspace_locks_cannot_be_deleted_as_sample_inputs(tmp_path, other_workspace):
    store = Store(tmp_path / 'workspace')
    target_store = Store(tmp_path / 'related') if other_workspace else store
    lock = file(target_store.home / 'worker.lock')
    store.put('samples', 'malformed', {'id': 'malformed', 'path': str(tmp_path / 'inputs'), 'inputs': [{'path': str(lock)}]})
    preview = plan(store, ('sample_files', 'malformed'))
    assert any('workspace' in reason.lower() for reason in preview['blocked'])
