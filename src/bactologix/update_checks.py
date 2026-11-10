"""Bounded release checks outside the UI thread and workflow queue."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import importlib.metadata
import json
import os
import re
import threading
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

from packaging.version import InvalidVersion, Version

from . import __version__, catalog, release_contract, updates
from .store import now

INTERVAL = 12 * 60 * 60
MANUAL_COOLDOWN = 60
LEASE_SECONDS = 10 * 60
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
STATE_KEY = 'availability'
PREFERENCE_KEY = 'automatic-update-checks'
_LOCK = threading.Lock()
_RUNNING = set()


def _api(path):
    request = Request('https://api.github.com/' + path, headers={
        'User-Agent': 'Bactologix/' + __version__, 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=15) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('Release response exceeds the availability-check size limit.')
    return json.loads(raw)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds')


def _read(db, kind, key, default=None):
    row = db.execute('SELECT body FROM items WHERE kind=? AND id=?', (kind, key)).fetchone()
    return json.loads(row[0]) if row else default


def _write(db, kind, key, value):
    db.execute('INSERT INTO items VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
               (kind, key, json.dumps(value)))


def configure(store, enabled):
    if type(enabled) is not bool:
        raise ValueError('Automatic update checks must be on or off.')
    store.put('settings', PREFERENCE_KEY, enabled)
    return panel(store)


def panel(store):
    state = store.get('update_checks', STATE_KEY, {})
    enabled = store.get('settings', PREFERENCE_KEY, True) is True
    checking = bool(state.get('token') and state.get('lease_until', 0) > time.time())
    return {'checking': checking, 'auto_check': enabled,
            'last_attempt': state.get('last_attempt'), 'last_success': state.get('last_success'),
            'errors': copy.deepcopy(state.get('errors', {})),
            'next_check_at': _iso(state['attempt_epoch'] + INTERVAL) if enabled and state.get('attempt_epoch') else None}


def tick(store, force=False):
    """Start at most one bounded check, returning immediately to the caller."""
    if type(force) is not bool:
        raise ValueError('The update-check request is invalid.')
    if os.environ.get('BACTOLOGIX_UPDATE_CHECKS') == '0':
        return False
    key = str(store.home)
    with _LOCK:
        if key in _RUNNING:
            return False
        timestamp = time.time()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not force and _read(db, 'settings', PREFERENCE_KEY, True) is not True:
                return False
            state = _read(db, 'update_checks', STATE_KEY, {})
            if state.get('token') and state.get('lease_until', 0) > timestamp:
                return False
            delay = MANUAL_COOLDOWN if force else INTERVAL
            if 0 <= timestamp - state.get('attempt_epoch', -INTERVAL) < delay:
                return False
            token = uuid.uuid4().hex
            state.update(token=token, lease_until=timestamp + LEASE_SECONDS,
                         last_attempt=_iso(timestamp), attempt_epoch=timestamp)
            _write(db, 'update_checks', STATE_KEY, state)
        _RUNNING.add(key)
        try:
            threading.Thread(target=_run, args=(store, token), name='bactologix-update-check', daemon=True).start()
        except BaseException:
            _RUNNING.discard(key)
            _finish(store, token, {'check': 'The background update check could not start.'}, False)
            raise
    return True


def _active(store):
    result = {}
    for tool in catalog.PIPELINES:
        key = store.get('settings', 'active-' + tool)
        item = store.get('installs', key) if key else None
        commit = (item or {}).get('version', {}).get('commit')
        if (isinstance(item, dict) and item.get('tool') == tool and item.get('ready')
                and re.fullmatch('[0-9a-f]{40}', str(commit or ''))):
            result[tool] = {'installation': key, 'commit': commit}
    return result


def _channel_accepts(version):
    check = getattr(updates, 'channel_accepts', None) or updates.regular_release
    try:
        return bool(check(version))
    except (InvalidVersion, ValueError, TypeError):
        return False


def _check_app(store):
    raw = _api('repos/' + updates.REPO + '/releases?per_page=100&page=1')
    if not isinstance(raw, list):
        raise ValueError('GitHub did not return an application release list.')
    rows = []
    for release in raw:
        if not isinstance(release, dict):
            raise ValueError('Application release metadata is malformed.')
        row = updates.release_record(release)
        if row and _channel_accepts(row['version']):
            rows.append(row)
    rows.sort(key=lambda row: Version(row['version']), reverse=True)
    # Merge into the manual chooser without discarding its older cached options.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = _read(db, 'app_catalog', 'releases', {})
        seen = {row['id'] for row in rows}
        kept = [row for row in old.get('releases', []) if row.get('id') not in seen and _channel_accepts(row.get('version'))]
        merged = sorted(rows + kept, key=lambda row: Version(row['version']), reverse=True)
        _write(db, 'app_catalog', 'releases', {'checked_at': now(), 'releases': merged,
                                             'availability_listing_limit': 100,
                                             'notification_release_ids': list(seen)})
        _write(db, 'update_check_results', 'app', {'checked_at': now(), 'releases': rows})


def _registry(store):
    dev = getattr(store, 'channel', 'stable') == 'dev'
    if dev:
        from . import dev_catalog
        path = dev_catalog.REGISTRY
    else:
        path = release_contract.REGISTRY
    response = _api(path)
    encoded = response.get('content') if isinstance(response, dict) else None
    if not isinstance(encoded, str):
        raise ValueError('The official compatibility registry could not be read.')
    doc = json.loads(base64.b64decode(''.join(encoded.split()), validate=True))
    if dev:
        dev_catalog.validate(doc)
        old = dev_catalog.document(store)
        previous = {(r['tool'], r['commit']): r for r in old['builds']}
        for row in doc['builds']:
            original = previous.get((row['tool'], row['commit']))
            without_label = lambda r: {k: v for k, v in r.items() if k not in {'name', 'preferred'}}
            if original and without_label(original) != without_label(row):
                raise ValueError('An existing development build contract changed; previous catalogue retained.')
        known = {(r['tool'], r['commit']) for r in doc['builds']}
        doc['builds'].extend(dict(r, preferred=False) for k, r in previous.items() if k not in known)
        store.put('dev_catalog', 'registry', doc)
        store.put('dev_catalog', 'checked_at', now())
        return doc['builds']
    if not isinstance(doc, dict) or doc.get('schema_version') != 1 or not isinstance(doc.get('releases'), list):
        raise ValueError('Unsupported compatibility registry; previous catalogue retained.')
    return doc['releases']


def _ahead(tool, active_commit, commit):
    if commit == active_commit:
        return False
    result = _api('repos/' + catalog.PIPELINES[tool]['repo'] + '/compare/' + active_commit + '...' + commit + '?per_page=1')
    if not isinstance(result, dict):
        raise ValueError('Could not establish pipeline source ordering.')
    return (result.get('status') == 'ahead' and result.get('base_commit', {}).get('sha') == active_commit
            and result.get('merge_base_commit', {}).get('sha') == active_commit
            and type(result.get('ahead_by')) is int and result['ahead_by'] > 0
            and result.get('behind_by') == 0)


def _check_pipeline(store, tool, active, registry):
    dev = getattr(store, 'channel', 'stable') == 'dev'
    reviewed = [r for r in registry if isinstance(r, dict) and r.get('tool') == tool
                and not release_contract.reason(tool, r) and bool(r.get('development')) == dev]
    if dev:
        selected = next((r for r in reviewed if r.get('preferred')), None)
        if selected is None:
            store.put('update_check_results', tool, {'active': active, 'checked_at': now(), 'candidate': None})
            return
        row = {'kind': 'development', 'ref': selected['tag'], 'commit': selected['commit'], 'publishedAt': None}
    else:
        raw = _api('repos/' + catalog.PIPELINES[tool]['repo'] + '/releases?per_page=30&page=1')
        if not isinstance(raw, list):
            raise ValueError('GitHub did not return a pipeline release list.')
        by_tag = {r['tag']: r for r in reviewed}
        release = next((r for r in raw if isinstance(r, dict) and not r.get('draft')
                        and r.get('tag_name') in by_tag
                        and bool(r.get('prerelease')) == (by_tag[r['tag_name']]['channel'] == 'prerelease')), None)
        if release is None:
            store.put('update_check_results', tool, {'active': active, 'checked_at': now(), 'candidate': None})
            return
        selected = by_tag[release['tag_name']]
        commit = _api('repos/' + catalog.PIPELINES[tool]['repo'] + '/commits/' + quote(selected['tag'], safe='')).get('sha')
        if commit != selected['commit']:
            raise ValueError('The published pipeline tag differs from its reviewed commit; previous catalogue retained.')
        row = dict(id='release:' + selected['tag'], kind='release', ref=selected['tag'], commit=commit,
                   name=release.get('name') or selected['tag'], prerelease=bool(release.get('prerelease')),
                   publishedAt=release.get('published_at'), preferred=False,
                   url='https://github.com/' + catalog.PIPELINES[tool]['repo'] + '/releases/tag/' + quote(selected['tag'], safe=''),
                   notes=(release.get('body') or '')[:50000])
        key = tool + ':' + commit
        previous = store.get('release_approvals', key)
        if previous is not None and not release_contract.equivalent_contract(previous, selected):
            raise ValueError('Reviewed contract changed for an existing immutable commit; previous approval retained.')
        store.put('release_approvals', key, selected)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = _read(db, 'catalog', tool, {})
            rows = [r for r in old.get('versions', []) if r.get('id') != row['id']]
            _write(db, 'catalog', tool, dict(old, versions=[row] + rows,
                                           checkedAt=now(), registry_checked_at=now(), live=True,
                                           availability_listing_limit=30))
    candidate = row if _ahead(tool, active['commit'], selected['commit']) else None
    store.put('update_check_results', tool, {'active': active, 'checked_at': now(), 'candidate': candidate})


def _finish(store, token, errors, succeeded):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        state = _read(db, 'update_checks', STATE_KEY, {})
        if state.get('token') != token:
            return
        state.update(token=None, lease_until=0, errors=errors, finished_at=now())
        if succeeded:
            state['last_success'] = now()
        _write(db, 'update_checks', STATE_KEY, state)


def _run(store, token):
    errors = {}
    try:
        try:
            _check_app(store)
        except Exception as exc:
            errors['app'] = 'Application update check failed; previous results retained. ' + str(exc)[:500]
        active = _active(store)
        if active:
            try:
                registry = _registry(store)
            except Exception as exc:
                registry = None
                errors['registry'] = 'Pipeline compatibility check failed; previous results retained. ' + str(exc)[:500]
            if registry is not None:
                for tool, item in active.items():
                    try:
                        _check_pipeline(store, tool, item, registry)
                    except Exception as exc:
                        errors[tool] = 'Pipeline update check failed; previous results retained. ' + str(exc)[:500]
        _finish(store, token, errors, not errors)
    finally:
        with _LOCK:
            _RUNNING.discard(str(store.home))


def candidates(store):
    """Cached channel-specific notices; no network calls or external routes."""
    result = []
    try:
        installed = importlib.metadata.version('bactologix')
        baseline = max(Version(__version__), Version(installed))
    except (importlib.metadata.PackageNotFoundError, InvalidVersion):
        baseline = Version(__version__)
    # Manual application-release refreshes also feed notifications.
    app = store.get('app_catalog', 'releases', {})
    releases = []
    for row in app.get('releases', []):
        if not isinstance(row, dict) or not isinstance(row.get('tag'), str) or not row['tag']:
            continue
        try:
            if ('notification_release_ids' in app and row.get('id') not in app['notification_release_ids']):
                continue
            if row.get('available') and _channel_accepts(row.get('version')) and Version(row['version']) > baseline:
                releases.append(row)
        except (InvalidVersion, KeyError, TypeError):
            continue
    if releases:
        row = max(releases, key=lambda r: Version(r['version']))
        result.append({'id': 'update:app:' + row['version'], 'category': 'updates',
                       'title': 'Bactologix update available', 'body': row['tag'] + ' is available for this installation channel.',
                       'target': {'kind': 'app_update'}})
    for tool, active in _active(store).items():
        record = store.get('update_check_results', tool, {})
        row = record.get('candidate')
        if (record.get('active') != active or not isinstance(row, dict)
                or not re.fullmatch('[0-9a-f]{40}', str(row.get('commit', ''))) or not isinstance(row.get('ref'), str)
                or row.get('commit') == active['commit']
                or not catalog.supported(tool, row.get('commit'), store)):
            continue
        result.append({'id': 'update:' + tool + ':' + row['commit'], 'category': 'updates',
                       'title': catalog.PIPELINES[tool]['name'] + ' update available',
                       'body': row['ref'] + ' is a reviewed source update for the selected installation.',
                       'target': {'kind': 'pipeline_update', 'tool': tool}})
    return result
