"""Explicit, reviewed reset of managed workspace data, never an application uninstall.

The existing no-follow cleanup executor supplies inventory, identity checks,
quarantine and recovery. A database location is not ownership evidence: linked
locations are retained even when a historical download also names that path.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

from . import storage_cleanup as cleanup
from .storage import _open
from .store import identifier, now

PLANS = 'factory_reset_plans'
CONFIRMATION = 'RESET THIS WORKSPACE'
DATABASE_CONFIRMATION = 'DELETE MANAGED DATABASES'
NOTICE = ('Permanently remove only the listed managed workspace files. Sample originals, linked databases, '
          'application files and bootstrap Conda are retained. Database deletion is off by default; '
          'only proven, unlinked managed databases are eligible. Job history and reset receipts remain. '
          'References in discovered/configured related workspaces are checked; undiscovered workspaces cannot be checked.')
RETIRE = {'runs', 'run_visibility', 'methods_evidence', 'methods_attempt_evidence', 'followup_output_owner',
          'reports', 'report_refreshes', 'installs', 'environment_checks', 'run_environment_checks',
          'storage_resource_owners', 'result_observations', 'report_evidence', 'smoke', 'storage',
          'update_check_results'}
_SAFE_ID = re.compile(r'[A-Za-z0-9_-]+')


def _paths(value):
    """Retain both alias locations and their resolved targets."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _paths(key)
            yield from _paths(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _paths(item)
    elif isinstance(value, str) and value.startswith(('/', '~')) and '\x00' not in value:
        try:
            path = Path(value).expanduser()
        except RuntimeError as exc:
            raise ValueError('A retained dependency home directory cannot be resolved.') from exc
        if not path.is_absolute():
            raise ValueError('A retained dependency is not an absolute path.')
        if '..' in path.parts:
            raise ValueError('A retained dependency contains an unresolved parent path.')
        yield path
        yield path.resolve()


def _database_paths(store, tool, text, run=None):
    from .databases import CATALOG, _get, _hybas_paths
    from .runs import parse_config
    config = parse_config(text)
    if tool == 'hybas':
        for path in _hybas_paths(store, config).values():
            yield from _paths(str(path))
    for spec in CATALOG.get(tool, []):
        for field in spec.get('fields', []):
            if not field.endswith(('database_version', 'prot_t5_identity')):
                yield from _paths(_get(config, field))
    yield from _paths(_get(config, 'resources.directory'))
    # Native configuration accepts more paths than the database picker exposes
    # (including per-sample module overrides and future provider fields). Treat
    # every absolute configured path as a dependency. Only paths which the app
    # itself writes to this exact frozen run are exempted from that protection.
    retained = dict(config)
    if run is not None:
        folder = Path(run['folder'])
        managed = {
            'hybas': {'base_dir': folder / 'outputs', 'sample_sheet': folder / 'samples.tsv'},
            'basis': {'bactomics_root': folder / 'outputs', 'samples': folder / 'samples.tsv'},
            'micpscope': {'output_dir': folder / 'outputs', 'samples': folder / 'samples.tsv'},
        }.get(tool, {})
        for key, path in managed.items():
            if retained.get(key) == str(path):
                retained.pop(key)
        manifest = retained.get('input_manifest')
        if tool == 'hybas' and isinstance(manifest, str) and Path(manifest).is_relative_to(folder):
            retained.pop('input_manifest')
    yield from _paths(retained)


def _alias_targets(paths, *, budget=None):
    """A linked database can itself contain aliases into a selected environment."""
    seen, found = set(), set()
    budget = budget if budget is not None else cleanup._scan_budget(reset=True)
    pending = list(paths)
    while pending:
        path = pending.pop()
        cleanup._scan_checkpoint(budget, 'Checking retained database links', path, force=True)
        cleanup._scan_limits(budget, path)
        try:
            resolved = path.resolve()
            info = resolved.stat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) in seen:
            continue
        seen.add((info.st_dev, info.st_ino))
        with _open(resolved) as fd:
            for entry in os.scandir(fd):
                child = resolved / entry.name
                cleanup._scan_entry(budget, 'Checking retained database links', child)
                if entry.is_symlink():
                    target = child.resolve()
                    found.add(target)
                    pending.append(target)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(child)
    return found


