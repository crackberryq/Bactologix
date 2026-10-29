"""Switch presentation without restarting workers or accepting a supplied URL."""
import inspect
import pytest
from bactologix import launcher, ui_backend
from bactologix.store import Store


def test_default_app_window():
    assert inspect.signature(launcher.serve).parameters['app_window'].default is True


def test_browser_handoff_uses_current_launcher(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(ui_backend.updates, 'assert_current', lambda s: None)
    monkeypatch.setattr(ui_backend.updates, 'status', lambda s: {'active_job': None})
    monkeypatch.setattr(ui_backend.updates.restart_control, 'current_launcher', lambda s: {'url': 'http://127.0.0.1:8501'})
    opened = []
    monkeypatch.setattr(launcher, 'open_browser', lambda url: opened.append(url) or True)
    result = ui_backend.dispatch(store, 'restart_as_browser', {'url': 'https://ignored.invalid'})
    assert opened == ['http://127.0.0.1:8501']
    assert 'jobs keep running' in result['message'] and not store.jobs()
    monkeypatch.setattr(launcher, 'open_browser', lambda url: False)
    with pytest.raises(ValueError, match='Could not open'):
        ui_backend.dispatch(store, 'restart_as_browser', {})
    monkeypatch.setattr(ui_backend.updates.restart_control, 'current_launcher', lambda s: None)
    with pytest.raises(ValueError, match='no active launcher'):
        ui_backend.dispatch(store, 'restart_as_browser', {})
