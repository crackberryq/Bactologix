"""Bounded, cached, channel-specific availability checks."""
from copy import deepcopy
import base64
import json
import threading
import time
import pytest
from bactologix import update_checks as checks
from bactologix.store import Store
from test_release_contract import review
from test_updates import release


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv('BACTOLOGIX_UPDATE_CHECKS', raising=False)
    monkeypatch.setattr(checks, '__version__', '1.0.0')
    monkeypatch.setattr(checks.importlib.metadata, 'version', lambda _: '1.0.0')
    result = Store(tmp_path / 'workspace')
    yield result
    checks._RUNNING.discard(str(result.home))


def active(store, tool='basis', sha='0' * 40):
    row = {'id': 'installed-' + tool, 'tool': tool, 'ready': True,
           'version': {'commit': sha, 'ref': 'legacy-branch'}}
    store.put('installs', row['id'], row)
    store.put('settings', 'active-' + tool, row['id'])
    return {'installation': row['id'], 'commit': sha}


def ids(store):
    return [row['id'] for row in checks.candidates(store)]


def test_app_current_channel_verified_asset_only_and_manual_history_kept(store, monkeypatch):
    old = checks.updates.release_record(release('1.0.0')); old['id'] = 1
    store.put('app_catalog', 'releases', {'releases': [old]})
    newer = release('2.0.0')
    wrong = release('9.0.0.dev1'); wrong['id'] = 88
    unavailable = release('3.0.0'); unavailable.update(id=89, assets=[])
    seen = []
    monkeypatch.setattr(checks, '_api', lambda path: seen.append(path) or [newer, wrong, unavailable])
    checks._check_app(store)
    assert seen == ['repos/crackberryq/Bactologix/releases?per_page=100&page=1']
    assert {r['version'] for r in store.get('app_catalog', 'releases')['releases']} == {'1.0.0', '2.0.0', '3.0.0'}
    assert ids(store) == ['update:app:2.0.0'] and store.jobs() == []


def test_manual_app_refresh_and_newer_installed_version(store, monkeypatch):
    store.put('app_catalog', 'releases', {'releases': [checks.updates.release_record(release('2.0.0'))]})
    monkeypatch.setattr(checks, '_api', lambda *_: pytest.fail('No network in panels'))
    assert ids(store) == ['update:app:2.0.0']
    monkeypatch.setattr(checks.importlib.metadata, 'version', lambda _: '2.0.0')
    assert checks.candidates(store) == []
    monkeypatch.setattr(checks, '__version__', '3.0.0')
    assert checks.candidates(store) == []


def test_withdrawn_newer_release_retained_for_history_cannot_alert(store, monkeypatch):
    old = checks.updates.release_record(release('99.0.0'))
    store.put('app_catalog', 'releases', {'releases': [old]})
    monkeypatch.setattr(checks, '_api', lambda _: [])
    checks._check_app(store)
    assert store.get('app_catalog', 'releases')['releases'] == [old]
    assert checks.candidates(store) == []


def pipeline_api(monkeypatch, row, baseline, status='ahead', tag_commit=None):
    requests = []
    def get(path):
        requests.append(path)
        if '/releases?' in path:
            return [{'tag_name': row['tag'], 'draft': False, 'prerelease': row['channel'] == 'prerelease', 'published_at': '2026-11-10'}]
        if '/commits/' in path: return {'sha': tag_commit or row['commit']}
        if '/compare/' in path:
            return {'status': status, 'base_commit': {'sha': baseline}, 'merge_base_commit': {'sha': baseline},
                    'ahead_by': 3 if status == 'ahead' else 0, 'behind_by': 0 if status == 'ahead' else 3}
        raise AssertionError(path)
    monkeypatch.setattr(checks, '_api', get)
    return requests


def test_verified_pipeline_descendant_and_changed_active_selection(store, monkeypatch):
    selected = active(store); row = review()
    requests = pipeline_api(monkeypatch, row, selected['commit'])
    checks._check_pipeline(store, 'basis', selected, [row])
    assert len(requests) == 3 and ids(store) == ['update:basis:' + row['commit']]
    assert checks.candidates(store)[0]['target'] == {'kind': 'pipeline_update', 'tool': 'basis'}
    assert store.jobs() == [] and checks.catalog.supported('basis', row['commit'], store)
    active(store, sha='9' * 40)
    assert checks.candidates(store) == []


@pytest.mark.parametrize('status', ['behind', 'diverged', 'identical', 'unknown'])
def test_pipeline_ancestor_or_unestablished_order_is_not_an_update(store, monkeypatch, status):
    selected = active(store); row = review()
    pipeline_api(monkeypatch, row, selected['commit'], status)
    checks._check_pipeline(store, 'basis', selected, [row])
    assert checks.candidates(store) == []


def test_equal_commit_needs_no_compare_and_no_alert(store, monkeypatch):
    row = review(); selected = active(store, sha=row['commit'])
    requests = pipeline_api(monkeypatch, row, selected['commit'])
    checks._check_pipeline(store, 'basis', selected, [row])
    assert len(requests) == 2 and checks.candidates(store) == []


