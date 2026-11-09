"""Reviewed reclamation of owned storage, with durable deletion receipts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from contextlib import contextmanager, ExitStack
from types import SimpleNamespace

from .storage import _open
from .store import identifier, now

MAX_ENTRIES = 300000
MAX_SECONDS = 30
MAX_DEPTH = 96
CONFIRMATION = 'DELETE'
NOTICE = ('Deletion is permanent. Verify transferred copies yourself, or explicitly choose to discard the data. '
          'Original sample files are a separate selection. References in this workspace and discovered/configured '
          'related workspaces are checked; unregistered files and undiscovered workspaces cannot be checked. '
          'Allocated bytes are an estimate: hard links, open files, compression and filesystem snapshots can reduce '
          'the space actually reclaimed. Empty sample directories and job logs are retained.')
_RECEIPTS = 'storage_cleanup_receipts'
_PLANS = 'storage_cleanup_plans'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def assert_mutation_allowed(db):
    if db.execute("SELECT 1 FROM jobs WHERE kind='storage_cleanup' AND status IN ('queued','running')").fetchone():
        raise ValueError('Storage cleanup is queued or running. Wait before changing this workspace.')
    for row in db.execute('SELECT body FROM items WHERE kind=?', (_RECEIPTS,)):
        if json.loads(row[0]).get('status') != 'completed':
            raise ValueError('An interrupted storage cleanup needs recovery in Storage before starting new work.')


def _exclusive(store):
    from .storage_guard import exclusive
    return exclusive(store)


def _put(db, kind, key, value):
    db.execute('INSERT INTO items VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
               (kind, key, json.dumps(value)))


def _paths(value):
    """Read dependency paths, including Dev manifest/AWARE inputs and native config."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.startswith('/'):
                yield from _paths(key)
            if key == 'config' and isinstance(item, str):
                try:
                    from .runs import parse_config
                    yield from _paths(parse_config(item))
                except (ValueError, TypeError):
                    pass
            yield from _paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _paths(item)
    elif isinstance(value, str) and value.startswith('/') and '\x00' not in value:
        try:
            yield Path(value).resolve()
        except (OSError, RuntimeError):
            yield Path(value)


def _inside(path, root):
    return path == root or root in path.parents


def _records(store):
    with store.connect() as db:
        return [(r['kind'], r['id'], json.loads(r['body'])) for r in db.execute('SELECT * FROM items')]


def _other_workspaces(store):
    """Read known neighbouring/default workspaces without initializing or writing them."""
    from platformdirs import user_data_dir
    homes = {Path(user_data_dir(name)) for name in ('bactologix', 'bactologix-dev')}
    homes.update(Path.home() / '.local/share' / name for name in ('bactologix', 'bactologix-dev'))
    configured = store.get('settings', 'storage-other-workspaces', [])
    errors = []
    discovery = os.environ.get('BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS')
    if discovery:
        try:
            defaults = json.loads(discovery)
            if not isinstance(defaults, list) or any(not isinstance(p, str) or not Path(p).is_absolute() for p in defaults):
                raise ValueError('Expected absolute workspace paths.')
            homes.update(Path(p) for p in defaults)
        except (ValueError, TypeError):
            errors.append('Preserved workspace discovery paths are invalid. Restart from the launcher before cleanup.')
    if not isinstance(configured, list):
        return [], [], ['Configured related workspaces must be a list of absolute paths.']
    for value in configured:
        if not isinstance(value, str) or not Path(value).is_absolute():
            errors.append('A configured related workspace path is invalid.')
        else:
            homes.add(Path(value))
    try:
        homes.update(p for p in store.home.parent.iterdir() if p.is_dir() and (p / 'workspace.sqlite3').exists())
    except OSError as exc:
        errors.append('Cannot inspect neighbouring workspaces: ' + str(exc))
    records, checked = [], []
    for home in sorted({p.resolve() for p in homes}):
        if home.resolve() == store.home:
            continue
        path = home / 'workspace.sqlite3'
        if not path.exists() and str(home) not in configured:
            continue
        try:
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                rows = db.execute("SELECT kind,id,body FROM items WHERE kind IN ('samples','runs','installs','configs','database_locations','database_downloads')").fetchall()
                records.extend((str(home), k, key, json.loads(body)) for k, key, body in rows)
            finally:
                db.close()
            checked.append(str(home))
        except (OSError, sqlite3.Error, ValueError) as exc:
            errors.append('Cannot verify related workspace ' + str(home) + ': ' + str(exc))
    return records, checked, errors


