"""Exercise independent desktop supervision while the main WSL check is delayed."""
import time
import uuid

import pytest

from bactologix import desktop
from bactologix.store import Store


def pulse(session, owner):
    # The installed Windows PowerShell launcher writes whole Unix seconds.
    desktop.atomic(session.root / 'heartbeat.json', {'session': owner, 'time': int(time.time())})


def write_pulses_for(session, owner, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pulse(session, owner)
        time.sleep(.06)


@pytest.fixture
def watched_job(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'LEASE_SECONDS', 2.5)
    store = Store(tmp_path / 'workspace')
    key = uuid.uuid4().hex
    control = tmp_path / key
    control.mkdir()
    session = desktop.Session(store, control, key)
    monkeypatch.setenv(desktop.ENV, key)
    pulse(session, key)
    with session.watch_heartbeats():
        session.tick('', True)
        job_id = store.submit('smoke', {})
        assert store.claim()['id'] == job_id
        yield session, store, job_id


def test_running_job_survives_healthy_host_while_main_reader_is_delayed(watched_job):
    session, store, job_id = watched_job
    original = store.get('desktop_sessions', session.key)
    with store.connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        # Keep the Windows producer active without calling tick or refreshing
        # the SQLite session. The worker must still see its owner as live.
        write_pulses_for(session, session.key, desktop.LEASE_SECONDS + .65)
        assert time.time() - original['last_seen'] > desktop.LEASE_SECONDS
        assert store.get('desktop_sessions', session.key)['last_seen'] == original['last_seen']
        assert not desktop.owner_cancelled(store, job_id)
        writer.rollback()
    assert not session.poll('', True)
    assert session.phase == 'ready'
    assert store.job(job_id)['cancel'] == 0


@pytest.mark.parametrize('interruption', ['missing', 'different-session'])
def test_late_matching_heartbeat_cannot_restore_a_broken_chain(watched_job, interruption):
    session, store, job_id = watched_job
    if interruption == 'missing':
        (session.root / 'heartbeat.json').unlink()
        time.sleep(desktop.LEASE_SECONDS + .65)
    else:
        write_pulses_for(session, uuid.uuid4().hex, desktop.LEASE_SECONDS + .65)
    assert desktop.owner_cancelled(store, job_id)
    # An unobserved interval occurred; even many later valid messages from
    # the original owner cannot establish continuity across it.
    write_pulses_for(session, session.key, desktop.LEASE_SECONDS + .45)
    assert desktop.owner_cancelled(store, job_id)
    session.poll('', True)
    assert store.job(job_id)['cancel'] == 1
    assert session.reason.startswith('Desktop supervisor disconnected')


def test_confirmed_close_wins_over_continuous_pulses(watched_job):
    session, store, job_id = watched_job
    saved = store.get('desktop_sessions', session.key)
    write_pulses_for(session, session.key, desktop.LEASE_SECONDS + .65)
    assert not desktop.owner_cancelled(store, job_id)
    desktop.atomic(session.root / 'request.json', {
        'session': session.key, 'action': 'confirm_close', 'nonce': uuid.uuid4().hex,
    })
    deadline = time.monotonic() + 3
    while not desktop.owner_cancelled(store, job_id) and time.monotonic() < deadline:
        pulse(session, session.key)
        time.sleep(.06)
    assert desktop.session_stop_reason(saved, store=store, owner=session.key) == (
        'The owning desktop session requested a stop (Window closed).')
    session.poll('', True)
    pulse(session, session.key)
    assert desktop.owner_cancelled(store, job_id)
    assert store.job(job_id)['cancel'] == 1