def _managed_database(store, kind, row, jobs):
    """Require a recorded app-created destination and its matching submitted job."""
    try:
        key, destination = row['id'], Path(row['destination'])
        if (not _SAFE_ID.fullmatch(key) or not destination.is_absolute()
                or not destination.is_relative_to(store.home) or destination == store.home
                or destination.resolve() != destination
                or any(p.is_symlink() for p in (destination, *destination.parents))):
            return False
        job = jobs.get(row.get('job'))
        if not job or job['status'] in {'queued', 'running'}:
            return False
        if kind == 'database_downloads':
            request = row['request']
            if not isinstance(request, dict):
                return False
            expected = 'bactologix-' + row['tool'] + '-' + row['resource'] + '-' + key.removeprefix('database-')
            if job['kind'] == 'database_redownload':
                from .database_redownload import SCHEMA, CHOICE, _digest
                review = request.get('redownload_review', {})
                if (not isinstance(review, dict) or review.get('schema') != SCHEMA or review.get('choice') != CHOICE
                        or review.get('request_sha256') != _digest({k: v for k, v in request.items() if k != 'redownload_review'})):
                    return False
            return (re.fullmatch(r'database-[a-f0-9]{12}', key) is not None
                    and job['kind'] in {'database_download', 'database_redownload'} and job['payload'] == request
                    and all(row.get(k) == v for k, v in request.items())
                    and request['destination'] == str(destination)
                    and destination.parent == Path(request['parent']) and destination.name == expected)
        app = row['application']
        expected = store.home / 'resources/micpscope' / (app['id'] + '-' + app['version']) / key
        return (re.fullmatch(r'reference-[a-f0-9]{12}', key) is not None and destination == expected
                and job['kind'] == 'database_build'
                and all(row.get(k) == v for k, v in job['payload'].items())
                and job['payload'].get('destination') == str(destination)
                and job['payload'].get('id') == key)
    except (KeyError, TypeError, ValueError, OSError):
        return False


