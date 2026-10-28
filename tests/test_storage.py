"""Storage scans are bounded, metadata-only and never authorize cleanup."""
import json
import os
from pathlib import Path

import pytest

from bactologix import storage as s
from bactologix.store import Store
from bactologix.ui_backend import dispatch


def file(path, size=13):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    return path


def run(store, rid, folder, output=None, **kwargs):
    row = {'id': rid, 'tool': 'hybas', 'folder': str(folder), 'output': str(output or folder / 'outputs'),
           'label': rid, 'config': '{}\n', **kwargs}
    store.put('runs', rid, row)
    return row


def scan(store, external=False):
    return s.scan(store, s.catalog(store)['revision'], external)


def location(result, path):
    return next(r for r in result['locations'] if r['path'] == str(path))


def test_opening_overview_does_not_walk_and_scan_never_reads_contents(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    original = file(store.home / 'runs/one/outputs/report.html')
    before = original.read_bytes()
    row = run(store, 'run-one', original.parent.parent)
    real = s.os.scandir
    monkeypatch.setattr(s.os, 'scandir', lambda *a: (_ for _ in ()).throw(AssertionError('implicit scan')))
    assert dispatch(store, 'inspect_storage', {})['storage']['last_scan'] is None
    monkeypatch.setattr(s.os, 'scandir', real)
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_bytes', lambda *a: (_ for _ in ()).throw(AssertionError('content read')))
        m.setattr(Path, 'read_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('content read')))
        result = scan(store)
    assert location(result, original.parent)['logical_bytes'] == 13
    assert original.read_bytes() == before and store.get('runs', 'run-one') == row
    assert not store.jobs()
    assert Store(store.home).get('storage', 'latest') == result
    assert json.loads(s.download(store)['text']) == result


def test_nested_and_shared_paths_and_hardlinks_are_counted_once(tmp_path):
    store = Store(tmp_path / 'workspace')
    folder = store.home / 'runs/one'
    output = folder / 'outputs'
    file(folder / 'config.yaml', 7)
    first = file(output / 'report', 23)
    os.link(first, output / 'same-report')
    run(store, 'run-one', folder)
    run(store, 'run-two', folder, output)
    store.put('run_visibility', 'run-two', {'id': 'run-two', 'archived': True})
    result = scan(store)
    assert location(result, output)['logical_bytes'] == 23
    assert location(result, folder)['logical_bytes'] == 7
    assert location(result, store.home / 'runs')['logical_bytes'] == 0
    assert len(location(result, output)['references']) == 2
    assert any('archived' in r['label'] for r in location(result, output)['references'])
    assert result['counters']['hardlinks'] >= 1
    assert result['logical_bytes'] == sum(r['logical_bytes'] for r in result['locations'])


def test_external_locations_are_opt_in_and_never_follow_links(tmp_path):
    store = Store(tmp_path / 'workspace')
    external = tmp_path / 'external'
    p = file(external / 'large', 101)
    link = store.home / 'runs/link'
    link.symlink_to(external, target_is_directory=True)
    run(store, 'run-ext', external, external)
    result = scan(store)
    assert location(result, external)['state'] == 'not_selected'
    assert result['counters']['links'] == 1
    result = scan(store, True)
    assert location(result, external)['logical_bytes'] == 101
    # A registered root with a linked parent is refused as well.
    run(store, 'run-link', link / 'sub', link / 'large')
    result = scan(store, True)
    assert location(result, link / 'large')['state'] == 'unavailable'
    assert result['partial'] and p.read_bytes() == b'x' * 101


def test_retained_repairs_sparse_files_and_capacity_deduplication(tmp_path):
    store = Store(tmp_path / 'workspace')
    cache = store.home / 'envs/hybas'
    file(cache / s.BACKUPS / 'job-one/env', 91)
    sparse = cache / 'sparse'
    with sparse.open('wb') as f:
        f.seek(1024 * 1024); f.write(b'x')
    result = scan(store)
    backup = location(result, cache / s.BACKUPS)
    assert backup['category'] == 'backups' and backup['logical_bytes'] == 91
    env = location(result, store.home / 'envs')
    assert env['logical_bytes'] == 1024 * 1024 + 1
    assert env['allocated_bytes'] == sparse.stat().st_blocks * 512
    assert len(result['devices']) == 1


@pytest.mark.parametrize('limit', ['entries', 'depth', 'time', 'locations'])
def test_limits_are_explicit_and_partial(tmp_path, monkeypatch, limit):
    store = Store(tmp_path / 'workspace')
    file(store.home / 'runs/a/b/c/d/e/f')
    if limit == 'entries': monkeypatch.setattr(s, 'MAX_ENTRIES', 1)
    elif limit == 'depth': monkeypatch.setattr(s, 'MAX_DEPTH', 1)
    elif limit == 'time': monkeypatch.setattr(s, 'MAX_SECONDS', 0)
    else: monkeypatch.setattr(s, 'MAX_LOCATIONS', 2)
    result = scan(store)
    assert result['partial']
    assert result['limits_reached'] or result['omitted'] or result['counters']['depth']


def test_inaccessible_special_and_refused_roots(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    os.mkfifo(store.home / 'runs/fifo')
    inaccessible = store.home / 'runs/private'; inaccessible.mkdir()
    original = s.os.scandir
    inode = inaccessible.stat().st_ino
    def denied(fd):
        if os.fstat(fd).st_ino == inode: raise PermissionError('fixture')
        return original(fd)
    monkeypatch.setattr(s.os, 'scandir', denied)
    run(store, 'run-root', Path('/'), Path('/proc'))
    result = scan(store, True)
    assert result['counters']['special'] == 1
    assert result['counters']['inaccessible'] >= 1 and result['partial']
    assert location(result, Path('/'))['state'] == location(result, Path('/proc'))['state'] == 'refused'


def test_registered_file_database_and_saved_references(tmp_path):
    store = Store(tmp_path / 'workspace')
    database = file(tmp_path / 'database/card.json', 43)
    store.put('configs', 'basis', 'modules:\n  rgi:\n    database: ' + str(database) + '\n    database_version: v2026\n')
    store.put('database_locations', 'basis', {'rgi': {'path': str(database.parent)}})
    result = scan(store, True)
    assert location(result, database)['logical_bytes'] == 43
    assert location(result, database.parent)['logical_bytes'] == 0
    assert all(r['category'] == 'databases' for r in result['locations'] if r['external'])
    assert not any('v2026' in r['path'] for r in result['locations'])


def test_scope_changes_and_input_validation(tmp_path):
    store = Store(tmp_path / 'workspace')
    before = s.catalog(store)['revision']
    scan(store)
    run(store, 'run-new', store.home / 'runs/new')
    assert s.panel(store)['scope_changed']
    with pytest.raises(ValueError, match='changed'): s.scan(store, before)
    for value in (None, [], 'yes', 1):
        with pytest.raises(ValueError): s.scan(store, s.catalog(store)['revision'], value)
    empty = Store(tmp_path / 'empty')
    with pytest.raises(ValueError): s.download(empty)


def test_active_work_and_shared_category_do_not_become_cleanup_permission(tmp_path):
    store = Store(tmp_path / 'workspace')
    shared = tmp_path / 'shared'
    file(shared / 'one', 25)
    run(store, 'run-one', shared)
    store.put('database_downloads', 'db-one', {'id': 'db-one', 'destination': str(shared)})
    jid = store.submit('smoke', {}); store.claim()
    result = scan(store, True)
    assert result['active_jobs_at_start'] == [jid]
    assert location(result, shared)['category'] == 'shared'
    assert 'exclusive ownership' in result['notice']
    assert 'reclaimable' in result['notice'] and 'delete' not in s.download(store)['name']


def test_unregistered_device_crossing_is_skipped(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    store = Store(tmp_path / 'workspace')
    file(store.home / 'runs/other-device', 901)
    original = s.os.scandir
    @contextmanager
    def crossing(fd):
        with original(fd) as entries:
            def wrapped():
                for entry in entries:
                    if entry.name == 'other-device':
                        st = entry.stat(follow_symlinks=False)
                        yield SimpleNamespace(name=entry.name, stat=lambda **kw: SimpleNamespace(st_mode=st.st_mode, st_dev=st.st_dev + 1))
                    else:
                        yield entry
            yield wrapped()
    monkeypatch.setattr(s.os, 'scandir', crossing)
    result = scan(store)
    assert result['counters']['mounts'] == 1 and result['partial']
    assert location(result, store.home / 'runs')['logical_bytes'] == 0


def test_scan_never_unlinks_or_renames_and_rejects_stale_dispatch(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    p = file(store.home / 'envs/retain', 19)
    def forbidden(*args, **kwargs): raise AssertionError('destructive operation')
    with monkeypatch.context() as m:
        m.setattr(os, 'unlink', forbidden)
        m.setattr(os, 'rename', forbidden)
        m.setattr(os, 'rmdir', forbidden)
        scan(store)
    assert p.stat().st_size == 19
    with pytest.raises(ValueError, match='changed'):
        dispatch(store, 'scan_storage', {'revision': 'stale'})


def test_invalid_relative_references_are_omitted_not_resolved_against_cwd(tmp_path):
    store = Store(tmp_path / 'workspace')
    run(store, 'run-relative', Path('relative'), Path('/tmp/../private'))
    current = s.catalog(store)
    assert current['omitted'] == 2
    assert all(Path(r['path']).is_absolute() and '..' not in Path(r['path']).parts for r in current['locations'])
    assert scan(store)['partial']


def test_discovered_backup_at_location_limit_is_not_misclassified(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    file(store.home / 'envs/orphan' / s.BACKUPS / 'retained', 111)
    monkeypatch.setattr(s, 'MAX_LOCATIONS', 7)
    assert not s.catalog(store)['omitted']
    result = scan(store)
    assert result['partial'] and 'location limit' in result['limits_reached']
    assert location(result, store.home / 'envs')['logical_bytes'] == 0