def test_retagged_release_preserves_catalog_and_cannot_add_approval(store, monkeypatch):
    selected = active(store); row = review()
    store.put('catalog', 'basis', {'versions': [{'id': 'kept'}]})
    pipeline_api(monkeypatch, row, selected['commit'], tag_commit='9' * 40)
    with pytest.raises(ValueError, match='published pipeline tag differs'):
        checks._check_pipeline(store, 'basis', selected, [row])
    assert store.get('catalog', 'basis') == {'versions': [{'id': 'kept'}]}
    assert not checks.catalog.supported('basis', row['commit'], store)


@pytest.mark.parametrize('change', [{'development': True}, {'tested': False}])
def test_unreviewed_and_wrong_channel_pipeline_sources_cannot_alert(store, monkeypatch, change):
    selected = active(store); row = review()
    pipeline_api(monkeypatch, row, selected['commit'])
    checks._check_pipeline(store, 'basis', selected, [dict(row, **change)])
    assert checks.candidates(store) == []


def test_immutable_approval_cannot_change(store, monkeypatch):
    selected = active(store); row = review()
    store.put('release_approvals', 'basis:' + row['commit'], row)
    changed = deepcopy(row); changed['runner']['python'] = '3.12'
    pipeline_api(monkeypatch, row, selected['commit'])
    with pytest.raises(ValueError, match='contract changed'):
        checks._check_pipeline(store, 'basis', selected, [changed])
    assert store.get('release_approvals', 'basis:' + row['commit']) == row


def test_official_registry_decodes_base64_newlines(store, monkeypatch):
    encoded = base64.b64encode(json.dumps({'schema_version': 1, 'releases': [review()]}).encode()).decode()
    paths = []
    monkeypatch.setattr(checks, '_api', lambda path: paths.append(path) or {'content': encoded[:10] + '\n' + encoded[10:]})
    assert checks._registry(store) == [review()]
    assert paths == [checks.release_contract.REGISTRY]


def test_offline_worker_keeps_successful_candidates_and_visible_error(store, monkeypatch):
    store.put('app_catalog', 'releases', {'releases': [checks.updates.release_record(release('2.0.0'))]})
    store.put('update_checks', checks.STATE_KEY, {'token': 'test', 'lease_until': time.time() + 100, 'last_success': 'old'})
    monkeypatch.setattr(checks, '_api', lambda _: (_ for _ in ()).throw(OSError('offline')))
    checks._run(store, 'test')
    panel = checks.panel(store)
    assert not panel['checking'] and panel['last_success'] == 'old' and 'offline' in panel['errors']['app']
    assert ids(store) == ['update:app:2.0.0']


def test_worker_only_queries_active_installed_pipelines(store, monkeypatch):
    selected = active(store); calls = []
    store.put('update_checks', checks.STATE_KEY, {'token': 'test'})
    monkeypatch.setattr(checks, '_check_app', lambda _: calls.append('app'))
    monkeypatch.setattr(checks, '_registry', lambda _: [review()])
    monkeypatch.setattr(checks, '_check_pipeline', lambda s, t, a, r: calls.append((t, a)))
    checks._run(store, 'test')
    assert calls == ['app', ('basis', selected)]
    assert checks.panel(store)['last_success'] and not checks.panel(store)['errors']


