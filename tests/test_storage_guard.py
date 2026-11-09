"""Deletion cannot overlap unregistered drafts or new workspace references."""
import subprocess
import sys

import pytest

from bactologix.storage_guard import activity, exclusive
from bactologix.store import Store


def test_cleanup_rejects_active_draft_in_other_process(tmp_path):
    store = Store(tmp_path / 'workspace')
    script = '''
import sys
from bactologix.store import Store
from bactologix.storage_guard import activity
with activity(Store(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.readline()
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(store.home)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(ValueError, match='workspace operation'):
            store.submit('storage_cleanup', {})
        assert not store.jobs()
    finally:
        child.communicate('\n', timeout=10)
    assert child.returncode == 0
    assert store.submit('storage_cleanup', {})


def test_queued_cleanup_blocks_jobs_and_new_references(tmp_path):
    store = Store(tmp_path)
    jid = store.submit('storage_cleanup', {})
    with pytest.raises(ValueError, match='cleanup'):
        store.submit('smoke', {})
    with pytest.raises(ValueError, match='cleanup'):
        store.put('samples', 'new', {'id': 'new'})
    store.put('job_telemetry', jid, {'id': jid})
    store.cancel(jid)
    store.put('samples', 'new', {'id': 'new'})
    assert store.get('samples', 'new') == {'id': 'new'}


def test_cleanup_rejects_pending_work_and_reentrant_upgrade(tmp_path):
    store = Store(tmp_path)
    jid = store.submit('smoke', {})
    with pytest.raises(ValueError, match='queued/running'):
        store.submit('storage_cleanup', {})
    store.cancel(jid)
    with activity(store):
        with activity(store):
            store.put('samples', 'new', {'id': 'new'})
        with pytest.raises(ValueError, match='workspace operation'):
            with exclusive(store):
                pass
    with exclusive(store):
        assert store.submit('storage_cleanup', {})


def test_activity_lock_does_not_follow_symlink(tmp_path):
    store = Store(tmp_path / 'workspace')
    victim = tmp_path / 'original'
    victim.write_text('unchanged')
    (store.home / 'storage-activity.lock').symlink_to(victim)
    with pytest.raises(OSError):
        with activity(store):
            pass
    assert victim.read_text() == 'unchanged'
