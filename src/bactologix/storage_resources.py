"""Read-only plans for recorded, exclusively owned pipeline resources.

The storage executor must re-enumerate under its locks, validate the whole batch,
and inventory paths without following links before applying metadata changes.
Folder names and conda-meta alone never establish managed ownership.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

import yaml

from .local import git_read

OWNER_KIND = 'storage_resource_owners'
_TOOLS = {'hybas', 'basis', 'micpscope'}


def _selection(selected):
    result = set()
    for entry in selected or ():
        if isinstance(entry, dict):
            result.add((entry.get('kind'), entry.get('id')))
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            result.add(tuple(entry))
        elif isinstance(entry, str) and ':' in entry:
            result.add(tuple(entry.split(':', 1)))
    return result


def _path(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else None


def _overlap(first, second):
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _paths(value):
    """Include absolute dictionary keys (saved-result dependency manifests)."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _paths(key)
            yield from _paths(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _paths(child)
    else:
        path = _path(value)
        if path is not None:
            yield path


def _real_directory(path, root):
    if path is None or not path.is_relative_to(root) or path == root:
        return False
    try:
        return (path.is_dir() and all(not p.is_symlink() for p in (path, *path.parents))
                and path.resolve() == path)
    except (OSError, RuntimeError):
        return False


def _id(path):
    return hashlib.sha256(str(path).encode()).hexdigest()[:24]


def _row(kind, key, label, paths, description, blocked=(), **extra):
    return {'kind': kind, 'id': key, 'label': label, 'paths': [str(p) for p in paths],
            'description': description, 'blocked': list(dict.fromkeys(blocked)),
            'record_deletions': [], 'record_updates': [], **extra}


def _cache(store, item):
    return _path(item.get('rule_cache') or str(store.home / 'envs' / item.get('tool', 'unknown')))


def _system_paths(store, installs):
    roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), Path(__file__).resolve().parent,
             store.home / 'tools', store.home / 'resources', store.home / 'runs']
    for value in [os.environ.get('CONDA_PREFIX'), os.environ.get('CONDA_EXE'),
                  store.get('settings', 'conda'), *(i.get('conda') for i in installs)]:
        path = _path(value)
        if path:
            roots.append(path.parent.parent if path.name in {'conda', 'mamba', 'micromamba'} else path)
    return roots


def _configured_paths(value, errors):
    yield from _paths(value)
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'config', 'submitted', 'yaml', 'text'} and isinstance(child, str):
                try:
                    parsed = yaml.safe_load(child)
                    if isinstance(parsed, (dict, list)):
                        yield from _paths(parsed)
                except yaml.YAMLError:
                    errors.append('A retained configuration cannot be read; repair it before retiring pipeline resources.')
            elif isinstance(child, (dict, list)):
                yield from _configured_paths(child, errors)
    elif isinstance(value, list):
        for child in value:
            yield from _configured_paths(child, errors)


def _registered_paths(store, selected, errors):
    """Protect retained data and references; history is not a live dependency."""
    ignored = {'installs', 'environment_checks', OWNER_KIND, 'storage', 'deleted_runs', 'processes', 'job_owners',
               'desktop_sessions'}
    with store.connect() as db:
        records = db.execute('SELECT kind,id,body FROM items').fetchall()
    for record in records:
        kind, key = record['kind'], record['id']
        if kind in ignored or kind.startswith('storage_'):
            continue
        if kind in {'runs', 'run_visibility'} and ('run', key) in selected:
            continue
        # Removing a sample registration never grants deletion of original input.
        body = json.loads(record['body'])
        if kind == 'configs' and isinstance(body, str):
            body = {'config': body}
        yield from _configured_paths(body, errors)
        if kind == 'runs' and isinstance(body, dict) and body.get('folder'):
            manifest = Path(body['folder']) / 'variant.json'
            if body.get('variant') or manifest.exists():
                try:
                    if manifest.is_symlink() or manifest.stat().st_size > 16 * 1024 * 1024:
                        raise ValueError('Untrusted or oversized variant manifest')
                    yield from _paths(json.loads(manifest.read_text()))
                except (OSError, ValueError):
                    errors.append('A retained variant manifest cannot be read; its resource dependencies are protected.')


def _ownership_job(item, check, jobs):
    """Require a native inventory plus a matching recorded deployment."""
    if not re.fullmatch('[a-f0-9]{64}', str(check.get('definition', ''))):
        return None
    for job in jobs:
        payload = job.get('payload', {})
        if job.get('status') in {'queued', 'running'}:
            continue
        if (job.get('kind') == 'environments' and payload.get('installation') == item['id']
                and (job.get('status') == 'succeeded' or check.get('job') == job.get('id'))
                and payload.get('action') in {'install', 'repair'}
                and payload.get('cache') == check.get('cache')
                and payload.get('commit') == item.get('version', {}).get('commit')
                and payload.get('definitions', {}).get(check.get('id')) == check.get('definition')):
            return job['id']
        # First setup temporarily replaces only its in-memory job payload.
        if (job.get('kind') == 'install' and job.get('status') == 'succeeded'
                and item.get('setup_mode') == 'first_install' and item.get('setup_completed')
                and check.get('job') == job.get('id')
                and payload.get('tool') == item.get('tool')
                and payload.get('version', {}).get('commit') == item.get('version', {}).get('commit')
                and check.get('cache') == item.get('rule_cache')):
            return job['id']
    return None


