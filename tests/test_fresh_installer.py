import importlib.util
from pathlib import Path
import pytest
spec = importlib.util.spec_from_file_location('fresh_installer', Path(__file__).parents[1] / 'scripts/install_bactologix.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def release(tag, date, pre=False, draft=False):
    return {'tag_name':tag,'published_at':date,'prerelease':pre,'draft':draft,'assets':[{'name':'bactologix-1-py3-none-any.whl'}]}


def test_channels_and_drafts():
    rows=[release('stable','2026-01-01'),release('alpha','2026-02-01',True),release('draft','2026-03-01',draft=True)]
    assert installer.select_release(rows,'stable')['tag_name']=='stable'
    assert installer.select_release(rows,'prerelease')['tag_name']=='alpha'
    with pytest.raises(ValueError):installer.select_release([rows[1]],'stable')


def test_unverified_asset_never_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(installer,'urlopen',lambda *a,**k:pytest.fail('Network used before validation'))
    with pytest.raises(ValueError):installer.download({'name':'wheel'},tmp_path/'wheel')
    with pytest.raises(ValueError):installer.download({'name':'wheel','digest':'sha256:'+'0'*64,'browser_download_url':'http://unsafe'},tmp_path/'wheel')


def test_conda_detection_does_not_guess_nonexistent_executable(monkeypatch, tmp_path):
    monkeypatch.setenv('CONDA_EXE',str(tmp_path/'absent'))
    monkeypatch.setattr(installer.shutil,'which',lambda name:None)
    monkeypatch.setattr(installer.Path,'home',lambda:tmp_path)
    assert installer.find_conda() is None
