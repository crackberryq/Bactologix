"""Resource planning uses disposable software fixtures, never installed tools."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from bactologix import maintenance, storage_resources as resources
from bactologix.store import Store


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def environment(prefix):
    (prefix / 'conda-meta').mkdir(parents=True)
    (prefix / 'conda-meta/history').write_text('disposable software fixture\n')
    return prefix


@pytest.fixture
def managed(tmp_path):
    store = Store(tmp_path / 'workspace')
    key = 'hybas-fixture'
    source = store.home / 'sources' / key
    definitions = source / 'workflow/envs'
    definitions.mkdir(parents=True)
    (definitions / 'assembly.yml').write_text('dependencies: [python=3.11]\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture')
    runner = environment(store.home / 'controllers' / ('hybas-' + 'a' * 20))
    cache = store.home / 'envs/hybas'
    prefix = environment(cache / ('b' * 32 + '_'))
    prefix.with_suffix('.env_setup_done').touch()
    item = {'id': key, 'tool': 'hybas', 'source': str(source), 'ready': True,
            'version': {'commit': git(source, 'rev-parse', 'HEAD'), 'ref': 'Fixture'},
            'controller': str(runner), 'controller_origin': 'managed', 'controller_digest': 'a' * 20,
            'rule_cache': str(cache)}
    store.put('installs', key, item)
    store.put('settings', 'active-hybas', key)
    recipe = maintenance.recipes(item)[0]
    payload = {'installation': key, 'tool': 'hybas', 'action': 'install', 'cache': str(cache),
               'commit': item['version']['commit'], 'definitions': {recipe['id']: recipe['definition']}}
    job = store.submit('environments', payload)
    store.update_job(job, status='succeeded')
    store.put('environment_checks', key, {'assembly': {**recipe, 'path': str(prefix), 'cache': str(cache),
                                                     'status': 'Verified', 'job': job}})
    return store, item, prefix


def rows(store, selected=None):
    return {row['kind']: row for row in resources.candidates(store, selected)}


def retirement(item):
    return [{'kind': 'installation', 'id': item['id']}]


def test_unused_current_installation_can_retire_with_exclusive_environments(managed):
    store, item, prefix = managed
    before = rows(store)
    assert not before['installation']['blocked']
    assert 'current selection' in before['installation']['description']
    assert {'kind': 'settings', 'id': 'active-hybas', 'expected': item['id']} in before['installation']['record_deletions']
    for kind in ('controller', 'environment'):
        assert before[kind]['conditional_blocked']
        assert before[kind]['depends_on'] == retirement(item)
    selected = rows(store, retirement(item))
    assert all(not row['blocked'] for row in selected.values())
    assert selected['environment']['paths'] == [str(prefix), str(prefix.with_suffix('.env_setup_done'))]
    assert prefix.exists() and Path(item['source']).exists(), 'Planning must never remove anything'


def test_ownership_survives_source_retirement_for_later_cleanup(managed):
    store, item, prefix = managed
    source = rows(store)['installation']
    for action in source['record_updates']:
        store.put(action['kind'], action['id'], action['body'])
    with store.connect() as db:
        for action in source['record_deletions']:
            db.execute('DELETE FROM items WHERE kind=? AND id=?', (action['kind'], action['id']))
    shutil.rmtree(item['source'])
    remaining = rows(store)
    assert set(remaining) == {'controller', 'environment'}
    assert all(not row['blocked'] for row in remaining.values())
    assert remaining['environment']['metadata']['ownership'][0]['job']


@pytest.mark.parametrize('archived', [False, True])
def test_retained_run_or_draft_blocks_installation_and_cache(managed, archived):
    store, item, prefix = managed
    store.put('runs', 'saved', {'id': 'saved', 'installation': item['id'], 'controller': item['controller'],
                              'rule_cache': item['rule_cache'], 'status': 'draft'})
    store.put('run_visibility', 'saved', {'archived': archived})
    blocked = rows(store, retirement(item))
    assert all(row['blocked'] for row in blocked.values())
    selected = rows(store, retirement(item) + [{'kind': 'run', 'id': 'saved'}])
    assert all(not row['blocked'] for row in selected.values())


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_pending_jobs_block_all_resource_retirement(managed, status):
    store, item, _ = managed
    job = store.submit('check', {'installation': item['id']})
    store.update_job(job, status=status)
    assert all(row['blocked'] for row in rows(store, retirement(item)).values())


def test_dirty_source_cannot_unlock_environment_dependency(managed):
    store, item, _ = managed
    Path(item['source'], 'private-result.txt').write_text('retain this')
    blocked = rows(store, retirement(item))
    assert 'changes or extra files' in ' '.join(blocked['installation']['blocked'])
    assert blocked['controller']['blocked'] and blocked['environment']['blocked']


def test_ignored_source_files_are_protected(managed):
    store, item, _ = managed
    Path(item['source'], '.git/info/exclude').write_text('private-result\n')
    Path(item['source'], 'private-result').write_text('retain this')
    assert rows(store)['installation']['blocked']


def test_shared_runner_and_cache_require_every_retained_installation(managed):
    store, item, _ = managed
    other = {**item, 'id': 'second', 'source': str(store.home / 'sources/second')}
    shutil.copytree(item['source'], other['source'])
    store.put('installs', other['id'], other)
    selected = rows(store, retirement(item))
    assert selected['controller']['depends_on'] == retirement(other)
    assert selected['environment']['depends_on'] == retirement(other)
    assert all(not row['blocked'] for row in resources.candidates(store, retirement(item) + retirement(other)))


@pytest.mark.parametrize('alteration', ['external-runner', 'external-cache', 'symlink', 'missing-deployment', 'wrong-definition'])
def test_unproven_or_external_resources_are_protected(managed, tmp_path, alteration):
    store, item, prefix = managed
    kind = 'environment'
    if alteration == 'external-runner':
        item['controller_origin'] = 'external'
        store.put('installs', item['id'], item)
        kind = 'controller'
    elif alteration == 'external-cache':
        outside = tmp_path / 'external-cache'
        shutil.move(prefix.parent, outside)
        checks = store.get('environment_checks', item['id'])
        checks['assembly'].update(cache=str(outside), path=str(outside / prefix.name))
        store.put('environment_checks', item['id'], checks)
        item['rule_cache'] = str(outside)
        store.put('installs', item['id'], item)
    elif alteration == 'symlink':
        outside = tmp_path / 'original-environment'
        shutil.move(prefix, outside)
        prefix.symlink_to(outside, target_is_directory=True)
    elif alteration == 'missing-deployment':
        with store.connect() as db:
            db.execute('DELETE FROM jobs')
    elif alteration == 'wrong-definition':
        checks = store.get('environment_checks', item['id'])
        checks['assembly']['definition'] = 'changed'
        store.put('environment_checks', item['id'], checks)
    assert rows(store, retirement(item))[kind]['blocked']


def test_first_install_actual_success_status_is_ownership_evidence(managed):
    store, item, _ = managed
    with store.connect() as db:
        db.execute('DELETE FROM jobs')
    item.update(setup_mode='first_install', setup_completed='2026-01-01')
    store.put('installs', item['id'], item)
    job = store.submit('install', {'tool': item['tool'], 'version': item['version']})
    store.update_job(job, status='succeeded')
    checks = store.get('environment_checks', item['id'])
    checks['assembly']['job'] = job
    store.put('environment_checks', item['id'], checks)
    assert not rows(store, retirement(item))['environment']['blocked']


def test_initial_setup_does_not_claim_a_later_check_in_a_changed_cache(managed):
    store, item, _ = managed
    with store.connect() as db:
        db.execute('DELETE FROM jobs')
    item.update(setup_mode='first_install', setup_completed='2026-01-01')
    store.put('installs', item['id'], item)
    job = store.submit('install', {'tool': item['tool'], 'version': item['version']})
    store.update_job(job, status='succeeded')
    assert rows(store, retirement(item))['environment']['blocked']


def test_storage_accounting_snapshot_is_not_a_dependency(managed):
    store, item, _ = managed
    store.put('storage', 'latest', {'paths': [str(store.home), str(store.home / 'sources'), str(store.home / 'envs')]})
    assert all(not row['blocked'] for row in rows(store, retirement(item)).values())


def test_deleted_run_history_does_not_remain_a_live_resource_dependency(managed):
    store, item, _ = managed
    run = {'id': 'old-run', 'installation': item['id'], 'source': item['source'],
           'controller': item['controller'], 'rule_cache': item['rule_cache'],
           'config': 'database: ' + item['controller'] + '/historical-reference\n'}
    store.put('runs', run['id'], run)
    assert all(row['blocked'] for row in rows(store, retirement(item)).values())
    # Match cleanup's tombstone: preserve the deleted record for audit, without
    # giving its historical paths the semantics of retained scientific data.
    store.put('deleted_runs', run['id'], {'id': run['id'], 'run': run, 'cleanup': 'fixture-receipt'})
    with store.connect() as db:
        db.execute("DELETE FROM items WHERE kind='runs' AND id=?", (run['id'],))
    assert all(not row['blocked'] for row in rows(store, retirement(item)).values())


@pytest.mark.parametrize('kind,body', [
    ('samples', lambda p: {'id': 'input', 'reads': str(p / 'input.fastq')}),
    ('database_builds', lambda p: {'id': 'reference', 'output': str(p / 'reference.dmnd')}),
    ('runs', lambda p: {'id': 'followup', 'rule_cache': '/unrelated/cache',
                       'config': {'saved_result_dependencies': {str(p / 'prior.tsv'): 'digest'}}}),
])
def test_registered_input_reference_or_saved_dependency_protects_environment(managed, kind, body):
    store, item, prefix = managed
    record = body(prefix)
    store.put(kind, record['id'], record)
    assert rows(store, retirement(item))['environment']['blocked']


def test_yaml_run_config_and_variant_manifest_dependencies_are_protected(managed, tmp_path):
    store, item, prefix = managed
    folder = tmp_path / 'variant'
    folder.mkdir()
    run = {'id': 'variant', 'folder': str(folder), 'rule_cache': '/unrelated/cache',
           'config': 'native_results_directory: ' + str(prefix / 'results') + '\n'}
    store.put('runs', run['id'], run)
    assert rows(store, retirement(item))['environment']['blocked']
    run.update(config='{}', variant=True)
    store.put('runs', run['id'], run)
    (folder / 'variant.json').write_text(json.dumps({'saved_result_dependencies': {str(prefix / 'receipt.json'): 'sha'}}))
    assert rows(store, retirement(item))['environment']['blocked']
    (folder / 'variant.json').write_text('corrupt')
    assert all(row['blocked'] for row in rows(store, retirement(item)).values())


def test_saved_config_yaml_string_protects_resources(managed):
    store, item, prefix = managed
    store.put('configs', 'hybas', 'database: ' + str(prefix / 'reference') + '\n')
    assert rows(store, retirement(item))['environment']['blocked']


def test_running_application_and_conda_root_protected(managed, monkeypatch):
    store, item, prefix = managed
    monkeypatch.setattr(resources.sys, 'prefix', str(prefix))
    assert rows(store, retirement(item))['environment']['blocked']
    monkeypatch.setattr(resources.sys, 'prefix', '/unrelated/app')
    store.put('settings', 'conda', str(prefix / 'bin/conda'))
    assert rows(store, retirement(item))['environment']['blocked']


def test_original_checkout_stays_protected_after_retirement(managed):
    store, item, prefix = managed
    item['original_source'] = str(prefix / 'original-checkout')
    store.put('installs', item['id'], item)
    assert rows(store, retirement(item))['environment']['blocked']


def test_complete_retained_inventory_can_release_old_recorded_environment(managed):
    store, item, old = managed
    checks = store.get('environment_checks', item['id'])
    retired = {**item, 'id': 'old-install', 'source': str(store.home / 'sources/old-install')}
    store.put(resources.OWNER_KIND, retired['id'], {'id': retired['id'], 'installation': retired, 'environment_checks': checks})
    # Preserve a deployment whose recorded owner matches the retired installation.
    job = store.jobs()[0]
    payload = {**job['payload'], 'installation': retired['id']}
    jid = store.submit('environments', {**payload, 'installation': item['id']})
    with store.connect() as db:
        db.execute('UPDATE jobs SET payload=?,status=? WHERE id=?', (json.dumps(payload), 'succeeded', jid))
    current = environment(old.parent / ('c' * 32 + '_'))
    checks['assembly']['path'] = str(current)
    store.put('environment_checks', item['id'], checks)
    old_row = next(r for r in resources.candidates(store) if r['kind'] == 'environment' and str(old) in r['paths'])
    assert not old_row['blocked']
    Path(item['source'], 'workflow/envs/assembly.yml').write_text('modified recipe')
    old_row = next(r for r in resources.candidates(store) if r['kind'] == 'environment' and str(old) in r['paths'])
    assert old_row['blocked'], 'An incomplete or modified current source cannot prove cache exclusivity'


def test_unregistered_folders_are_never_offered(managed):
    store, item, _ = managed
    unknown = environment(store.home / 'envs/hybas' / ('d' * 32 + '_'))
    assert all(str(unknown) not in row['paths'] for row in resources.candidates(store))


@pytest.mark.parametrize('marker_kind', ['directory', 'symlink', 'dangling-link', 'fifo'])
def test_completion_marker_must_be_a_regular_file(managed, tmp_path, marker_kind):
    store, item, prefix = managed
    marker = prefix.with_suffix('.env_setup_done')
    marker.unlink()
    if marker_kind == 'directory':
        marker.mkdir()
        (marker / 'unrelated-results.txt').write_text('must remain')
    elif marker_kind in {'symlink', 'dangling-link'}:
        target = tmp_path / 'original-marker'
        if marker_kind == 'symlink':
            target.write_text('must remain')
        marker.symlink_to(target)
    else:
        os.mkfifo(marker)
    candidate = rows(store, retirement(item))['environment']
    assert 'not a regular file' in ' '.join(candidate['blocked'])
    assert not candidate['conditional_blocked']
    assert str(marker) not in candidate['paths']
    assert marker.exists() or marker.is_symlink()
