"""Storage opening is cheap; slow observations do not occupy the UI bridge."""
import threading
import time

import pytest

from bactologix import run_revisions, storage, storage_cleanup, ui_backend
from bactologix.runs import create_run
from bactologix.store import Store
from test_native import workspace, installation, sample


def test_opening_storage_does_not_discover_cleanup_or_measure_files(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    monkeypatch.setattr(storage_cleanup, 'panel', lambda *_: pytest.fail('Cleanup is explicitly requested'))
    monkeypatch.setattr(storage, 'scan', lambda *_: pytest.fail('Sizes are explicitly requested'))
    result = ui_backend.dispatch(store, 'inspect_storage', {})
    assert result['storage']['locations']
    assert result['storage']['last_scan'] is None
    assert 'storage_cleanup' not in result


def test_measurement_does_not_repeat_cleanup_discovery(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    monkeypatch.setattr(storage_cleanup, 'panel', lambda *_: pytest.fail('Measurement must not discover cleanup'))
    revision = storage.panel(store)['revision']
    result = ui_backend.dispatch(store, 'scan_storage', {'revision': revision})
    assert result['storage']['last_scan'] is not None
    assert 'storage_cleanup' not in result


@pytest.mark.parametrize('kind', ['inspect_storage', 'inspect_storage_cleanup', 'scan_storage',
    'preview_storage_cleanup', 'scan_databases', 'check_database', 'edit_run_settings'])
def test_slow_observation_does_not_block_the_request_bridge(tmp_path, monkeypatch, kind):
    store, session = Store(tmp_path / 'workspace'), {}
    entered, release = threading.Event(), threading.Event()
    def dispatch(*_):
        entered.set()
        assert release.wait(2)
        return {'fixture': 'result'}
    monkeypatch.setattr(ui_backend, 'dispatch', dispatch)
    started = time.monotonic()
    response = ui_backend.handle_request(store, {'id': 'slow', 'kind': kind, 'payload': {}}, session)
    try:
        assert time.monotonic() - started < .2
        assert entered.wait(1)
        token = response['async_task']['token']
        cancelled = ui_backend.handle_request(store, {'id': 'cancel', 'kind': 'cancel_ui_task',
            'payload': {'token': token}}, session)
        assert cancelled['async_task']['status'] == 'cancelled'
        assert not store.jobs()
    finally:
        release.set()


def test_editor_reads_saved_settings_but_revision_still_verifies_inputs(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, '{}')
    def changed_input(*_, **__):
        raise ValueError('Recorded inputs changed')
    monkeypatch.setattr(run_revisions, 'check_run_files', changed_input)
    panel = run_revisions.editor(workspace, run['id'])
    assert panel['run_config']['text'] == run['config']
    payload = {'id': run['id'], 'signature': panel['edit_run']['signature'],
               'config': {'tool': 'hybas', 'text': run['config'], 'changes': {}}}
    with pytest.raises(ValueError, match='Recorded inputs changed'):
        run_revisions.apply(workspace, payload)
    assert len(workspace.all('runs')) == 1
