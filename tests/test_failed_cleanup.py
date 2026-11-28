"""Failed-item cleanup through the Jobs action and the real queue worker."""
from pathlib import Path

import pytest

from bactologix import worker
from bactologix.ui_backend import dispatch
from test_factory_reset import database, store
from test_storage_cleanup import file, sample, saved_run
from test_storage_resources import git


def attempt(store, kind, payload, status):
    job = store.submit(kind, payload)
    store.update_job(job, status=status)
    return job


def workflow(store, run, status, action='run'):
    return attempt(store, 'workflow', {'run_id': run['id'], 'action': action}, status)


def failed_database(store, key='database-0123456789ab', parent=None):
    row = database(store, key=key, parent=parent)
    folder = store.home / 'maintenance' / row['id']
    file(folder / 'workflow' / 'Hybas.smk')
    file(folder / 'config.yaml', b'{}\n')
    row.update(status='failed', folder=str(folder), error='Download stopped')
    store.update_job(row['job'], status='failed', error=row['error'])
    store.put('database_downloads', row['id'], row)
    return row


def failed_pipeline(store):
    # A clean checkout with the native source naming convention proves ownership.
    source = store.home / 'sources' / 'fixture'
    file(source / 'Hybas.smk')
    git(source, 'init', '-q')
    git(source, 'add', 'Hybas.smk')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture')
    version = {'commit': git(source, 'rev-parse', 'HEAD'), 'ref': 'test-version'}
    key = 'hybas-' + version['commit']
    source = source.rename(source.parent / key)
    row = {'id': key, 'tool': 'hybas', 'source': str(source), 'version': version,
           'controller': None, 'ready': False, 'rule_cache': str(store.home / 'envs/hybas'),
           'setup_mode': 'first_install', 'setup_phase': 'environments'}
    store.put('installs', key, row)
    job = attempt(store, 'install', {'tool': 'hybas', 'version': version}, 'failed')
    folder = file(store.home / 'maintenance' / job / 'hybas-install-example' / 'Environments.smk').parents[1]
    return row, job, folder


def preview(store):
    return dispatch(store, 'preview_storage_cleanup', {'scope': 'failed'})['cleanup_plan']


def queue_cleanup(store, plan):
    return dispatch(store, 'execute_storage_cleanup', {
        'plan_id': plan['id'], 'signature': plan['signature'],
        'confirmation': plan['confirmation'], 'acknowledged': True})['job_id']


def targets(plan):
    return {(item['kind'], item['id']) for item in plan['targets']}


def test_bulk_cleanup_reclaims_failed_outputs_and_installs_and_keeps_shared_files(store):
    source, reads = sample(store)
    run = saved_run(store, 'run-failed', completed=False, sample=source, inputs=[{'path': str(reads)}])
    (Path(run['folder']) / 'input.fastq.gz').symlink_to(reads)
    workflow(store, run, 'failed')
    failed_db = failed_database(store)
    retained_db = database(store, key='database-aaaaaaaabbbb')
    failed_install, install_job, workdir = failed_pipeline(store)
    cache = file(store.home / 'envs/hybas' / 'shared-env' / 'conda-meta/history')
    retained_install = store.get('installs', 'hybas-install')
    retained_source = Path(retained_install['source']) / 'Hybas.smk'
    completed = saved_run(store, 'run-complete')
    workflow(store, completed, 'succeeded')

    plan = preview(store)
    assert plan['scope'] == 'failed'
    assert not plan['blocked'], plan['blocked']
    assert targets(plan) == {('run', run['id']), ('failed_database', failed_db['id']),
                             ('installation', failed_install['id']), ('failed_setup', install_job)}
    locations = {Path(run['folder']), Path(failed_db['folder']), Path(failed_db['destination']),
                 Path(failed_install['source']), workdir}
    assert locations == {Path(row['path']) for row in plan['paths']}
    assert plan['allocated_bytes'] > 0
    assert all(path.is_dir() for path in locations)

    job = queue_cleanup(store, plan)
    worker.work(store)
    assert store.job(job)['status'] == 'succeeded', store.log_path(job).read_text()
    receipt = dispatch(store, 'inspect_storage_cleanup_receipt', {'id': job})['cleanup_receipt']
    assert set(receipt['deleted_paths']) == {str(path) for path in locations}
    assert not any(path.exists() for path in locations)
    assert store.get('runs', run['id']) is None
    assert store.get('database_downloads', failed_db['id']) is None
    assert store.get('installs', failed_install['id']) is None
    assert reads.read_bytes() == b'original reads'
    assert cache.is_file() and retained_source.is_file()
    assert Path(completed['folder']).is_dir() and store.get('runs', completed['id']) == completed
    assert (Path(retained_db['result_path']) / 'hash.k2d').is_file()
    assert store.get('installs', retained_install['id']) == retained_install
    assert not targets(preview(store))


