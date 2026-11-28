"""A passive refresh keeps control readings current without reopening old log content."""
import os
from pathlib import Path
from unittest.mock import patch

from test_native import installation, sample, workspace
from bactologix import support
from bactologix.store import Store
from bactologix.ui_backend import snapshot


def test_unchanged_failed_logs_are_not_reread_and_changed_evidence_is_observed(tmp_path):
    store = Store(tmp_path)
    jobs = [store.submit('smoke', {}) for _ in range(25)]
    for jid in jobs:
        store.update_job(jid, status='failed')
        store.log_path(jid).write_text('LockException: conflict\n')
    log = store.log_path(jobs[0])
    with patch.object(support, 'read_log_tail', wraps=support.read_log_tail) as read:
        first = snapshot(store)
        assert read.call_count == len(jobs)
        assert all(j['guidance']['category'] == 'workflow_lock' for j in first['jobs'])
        read.reset_mock()
        with patch.object(store, 'connect', wraps=store.connect) as connections:
            snapshot(store)
            baseline = connections.call_count
        read.assert_not_called()

        old = log.stat()
        replacement = log.with_suffix('.new')
        replacement.write_text('Permission denied'.ljust(old.st_size))
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        replacement.replace(log)
        extra = store.submit('smoke', {})
        store.update_job(extra, status='failed')
        with patch.object(store, 'connect', wraps=store.connect) as connections:
            second = snapshot(store)
            assert connections.call_count == baseline
        by_id = {j['id']: j for j in second['jobs']}
        assert by_id[jobs[0]]['guidance']['category'] == 'permissions'
        assert by_id[extra]['guidance']['log_status'] == 'not found'
        assert read.call_count == 2

        # A redirected log must not expose an old excerpt or read the destination.
        secret = Path(tmp_path) / 'external.log'
        secret.write_text('secret data')
        log.unlink()
        log.symlink_to(secret)
        record = next(j for j in snapshot(store)['jobs'] if j['id'] == jobs[0])
        assert record['guidance']['category'] == 'log'
        assert 'unavailable' in record['guidance']['log_status']
        assert 'secret data' not in str(record['guidance'])


def test_monitor_receipts_are_reread_in_bulk_and_control_transitions_clear_old_guidance(tmp_path):
    store = Store(tmp_path)
    jid = store.submit('workflow', {'action': 'run', 'run_id': 'run-example'})
    store.update_job(jid, status='interrupted')
    store.log_path(jid).write_text('Stopped\n')
    assert snapshot(store)['jobs'][0]['guidance']['category'] == 'interrupted'
    store.put('disk_safety', jid, {'id': jid, 'state': 'finished', 'reason': 'pause_failed',
                                  'pause_trigger': 'stale', 'checked_at': '2000-01-01T00:00:00+00:00',
                                  'can_resume': True})
    row = snapshot(store)['jobs'][0]
    assert row['guidance']['category'] == 'storage_pause'
    assert not row['disk_safety']['can_resume']
    store.put('disk_safety', jid, {'id': jid, 'state': 'finished'})
    assert snapshot(store)['jobs'][0]['guidance']['category'] == 'interrupted'


def test_workspace_refresh_does_not_rehash_checked_source_or_force_a_hardware_probe(workspace, installation, sample):
    from bactologix import compute_memory, hardware, readiness, runs
    from bactologix.ui_backend import dispatch

    run = runs.create_run(workspace, 'hybas', sample, compute_memory.with_swap('{}', 2048))
    with patch.object(readiness, 'tree_digest', wraps=readiness.tree_digest) as hash_source, \
            patch.object(hardware, 'detect', wraps=hardware.detect) as probe:
        dispatch(workspace, 'inspect_run', {'id': run['id']})
        assert hash_source.call_count >= 1 and probe.call_count >= 1
        hash_source.reset_mock()
        probe.reset_mock()
        for _ in range(2):
            dispatch(workspace, 'refresh', {'id': run['id']})
            snapshot(workspace)
        hash_source.assert_not_called()
        probe.assert_not_called()
