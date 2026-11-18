"""Display optimization never substitutes for deletion authorization checks."""
import json
from pathlib import Path

import pytest

from bactologix import storage_cleanup as cleanup, storage_resources as resources
from test_storage_resources import environment, managed


@pytest.fixture
def unused_environment(managed):
    store, item, old = managed
    checks = store.get('environment_checks', item['id'])
    retired = {**item, 'id': 'old-install', 'source': str(store.home / 'sources/old-install')}
    store.put(resources.OWNER_KIND, retired['id'], {
        'id': retired['id'], 'installation': retired, 'environment_checks': checks})
    payload = {**store.jobs()[0]['payload'], 'installation': retired['id']}
    jid = store.submit('environments', {**payload, 'installation': item['id']})
    with store.connect() as db:
        db.execute('UPDATE jobs SET payload=?,status=? WHERE id=?',
                   (json.dumps(payload), 'succeeded', jid))
    current = environment(old.parent / ('c' * 32 + '_'))
    checks['assembly']['path'] = str(current)
    store.put('environment_checks', item['id'], checks)
    return store, item, old


def old_environment(rows, old):
    return next(row for row in rows if row['kind'] == 'environment' and str(old) in row['paths'])


def test_display_reuses_source_check_only_within_one_invocation(unused_environment, monkeypatch):
    store, item, old = unused_environment
    actual = resources.git_read
    statuses = []

    def counted(path, *args):
        if args[0] == 'status':
            statuses.append(str(path))
        return actual(path, *args)

    monkeypatch.setattr(resources, 'git_read', counted)
    before = resources.candidates(store, display_only=True)
    assert not old_environment(before, old)['blocked']
    assert statuses == [item['source']], 'Display must not repeat the full ignored/untracked Git walk'

    Path(item['source'], 'private-output.txt').write_text('retain this generated output')
    after = resources.candidates(store, display_only=True)
    assert old_environment(after, old)['blocked']
    assert statuses == [item['source'], item['source']], 'A new request must perform a new source check'


def test_default_authority_rechecks_source_changed_between_checks(unused_environment, monkeypatch):
    store, item, old = unused_environment
    actual = resources.git_read
    statuses = []
    generated = Path(item['source'], 'private-output.txt')

    def source_changes(path, *args):
        value = actual(path, *args)
        if args[0] == 'status':
            statuses.append(str(path))
            if len(statuses) == 1:
                generated.write_text('retain this generated output')
        return value

    monkeypatch.setattr(resources, 'git_read', source_changes)
    result = resources.candidates(store)
    assert statuses == [item['source'], item['source']]
    assert old_environment(result, old)['blocked'], 'Changed source cannot prove cache exclusivity'
    assert old.is_dir() and generated.read_text() == 'retain this generated output'


def test_cleanup_panel_optimization_never_authorizes_later_preview(unused_environment, monkeypatch):
    store, item, old = unused_environment
    monkeypatch.setattr('psutil.process_iter', lambda: [])
    actual = resources.candidates
    modes = []

    def observed(*args, **kwargs):
        modes.append(kwargs.get('display_only', False))
        return actual(*args, **kwargs)

    monkeypatch.setattr(resources, 'candidates', observed)
    row = next(row for row in cleanup.panel(store)['candidates'] if row['kind'] == 'installation')
    assert not row['blocked'] and modes == [True]
    Path(item['source'], 'private-output.txt').write_text('retain this generated output')
    preview = cleanup.preview(store, [{'kind': row['kind'], 'id': row['id']}])
    assert modes[-1] is False and preview['blocked']
    assert old.is_dir()
