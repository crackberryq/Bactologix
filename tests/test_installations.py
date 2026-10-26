"""Cache reuse and conservative source removal, using disposable software fixtures."""
from contextlib import contextmanager
from pathlib import Path
import shutil

import pytest

from bactologix import engine, installations, maintenance
from bactologix.engine import Context
from bactologix.local import git_read
from bactologix.store import Store
from bactologix.ui_backend import dispatch
from test_local import checkout
from test_maintenance import installed


@pytest.fixture
def managed(tmp_path, checkout):
    store = Store(tmp_path / 'workspace')
    key = 'hybas-older-fixture'
    source = store.home / 'sources' / key
    shutil.copytree(checkout, source)
    cache = tmp_path / 'old-cache'
    (cache / 'fixture/conda-meta').mkdir(parents=True)
    (cache / 'fixture/conda-meta/history').write_text('keep cached tools')
    runner = store.home / 'controllers/shared-runner'
    runner.mkdir()
    (runner / 'sentinel').write_text('keep runner')
    item = {'id': key, 'tool': 'hybas', 'source': str(source), 'original_source': str(checkout),
            'controller': str(runner), 'ready': True, 'rule_cache': str(cache),
            'version': {'commit': git_read(source, 'rev-parse', 'HEAD'), 'ref': 'Older fixture'}}
    store.put('installs', key, item)
    store.put('environment_checks', key, {'fixture': {'status': 'Verified'}})
    return store, item


def test_uninstall_retains_shared_content_and_original_checkout(managed):
    store, item = managed
    for folder in ('resources', 'runs', 'logs'):
        (store.home / folder / 'sentinel').write_text('keep ' + folder)
    store.put('installs', 'other', {**item, 'id': 'other', 'source': item['original_source']})
    store.put('settings', 'active-hybas', 'other')
    plan = dispatch(store, 'preview_uninstall', {'id': item['id']})['uninstall_plan']
    assert not plan['blocked']
    result = dispatch(store, 'uninstall_version', {'id': item['id'], 'signature': plan['signature']})
    assert 'retained' in result['message']
    assert not store.get('installs', item['id']) and not store.get('environment_checks', item['id'])
    assert not Path(item['source']).exists()
    assert Path(item['original_source'], 'Hybas.smk').is_file()
    assert Path(item['controller'], 'sentinel').read_text() == 'keep runner'
    assert Path(item['rule_cache'], 'fixture/conda-meta/history').read_text() == 'keep cached tools'
    assert store.get('installs', 'other')['rule_cache'] == item['rule_cache']
    for folder in ('resources', 'runs', 'logs'):
        assert (store.home / folder / 'sentinel').read_text() == 'keep ' + folder


@pytest.mark.parametrize('blocker,phrase', [
    ('active', 'active version'), ('run', 'saved run'), ('archived', 'saved run'),
    ('job', 'running jobs'), ('download', 'unfinished database'),
    ('tracked', 'changes or extra files'), ('untracked', 'changes or extra files'),
    ('ignored', 'changes or extra files'), ('unowned', 'managed source'),
    ('symlink', 'managed source'), ('nested-cache', 'retained cache'),
])
def test_uninstall_blocks_dependencies_and_unverified_content(managed, blocker, phrase):
    store, item = managed
    source = Path(item['source'])
    if blocker == 'active':
        store.activate_installation(item['id'])
    elif blocker in {'run', 'archived'}:
        store.put('runs', 'saved', {'id': 'saved', 'installation': item['id']})
        store.put('run_visibility', 'saved', {'archived': blocker == 'archived'})
    elif blocker == 'job':
        store.submit('check', {'installation': item['id']})
    elif blocker == 'download':
        store.put('database_downloads', 'resume', {'id': 'resume', 'installation': item['id'], 'status': 'failed'})
    elif blocker == 'tracked':
        (source / 'config.yaml').write_text('threads: 99\n')
    elif blocker == 'untracked':
        (source / 'report.txt').write_text('keep this')
    elif blocker == 'ignored':
        (source / '.git/info/exclude').write_text('private-output\n')
        (source / 'private-output').write_text('keep ignored data')
    elif blocker == 'unowned':
        item['source'] = item['original_source']
        store.put('installs', item['id'], item)
    elif blocker == 'symlink':
        shutil.rmtree(source)
        source.symlink_to(item['original_source'], target_is_directory=True)
    elif blocker == 'nested-cache':
        item['rule_cache'] = str(source / 'cache')
        store.put('installs', item['id'], item)
    plan = installations.uninstall_plan(store, item['id'])
    assert any(phrase in reason for reason in plan['blocked'])
    with pytest.raises(ValueError, match=phrase):
        installations.uninstall(store, item['id'], plan['signature'])
    assert store.get('installs', item['id']) and source.exists()


def test_uninstall_revalidates_preview_and_new_dependencies(managed):
    store, item = managed
    old = installations.uninstall_plan(store, item['id'])
    store.put('installs', item['id'], {**item, 'checked': 'new check'})
    with pytest.raises(ValueError, match='changed'):
        installations.uninstall(store, item['id'], old['signature'])
    current = installations.uninstall_plan(store, item['id'])
    store.put('runs', 'late', {'id': 'late', 'installation': item['id']})
    with pytest.raises(ValueError, match='saved run'):
        installations.uninstall(store, item['id'], current['signature'])
    assert Path(item['source']).exists()


