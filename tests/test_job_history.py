from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from bactologix.store import Store
from bactologix.ui_backend import dispatch, handle_request, snapshot


def test_clear_history_preserves_jobs_and_only_marks_finished_attempts(tmp_path):
    store = Store(tmp_path)
    queued = store.submit('smoke', {})
    running = store.submit('smoke', {})
    store.update_job(running, status='running')
    terminal = []
    for status in ('succeeded', 'failed', 'interrupted', 'cancelled'):
        jid = store.submit('workflow', {'run_id': 'run-example', 'action': 'run'})
        store.update_job(jid, status=status)
        terminal.append(jid)
    failed = terminal[1]
    store.log_path(failed).write_text('Workflow attempt log')
    store.put('job_owners', failed, 'desktop-session')
    telemetry = {'id': failed, 'schema': 1, 'state': 'complete'}
    store.put('job_telemetry', failed, telemetry)
    before = store.jobs()
    queue = store.queue_snapshot()

    session = {}
    request = {'id': 'clear-first', 'kind': 'clear_job_history', 'payload': {}}
    response = handle_request(store, request, session)
    assert response['ok'] and response['cleared'] == 4
    assert store.cleared_job_ids() == set(terminal)
    assert store.jobs() == before
    assert store.queue_snapshot() == queue
    assert dispatch(store, 'clear_job_history', {})['cleared'] == 0

    store.update_job(running, status='succeeded')
    assert handle_request(store, request, session) == response
    reopened = Store(tmp_path)
    assert reopened.cleared_job_ids() == set(terminal)
    jobs = {j['id']: j for j in snapshot(reopened)['jobs']}
    assert {key for key, j in jobs.items() if j.get('history_hidden')} == set(terminal)
    assert jobs[queued]['status'] == 'queued' and jobs[running]['status'] == 'succeeded'
    assert jobs[failed]['payload']['run_id'] == 'run-example'
    assert jobs[failed]['telemetry']['id'] == failed
    assert reopened.get('job_owners', failed) == 'desktop-session'
    assert dispatch(reopened, 'job_log', {'id': failed})['log']['text'] == 'Workflow attempt log'


def test_concurrent_clears_count_each_attempt_once(tmp_path):
    store = Store(tmp_path)
    jid = store.submit('smoke', {})
    store.update_job(jid, status='succeeded')
    barrier = Barrier(2)

    def clear():
        barrier.wait()
        return store.clear_job_history()

    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(clear) for _ in range(2)]
        assert sorted(call.result() for call in calls) == [0, 1]
    assert store.cleared_job_ids() == {jid}