def _sample_files(sample):
    """Known original files only; never delete an entire registered sample folder."""
    root = Path(sample.get('path', ''))
    if not root.is_absolute():
        raise ValueError('Sample location must be an absolute directory.')
    files = set()
    for item in sample.get('inputs', []):
        if isinstance(item, dict) and item.get('path'):
            files.add(str(Path(item['path'])))
        elif isinstance(item, str) and item.startswith('/'):
            files.add(item)
    for mode in (() if 'inputs' in sample else ('illumina', 'nanopore')):
        directory = root / 'raw' / mode
        if directory.exists():
            with _open(directory) as fd:
                for entry in os.scandir(fd):
                    if re.search(r'\.(fastq|fq)(\.gz)?$', entry.name, re.I):
                        files.add(str(directory / entry.name))
    for suffix in (() if 'inputs' in sample else ('.fna', '.faa', '.gff', '.gbk', '.evidence.csv', '.interpro.tsv')):
        path = root / 'annotation' / (sample['id'] + suffix)
        if path.exists() or path.is_symlink():
            files.add(str(path))
    for value in files:
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('A registered original input path is invalid.')
        with _open(path.parent) as fd:
            info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Original input deletion requires regular files, without redirected paths: ' + value)
    return sorted(files)


def _base_candidates(store, selected):
    rows, records = [], _records(store)
    selected_keys = {(t['kind'], t['id']) for t in selected}
    selected_runs = {key for kind, key in selected_keys if kind == 'run'}
    run_rows = [body for kind, _, body in records if kind == 'runs']
    for run in run_rows:
        key = run['id']
        folder = store.home / 'runs' / key
        blocked = []
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key) or Path(run.get('folder', '')) != folder:
            blocked.append('Saved folder is not the exact workspace-owned run directory.')
        for child in run_rows:
            if child.get('parent') == key and child['id'] not in selected_runs:
                blocked.append('Retained child run depends on this run: ' + child['id'])
        paths = [str(folder)]
        deletions = [{'kind': 'runs', 'id': key, 'expected': run}]
        for kind, record_id, body in records:
            if kind in {'run_visibility', 'methods_evidence', 'followup_output_owner'} and record_id == key:
                deletions.append({'kind': kind, 'id': record_id, 'expected': body})
            if kind in {'reports', 'report_refreshes'} and isinstance(body, dict) and (body.get('run') == key or body.get('run_id') == key):
                deletions.append({'kind': kind, 'id': record_id, 'expected': body})
                if kind == 'report_refreshes':
                    edition = store.home / 'report-refreshes' / record_id
                    if not re.fullmatch(r'[A-Za-z0-9_-]+', record_id) or not _inside(Path(body.get('path', '')), edition):
                        blocked.append('A saved report edition has an unexpected path: ' + record_id)
                    elif edition.exists():
                        paths.append(str(edition))
        rows.append({'kind': 'run', 'id': key, 'label': run.get('label', key), 'paths': paths,
                     'blocked': blocked, 'status': 'completed' if run.get('completed') else 'saved',
                     'description': 'Delete saved run files and report editions. Original sample inputs are retained.',
                     'record_deletions': deletions, 'metadata': run})
    for sample in store.all('samples'):
        common = {'id': sample['id'], 'label': sample['id'],
                  'record_deletions': [{'kind': 'samples', 'id': sample['id'], 'expected': sample}], 'metadata': sample}
        rows.append({**common, 'kind': 'sample_registration', 'paths': [], 'blocked': [],
                     'description': 'Unregister this sample. Keep its files and all saved run evidence.'})
        try:
            paths, blocked = _sample_files(sample), []
            if not paths:
                blocked.append('No individually registered original input files were found.')
        except (OSError, ValueError) as exc:
            paths, blocked = [], [str(exc)]
        rows.append({**common, 'kind': 'sample_files', 'paths': paths, 'blocked': blocked,
                     'description': 'Permanently delete the listed original input files and unregister this sample. Empty directories remain.'})
    from .storage_resources import candidates
    rows.extend(candidates(store, selected))
    for receipt in store.all(_RECEIPTS):
        if receipt.get('status') == 'completed':
            continue
        rows.append({'kind': 'cleanup_remaining', 'id': receipt['id'], 'label': 'Recover cleanup ' + receipt['id'],
                     'category': 'recovery', 'paths': [], 'blocked': [],
                     'description': 'Finish this interrupted deletion; files already removed cannot be restored.',
                     'recovery': receipt, 'record_deletions': []})
    return rows


