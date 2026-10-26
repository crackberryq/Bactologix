"""Software-only checks for the release gate consumed by the in-app updater."""
import copy
import hashlib
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_updates import wheel_bytes

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).parents[1] / 'scripts/publish_release.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
VERSION = '99.0.0a1'
COMMIT = 'a' * 40


@pytest.fixture
def publication(tmp_path, monkeypatch):
    (tmp_path / 'pyproject.toml').write_text(f'[project]\nname="bactologix"\nversion="{VERSION}"\n')
    (tmp_path / 'CHANGELOG.md').write_text(f'# Releases\n\n## {VERSION} — Test\n\n- Software release.\n')
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / f'bactologix-{VERSION}-py3-none-any.whl').write_bytes(wheel_bytes())
    (dist / f'bactologix-{VERSION}.tar.gz').write_bytes(b'software fixture')
    state = {'release': None, 'calls': [], 'bad_digest': False, 'ref': None}

    def api(path, method='GET', payload=None, content=None):
        state['calls'].append((path, method, copy.deepcopy(payload)))
        if method == 'GET':
            if '/git/ref/' in path:
                return state['ref']
            return copy.deepcopy(state['release'])
        if method == 'POST' and content is None:
            state['release'] = {**payload, 'id': 77, 'assets': []}
        elif method == 'POST':
            name = parse_qs(urlsplit(path).query)['name'][0]
            state['release']['assets'].append({
                'id': len(state['release']['assets']) + 1, 'name': name, 'state': 'uploaded',
                'size': len(content), 'digest': 'sha256:' + ('0' * 64 if state['bad_digest'] else hashlib.sha256(content).hexdigest()),
                'browser_download_url': f'https://github.com/{publisher.REPO}/releases/download/v{VERSION}/{name}'})
        elif method == 'PATCH':
            state['release'].update(payload)
        else:
            pytest.fail('Unexpected publication action')
        return copy.deepcopy(state['release'])

    monkeypatch.setattr(publisher, 'github', api)
    return tmp_path, dist, state


def test_publish_stays_draft_until_all_assets_are_verified(publication):
    root, dist, state = publication
    record = publisher.publish(root, dist, COMMIT)
    assert record['available'] and record['version'] == VERSION
    assert state['release']['target_commitish'] == COMMIT
    assert state['release']['prerelease'] and not state['release']['draft']
    assert len(state['release']['assets']) == 3
    assert [method for _, method, _ in state['calls']][-2:] == ['GET', 'PATCH']
    assert state['calls'][2][2]['draft'] is True
    assert 'Refresh releases' in state['release']['body']
    # An existing published release is never overwritten, even by a rebuilt artifact.
    before = copy.deepcopy(state['release'])
    state['calls'].clear()
    publisher.publish(root, dist, COMMIT)
    assert state['release'] == before
    assert all(method == 'GET' for _, method, _ in state['calls'])


def test_checksum_mismatch_cannot_publish(publication):
    root, dist, state = publication
    state['bad_digest'] = True
    with pytest.raises(ValueError, match='checksum/size'):
        publisher.publish(root, dist, COMMIT)
    assert state['release']['draft']
    assert not any(method == 'PATCH' for _, method, _ in state['calls'])


def test_wrong_tag_commit_cannot_publish(publication):
    root, dist, state = publication
    state['ref'] = {'object': {'type': 'commit', 'sha': 'b' * 40}}
    with pytest.raises(ValueError, match='tag targets a different commit'):
        publisher.publish(root, dist, COMMIT)
    assert all(method == 'GET' for _, method, _ in state['calls'])


def test_existing_draft_cannot_be_retargeted(publication):
    root, dist, state = publication
    state['release'] = {'draft': True, 'target_commitish': 'b' * 40}
    with pytest.raises(ValueError, match='draft targets a different commit'):
        publisher.publish(root, dist, COMMIT)
    assert all(method == 'GET' for _, method, _ in state['calls'])


def test_wrong_wheel_version_cannot_upload(publication):
    root, dist, state = publication
    (dist / f'bactologix-{VERSION}-py3-none-any.whl').write_bytes(wheel_bytes('98.0.0a1'))
    with pytest.raises(ValueError, match='identity'):
        publisher.publish(root, dist, COMMIT)
    assert not state['calls']