def test_tick_does_not_block_and_does_not_enqueue_or_duplicate(store, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    monkeypatch.setattr(checks, '_check_app', lambda _: (entered.set(), finish.wait(3)))
    try:
        assert checks.tick(store) and entered.wait(1)
        assert checks.panel(store)['checking']
        assert not checks.tick(Store(store.home), force=True)
        assert store.jobs() == []
    finally:
        finish.set(); deadline = time.monotonic() + 3
        while str(store.home) in checks._RUNNING and time.monotonic() < deadline: time.sleep(.01)
    assert not checks.panel(store)['checking']


def test_database_lease_manual_cooldown_and_automatic_preference(store, monkeypatch):
    calls = []
    class Deferred:
        def __init__(self, **kw): calls.append(kw)
        def start(self): pass
    monkeypatch.setattr(checks.threading, 'Thread', Deferred)
    epoch = 1000000; monkeypatch.setattr(checks.time, 'time', lambda: epoch)
    checks.configure(store, False)
    assert not checks.tick(store) and checks.tick(store, force=True)
    assert calls[-1]['daemon'] is True
    checks._RUNNING.clear()
    assert not checks.tick(Store(store.home), force=True)
    checks._finish(store, store.get('update_checks', checks.STATE_KEY)['token'], {}, True)
    assert not checks.tick(store, force=True)
    epoch += checks.MANUAL_COOLDOWN
    assert checks.tick(store, force=True)
    checks._RUNNING.clear()
    checks._finish(store, store.get('update_checks', checks.STATE_KEY)['token'], {}, True)
    checks.configure(store, True); epoch += checks.MANUAL_COOLDOWN
    assert not checks.tick(store)
    epoch += checks.INTERVAL
    assert checks.tick(store)


def test_expired_lease_recovers_and_late_old_completion_cannot_overwrite(store, monkeypatch):
    monkeypatch.setattr(checks.threading, 'Thread', type('Deferred', (), {'__init__': lambda self, **kw: None, 'start': lambda self: None}))
    store.put('update_checks', checks.STATE_KEY, {'token': 'dead', 'lease_until': 1, 'attempt_epoch': 1})
    assert checks.tick(store)
    state = store.get('update_checks', checks.STATE_KEY)
    assert state['token'] != 'dead'
    checks._finish(store, 'dead', {'bad': 'late'}, False)
    assert store.get('update_checks', checks.STATE_KEY) == state


def test_offline_environment_and_typed_preferences(store, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_UPDATE_CHECKS', '0')
    monkeypatch.setattr(checks.threading, 'Thread', lambda **_: pytest.fail('No thread may start'))
    assert not checks.tick(store) and not checks.tick(store, force=True)
    assert store.get('update_checks', checks.STATE_KEY) is None
    monkeypatch.setattr(checks, '_api', lambda *_: pytest.fail('No network in panels'))
    assert checks.panel(store)['auto_check']
    assert checks.configure(store, False)['auto_check'] is False and checks.candidates(store) == []
    with pytest.raises(ValueError): checks.configure(store, 'false')
    with pytest.raises(ValueError): checks.tick(store, force='yes')


def test_response_size_limit(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n):
            assert n == checks.MAX_RESPONSE_BYTES + 1
            return b'x' * n
    monkeypatch.setattr(checks, 'urlopen', lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match='size limit'): checks._api('repos/crackberryq/Bactologix/releases')


def test_dev_preferred_build_uses_ancestry_without_release_tag_guessing(store, monkeypatch):
    dev = pytest.importorskip('bactologix.dev_catalog')
    store.channel = 'dev'; selected = active(store)
    row = dict(review(), development=True, preferred=True)
    monkeypatch.setattr(checks, '_api', lambda path: {'status': 'ahead', 'base_commit': {'sha': selected['commit']},
                       'merge_base_commit': {'sha': selected['commit']}, 'ahead_by': 1, 'behind_by': 0})
    monkeypatch.setattr(dev, 'approval', lambda *_: row)
    checks._check_pipeline(store, 'basis', selected, [row])
    assert ids(store) == ['update:basis:' + row['commit']]


def test_dev_app_channel_accepts_only_its_published_wheel(store, monkeypatch):
    pytest.importorskip('bactologix.channels')
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
    candidate = release('2.0.0.dev1')
    candidate['tag_name'] = 'dev-v2.0.0.dev1'
    asset = candidate['assets'][0]
    asset['name'] = 'Bactologix-Dev-2.0.0.dev1.whl'
    asset['browser_download_url'] = 'https://github.com/crackberryq/Bactologix/releases/download/dev-v2.0.0.dev1/' + asset['name']
    stable = release('99.0.0'); stable['id'] = 99
    monkeypatch.setattr(checks, '_api', lambda _: [stable, candidate])
    checks._check_app(store)
    assert ids(store) == ['update:app:2.0.0.dev1']


def test_dev_registry_retains_old_builds_and_rejects_immutable_changes(store, monkeypatch):
    dev = pytest.importorskip('bactologix.dev_catalog')
    store.channel = 'dev'
    old = dict(review(), development=True, preferred=True)
    new = dict(old, commit='c' * 40, tag='dev-next')
    original = {'schema_version': 1, 'channel': 'dev', 'builds': [old]}
    incoming = {'schema_version': 1, 'channel': 'dev', 'builds': [new]}
    monkeypatch.setattr(dev, 'document', lambda _: deepcopy(original))
    monkeypatch.setattr(checks, '_api', lambda path: {'content': base64.b64encode(json.dumps(incoming).encode()).decode()})
    result = checks._registry(store)
    assert result == [new, dict(old, preferred=False)]
    saved = store.get('dev_catalog', 'registry')
    incoming['builds'] = [dict(old, tag='changed-immutable-tag')]
    with pytest.raises(ValueError, match='contract changed'): checks._registry(store)
    assert store.get('dev_catalog', 'registry') == saved


def test_one_pipeline_failure_does_not_hide_other_successful_checks(store, monkeypatch):
    active(store, 'hybas'); active(store, 'basis'); calls = []
    store.put('update_checks', checks.STATE_KEY, {'token': 'test'})
    monkeypatch.setattr(checks, '_check_app', lambda _: None)
    monkeypatch.setattr(checks, '_registry', lambda _: [])
    def check(s, tool, *args):
        calls.append(tool)
        if tool == 'hybas': raise OSError('offline')
    monkeypatch.setattr(checks, '_check_pipeline', check)
    checks._run(store, 'test')
    assert calls == ['hybas', 'basis']
    assert set(checks.panel(store)['errors']) == {'hybas'}


def test_partial_legacy_cached_release_does_not_break_workspace_notifications(store):
    store.put('app_catalog', 'releases', {'releases': [None, {'version': '999.0', 'available': True}]})
    assert checks.candidates(store) == []
