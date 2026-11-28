"""Identify disposable setup attempts from their persisted job and owned paths."""
from collections import defaultdict
import os
from pathlib import Path
import re
import stat

import yaml

from . import factory_reset, maintenance
from .storage import _open

_DOWNLOADS = {'database_download', 'database_redownload'}


def _existing_folder(path):
    return path.is_dir() or path.is_symlink()


def _matches_config(path, text):
    """Check the small frozen config through the same no-follow boundary as cleanup."""
    expected = text.encode()
    try:
        with _open(path) as fd:
            info = os.fstat(fd)
            return (stat.S_ISREG(info.st_mode) and info.st_size == len(expected)
                    and os.read(fd, len(expected) + 1) == expected)
    except OSError:
        return False


def _database_candidate(store, kind, record, job, jobs_by_id, *, recorded=True):
    key = record['id']
    folder = store.home / 'maintenance' / key
    destination = Path(record.get('destination') or '.')
    blocked = []
    paths = []
    if not factory_reset._managed_database(store, kind, record, jobs_by_id):
        blocked.append('The database destination or its creating job could not be verified.')
    elif record.get('folder', str(folder)) != str(folder) or folder.resolve() != folder:
        blocked.append('The database setup folder has moved or is redirected.')
    else:
        # A builder can report a failure before its exclusive mkdir succeeds.
        # Its build config is created only after it owns the destination.
        if kind == 'database_builds' and _existing_folder(destination):
            expected = {'reference_spec': str(folder / 'references.yaml'), 'build_dir': str(destination),
                        'threads': record.get('cores'), 'memory_mb': record.get('memory_mb')}
            if not _matches_config(folder / 'build.yaml', yaml.safe_dump(expected)):
                blocked.append('The database build did not establish ownership of this destination.')
        paths = [str(p) for p in (folder, destination) if _existing_folder(p)]
        for original in record.get('input_sha256', {}):
            if any(Path(original).resolve().is_relative_to(Path(p)) for p in paths):
                blocked.append('This database setup contains a registered original input file.')
    label = record.get('resource') or record.get('name') or 'Reference database'
    return {'kind': 'failed_database', 'id': key, 'label': str(label) + ' · ' + key,
            'paths': paths, 'blocked': blocked, 'failed_jobs': [job['id']], 'status': job['status'],
            'description': 'Remove the partial database and its setup files.',
            'record_deletions': [{'kind': kind, 'id': key, 'expected': record}] if recorded else [],
            'metadata': {'job': job['id'], 'request': job['payload'], 'finished': job.get('finished')}}


def _unrecorded_download(store, job):
    """The frozen config is written after both directories are created exclusively,
    but before a worker records the download. A folder name alone proves nothing.
    """
    request = job['payload']
    key = request.get('id')
    if not isinstance(key, str) or not re.fullmatch(r'database-[a-f0-9]{12}', key):
        return None
    folder = store.home / 'maintenance' / key
    config_path = folder / 'config.yaml'
    if (folder.resolve() != folder or not (folder / 'workflow').is_dir()
            or config_path.is_symlink() or not config_path.is_file()):
        return None
    try:
        cfg, _, _ = maintenance.download_config(request, folder)
        if not _matches_config(config_path, yaml.safe_dump(cfg, sort_keys=False)):
            return None
    except (KeyError, OSError, TypeError, ValueError):
        return None
    return {**request, 'request': request, 'folder': str(folder), 'job': job['id']}


def _install_key(job, local_sources):
    payload = job['payload']
    if job['kind'] == 'install':
        return str(payload.get('tool')) + '-' + str(payload.get('version', {}).get('commit'))
    if job['kind'] == 'connect':
        return local_sources.get((payload.get('tool'), payload.get('path'), payload.get('commit')))
    if job['kind'] == 'check' and (payload.get('setup') or payload.get('repair')):
        return payload.get('installation')
    return None


def candidates(store, selected, resource_rows, jobs):
    """Return failed operations only. Reuse the regular install/dependency proofs."""
    from .storage_cleanup import failed_job
    rows, setup_attempts, databases = [], defaultdict(list), defaultdict(list)
    jobs_by_id = {j['id']: j for j in jobs}
    installs = [r['metadata']['installation'] for r in resource_rows if r['kind'] == 'installation']
    local_sources = {(i['tool'], i['original_source'], i['version']['commit']): i['id']
                     for i in installs if i.get('original_source')}
    for job in jobs:  # Store.jobs() returns newest first, including retries.
        payload = job['payload']
        if job['kind'] in _DOWNLOADS or job['kind'] == 'database_build':
            databases[payload.get('id')].append(job)
        key = _install_key(job, local_sources)
        if key:
            setup_attempts[key].append(job)
        install_scratch = job['kind'] == 'install' or (job['kind'] == 'environments'
                                                       and payload.get('action') in {'install', 'repair'})
        if install_scratch and re.fullmatch(r'job-[a-f0-9]{12}', job['id']) and failed_job(store, job):
            folder = store.home / 'maintenance' / job['id']
            if _existing_folder(folder):
                rows.append({'kind': 'failed_setup', 'id': job['id'], 'label': 'Pipeline setup · ' + job['id'],
                             'paths': [str(folder)], 'blocked': [], 'record_deletions': [],
                             'description': 'Remove the temporary workspace of this setup attempt.',
                             'status': job['status'], 'failed_jobs': [job['id']],
                             'metadata': {'job': job['id'], 'finished': job.get('finished')}})

    for key, attempts in databases.items():
        job = attempts[0]
        if not failed_job(store, job) or any(a['status'] == 'succeeded' for a in attempts):
            continue
        kind = 'database_builds' if job['kind'] == 'database_build' else 'database_downloads'
        record = store.get(kind, key)
        if record is None and job['kind'] in _DOWNLOADS:
            partial = _unrecorded_download(store, job)
            if partial is not None:
                rows.append(_database_candidate(store, kind, partial, job, jobs_by_id, recorded=False))
        elif (record and record.get('status') in {'failed', 'running', 'interrupted'}
              and record.get('job') == job['id']):
            rows.append(_database_candidate(store, kind, record, job, jobs_by_id))

    failed = {}
    for row in resource_rows:
        if row['kind'] != 'installation':
            continue
        item = row['metadata']['installation']
        attempts = setup_attempts.get(item['id'], [])
        if (not attempts or not failed_job(store, attempts[0]) or item.get('setup_completed')
                or any(j['status'] == 'succeeded' for j in attempts)):
            continue
        # First-time setup can verify a runner before it fails preparing tools.
        if item.get('setup_mode') != 'first_install' and (item.get('ready') or item.get('checked')):
            continue
        failed[item['id']] = attempts[0]['id']
        rows.append({**row, 'status': attempts[0]['status'], 'failed_jobs': [attempts[0]['id']]})

    for row in resource_rows:
        if row['kind'] not in {'environment', 'controller'}:
            continue
        owners = {item['id'] for item, _ in row['metadata']['owners']}
        if owners and owners.issubset(failed):
            rows.append({**row, 'failed_jobs': sorted({failed[k] for k in owners})})
    return rows
