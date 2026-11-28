"""Source defaults never come from mutable application templates or scripts."""
import hashlib
import subprocess

import pytest

from bactologix import setup_advice_source as source
from bactologix.store import Store


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    checkout = tmp_path / 'pipeline'
    checkout.mkdir()
    git(checkout, 'init', '-q')
    git(checkout, 'config', 'user.name', 'Setup test')
    git(checkout, 'config', 'user.email', 'setup@example.test')
    raw = 'polish_mode: tripolish\nracon_rounds: 2\n'
    (checkout / 'config.example.yaml').write_text(raw)
    git(checkout, 'add', 'config.example.yaml')
    git(checkout, 'commit', '-qm', 'Fixture')
    commit = git(checkout, 'rev-parse', 'HEAD')
    item = {'id': 'runner', 'tool': 'hybas', 'ready': True, 'source': str(checkout),
            'version': {'commit': commit, 'ref': 'fixture-source'}}
    store.put('installs', item['id'], item)
    store.put('settings', 'active-hybas', item['id'])
    monkeypatch.setattr(source, 'supported', lambda tool, sha, _store: sha == commit)
    return store, checkout, item, raw


def test_selected_source_defaults_include_identity_but_no_inferred_profile(installed):
    store, checkout, item, raw = installed
    result = source.load(store, 'hybas')
    assert result == {'commit': item['version']['commit'], 'label': 'fixture-source',
                      'verified': True, 'config_path': 'config.example.yaml',
                      'config_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                      'defaults': {'polish_mode': 'tripolish', 'racon_rounds': 2},
                      'profile': None, 'installation': 'runner'}


@pytest.mark.parametrize('change', ['modified', 'staged', 'untracked', 'stale', 'wrong_tool', 'not_ready', 'incomplete'])
def test_unusable_source_never_returns_defaults(installed, change):
    store, checkout, item, _ = installed
    if change in {'modified', 'staged'}:
        (checkout / 'config.example.yaml').write_text('racon_rounds: 99\n')
        if change == 'staged':
            git(checkout, 'add', 'config.example.yaml')
    elif change == 'untracked':
        (checkout / 'extra.py').write_text('raise RuntimeError("must not execute")')
    elif change == 'stale':
        (checkout / 'extra.py').write_text('raise RuntimeError("must not execute")')
        git(checkout, 'add', 'extra.py')
        git(checkout, 'commit', '-qm', 'Different source')
    elif change == 'wrong_tool':
        item['tool'] = 'basis'
    elif change == 'not_ready':
        item['ready'] = False
    elif change == 'incomplete':
        item.update(setup_mode='first_install', setup_completed=False)
    store.put('installs', item['id'], item)
    result = source.load(store, 'hybas')
    assert not result['verified'] and not result['defaults'] and result['profile'] is None
    assert result['error']


@pytest.mark.parametrize('tool', ['basis', 'micpscope'])
def test_downstream_pipeline_reads_its_committed_config(installed, monkeypatch, tool):
    store, checkout, item, _ = installed
    (checkout / 'config.yaml').write_text('threads: 1\nreference_min_identity: 30\n')
    git(checkout, 'add', 'config.yaml')
    git(checkout, 'commit', '-qm', 'Downstream configuration')
    item.update(tool=tool, version={'commit': git(checkout, 'rev-parse', 'HEAD')})
    store.put('installs', item['id'], item)
    store.put('settings', 'active-' + tool, item['id'])
    monkeypatch.setattr(source, 'supported', lambda *_: True)
    result = source.load(store, tool)
    assert result['verified'] and result['config_path'] == 'config.yaml'
    assert result['defaults']['reference_min_identity'] == 30 and result['profile'] is None


@pytest.mark.parametrize('content', [b'- not-a-mapping\n', b'racon_rounds: [\n', b'key: !!python/object/apply:os.system ["false"]', b'key: \xff'])
def test_invalid_committed_yaml_is_not_advice(installed, monkeypatch, content):
    store, checkout, item, _ = installed
    (checkout / 'config.example.yaml').write_bytes(content)
    git(checkout, 'add', 'config.example.yaml')
    git(checkout, 'commit', '-qm', 'Invalid configuration')
    item['version']['commit'] = git(checkout, 'rev-parse', 'HEAD')
    store.put('installs', item['id'], item)
    monkeypatch.setattr(source, 'supported', lambda *_: True)
    result = source.load(store, 'hybas')
    assert not result['verified'] and not result['defaults'] and result['error']


def test_oversized_blob_is_rejected_before_reading_content(installed, monkeypatch):
    store, _, _, _ = installed
    real_git = source._git
    seen = []
    def read(checkout, *args):
        seen.append(args)
        if args[:2] == ('cat-file', '-s'):
            return str(source.MAX_CONFIG_BYTES + 1).encode()
        return real_git(checkout, *args)
    monkeypatch.setattr(source, '_git', read)
    result = source.load(store, 'hybas')
    assert not result['verified'] and '2 MB' in result['error']
    assert not any(args[:2] == ('cat-file', 'blob') for args in seen)


def test_git_timeout_is_unavailable_advice(installed, monkeypatch):
    store, _, _, _ = installed
    def timeout(*_):
        raise subprocess.TimeoutExpired('git', 10)
    monkeypatch.setattr(source, '_git', timeout)
    assert not source.load(store, 'hybas')['verified']


def test_no_active_runner_or_unknown_tool_is_unverified(tmp_path):
    store = Store(tmp_path)
    assert not source.load(store, 'hybas')['verified']
    assert not source.load(store, 'unknown')['verified']


def test_unsupported_commit_and_nested_checkout_are_unverified(installed, monkeypatch):
    store, checkout, item, _ = installed
    monkeypatch.setattr(source, 'supported', lambda *_: False)
    assert not source.load(store, 'hybas')['verified']
    monkeypatch.setattr(source, 'supported', lambda *_: True)
    nested = checkout / 'subfolder'
    nested.mkdir()
    item['source'] = str(nested)
    store.put('installs', item['id'], item)
    assert not source.load(store, 'hybas')['verified']


def test_symlink_template_is_not_followed(installed, monkeypatch):
    store, checkout, item, raw = installed
    template = checkout / 'config.example.yaml'
    template.unlink()
    (checkout / 'elsewhere.yaml').write_text(raw)
    template.symlink_to('elsewhere.yaml')
    git(checkout, 'add', '.')
    git(checkout, 'commit', '-qm', 'Symlink template')
    item['version']['commit'] = git(checkout, 'rev-parse', 'HEAD')
    store.put('installs', item['id'], item)
    monkeypatch.setattr(source, 'supported', lambda *_: True)
    assert not source.load(store, 'hybas')['verified']


def test_source_changed_during_read_does_not_return_old_defaults(installed, monkeypatch):
    store, checkout, _, _ = installed
    real_git = source._git
    def read(root, *args):
        value = real_git(root, *args)
        if args[:2] == ('cat-file', 'blob'):
            (checkout / 'config.example.yaml').write_text('racon_rounds: 4\n')
        return value
    monkeypatch.setattr(source, '_git', read)
    result = source.load(store, 'hybas')
    assert not result['verified'] and result['defaults'] == {}
