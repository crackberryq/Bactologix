"""Publish verified CI packages as a release; never replace a published asset."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from packaging.version import Version
from bactologix.updates import MAX_WHEEL_BYTES, REPO, release_record, validate_wheel


def github(path, method='GET', payload=None, content=None):
    host = 'https://uploads.github.com/' if content is not None else 'https://api.github.com/'
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
               'Accept': 'application/vnd.github+json', 'User-Agent': 'Bactologix-release',
               'Content-Type': 'application/octet-stream' if content is not None else 'application/json'}
    body = content if content is not None else json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(host + path, data=body, headers=headers, method=method), timeout=120) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404 and method == 'GET':
            return None
        raise RuntimeError(f'GitHub {method} {path}: HTTP {error.code}') from None


def verify_assets(release, files, version):
    # Check the draft using the same acceptance rules as the installed updater.
    record = release_record({**release, 'draft': False})
    if not record or not record['available'] or record['version'] != version:
        raise ValueError('The app updater would reject this release wheel.')
    assets = release.get('assets', [])
    for file in files:
        matches = [asset for asset in assets if asset['name'] == file.name]
        digest = 'sha256:' + hashlib.sha256(file.read_bytes()).hexdigest()
        if len(matches) != 1 or matches[0].get('digest') != digest or matches[0].get('size') != file.stat().st_size:
            raise ValueError('Uploaded asset checksum/size mismatch: ' + file.name)
    return record


def publish(root, dist, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Publish from an exact checked commit.')
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    if project['name'] != 'bactologix':
        raise ValueError('Unexpected package identity.')
    version = str(Version(project['version']))
    tag = 'v' + version
    wheel = dist / f'bactologix-{version}-py3-none-any.whl'
    source = dist / f'bactologix-{version}.tar.gz'
    validate_wheel(wheel, version)
    if not 0 < wheel.stat().st_size <= MAX_WHEEL_BYTES or not source.is_file():
        raise ValueError('Expected CI wheel and source archive are required.')
    base = f'repos/{REPO}/releases'
    release = github(base + '/tags/' + tag)
    if release and not release['draft']:
        record = release_record(release)
        if not record or not record['available']:
            raise ValueError('Existing published release is not installable; manual review is required.')
        print(f'{tag} is already published. Bump the package version to publish another update.')
        return record
    ref = github(f'repos/{REPO}/git/ref/tags/{tag}')
    if ref:
        obj = ref['object']
        for _ in range(5):
            if obj['type'] != 'tag':
                break
            obj = github(f'repos/{REPO}/git/tags/{obj["sha"]}')['object']
        if obj['type'] != 'commit' or obj['sha'] != commit:
            raise ValueError('Existing tag targets a different commit; refusing to publish.')
    if release and release.get('target_commitish') != commit:
        raise ValueError('Existing draft targets a different commit; refusing to replace it.')
    checksum = dist / 'SHA256SUMS'
    checksum.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                               for p in (wheel, source)))
    files = [wheel, source, checksum]
    heading = '## ' + version + ' '
    changelog = (root / 'CHANGELOG.md').read_text()
    sections = changelog.split(heading, 1)
    if len(sections) != 2:
        raise ValueError('Add release notes to CHANGELOG.md before publishing.')
    notes = sections[1].split('\n', 1)[1].split('\n## ', 1)[0].strip()
    notes += f'\n\nTested source: `{commit}`. Install through **Update Bactologix → Refresh releases → {tag}**. Restart Bactologix after installation.\n'
    if not release:
        release = github(base, 'POST', {'tag_name': tag, 'target_commitish': commit, 'name': 'Bactologix ' + tag,
                                      'body': notes, 'draft': True, 'prerelease': Version(version).is_prerelease})
    # Draft retries reuse identical uploads; conflicting uploads require review.
    for file in files:
        existing = [a for a in release.get('assets', []) if a['name'] == file.name]
        if existing:
            expected = 'sha256:' + hashlib.sha256(file.read_bytes()).hexdigest()
            if len(existing) != 1 or existing[0].get('digest') != expected:
                raise ValueError('Existing draft asset differs: ' + file.name)
            continue
        github(base + f'/{release["id"]}/assets?name=' + quote(file.name), 'POST', content=file.read_bytes())
    release = github(base + '/' + str(release['id']))
    verify_assets(release, files, version)
    release = github(base + '/' + str(release['id']), 'PATCH', {'draft': False, 'make_latest': 'false'})
    if release.get('draft'):
        raise ValueError('Release is still a draft.')
    record = verify_assets(release, files, version)
    print('Published installable release: ' + record['url'])
    return record


if __name__ == '__main__':
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise SystemExit('Release publication is limited to this repository on main.')
    publish(Path.cwd(), Path('dist'), os.environ['GITHUB_SHA'])
