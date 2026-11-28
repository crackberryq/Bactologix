"""Preparation capability follows the reviewed actual installation, not defaults."""
from pathlib import Path
import subprocess

import pytest

from bactologix import maintenance as m
from bactologix.catalog import PIPELINES
from bactologix.store import Store
from test_maintenance import installed, FakeContext


@pytest.mark.parametrize('channel', ['stable', 'dev'])
def test_reviewed_nondefault_installation_is_available(installed, monkeypatch, channel):
    store, item = installed
    commit = item['version']['commit']
    row = store.get('release_approvals', 'hybas:' + commit)
    if channel == 'dev':
        monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
        store = Store(store.home.parent / 'dev-workspace')
        store.put('dev_catalog', 'registry', {'schema_version': 1, 'channel': 'dev',
                  'builds': [{**row, 'development': True}]})
        item['rule_cache'] = str(store.home / 'envs/hybas')
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', 'f' * 40)
    result = m.download_capability(item, store)
    assert result['available'], result['reason']
    assert m.download_supported(item, store)


@pytest.mark.parametrize('change, reason', [
    ('missing', 'Select a pipeline'), ('ready', 'Finish runner'),
    ('first_install', 'Finish first-time'), ('unreviewed', 'no reviewed'),
    ('unsupported', 'not approved'), ('boolean', 'not approved'),
    ('source_missing', 'folder is missing'), ('dirty', 'source changed'),
    ('commit', 'source changed'), ('hash', 'preflight failed'),
    ('controller', 'runner is missing'), ('conda', 'Conda executable'),
])
def test_disabled_status_explains_actual_failure(installed, monkeypatch, change, reason):
    store, item = installed
    key = 'hybas:' + item['version']['commit']
    row = store.get('release_approvals', key)
    if change == 'missing': item = None
    elif change == 'ready': item['ready'] = False
    elif change == 'first_install': item.update(setup_mode='first_install', setup_completed=False)
    elif change == 'unreviewed': store.put('release_approvals', key, {})
    elif change in {'unsupported', 'boolean'}:
        row['database_downloads'] = True if change == 'boolean' else 2
        store.put('release_approvals', key, row)
    elif change == 'source_missing': item['source'] += '-removed'
    elif change == 'dirty': (Path(item['source']) / 'Hybas.smk').write_text('changed')
    elif change == 'commit':
        item['version'] = {**item['version'], 'commit': 'a' * 40}
        store.put('release_approvals', 'hybas:' + 'a' * 40, {**row, 'commit': 'a' * 40})
    elif change == 'hash':
        row['files_sha256']['Hybas.smk'] = '0' * 64
        store.put('release_approvals', key, row)
    elif change == 'controller': (Path(item['controller']) / 'bin/snakemake').unlink()
    else: monkeypatch.setattr(m, 'find_conda', lambda _: None)
    result = m.download_capability(item, store)
    assert not result['available']
    assert reason in result['reason']


def test_default_commit_without_review_is_not_preparation_proof(installed):
    store, item = installed
    store.put('release_approvals', 'hybas:' + item['version']['commit'], {})
    assert item['version']['commit'] == PIPELINES['hybas']['commit']
    assert not m.download_capability(item, store)['available']


def test_execution_rechecks_capability_without_starting_command(installed, tmp_path):
    store, item = installed
    payload = m.download_request(store, 'hybas', 'busco', str(tmp_path))
    ctx = FakeContext(store, payload, 'database_download')
    assert m.download_panel_capability(item, store)['available']
    (Path(item['controller']) / 'bin/snakemake').unlink()
    panel = m.download_panel_capability(item, store)
    assert panel['available'] and panel['cached']
    with pytest.raises(ValueError, match='runner is missing'):
        m.execute_download(ctx)
    assert not ctx.calls
    assert not Path(payload['destination']).exists()


def test_panel_cache_is_short_lived_and_does_not_repeat_source_scans(installed, monkeypatch):
    store, item = installed
    clock = [100.0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    m._DOWNLOAD_PANEL_CACHE.clear()
    original = m.git_read
    calls = []
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(m, 'git_read', counted)
    initial = m.download_panel_capability(item, store)
    assert initial['available'] and not initial['cached'] and initial['checked_at']
    assert len(calls) == 2
    for _ in range(10):
        status = m.download_panel_capability(item, store)
        assert status['available'] and status['cached']
        assert status['checked_at'] == initial['checked_at']
    assert len(calls) == 2
    (Path(item['source']) / 'Hybas.smk').write_text('changed after display check')
    clock[0] += 15
    expired = m.download_panel_capability(item, store)
    assert not expired['available'] and not expired['cached']
    assert 'source changed' in expired['reason'] and len(calls) == 4


@pytest.mark.parametrize('changed', ['installation', 'approval'])
def test_panel_cache_invalidates_changed_metadata(installed, changed):
    store, item = installed
    m._DOWNLOAD_PANEL_CACHE.clear()
    assert m.download_panel_capability(item, store)['available']
    if changed == 'installation':
        item = {**item, 'ready': False}
    else:
        key = 'hybas:' + item['version']['commit']
        row = store.get('release_approvals', key)
        row['database_downloads'] = 2
        store.put('release_approvals', key, row)
    status = m.download_panel_capability(item, store)
    assert not status['available'] and not status['cached']


@pytest.mark.parametrize('error', [RuntimeError('fixture preflight failure'),
    subprocess.CalledProcessError(1, ['fixture-check']), subprocess.TimeoutExpired('fixture-check', 1)])
def test_preflight_errors_are_actionable_unavailable_status(installed, monkeypatch, error):
    store, item = installed
    def fail(*args):
        raise error
    monkeypatch.setattr('bactologix.release_contract.preflight', fail)
    result = m.download_capability(item, store)
    assert not result['available'] and 'Database preparation check failed' in result['reason']


def test_panel_cache_has_bounded_entries(installed, monkeypatch):
    store, item = installed
    m._DOWNLOAD_PANEL_CACHE.clear()
    monkeypatch.setattr(m, 'download_capability', lambda *_: {'available': False, 'reason': 'fixture'})
    for number in range(m._DOWNLOAD_PANEL_CACHE_LIMIT + 5):
        m.download_panel_capability({**item, 'id': 'fixture-' + str(number)}, store)
    assert len(m._DOWNLOAD_PANEL_CACHE) == m._DOWNLOAD_PANEL_CACHE_LIMIT