def _candidates(store, selected=(), own_job=None):
    rows = _base_candidates(store, selected)
    chosen = {(t['kind'], t['id']) for t in selected}
    deleted_records = {(r['kind'], r['id']) for row in rows if (row['kind'], row['id']) in chosen
                       for r in row.get('record_deletions', [])}
    dependencies = [(str(store.home), k, key, body) for k, key, body in _records(store)
                    if k in {'samples', 'runs', 'installs', 'configs', 'database_locations', 'database_downloads'}]
    others, checked, errors = _other_workspaces(store)
    dependencies.extend(others)
    inspected = []
    for home, kind, key, body in dependencies:
        if kind == 'runs' and not (home == str(store.home) and ('runs', key) in deleted_records):
            manifest = Path(body.get('folder', '')) / 'variant.json'
            if body.get('variant') or manifest.exists() or manifest.is_symlink():
                try:
                    with _open(manifest) as fd:
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
                            raise ValueError('Variant dependency manifest is not a bounded regular file.')
                        with os.fdopen(os.dup(fd)) as stream:
                            evidence = json.load(stream)
                    body = {**body, 'cleanup_dependency_manifest': evidence}
                except (OSError, ValueError) as exc:
                    errors.append('Cannot inspect retained variant ' + key + ': ' + str(exc))
        inspected.append((home, kind, key, body))
    dependencies = inspected
    busy = [j for j in store.jobs() if j['status'] in {'queued', 'running'} and j['id'] != own_job]
    unfinished = [r for r in store.all(_RECEIPTS) if r.get('status') != 'completed']
    with store.connect() as db:
        from .variants import lock_active
        locked = [r['id'] for r in store.all('runs') if lock_active(db, r['id'])]
    for row in rows:
        row['blocked'] = list(row.get('blocked', []))
        hard_blocked = False
        if busy:
            row['blocked'].append('Finish or cancel all queued/running jobs before cleaning storage.')
            hard_blocked = True
        if locked:
            row['blocked'].append('A run variant is being copied. Wait for it to finish.')
            hard_blocked = True
        if unfinished and row['kind'] != 'cleanup_remaining':
            row['blocked'].append('Recover the interrupted cleanup first.')
            hard_blocked = True
        if row['kind'] == 'cleanup_remaining':
            if len(selected) > 1:
                row['blocked'].append('Recover one interrupted cleanup at a time.')
            continue
        if row['paths']:
            row['blocked'].extend(errors)
            hard_blocked = hard_blocked or bool(errors)
        if row['kind'] == 'sample_files' and any(
                _inside(Path(value), Path(home)) for value in row['paths'] for home in [str(store.home), *checked]):
            row['blocked'].append('Sample files inside a managed workspace must be cleaned through their owning run or resource; unregister the sample separately.')
            hard_blocked = True
        for value in row['paths']:
            target = Path(value)
            for home, kind, key, body in dependencies:
                if home == str(store.home) and ((kind, key) in deleted_records or
                    any(r['kind'] == kind and r['id'] == key for r in row.get('record_deletions', []))):
                    continue
                if kind == 'configs' and isinstance(body, str):
                    try:
                        from .runs import parse_config
                        body = parse_config(body)
                    except (ValueError, TypeError):
                        row['blocked'].append('A saved configuration cannot be inspected: ' + key)
                        hard_blocked = True
                refs = list(_paths(body))
                referenced = any(_inside(p, target) or _inside(target, p) for p in refs)
                if referenced:
                    row['blocked'].append('Referenced by ' + kind + ' ' + key + (' in ' + home if home != str(store.home) else ''))
                    dependency = any(d.get('kind') == 'installation' and d.get('id') == key for d in row.get('depends_on', []) if isinstance(d, dict))
                    if home != str(store.home) or kind != 'installs' or not dependency:
                        hard_blocked = True
        row['blocked'] = sorted(set(row['blocked']))
        if hard_blocked:
            row['conditional_blocked'] = False
    return rows, checked


