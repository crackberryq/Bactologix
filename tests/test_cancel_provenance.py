"""Show the control observation that stopped the currently owned native command."""
import json
import time

import pytest

from bactologix import desktop, engine, supervision
from test_supervision import background, fixture, wait_for


@pytest.mark.parametrize(('trigger', 'expected'), [
    ('job', 'A stop request was recorded for this job.'),
    ('close', 'The owning desktop session requested a stop (Window closed).'),
    ('expiry', 'A stop request was recorded for this job.'),
    ('stopping-proof', 'The owning desktop session signalled a stop.'),
    ('proof-with-reason', 'The owning desktop session requested a stop (Desktop disconnected).'),
])
def test_stop_reason_survives_native_teardown(fixture, trigger, expected):
    store, jid, marker, command, _ = fixture
    owner = 'd' * 32
    session = {'phase': 'ready', 'last_seen': time.time(), 'cancelled': False}
    store.put('desktop_sessions', owner, session)
    store.put('job_owners', jid, owner)
    ctx = engine.Context(store, store.claim())
    thread, outcome = background(ctx, command)
    try:
        wait_for(marker.exists)
        if trigger == 'job':
            store.cancel(jid)
        elif trigger == 'close':
            session.update(phase='stopping', reason='Window closed', cancelled=True)
            with store.connect() as db:
                db.execute("UPDATE items SET body=? WHERE kind='desktop_sessions' AND id=?",
                           (json.dumps(session), owner))
                db.execute('UPDATE jobs SET cancel=1 WHERE id=?', (jid,))
        elif trigger == 'expiry':
            store.put('desktop_sessions', owner, dict(session, last_seen=time.time() - 100))
            assert not ctx.refresh_desktop_cancel()
            assert thread.is_alive()
            store.cancel(jid)
        else:
            proof = {'session': owner, 'stopping': True}
            if trigger == 'proof-with-reason':
                proof['reason'] = 'Desktop disconnected'
            desktop.atomic(desktop._lease_path(store, owner), proof)

        thread.join(7)
        assert not thread.is_alive()
        assert len(outcome) == 1 and isinstance(outcome[0], engine.Cancelled)
        assert expected in str(outcome[0])
        assert expected in store.log_path(jid).read_text()
        assert 'Stopped by user' not in str(outcome[0])
        assert supervision.snapshot(store, jid, 'process')['cleanup_confirmed']
        with pytest.raises(engine.Cancelled) as repeated:
            ctx.check_cancel_only()
        assert str(repeated.value) == str(outcome[0])
    finally:
        store.cancel(jid)
        thread.join(7)
        ctx.disk_guard.finish('cancelled')
        ctx.log.close()


def test_explicit_job_stop_takes_precedence_over_unreadable_proof(fixture):
    store, jid, _, _, _ = fixture
    owner = 'd' * 32
    store.put('job_owners', jid, owner)
    store.put('desktop_sessions', owner, {'phase': 'ready', 'last_seen': time.time()})
    ctx = engine.Context(store, store.claim())
    try:
        store.cancel(jid)
        desktop._lease_path(store, owner).write_text('unreadable')
        with pytest.raises(engine.Cancelled, match='A stop request was recorded for this job'):
            ctx.check_cancel_only()
    finally:
        ctx.disk_guard.finish('cancelled')
        ctx.log.close()