def test_missing_managed_source_can_be_unregistered(managed):
    store, item = managed
    shutil.rmtree(item['source'])
    plan = installations.uninstall_plan(store, item['id'])
    assert not plan['blocked'] and not plan['source_exists']
    installations.uninstall(store, item['id'], plan['signature'])
    assert not store.get('installs', item['id'])


def test_failed_database_transaction_restores_source(managed, monkeypatch):
    store, item = managed
    plan = installations.uninstall_plan(store, item['id'])
    original = store.connect
    class FailingDelete:
        def __init__(self, db): self.db = db
        def execute(self, sql, *args):
            if sql.startswith('DELETE FROM items'):
                raise RuntimeError('transaction failed')
            return self.db.execute(sql, *args)
    @contextmanager
    def failing_connect():
        with original() as db:
            yield FailingDelete(db)
    monkeypatch.setattr(store, 'connect', failing_connect)
    with pytest.raises(RuntimeError, match='transaction failed'):
        installations.uninstall(store, item['id'], plan['signature'])
    assert Path(item['source'], 'Hybas.smk').exists() and store.get('installs', item['id'])
    assert not list(store.home.glob('uninstall-*'))


def test_cleanup_failure_reports_retained_quarantine(managed, monkeypatch):
    store, item = managed
    plan = installations.uninstall_plan(store, item['id'])
    def fail(_): raise OSError('permission changed')
    monkeypatch.setattr(installations.shutil, 'rmtree', fail)
    message = installations.uninstall(store, item['id'], plan['signature'])
    retained = list(store.home.glob('uninstall-*/source/Hybas.smk'))
    assert len(retained) == 1 and str(retained[0].parent.parent) in message
    assert not store.get('installs', item['id'])


def test_removed_installation_cannot_be_reactivated_or_queued(managed):
    store, item = managed
    plan = installations.uninstall_plan(store, item['id'])
    installations.uninstall(store, item['id'], plan['signature'])
    with pytest.raises(ValueError, match='no longer ready'):
        store.activate_installation(item['id'])
    for kind in ('check', 'environments', 'database_download'):
        with pytest.raises(ValueError, match='removed'):
            store.submit(kind, {'installation': item['id']})
    with pytest.raises(ValueError, match='removed'):
        store.register_run({'id': 'late-draft', 'installation': item['id'], 'commit': item['version']['commit']})
    assert not store.jobs() and not store.all('runs')


def test_cache_choices_find_previous_versions_and_original_checkout(managed):
    store, item = managed
    original_cache = Path(item['original_source'], '.snakemake/conda')
    original_cache.mkdir(parents=True)
    store.put('installs', 'same-cache', {**item, 'id': 'same-cache'})
    store.put('installs', 'other-pipeline', {**item, 'id': 'other-pipeline', 'tool': 'basis',
                                           'rule_cache': str(store.home)})
    rows = dispatch(store, 'reuse_environment_choices', {'id': item['id']})['cache_choices']['choices']
    assert {row['path'] for row in rows} == {item['rule_cache'], str(original_cache)}
    assert next(row for row in rows if row['path'] == item['rule_cache'])['environments'] == 1
    store.put('installs', item['id'], {**item, 'rule_cache': str(Path(item['rule_cache'], 'fixture'))})
    assert not any(row['path'].endswith('/fixture') for row in installations.cache_choices(store, item))


@pytest.mark.parametrize('previous_cache', [True, False])
def test_new_install_inherits_cache_but_retry_preserves_its_cache(managed, monkeypatch, previous_cache):
    store, old = managed
    if not previous_cache:
        old.pop('rule_cache')
        store.put('installs', old['id'], old)
    store.activate_installation(old['id'])
    version = {**old['version'], 'ref': 'New fixture'}
    key = 'hybas-' + version['commit']
    shutil.copytree(old['source'], store.home / 'sources' / key)
    monkeypatch.setattr(engine, 'prepare_controller', lambda *args, **kwargs: None)
    jid = store.submit('install', {'tool': 'hybas', 'version': version})
    ctx = Context(store, store.job(jid))
    expected = old.get('rule_cache') or str(store.home / 'envs/hybas')
    try:
        engine.install(ctx)
        assert store.get('installs', key)['rule_cache'] == expected
        store.put('installs', old['id'], {**old, 'rule_cache': '/different-active-cache'})
        engine.install(ctx)
        assert store.get('installs', key)['rule_cache'] == expected
    finally:
        ctx.log.close()


@pytest.mark.parametrize('change', ['removed', 'queued'])
def test_cache_save_revalidates_installation_and_job_lock(installed, monkeypatch, tmp_path, change):
    store, item = installed
    cache = tmp_path / 'selected-cache'; cache.mkdir()
    before = maintenance.check_deployment_path
    def changed(*args):
        before(*args)
        if change == 'removed':
            with store.connect() as db:
                db.execute("DELETE FROM items WHERE kind='installs' AND id=?", (item['id'],))
        else:
            store.submit('check', {'installation': item['id']})
    monkeypatch.setattr(maintenance, 'check_deployment_path', changed)
    with pytest.raises(ValueError, match='changed|running jobs'):
        maintenance.set_cache(store, item['id'], str(cache))
    assert store.get('installs', item['id']) == (None if change == 'removed' else item)