def panel(store):
    rows, checked = _candidates(store)
    return {'candidates': [_public_candidate(r) for r in rows],
            'history': [_receipt_summary(r) for r in store.all(_RECEIPTS)[:30]],
            'active_job': next((j for j in store.jobs() if j['kind'] == 'storage_cleanup' and j['status'] in {'queued', 'running'}), None),
            'checked_workspaces': [str(store.home), *checked], 'notice': NOTICE}


def _public_candidate(row):
    result = {k: row[k] for k in ('kind', 'id', 'label', 'category', 'paths', 'blocked', 'description', 'status', 'logical_bytes', 'allocated_bytes', 'depends_on', 'conditional_blocked') if k in row}
    result['created_at'] = row.get('created_at') or row.get('metadata', {}).get('created')
    return result


def _receipt_summary(row):
    result = {k: row[k] for k in ('id', 'status', 'created_at', 'finished_at', 'targets', 'logical_bytes', 'allocated_bytes', 'error') if k in row}
    result['job_id'] = row.get('recovery_job', row['id'])
    result['targets'] = [{k: c[k] for k in ('kind', 'id', 'label') if k in c} for c in row.get('candidates', row.get('targets', []))]
    return result


def receipt(store, key):
    row = store.get(_RECEIPTS, key)
    if not row:
        raise ValueError('Choose an existing storage cleanup receipt.')
    return {**_receipt_summary(row), 'candidates': [_public_candidate(c) for c in row.get('candidates', [])],
            'operations': [{k: op[k] for k in ('original', 'quarantine', 'state')} for op in row.get('operations', [])],
            'deleted_paths': [op['original'] for op in row.get('operations', []) if op['state'] == 'deleted'],
            'retained_paths': [op['quarantine'] if op['state'] == 'staged' else op['original'] for op in row.get('operations', []) if op['state'] != 'deleted']}


@contextmanager
def _related_locks(store):
    """Exclude related workspaces' copies, workers and DB writers during deletion."""
    from .workflow_unlock import ownership
    _, homes, errors = _other_workspaces(store)
    if errors:
        raise ValueError('; '.join(errors))
    with ExitStack() as stack:
        for home in sorted(homes):
            other = SimpleNamespace(home=Path(home))
            stack.enter_context(_exclusive(other))
            stack.enter_context(ownership(other))
            db = sqlite3.connect((other.home / 'workspace.sqlite3').as_uri() + '?mode=rw', uri=True, timeout=0)
            stack.callback(db.close)
            try:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
                    raise ValueError('A related workspace has queued/running jobs: ' + home)
            except sqlite3.Error as exc:
                raise ValueError('A related workspace is changing. Wait before cleanup: ' + home) from exc
        yield


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink]


def _mounts():
    # st_dev alone misses same-device bind mounts. Linux is required for jobs.
    try:
        text = Path('/proc/self/mountinfo').read_text()
    except OSError as exc:
        raise ValueError('Mount boundaries cannot be verified on this system.') from exc
    return {Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])) for line in text.splitlines()}