def _inventory(store, item):
    """Return exact current prefixes or None when completeness is unknown."""
    from .maintenance import recipes
    try:
        source = Path(item['source'])
        if (git_read(source, 'rev-parse', 'HEAD') != item.get('version', {}).get('commit')
                or git_read(source, 'status', '--porcelain', '--untracked-files=all', '--ignored')):
            return None
        definitions = recipes(item)
        checks = store.get('environment_checks', item['id'], {})
        cache = _cache(store, item)
        if not definitions or not isinstance(checks, dict):
            return None
        result = set()
        for definition in definitions:
            check = checks.get(definition['id'], {})
            prefix = _path(check.get('path'))
            if (check.get('definition') != definition['definition'] or check.get('cache') != str(cache)
                    or prefix is None or prefix.parent != cache):
                return None
            result.add(prefix)
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def candidates(store, selected=None):
    """Accept selected {kind,id}, pairs, or kind:id strings.

    Selection only changes dependency planning. The executor must reject the
    entire batch if any selected target is missing or blocked.
    """
    selected = _selection(selected)
    installs = store.all('installs')
    jobs = store.jobs()
    busy = [j for j in jobs if j['status'] in {'queued', 'running'}
            and j['kind'] not in {'storage_cleanup', 'storage_delete'}]
    runs = [r for r in store.all('runs') if ('run', r.get('id')) not in selected]
    reference_errors = []
    registered = list(_registered_paths(store, selected, reference_errors))
    protected = _system_paths(store, installs)
    result, owners = [], []
    live_ids = {item['id'] for item in installs}
    for saved in store.all(OWNER_KIND):
        if isinstance(saved, dict) and saved.get('id') not in live_ids and isinstance(saved.get('installation'), dict):
            owners.append(saved)
    for item in installs:
        key = item['id']
        checks = store.get('environment_checks', key, {})
        owners.append({'id': key, 'installation': item, 'environment_checks': checks})
        source = _path(item.get('source'))
        expected = store.home / 'sources' / key
        reasons = list(reference_errors)
        owned = (source == expected and expected.parent == store.home / 'sources'
                 and not expected.is_symlink() and all(not p.is_symlink() for p in expected.parents))
        if not owned:
            reasons.append('Only an exact Bactologix-managed source snapshot can be retired.')
        if any(r.get('installation') == key for r in runs):
            reasons.append('Saved runs or drafts, including archived runs, still depend on this installation.')
        if any(d.get('installation') == key and d.get('status') != 'completed'
               for d in store.all('database_downloads')):
            reasons.append('An unfinished database download still needs this installation for resume.')
        if busy:
            reasons.append('Finish or cancel queued and running jobs before retiring pipeline resources.')
        keep = [*protected, *registered]
        for other in installs:
            keep.extend(p for name in ('rule_cache', 'controller', 'original_source')
                        if (p := _path(other.get(name))) is not None)
            if other['id'] != key:
                keep.extend(_paths(other.get('source')))
        if source and any(_overlap(source.resolve(), p.resolve()) for p in keep):
            reasons.append('This source overlaps retained data, an original checkout, a runner or an environment cache.')
        if owned and source.exists():
            try:
                if (git_read(source, 'rev-parse', 'HEAD') != item.get('version', {}).get('commit')
                        or git_read(source, 'status', '--porcelain', '--untracked-files=all', '--ignored')):
                    reasons.append('The source has changes or extra files. Preserve them before retirement.')
            except (OSError, ValueError):
                reasons.append('The source cannot be verified as an unchanged Git snapshot.')
        active_key = 'active-' + item.get('tool', '')
        active = store.get('settings', active_key)
        description = 'Retire this pipeline version and remove its unchanged managed source snapshot.'
        if active == key:
            description += ' This is the current selection; retirement clears it without selecting another version.'
        row = _row('installation', key, item.get('tool', 'Pipeline') + ' · ' + item.get('version', {}).get('ref', key),
                   [source] if owned and source.exists() else [], description, reasons,
                   metadata={'installation': item, 'environment_checks': checks, 'active': active})
        row['record_deletions'] = [{'kind': 'installs', 'id': key, 'expected': item},
                                   {'kind': 'environment_checks', 'id': key}]
        if active == key:
            row['record_deletions'].append({'kind': 'settings', 'id': active_key, 'expected': key})
        row['record_updates'] = [{'kind': OWNER_KIND, 'id': key,
                                  'body': {'id': key, 'installation': item, 'environment_checks': checks}}]
        result.append(row)
    retiring = {row['id'] for row in result if ('installation', row['id']) in selected and not row['blocked']}
    retained = [item for item in installs if item['id'] not in retiring]
    inventories = {item['id']: _inventory(store, item) for item in retained}
    grouped = {}
    for owner in owners:
        item = owner['installation']
        prefix = _path(item.get('controller'))
        if prefix and (prefix.exists() or prefix.is_symlink()):
            grouped.setdefault(('controller', str(prefix)), []).append((item, None))
        checks = owner.get('environment_checks', {})
        for recipe, check in checks.items() if isinstance(checks, dict) else ():
            if not isinstance(check, dict):
                continue
            check = {**check, 'id': check.get('id', recipe)}
            prefix = _path(check.get('path'))
            if prefix and (prefix.exists() or prefix.is_symlink()):
                grouped.setdefault(('environment', str(prefix)), []).append((item, check))
    for (kind, value), evidence in grouped.items():
        prefix = Path(value)
        reasons, proof, dependencies = list(reference_errors), [], []
        for item, check in evidence:
            if kind == 'controller':
                digest = str(item.get('controller_digest', ''))
                expected = item.get('tool', '') + '-' + digest
                owned = (item.get('controller_origin') == 'managed' and re.fullmatch('[a-f0-9]{20}', digest)
                         and prefix.parent == store.home / 'controllers'
                         and (prefix.name == expected or re.fullmatch(re.escape(expected) + r'-repair-[a-f0-9]+', prefix.name)))
                if owned:
                    proof.append({'installation': item['id'], 'digest': digest, 'origin': 'managed'})
                if item.get('controller_origin') == 'external':
                    reasons.append('A connected installation identifies this runner as an external user environment.')
            else:
                managed_cache = store.home / 'envs' / item.get('tool', 'unknown')
                job = _ownership_job(item, check, jobs)
                owned = (item.get('tool') in _TOOLS and prefix.parent == managed_cache
                         and check.get('cache') == str(managed_cache)
                         and re.fullmatch(r'[a-f0-9]{8}(?:[a-f0-9]{24})?_?', prefix.name) and job)
                if owned:
                    proof.append({'installation': item['id'], 'recipe': check['id'],
                                  'definition': check.get('definition'), 'job': job, 'check': check})
        managed_root = store.home / ('controllers' if kind == 'controller' else 'envs')
        if not proof or not _real_directory(prefix, managed_root):
            reasons.append('Managed ownership cannot be verified; external, linked and unrecorded environments are protected.')
        if not (prefix / 'conda-meta/history').is_file():
            reasons.append('This folder cannot be verified as a recorded Conda environment.')
        for item in retained:
            used = False
            if (runner := _path(item.get('controller'))) is not None:
                used = _overlap(runner.resolve(), prefix.resolve())
            if (cache := _cache(store, item)) is not None and _overlap(prefix.resolve(), cache.resolve()):
                inventory = inventories[item['id']]
                used = used or kind == 'controller' or inventory is None or any(
                    _overlap(prefix.resolve(), entry.resolve()) for entry in inventory)
            if any(_overlap(prefix.resolve(), p.resolve()) for name in ('original_source', 'source')
                   if (p := _path(item.get(name))) is not None):
                reasons.append('This environment overlaps a retained source or original checkout.')
            if used:
                dependencies.append({'kind': 'installation', 'id': item['id']})
        if kind == 'environment' and any((cache := _path(run.get('rule_cache'))) is None
                                          or _overlap(prefix.resolve(), cache.resolve()) for run in runs):
            reasons.append('Saved runs or drafts still need this tool cache, or their cache dependency is unknown.')
        if kind == 'controller' and any(_path(run.get('controller')) == prefix for run in runs):
            reasons.append('A saved run or draft still references this runner.')
        if any(_overlap(prefix.resolve(), p.resolve()) for p in [*protected, *registered]):
            reasons.append('This environment overlaps retained data, inputs, references, or the running application.')
        if any(_overlap(prefix.resolve(), p.resolve()) for owner in owners
               if (p := _path(owner['installation'].get('original_source'))) is not None):
            reasons.append('This environment overlaps an original user checkout.')
        if busy:
            reasons.append('Finish or cancel queued and running jobs before removing environments.')
        paths = [prefix]
        marker = prefix.with_suffix('.env_setup_done')
        if kind == 'environment':
            try:
                marker_stat = marker.lstat()
            except FileNotFoundError:
                pass
            except OSError:
                reasons.append('The environment completion marker cannot be inspected safely.')
            else:
                if stat.S_ISREG(marker_stat.st_mode):
                    paths.append(marker)
                else:
                    reasons.append('The environment completion marker is not a regular file; preserve it before cleanup.')
        conditional = bool(dependencies) and not reasons
        if dependencies:
            reasons.append('Retire the installation(s) that still use this environment in the same cleanup, or keep this environment.')
        label = ('Pipeline runner' if kind == 'controller' else 'Tool environment') + ' · ' + prefix.name
        result.append(_row(kind, _id(prefix), label, paths,
                           'Remove this recorded managed environment after all its dependencies are retired. It can be installed again when needed.',
                           reasons, depends_on=dependencies, conditional_blocked=conditional,
                           metadata={'ownership': proof, 'owners': evidence}))
    return result