def test_failed_filter_preserves_successful_retries_partial_reviews_and_drafts(store):
    for key, history, fields in [
        ('run-recovered', ['failed', 'succeeded'], {}),
        ('run-previous-success', ['succeeded', 'failed'], {}),
        ('run-older-results', ['failed'], {'previous_completed': '2026-01-12'}),
        ('run-review', ['failed'], {'identification': {'state': 'awaiting_review'}}),
        ('run-aware', ['failed'], {'aware': {'state': 'complete'}}),
        ('run-draft', [], {}),
        ('run-stopped', ['cancelled'], {}),
    ]:
        run = saved_run(store, key, completed=False, **fields)
        for status in history:
            workflow(store, run, status)
    failed = saved_run(store, 'run-unsuccessful', completed=False)
    workflow(store, failed, 'succeeded', 'dry-run')
    workflow(store, failed, 'failed')
    assert targets(preview(store)) == {('run', failed['id'])}


def test_retained_dependency_skips_only_the_protected_failed_run(store):
    owner = saved_run(store, 'run-referenced', completed=False)
    workflow(store, owner, 'failed')
    saved_run(store, 'run-child', completed=False, parent=owner['id'])
    safe = saved_run(store, 'run-unused', completed=False)
    workflow(store, safe, 'failed')
    plan = preview(store)
    assert targets(plan) == {('run', safe['id'])}
    assert any(row['id'] == owner['id'] and row['blocked'] for row in plan['skipped'])
    assert Path(owner['output']).exists()


def test_retry_after_preview_invalidates_bulk_deletion(store):
    run = saved_run(store, 'run-retried', completed=False)
    workflow(store, run, 'failed')
    plan = preview(store)
    assert targets(plan) == {('run', run['id'])}
    workflow(store, run, 'succeeded')
    with pytest.raises(ValueError):
        queue_cleanup(store, plan)
    assert Path(run['folder']).is_dir()
    assert not any(job['kind'] == 'storage_cleanup' for job in store.jobs())


def test_edit_after_preview_protects_failed_validation_draft(store):
    run = saved_run(store, 'run-edited', completed=False)
    workflow(store, run, 'failed', 'dry-run')
    plan = preview(store)
    assert targets(plan) == {('run', run['id'])}
    run.update(draft_generation=1, previous_draft_jobs=[store.jobs()[0]['id']])
    store.put('runs', run['id'], run)
    with pytest.raises(ValueError, match='draft changed'):
        queue_cleanup(store, plan)
    assert ('run', run['id']) not in targets(preview(store))
    assert Path(run['folder']).is_dir()


def test_running_retry_is_preserved(store):
    other = saved_run(store, 'run-retrying', completed=False)
    workflow(store, other, 'failed')
    workflow(store, other, 'running')
    plan = preview(store)
    assert not plan['targets'] or plan['blocked']
    assert Path(other['folder']).is_dir()


def test_external_database_destination_is_never_selected(store, tmp_path):
    failed_db = failed_database(store, parent=tmp_path / 'shared-drive')
    plan = preview(store)
    assert all(Path(row['path']).is_relative_to(store.home) for row in plan['paths'])
    assert (Path(failed_db['result_path']) / 'hash.k2d').is_file()


def test_retry_success_keeps_the_working_database_and_installation(store):
    row = failed_database(store)
    retry = attempt(store, 'database_download', row['request'], 'succeeded')
    row.update(status='completed', job=retry)
    store.put('database_downloads', row['id'], row)
    item, _, failed_scratch = failed_pipeline(store)
    attempt(store, 'check', {'installation': item['id'], 'setup': True}, 'succeeded')
    item.update(ready=True, setup_completed='2026-01-12')
    store.put('installs', item['id'], item)

    plan = preview(store)
    assert ('failed_database', row['id']) not in targets(plan)
    assert ('installation', item['id']) not in targets(plan)
    kept = [Path(row['folder']), Path(row['destination']), Path(item['source'])]
    assert all(Path(entry['path']) not in kept for entry in plan['paths'])
    assert all(path.exists() for path in kept)
    # The old failed job's private scratch is safe to retire even after recovery.
    assert str(failed_scratch) in {entry['path'] for entry in plan['paths']}


@pytest.mark.parametrize('confirmed', [None, False, True])
def test_interrupted_run_needs_explicit_confirmation_that_the_process_ended(store, confirmed):
    from bactologix import supervision

    run = saved_run(store, 'run-interrupted', completed=False)
    job = workflow(store, run, 'interrupted')
    if confirmed is not None:
        supervision.save(store, job, {'id': job, 'state': 'interrupted', 'cleanup_confirmed': confirmed})
    plan = preview(store)
    assert (('run', run['id']) in targets(plan)) is (confirmed is True)
    assert Path(run['folder']).is_dir()
