"""Ownership evidence for individual failed setup artifacts."""
from pathlib import Path

import pytest
import yaml

from bactologix import maintenance, supervision, worker
from test_failed_cleanup import attempt, failed_pipeline, preview, queue_cleanup, targets
from test_factory_reset import database, file, store
from test_storage_resources import environment


def test_first_install_can_remove_proven_runner_while_retaining_unattributed_conda_files(store):
    item, _, _ = failed_pipeline(store)
    runner = environment(store.home / 'controllers' / ('hybas-' + 'a' * 20))
    item.update(ready=True, controller=str(runner), controller_origin='managed', controller_digest='a' * 20)
    store.put('installs', item['id'], item)
    store.put('settings', 'active-hybas', item['id'])
    partial = environment(store.home / 'envs/hybas' / ('b' * 32 + '_'))
    store.put('environment_checks', item['id'], {'medaka': {'status': 'Installation requested'}})
    plan = preview(store)
    assert {str(runner), item['source']}.issubset(p['path'] for p in plan['paths'])
    jid = queue_cleanup(store, plan)
    worker.work(store)
    assert store.job(jid)['status'] == 'succeeded'
    assert not runner.exists() and not Path(item['source']).exists()
    assert partial.exists()


@pytest.mark.parametrize('confirmed', [False, True])
def test_interrupted_install_scratch_with_later_success(store, confirmed):
    item, jid, scratch = failed_pipeline(store)
    store.update_job(jid, status='interrupted')
    supervision.save(store, jid, {'id': jid, 'state': 'interrupted', 'cleanup_confirmed': confirmed})
    attempt(store, 'install', {'tool': 'hybas', 'version': item['version']}, 'succeeded')
    assert (('failed_setup', jid) in targets(preview(store))) == confirmed
    assert ('installation', item['id']) not in targets(preview(store))
    assert scratch.is_dir()


@pytest.mark.parametrize('frozen', [False, True])
def test_failure_before_download_record_requires_after_creation_config(store, frozen):
    row = database(store)
    store.update_job(row['job'], status='failed')
    with store.connect() as db:
        db.execute("DELETE FROM items WHERE kind='database_downloads' AND id=?", (row['id'],))
    folder = store.home / 'maintenance' / row['id']
    file(folder / 'workflow/Hybas.smk')
    cfg = maintenance.download_config(row['request'], folder)[0]
    if not frozen:
        cfg['db_root'] = '/unrelated'
    file(folder / 'config.yaml', yaml.safe_dump(cfg, sort_keys=False).encode())
    assert (('failed_database', row['id']) in targets(preview(store))) == frozen


@pytest.mark.parametrize('created', [False, True])
def test_failed_builder_claims_only_destination_it_created(store, created):
    key = 'reference-0123456789ab'
    folder = store.home / 'maintenance' / key
    output = store.home / 'resources/micpscope' / 'example-1' / key
    original = file(store.home.parent / 'user-input' / 'original.dat')
    package = {'id': 'example', 'version': '1'}
    p = {'id': key, 'tool': 'micpscope', 'installation': 'micp', 'folder': str(folder),
         'destination': str(output), 'application': package, 'cores': 1, 'memory_mb': 2048,
         'input_sha256': {str(original): 'a' * 64}}
    jid = attempt(store, 'database_build', p, 'failed')
    store.put('database_builds', key, {**p, 'status': 'failed', 'job': jid})
    file(folder / 'references.yaml', b'references: []')
    file(output / 'partial.bin')
    if created:
        config = {'reference_spec': str(folder / 'references.yaml'), 'build_dir': str(output),
                  'threads': 1, 'memory_mb': 2048}
        file(folder / 'build.yaml', yaml.safe_dump(config).encode())
    plan = preview(store)
    assert (('failed_database', key) in targets(plan)) == created
    if created:
        operation = queue_cleanup(store, plan)
        worker.work(store)
        assert store.job(operation)['status'] == 'succeeded'
        assert not output.exists() and not folder.exists()
    else:
        assert (output / 'partial.bin').is_file()
    assert original.is_file()


def test_saved_relative_kraken_alias_protects_failed_snapshot(store):
    from bactologix import databases
    from test_failed_cleanup import failed_database

    operation = failed_database(store)
    index = Path(operation['result_path'])
    file(index / 'opts.k2d')
    file(index / 'taxo.k2d')
    store.put('configs', 'hybas', 'db_root: db\nrun_kraken: true\n')
    config = yaml.safe_load(databases.save(store, 'hybas', {'kraken': str(index)},
                                            databases.panel(store, 'hybas')['revision']))
    assert config['kraken_db_name'] == 'kraken'
    assert (Path(config['db_root']) / 'kraken').resolve() == index
    plan = preview(store)
    assert ('failed_database', operation['id']) not in targets(plan)
    assert (index / 'hash.k2d').is_file()
