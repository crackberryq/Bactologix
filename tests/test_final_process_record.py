import sqlite3
import sys

import pytest

from bactologix import engine, supervision
from test_supervision import fixture


@pytest.fixture
def command_context(fixture, monkeypatch):
    store, jid, marker, command, _ = fixture
    monkeypatch.setattr(engine, 'WORKFLOW_CONTROL_GRACE_SECONDS', .12)
    ctx = engine.Context(store, store.claim())
    try:
        yield ctx, store, jid, marker, command
    finally:
        ctx.disk_guard.finish('interrupted')
        ctx.log.close()


def lock_final_writes(monkeypatch, ctx, *, failures=None):
    original = ctx.store.put_process
    states = []

    def put(jid, record):
        if 'observed_returncode' in record:
            states.append(ctx.monitor['state'])
            if failures is None or len(states) <= failures:
                raise sqlite3.OperationalError('database is locked')
        return original(jid, record)

    monkeypatch.setattr(ctx.store, 'put_process', put)
    return states


@pytest.mark.parametrize('code', [0, 7])
def test_final_write_failure_reports_observed_exit(command_context, monkeypatch, code):
    ctx, store, jid, _, _ = command_context
    writes = lock_final_writes(monkeypatch, ctx)
    with pytest.raises(supervision.MonitoringInterrupted) as caught:
        ctx.command([sys.executable, '-c', f'raise SystemExit({code})'])
    assert len(writes) >= 2 and caught.value.cleanup_confirmed
    assert f'exited with code {code}' in str(caught.value)
    assert 'record could not be saved' in str(caught.value)
    monitor = supervision.snapshot(store, jid)
    assert monitor['state'] == 'interrupted' and monitor['command_returncode'] == code
    assert 'database is locked' in monitor['persistence_error']
    record = supervision.snapshot(store, jid, 'process')
    assert record['observed_returncode'] == record['returncode'] == code


@pytest.mark.parametrize('failures', [1, None])
def test_final_write_after_monitor_interruption_preserves_original_reason(command_context, monkeypatch, failures):
    ctx, store, jid, marker, command = command_context
    writes = lock_final_writes(monkeypatch, ctx, failures=failures)
    tick = ctx.disk_guard.tick

    def interrupt():
        if marker.exists():
            raise supervision.MonitoringInterrupted('Control read interrupted during native work.')
        return tick()

    monkeypatch.setattr(ctx.disk_guard, 'tick', interrupt)
    with pytest.raises(supervision.MonitoringInterrupted) as caught:
        ctx.command(command)
    assert len(writes) >= 2 and set(writes) == {'interrupted'}
    assert 'Control read interrupted during native work.' in str(caught.value)
    assert caught.value.cleanup_confirmed
    monitor = supervision.snapshot(store, jid)
    assert monitor['state'] == 'interrupted' and monitor['command_returncode'] is None
    record = supervision.snapshot(store, jid, 'process')
    assert record['observed_returncode'] is None and record['returncode'] is not None
    assert not supervision.remaining(record['identities'])
    if failures is None:
        assert 'record could not be saved' in str(caught.value)
        assert 'database is locked' in monitor['persistence_error']
    else:
        assert store.get('processes', jid)['pid'] is None
        assert 'persistence_error' not in monitor


def test_unconfirmed_cleanup_retains_native_identity(command_context, monkeypatch):
    ctx, store, jid, _, _ = command_context
    monkeypatch.setattr(supervision, 'verify_exit', lambda identities: False)
    writes = lock_final_writes(monkeypatch, ctx)
    with pytest.raises(supervision.MonitoringInterrupted) as caught:
        ctx.command([sys.executable, '-c', 'raise SystemExit(7)'])
    assert writes == ['interrupted'] and not caught.value.cleanup_confirmed
    assert 'exited with code 7' in str(caught.value)
    assert 'cleanup could not be confirmed' in str(caught.value)
    assert 'record could not be saved' in str(caught.value)
    record = supervision.snapshot(store, jid, 'process')
    assert not record['cleanup_confirmed'] and record['observed_returncode'] == 7
    assert store.get('processes', jid)['pid'] == record['pid']