def _related_records(store):
    records, homes, errors = cleanup._other_workspaces(store)
    for home in homes:
        try:
            with sqlite3.connect((Path(home) / 'workspace.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
                records.extend((home, kind, key, json.loads(body)) for kind, key, body in db.execute(
                    "SELECT kind,id,body FROM items WHERE kind IN ('database_builds','reference_packages')"))
        except (OSError, sqlite3.Error, ValueError) as exc:
            errors.append('Cannot verify related database references: ' + str(exc))
    return records, homes, errors


def _idle(store, own_job=None):
    from .supervision import assert_recoverable
    jobs = store.jobs()
    if any(j['status'] in {'queued', 'running'} and j['id'] != own_job for j in jobs):
        raise ValueError('Finish or cancel all queued/running jobs before resetting this workspace.')
    ids = {j['id'] for j in jobs if j['id'] != own_job}
    for path in (store.home / 'logs').glob('*.process.json'):
        key = path.name.removesuffix('.process.json')
        if key != own_job:
            ids.add(key)
    assert_recoverable(store, ids)
    with store.connect() as db:
        from .variants import lock_active
        if any(lock_active(db, r['id']) for r in store.all('runs')):
            raise ValueError('A run variant is being copied. Wait before reset.')
    if any(r.get('status') != 'completed' and r.get('id') != own_job
           for r in store.all(cleanup._RECEIPTS)):
        raise ValueError('Recover the interrupted storage cleanup before resetting.')


def _build(store, delete_databases=False, own_job=None, *, checkpoint=None):
    if type(delete_databases) is not bool:
        raise ValueError('Choose explicitly whether to delete unlinked managed databases.')
    blocked, preserved, raw, protections, database_links = [], [], [], {}, set()
    budget = cleanup._scan_budget(reset=True, checkpoint=checkpoint)
    cleanup._scan_checkpoint(budget, 'Checking workspace ownership', store.home, force=True)
    records = cleanup._records(store)
    jobs = {j['id']: j for j in store.jobs()}
    owned_databases = {Path(body['destination']) for kind, _, body in records
                       if kind in {'database_downloads', 'database_builds'}
                       and _managed_database(store, kind, body, jobs)}
    try:
        _idle(store, own_job)
    except (OSError, ValueError, RuntimeError) as exc:
        blocked.append(str(exc))
    retire = [{'kind': kind, 'id': key, 'expected': body} for kind, key, body in records
              if kind in RETIRE or kind == 'settings' and key.startswith('active-')]

    def protect(paths, reason, aliases=False):
        for path in paths:
            protections.setdefault(path, set()).add(reason)
            if aliases:
                database_links.add(path)

    def target(path, reason, expected=None):
        path = Path(path)
        if (not path.is_absolute() or '..' in path.parts or path == store.home
                or not path.is_relative_to(store.home) or expected is not None and path != expected):
            blocked.append('Untrusted managed reset path: ' + str(path))
            return
        if path.exists() or path.is_symlink():
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                blocked.append('A reset target is redirected by a symbolic link: ' + str(path))
            else:
                raw.append(path)

    protect([store.home / 'tools', Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
             Path(__file__).resolve().parent], 'Application or bootstrap environment')
    for kind, key, body in records:
        cleanup._scan_checkpoint(budget, 'Checking retained dependencies', store.home, force=True)
        try:
            if kind == 'configs':
                protect(_database_paths(store, key, body), 'Linked database in saved defaults', True)
            elif kind in {'database_locations', 'reference_packages'}:
                protect(_paths(body), 'Manually linked or ambiguous database registration', True)
            elif kind == 'samples':
                protect(_paths(body), 'Registered sample originals')
            elif kind == 'runs':
                protect(_paths(body.get('inputs', [])), 'Recorded original run inputs')
                protect(_paths(body.get('sample', {})), 'Recorded original sample')
                references = _database_paths(store, body['tool'], body.get('config', '{}'), body)
                protect((p for p in references if not delete_databases or not any(
                    cleanup._inside(p, root) for root in owned_databases)), 'Database used by a saved run', True)
                target(body.get('folder', ''), 'Saved run', store.home / 'runs' / key)
            elif kind == 'report_refreshes':
                edition = store.home / 'report-refreshes' / key
                if not cleanup._inside(Path(body.get('path', '')), edition):
                    blocked.append('A report edition has an unexpected saved path: ' + key)
                else:
                    target(edition, 'Managed report edition')
            elif kind == 'installs':
                protect(_paths(body.get('original_source')), 'Original pipeline checkout')
                source = Path(body.get('source', ''))
                if source == store.home / 'sources' / key:
                    target(source, 'Managed pipeline source')
                else:
                    protect(_paths(body.get('source')), 'Unverified or external pipeline source')
            elif kind in {'database_downloads', 'database_builds'}:
                if not isinstance(body.get('destination'), str) or not Path(body['destination']).is_absolute():
                    raise ValueError('A database destination cannot be verified')
                if delete_databases and Path(body.get('destination', '')) in owned_databases:
                    target(body['destination'], 'Proven app-created database')
                    # The record retires only if its complete destination is selected below.
                else:
                    protect(_paths(body.get('destination')), 'Database retained: unselected or ownership not proven', True)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            blocked.append('Cannot inspect ' + kind + ' ' + key + ': ' + str(exc))

    from . import storage_resources as resources
    owners = [body for kind, _, body in records if kind == 'installs']
    owners += [body['installation'] for kind, _, body in records
               if kind == resources.OWNER_KIND and isinstance(body, dict) and isinstance(body.get('installation'), dict)]
    checks = {key: body for kind, key, body in records if kind == 'environment_checks'}
    checks.update({key: body.get('environment_checks', {}) for kind, key, body in records
                   if kind == resources.OWNER_KIND and key not in checks})
    for item in owners:
        prefix = Path(item.get('controller') or '/')
        digest = str(item.get('controller_digest', ''))
        name = item.get('tool', '') + '-' + digest
        if (item.get('controller_origin') == 'managed' and re.fullmatch('[a-f0-9]{20}', digest)
                and prefix.parent == store.home / 'controllers'
                and (prefix.name == name or re.fullmatch(re.escape(name) + r'-repair-[a-f0-9]+', prefix.name))):
            target(prefix, 'Managed pipeline controller')
        elif prefix != Path('/'):
            protect(_paths(str(prefix)), 'External or unverified pipeline controller')
        cache = store.home / 'envs' / item.get('tool', 'unknown')
        for recipe, check in checks.get(item['id'], {}).items():
            if not isinstance(check, dict):
                blocked.append('Invalid environment ownership record: ' + item['id'])
                continue
            check = {**check, 'id': check.get('id', recipe)}
            prefix = Path(check.get('path') or '/')
            if (prefix.parent == cache and check.get('cache') == str(cache)
                    and re.fullmatch(r'[a-f0-9]{8}(?:[a-f0-9]{24})?_?', prefix.name)
                    and resources._ownership_job(item, check, list(jobs.values()))):
                target(prefix, 'Recorded tool environment')
                target(prefix.with_suffix('.env_setup_done'), 'Tool environment marker')
            elif prefix != Path('/'):
                protect(_paths(str(prefix)), 'External or unverified tool environment')
    for relative in ('cache/conda/pkgs', 'cache/pip', 'cache/matplotlib', 'cache/numba'):
        target(store.home / relative, 'Dedicated workspace package/cache files')
    for key in jobs:
        if key != own_job and _SAFE_ID.fullmatch(key):
            for suffix in ('.log', '.monitor.json', '.process.json'):
                target(store.home / 'logs' / (key + suffix), 'Historical job log or monitoring record')
    related, homes, errors = _related_records(store)
    blocked.extend(errors)
    for home, kind, key, body in related:
        try:
            from types import SimpleNamespace
            if kind == 'configs' and isinstance(body, str):
                protect(_database_paths(SimpleNamespace(home=Path(home)), key, body),
                        'Linked database in related workspace ' + home, True)
                from .runs import parse_config
                body = parse_config(body)
            if kind == 'runs' and isinstance(body, dict):
                protect(_database_paths(SimpleNamespace(home=Path(home)), body['tool'], body.get('config', '{}')),
                        'Database used by related workspace ' + home, True)
            protect(_paths(body), 'Reference in related workspace ' + home,
                    kind in {'database_locations', 'reference_packages'})
        except (ValueError, OSError) as exc:
            blocked.append('Cannot inspect related workspace reference: ' + str(exc))
    references_complete = True
    try:
        protect(_alias_targets(database_links, budget=budget), 'Nested alias or content of a retained database')
    except (OSError, ValueError) as exc:
        references_complete = False
        blocked.append('Cannot verify database aliases: ' + str(exc))

    def partition(path):
        cleanup._scan_checkpoint(budget, 'Separating retained files', path, force=True)
        cleanup._scan_limits(budget, path, len(path.relative_to(store.home).parts))
        above = [p for p in protections if cleanup._inside(path, p)]
        if above:
            preserved.append({'path': str(path), 'reasons': sorted({s for p in above for s in protections[p]})})
            return []
        below = [p for p in protections if cleanup._inside(p, path)]
        if below:
            preserved.append({'path': str(path), 'reasons': ['Contains retained data; only unprotected children are eligible']})
            with _open(path) as fd:
                if not stat.S_ISDIR(os.fstat(fd).st_mode):
                    return []
                result = []
                for name in cleanup._directory_names(fd, budget, path):
                    child = path / name
                    if child.is_symlink():
                        preserved.append({'path': str(child), 'reasons': ['Link retained inside a protected tree']})
                    else:
                        result.extend(partition(child))
                return result
        return [path]

    raw = sorted(set(raw))
    raw = [p for p in raw if not any(p != q and cleanup._inside(p, q) for q in raw)]
    paths = []
    for path in raw:
        try:
            paths.extend(partition(path))
        except (OSError, ValueError) as exc:
            references_complete = False
            blocked.append('Cannot separate retained data from reset targets: ' + str(exc))
    for path, reasons in protections.items():
        if any('database' in reason.lower() for reason in reasons):
            preserved.append({'path': str(path), 'reasons': sorted(reasons)})
    for kind, key, body in records:
        if (kind in {'database_downloads', 'database_builds'} and Path(body.get('destination', '')) in owned_databases
                and any(cleanup._inside(Path(body['destination']), p) for p in paths)):
            retire.append({'kind': kind, 'id': key, 'expected': body})
    inventories = []
    inventory_complete = references_complete
    for path in paths:
        cleanup._scan_checkpoint(budget, 'Reviewing managed files', path, force=True)
        try:
            inventories.append(cleanup._inventory(str(path), budget))
        except (OSError, ValueError) as exc:
            inventory_complete = False
            blocked.append(str(exc))
    cleanup._scan_checkpoint(budget, 'Checking active processes', store.home, force=True)
    try:
        cleanup._check_processes([str(p) for p in paths])
    except (OSError, ValueError) as exc:
        blocked.append(str(exc))
    retire.sort(key=lambda row: (row['kind'], row['id']))
    preserved = [{'path': path, 'reasons': list(reasons)} for path, reasons in sorted({
        (row['path'], tuple(row['reasons'])) for row in preserved})]
    candidate = {'kind': 'factory_reset', 'id': 'workspace', 'label': 'Workspace factory reset',
                 'paths': [str(p) for p in paths], 'blocked': sorted(set(blocked)),
                 'record_deletions': retire, 'description': NOTICE}
    result = {'targets': [{'kind': 'factory_reset', 'id': 'workspace'}], 'candidates': [candidate],
              'inventories': inventories, 'blocked': sorted(set(blocked)), 'delete_databases': delete_databases,
              'inventory_complete': inventory_complete,
              'logical_bytes': sum(r['logical_bytes'] for r in inventories),
              'allocated_bytes': sum(r['allocated_bytes'] for r in inventories),
              'checked_workspaces': [str(store.home), *homes], 'preserved': preserved}
    cleanup._scan_checkpoint(budget, 'Finalizing complete review' if inventory_complete else 'Finalizing incomplete review', store.home, force=True)
    result['signature'] = cleanup._hash(result)
    cleanup._scan_checkpoint(budget, 'Review finished', store.home, force=True)
    return result


def preview(store, delete_databases=False, *, checkpoint=None):
    from .workflow_unlock import ownership
    with cleanup._exclusive(store), ownership(store):
        plan = _build(store, delete_databases, checkpoint=checkpoint)
        plan.update(id=identifier('reset-plan'), created_at=now())
        store.put(PLANS, plan['id'], plan)
        _prune_plans(store)
    public = cleanup._public_plan(plan)
    return {**public, 'confirmation': CONFIRMATION, 'database_confirmation': DATABASE_CONFIRMATION,
            'delete_databases': delete_databases, 'preserved': plan['preserved'], 'notice': NOTICE,
            'file_count': sum(r['files'] for r in plan['inventories']),
            'record_count': len(plan['candidates'][0]['record_deletions'])}


def _prune_plans(store, completed=None):
    """Keep one recent review plus plans required by pending reset jobs."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = {json.loads(row[0]).get('plan_id') for row in db.execute(
            "SELECT payload FROM jobs WHERE kind='factory_reset' AND status IN ('queued','running')")}
        keys = [row[0] for row in db.execute('SELECT id FROM items WHERE kind=? ORDER BY rowid DESC', (PLANS,))]
        for key in keys[1:]:
            if key not in active:
                db.execute('DELETE FROM items WHERE kind=? AND id=?', (PLANS, key))
        if completed:
            db.execute('DELETE FROM items WHERE kind=? AND id=?', (PLANS, completed))


def _verify(store, plan, own_job=None, *, checkpoint=None):
    current = _build(store, plan['delete_databases'], own_job, checkpoint=checkpoint)
    if current['blocked']:
        raise ValueError('Workspace reset is blocked: ' + '; '.join(current['blocked']))
    if current['signature'] != plan['signature']:
        raise ValueError('Files or dependencies changed since preview. Review a new workspace reset preview.')


def request(store, plan_id, signature, confirmation, acknowledged, database_confirmation=''):
    if confirmation != CONFIRMATION or acknowledged is not True:
        raise ValueError('Review the deletion paths and type ' + CONFIRMATION + '.')
    plan = store.get(PLANS, plan_id)
    if not plan or plan['signature'] != signature:
        raise ValueError('Review a current workspace reset preview.')
    if plan['delete_databases'] and database_confirmation != DATABASE_CONFIRMATION:
        raise ValueError('Database deletion needs the separate confirmation ' + DATABASE_CONFIRMATION + '.')
    from .workflow_unlock import ownership
    from .engine import launch_worker
    with cleanup._exclusive(store), ownership(store):
        # Confirm the recorded plan cheaply here. The worker rebuilds the full
        # inventory under exclusive locks before staging or deleting anything.
        # This keeps a second large directory traversal off the UI thread.
        if plan['blocked']:
            raise ValueError('Workspace reset is blocked: ' + '; '.join(plan['blocked']))
        _idle(store)
        jid = store.submit('factory_reset', {'plan_id': plan_id, 'signature': signature})
    launch_worker(store)
    return jid


def execute(ctx):
    """Worker-only execution; interrupted work is recovered by ordinary Storage cleanup."""
    from .workflow_unlock import ownership
    store, payload = ctx.store, ctx.job['payload']
    guard = nullcontext() if getattr(ctx, 'lock_fd', None) is not None else ownership(store)
    with guard, cleanup._exclusive(store), cleanup._related_locks(store):
        plan = store.get(PLANS, payload.get('plan_id'))
        if not plan or plan['signature'] != payload.get('signature'):
            raise ValueError('The reviewed workspace reset plan is unavailable.')
        _verify(store, plan, ctx.job['id'], checkpoint=lambda **_: ctx.check_cancel())
        receipt = {'id': ctx.job['id'], 'created_at': now(), 'status': 'staging',
                   'operation': 'factory_reset', 'targets': plan['targets'], 'candidates': plan['candidates'],
                   'logical_bytes': plan['logical_bytes'], 'allocated_bytes': plan['allocated_bytes'],
                   'preserved': plan['preserved'], 'delete_databases': plan['delete_databases'], 'operations': []}
        for index, inventory in enumerate(plan['inventories']):
            path = Path(inventory['path'])
            receipt['operations'].append({'original': str(path),
                'quarantine': str(path.parent / ('.bactologix-cleanup-' + receipt['id'] + '-' + str(index))),
                'inventory': inventory, 'state': 'planned'})
        cleanup._write_receipt(store, receipt)
        try:
            for operation in receipt['operations']:
                ctx.check_cancel()
                cleanup._stage(operation)
                cleanup._write_receipt(store, receipt)
            cleanup._retire_records(store, receipt)
            receipt['status'] = 'deleting'
            cleanup._write_receipt(store, receipt)
            for operation in receipt['operations']:
                ctx.write('Permanently deleting reviewed workspace files: ' + operation['original'])
                cleanup._erase(operation, ctx.check_cancel)
                cleanup._write_receipt(store, receipt)
            receipt.update(status='completed', finished_at=now())
            cleanup._write_receipt(store, receipt)
        except BaseException as exc:
            receipt.update(status='partial', error=str(exc) or type(exc).__name__)
            cleanup._write_receipt(store, receipt)
            raise
        try:
            _prune_plans(store, completed=plan['id'])
        except (sqlite3.Error, OSError):
            pass  # Pruning cannot invalidate a completed deletion receipt.
        ctx.write('Workspace reset completed. Retained files and deletion paths are recorded in the cleanup receipt.')
    return cleanup._receipt_summary(receipt)
