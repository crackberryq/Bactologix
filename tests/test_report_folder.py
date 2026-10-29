from pathlib import Path
from types import SimpleNamespace
import pytest
from bactologix import launcher
from bactologix.store import Store
from bactologix.ui_backend import dispatch


def test_wsl_folder_uses_translated_path_without_shell(tmp_path, monkeypatch):
    folder = tmp_path / "a folder & more"
    folder.mkdir()
    calls = []
    monkeypatch.setattr(launcher, 'is_wsl', lambda: True)
    def translate(argv, **kwargs):
        assert argv == ['wslpath', '-w', str(folder)]
        return SimpleNamespace(stdout='C:\\a folder & more\n')
    monkeypatch.setattr(launcher.subprocess, 'run', translate)
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda argv, **kwargs: calls.append((argv, kwargs)))
    assert launcher.open_folder(folder) == str(folder)
    assert calls[0][0] == ['explorer.exe', 'C:\\a folder & more']
    assert not calls[0][1].get('shell')


def test_no_desktop_gives_manual_path(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'is_wsl', lambda: False)
    monkeypatch.setattr(launcher.sys, 'platform', 'linux')
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    with pytest.raises(ValueError, match='No desktop session'):
        launcher.open_folder(tmp_path)


def test_folder_resolved_from_registered_report(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    report = tmp_path / 'report.html'
    report.write_text('<h1>Report</h1>')
    dispatch(store, 'register_report', {'path': str(report), 'name': 'Report'})
    row = store.all('reports')[0]
    opened = []
    monkeypatch.setattr(launcher, 'open_folder', lambda path: opened.append(path) or str(path))
    dispatch(store, 'open_report_folder', {'id': row['id'], 'path': '/untrusted'})
    assert opened == [tmp_path]
    with pytest.raises(ValueError, match='no longer exists'):
        dispatch(store, 'open_report_folder', {'id': '/untrusted'})
    assert len(opened) == 1
    assert not store.jobs()