def _inventory(value, budget):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise ValueError('Invalid cleanup target path.')
    if any(_inside(p, path) for p in budget['mounts']):
        raise ValueError('A mount point is inside the cleanup target: ' + str(path))
    manifest, logical, allocated, files, links = [], 0, 0, 0, 0
    with _open(path.parent) as parent:
        parent_stamp = _stamp(os.fstat(parent))[:3]
        root = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(root.st_mode):
            raise ValueError('A cleanup root must not be a symbolic link: ' + str(path))

        def visit(fd, name, rel, depth):
            nonlocal logical, allocated, files, links
            budget['entries'] += 1
            if budget['entries'] > MAX_ENTRIES or time.monotonic() - budget['started'] > MAX_SECONDS or depth > MAX_DEPTH:
                raise ValueError('Cleanup preview exceeded its safe scan limit. Select fewer targets.')
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if info.st_dev != root.st_dev:
                raise ValueError('Cleanup cannot cross filesystem boundaries.')
            mode = info.st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                raise ValueError('A special file prevents cleanup: ' + str(path / rel))
            row = {'name': rel, 'stamp': _stamp(info)}
            if stat.S_ISLNK(mode):
                row['link'] = os.readlink(name, dir_fd=fd)
                links += 1
            if stat.S_ISREG(mode):
                files += 1
                identity = (info.st_dev, info.st_ino)
                if identity not in budget['inodes']:
                    budget['inodes'].add(identity)
                    logical += info.st_size
                    allocated += info.st_blocks * 512
            manifest.append(row)
            if stat.S_ISDIR(mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if _stamp(os.fstat(child)) != row['stamp']:
                        raise ValueError('A directory changed during preview.')
                    names = sorted(os.listdir(child))
                    if names and (rel == '.snakemake/locks' or rel.endswith('/.snakemake/locks')):
                        raise ValueError('Native workflow locks remain. Use workflow unlock before cleanup.')
                    for entry in names:
                        visit(child, entry, str(Path(rel) / entry) if rel else entry, depth + 1)
                    if _stamp(os.fstat(child)) != row['stamp']:
                        raise ValueError('A directory changed during preview.')
                finally:
                    os.close(child)
        visit(parent, path.name, '', 0)
    return {'path': str(path), 'kind': 'directory' if stat.S_ISDIR(root.st_mode) else 'file',
            'logical_bytes': logical, 'allocated_bytes': allocated, 'files': files, 'links': links,
            'parent_stamp': parent_stamp, 'manifest': manifest}


def _normalize(targets):
    if not isinstance(targets, list) or not targets or len(targets) > 300:
        raise ValueError('Select between one and 300 cleanup targets.')
    result = []
    for row in targets:
        if not isinstance(row, dict) or not isinstance(row.get('kind'), str) or not isinstance(row.get('id'), str):
            raise ValueError('Choose registered cleanup targets.')
        item = {'kind': row['kind'], 'id': row['id']}
        if item not in result:
            result.append(item)
    return sorted(result, key=lambda r: (r['kind'], r['id']))


def _recovery_paths(receipt):
    result = []
    for operation in receipt.get('operations', []):
        original, quarantine = Path(operation['original']), Path(operation['quarantine'])
        expected = operation['inventory']['manifest'][0]['stamp'][:2]
        found = []
        for path in (original, quarantine):
            try:
                with _open(path.parent) as fd:
                    info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                if _stamp(info)[:2] == expected:
                    found.append(str(path))
                elif path == quarantine or operation.get('state') != 'deleted':
                    raise ValueError('Interrupted cleanup path was replaced; manual recovery is required: ' + str(path))
            except FileNotFoundError:
                pass
        if len(found) > 1:
            raise ValueError('Ambiguous interrupted cleanup paths; manual recovery is required.')
        result.extend(found)
    return result


def _build(store, targets, own_job=None):
    targets = _normalize(targets)
    candidates, checked = _candidates(store, targets, own_job)
    indexed = {(r['kind'], r['id']): r for r in candidates}
    selected, blocked = [], []
    for target in targets:
        row = indexed.get((target['kind'], target['id']))
        if row is None:
            raise ValueError('A cleanup target no longer exists. Refresh Storage.')
        row = dict(row)
        if row['kind'] == 'cleanup_remaining':
            try:
                row['paths'] = _recovery_paths(row['recovery'])
            except (OSError, ValueError) as exc:
                row['blocked'] = [*row['blocked'], str(exc)]
        selected.append(row)
        blocked.extend(row['blocked'])
    pairs = {(r['kind'], r['id']) for r in selected}
    if any(k == 'sample_files' and ('sample_registration', i) in pairs for k, i in pairs):
        blocked.append('Choose either unregister or delete original files for each sample, not both.')
    values = sorted(set(p for row in selected for p in row['paths']))
    values = [p for p in values if not any(p != other and _inside(Path(p), Path(other)) for other in values)]
    inventories = []
    budget = {'started': time.monotonic(), 'entries': 0, 'inodes': set(), 'mounts': _mounts()}
    for value in values:
        try:
            inventories.append(_inventory(value, budget))
        except (OSError, ValueError) as exc:
            blocked.append(str(exc))
    result = {'targets': targets, 'candidates': selected, 'inventories': inventories, 'blocked': sorted(set(blocked)),
              'logical_bytes': sum(r['logical_bytes'] for r in inventories),
              'allocated_bytes': sum(r['allocated_bytes'] for r in inventories), 'checked_workspaces': [str(store.home), *checked]}
    result['signature'] = _hash(result)
    return result


def _public_plan(plan):
    return {**{k: plan[k] for k in ('id', 'signature', 'targets', 'blocked', 'logical_bytes', 'allocated_bytes', 'checked_workspaces')},
            'candidates': [_public_candidate(r) for r in plan['candidates']],
            'paths': [{k: r[k] for k in ('path', 'kind', 'logical_bytes', 'allocated_bytes', 'files', 'links')} for r in plan['inventories']],
            'confirmation': CONFIRMATION, 'requires_acknowledgment': True, 'notice': NOTICE}


def preview(store, targets):
    with _exclusive(store):
        plan = _build(store, targets)
        plan.update(id=identifier('cleanup-plan'), created_at=now())
        store.put(_PLANS, plan['id'], plan)
        _prune_plans(store)
    return _public_plan(plan)


def _prune_plans(store, completed=None):
    """Keep only recent review manifests and plans awaiting execution."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = {json.loads(r[0]).get('plan_id') for r in db.execute(
            "SELECT payload FROM jobs WHERE kind='storage_cleanup' AND status IN ('queued','running')")}
        keys = [r[0] for r in db.execute('SELECT id FROM items WHERE kind=? ORDER BY rowid DESC', (_PLANS,))]
        for key in keys[3:]:
            if key not in active:
                db.execute('DELETE FROM items WHERE kind=? AND id=?', (_PLANS, key))
        if completed:
            db.execute('DELETE FROM items WHERE kind=? AND id=?', (_PLANS, completed))


def _verify(store, plan, own_job=None):
    current = _build(store, plan['targets'], own_job)
    if current['blocked']:
        raise ValueError('Cleanup is blocked: ' + '; '.join(current['blocked']))
    if current['signature'] != plan['signature']:
        raise ValueError('Files or dependencies changed since preview. Review a new cleanup preview.')
    return current


def _check_processes(paths):
    """Protect manual same-user work as well as the worker's inherited lock."""
    import psutil
    from .workflow_unlock import protected_session_helper
    roots = [Path(p) for p in paths]
    if not roots:
        return
    for proc in psutil.process_iter():
        if proc.pid == os.getpid():
            continue
        try:
            owners = proc.uids()
            if os.getuid() not in (owners.real, owners.effective, owners.saved):
                continue
            refs = [Path(proc.cwd()), Path(proc.exe()), *(Path(f.path) for f in proc.open_files())]
            if any(_inside(p, root) for p in refs for root in roots):
                raise ValueError('Process ' + str(proc.pid) + ' is using a selected cleanup path. Stop it before cleanup.')
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied as exc:
            if protected_session_helper(proc, os.getuid()):
                continue
            raise ValueError('Cannot inspect a user process; cleanup is blocked until ownership can be verified.') from exc


def request(store, plan_id, signature, confirmation, acknowledged):
    if confirmation != CONFIRMATION or acknowledged is not True:
        raise ValueError('Verify your transferred copies or intentionally discard these files, then type DELETE.')
    plan = store.get(_PLANS, plan_id)
    if not plan or signature != plan['signature']:
        raise ValueError('Review a current cleanup preview before deleting files.')
    from .engine import launch_worker
    with _exclusive(store):
        _verify(store, plan)
        _check_processes([r['path'] for r in plan['inventories']])
        jid = store.submit('storage_cleanup', {'plan_id': plan_id, 'signature': signature})
    launch_worker(store)
    return jid


def _write_receipt(store, receipt):
    persisted = receipt
    if receipt.get('status') == 'completed':
        persisted = {**receipt, 'operations': [{**op, 'inventory': {
            k: v for k, v in op['inventory'].items() if k not in {'manifest', 'parent_stamp'}}}
            for op in receipt['operations']]}
    store.put(_RECEIPTS, receipt['id'], persisted)


def _retire_records(store, receipt):
    """Tombstones and removal of live records share one SQLite transaction."""
    removed = list(receipt.get('removed_records', []))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for candidate in receipt['candidates']:
            for record in candidate.get('record_deletions', []):
                row = db.execute('SELECT body FROM items WHERE kind=? AND id=?', (record['kind'], record['id'])).fetchone()
                if row:
                    old = json.loads(row[0])
                    if 'expected' in record and old != record['expected']:
                        raise ValueError('A workspace record changed during cleanup; recovery is required.')
                    removed.append({'kind': record['kind'], 'id': record['id'], 'body': old})
                    db.execute('DELETE FROM items WHERE kind=? AND id=?', (record['kind'], record['id']))
                    if record['kind'] == 'runs':
                        _put(db, 'deleted_runs', record['id'], {'id': record['id'], 'cleanup': receipt['id'], 'deleted_at': now(), 'run': old})
            for record in candidate.get('record_updates', []):
                _put(db, record['kind'], record['id'], record['body'])
        _put(db, _RECEIPTS, receipt['id'], {**receipt, 'records_retired': True, 'removed_records': removed})
    # Keep success flags out of error receipts if SQLite COMMIT failed.
    receipt.update(records_retired=True, removed_records=removed)


def _stage(operation):
    original, quarantine = Path(operation['original']), Path(operation['quarantine'])
    with _open(original.parent) as fd:
        if _stamp(os.fstat(fd))[:3] != operation['inventory']['parent_stamp']:
            raise ValueError('A cleanup parent directory changed.')
        info = os.stat(original.name, dir_fd=fd, follow_symlinks=False)
        expected = operation['inventory']['manifest'][0]['stamp']
        actual = _stamp(info)
        # Renaming another selected hard link changes this inode's ctime too.
        if actual[:5] != expected[:5] or not 0 < actual[6] <= expected[6]:
            raise ValueError('A cleanup target changed before staging.')
        try:
            os.stat(quarantine.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('The cleanup staging path already exists.')
        os.rename(original.name, quarantine.name, src_dir_fd=fd, dst_dir_fd=fd)
        moved = os.stat(quarantine.name, dir_fd=fd, follow_symlinks=False)
        if _stamp(moved)[:2] != _stamp(info)[:2]:
            raise ValueError('The cleanup target changed during staging; manual recovery is required.')
        os.fsync(fd)
    operation['state'] = 'staged'


def _erase(operation, check_cancel):
    """Unlink only reviewed identities, with no-follow descriptors at every level."""
    path = Path(operation['quarantine'])
    rows = {r['name']: r for r in operation['inventory']['manifest']}
    if any(_inside(m, path) for m in _mounts()):
        raise ValueError('A mount point appeared inside the cleanup target.')
    root_stamp = rows['']['stamp']

    def remove(fd, name, rel):
        check_cancel()
        expected = rows[rel]['stamp']
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        actual = _stamp(info)
        # Rename changes root ctime. Directory mtimes change as children are removed.
        if actual[:3] != expected[:3] or (not stat.S_ISDIR(info.st_mode) and
                (actual[3:5] != expected[3:5] or not 0 < actual[6] <= expected[6])):
            raise ValueError('A reviewed file changed during cleanup: ' + str(path / rel))
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if _stamp(os.fstat(child))[:3] != expected[:3]:
                    raise ValueError('A cleanup directory changed.')
                for entry in sorted(os.listdir(child)):
                    item = str(Path(rel) / entry) if rel else entry
                    if item not in rows:
                        raise ValueError('An unreviewed file appeared during cleanup: ' + str(path / item))
                    remove(child, entry, item)
                os.fsync(child)
            finally:
                os.close(child)
            if _stamp(os.stat(name, dir_fd=fd, follow_symlinks=False))[:3] != expected[:3]:
                raise ValueError('A cleanup directory was replaced.')
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)

    with _open(path.parent) as parent:
        if _stamp(os.fstat(parent))[:3] != operation['inventory']['parent_stamp']:
            raise ValueError('A cleanup parent was replaced.')
        if _stamp(os.stat(path.name, dir_fd=parent, follow_symlinks=False))[:3] != root_stamp[:3]:
            raise ValueError('A staged cleanup target was replaced.')
        remove(parent, path.name, '')
        os.fsync(parent)
    operation['state'] = 'deleted'


def execute(ctx):
    """Run in the serialized worker; inherited worker locks protect native children."""
    from contextlib import nullcontext
    from .workflow_unlock import ownership
    store, payload = ctx.store, ctx.job['payload']
    worker_guard = nullcontext() if getattr(ctx, 'lock_fd', None) is not None else ownership(store)
    with worker_guard, _exclusive(store), _related_locks(store):
        plan = store.get(_PLANS, payload.get('plan_id'))
        if not plan or plan['signature'] != payload.get('signature'):
            raise ValueError('The reviewed cleanup plan is unavailable.')
        _verify(store, plan, ctx.job['id'])
        _check_processes([r['path'] for r in plan['inventories']])
        recovering = plan['candidates'][0]['kind'] == 'cleanup_remaining'
        if recovering:
            receipt = store.get(_RECEIPTS, plan['candidates'][0]['id'])
            inventories = {r['path']: r for r in plan['inventories']}
            for operation in receipt['operations']:
                if operation['quarantine'] in inventories:
                    operation['inventory'] = inventories[operation['quarantine']]
                    operation['state'] = 'staged'
                elif operation['original'] in inventories:
                    operation['inventory'] = inventories[operation['original']]
                    operation['state'] = 'planned'
                else:
                    operation['state'] = 'deleted'
            receipt['recovery_job'] = ctx.job['id']
        else:
            receipt = {'id': ctx.job['id'], 'created_at': now(), 'status': 'staging', 'targets': plan['targets'],
                       'candidates': plan['candidates'], 'logical_bytes': plan['logical_bytes'],
                       'allocated_bytes': plan['allocated_bytes'], 'operations': []}
            for index, inventory in enumerate(plan['inventories']):
                path = Path(inventory['path'])
                receipt['operations'].append({'original': str(path),
                    'quarantine': str(path.parent / ('.bactologix-cleanup-' + receipt['id'] + '-' + str(index))),
                    'inventory': inventory, 'state': 'planned'})
        receipt['status'] = 'staging'
        receipt.pop('error', None)
        _write_receipt(store, receipt)
        try:
            for operation in receipt['operations']:
                ctx.check_cancel()
                if operation['state'] == 'planned':
                    _stage(operation)
                    _write_receipt(store, receipt)
            if not receipt.get('records_retired'):
                _retire_records(store, receipt)
            receipt['status'] = 'deleting'
            _write_receipt(store, receipt)
            for operation in receipt['operations']:
                if operation['state'] != 'deleted':
                    ctx.write('Permanently deleting reviewed files: ' + operation['original'])
                    _erase(operation, ctx.check_cancel)
                    _write_receipt(store, receipt)
            receipt.update(status='completed', finished_at=now())
            _write_receipt(store, receipt)
        except BaseException as exc:
            receipt.update(status='partial', error=str(exc) or type(exc).__name__)
            _write_receipt(store, receipt)
            raise
        try:
            _prune_plans(store, plan['id'])
        except (sqlite3.Error, OSError):
            pass  # A cache-pruning failure cannot undo a completed deletion.
        ctx.write('Storage cleanup completed. Original records and deletion paths are retained in the receipt.')
    return _receipt_summary(receipt)
