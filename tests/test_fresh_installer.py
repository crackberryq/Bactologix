import importlib.util
from pathlib import Path
import pytest
spec = importlib.util.spec_from_file_location('fresh_installer', Path(__file__).parents[1] / 'scripts/install_bactologix.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def release(tag, date, pre=False, draft=False):
    return {'tag_name':tag,'published_at':date,'prerelease':pre,'draft':draft,'assets':[{'name':'bactologix-'+tag.removeprefix('v')+'-py3-none-any.whl'}]}


def test_channels_and_drafts():
    rows=[release('v1.0.0','2026-01-01'),release('v1.1.0a1','2026-02-01',True),release('v1.2.0a1','2026-03-01',draft=True)]
    assert installer.select_release(rows,'stable')['tag_name']=='v1.0.0'
    assert installer.select_release(rows,'prerelease')['tag_name']=='v1.1.0a1'
    with pytest.raises(ValueError):installer.select_release([rows[1]],'stable')


@pytest.mark.parametrize('channel', ['stable', 'prerelease'])
@pytest.mark.parametrize('tag', ['v99.0.0.dev1', 'v99.0.0a1.dev7', 'v99.0.0-dev7', 'v99.0.0DEV7', 'v99.0.0.dev'])
def test_regular_installer_rejects_development_tags_regardless_of_github_flag(channel, tag):
    development = release(tag, '2099-01-01', pre=False)
    regular = release('v1.0.0', '2026-01-01')
    assert installer.select_release([development, regular], channel) == regular
    with pytest.raises(ValueError, match='development releases'):
        installer.select_release([development], channel)


def test_regular_tag_cannot_select_development_wheel_or_duplicate_wheels():
    regular = release('v1.0.0', '2026-01-01')
    regular['assets'][0]['name'] = 'bactologix-1.0.0.dev1-py3-none-any.whl'
    with pytest.raises(ValueError):
        installer.select_release([regular], 'prerelease')
    regular = release('v1.0.0', '2026-01-01')
    regular['assets'] *= 2
    assert installer.application_asset(regular) is None


def test_regular_filename_cannot_hide_development_wheel_metadata(tmp_path):
    from test_updates import wheel_bytes
    regular = release('v99.0.0a1', '2026-01-01')
    path = tmp_path / regular['assets'][0]['name']
    path.write_bytes(wheel_bytes('99.0.0a1.dev1'))
    with pytest.raises(ValueError, match='identity'):
        installer.validate_application_wheel(path, regular)
    path.write_bytes(wheel_bytes())
    installer.validate_application_wheel(path, regular)


def test_unverified_asset_never_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(installer,'urlopen',lambda *a,**k:pytest.fail('Network used before validation'))
    with pytest.raises(ValueError):installer.download({'name':'wheel'},tmp_path/'wheel')
    with pytest.raises(ValueError):installer.download({'name':'wheel','digest':'sha256:'+'0'*64,'browser_download_url':'http://unsafe'},tmp_path/'wheel')


def test_conda_detection_does_not_guess_nonexistent_executable(monkeypatch, tmp_path):
    monkeypatch.setenv('CONDA_EXE',str(tmp_path/'absent'))
    monkeypatch.setattr(installer.shutil,'which',lambda name:None)
    monkeypatch.setattr(installer.Path,'home',lambda:tmp_path)
    assert installer.find_conda() is None


@pytest.mark.parametrize('launch', [False, True])
@pytest.mark.parametrize('environment,existing', [(None, False), ('bactomics', False), ('bactomix', True), ('bactomics', True)])
def test_install_and_reuse(tmp_path, monkeypatch, environment, existing, launch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(installer.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(installer.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(installer.shutil, 'disk_usage', lambda p: SimpleNamespace(free=10 * 2**30))
    monkeypatch.setattr(installer, 'find_conda', lambda: '/fixture/conda')
    monkeypatch.setattr(installer.sys, 'argv', ['installer'] + ([] if launch else ['--no-launch']))
    envs = [str(tmp_path/environment)] if environment else []
    commands = []
    def run(argv, log, capture=False):
        argv = list(map(str, argv)); commands.append(argv)
        if argv[1:3] == ['env', 'list']: return json.dumps({'envs':envs})
        if argv[1] == 'create': envs.append(str(tmp_path/'bactomics'))
        if 'importlib.util' in argv[-1]: return str(existing)
        return ''
    monkeypatch.setattr(installer, 'run', run)
    monkeypatch.setattr(installer, 'get_json', lambda url: [release('v99.0.0a1', '2026-01-01', True)])
    from test_updates import wheel_bytes
    monkeypatch.setattr(installer, 'download', lambda a,p:p.write_bytes(wheel_bytes()))
    existing_data = tmp_path / '.local/share/bactologix/retained-run.json'
    existing_data.parent.mkdir(parents=True)
    existing_data.write_text('original workspace')
    def launch_app(argv):
        assert not (tmp_path/'.local/share/bactomics/install.lock').exists()
        assert argv == [str(Path(envs[0])/'bin/bactologix')]
        return 0
    monkeypatch.setattr(installer.subprocess, 'call', launch_app)
    assert installer.main() == 0
    assert any('install' in cmd for cmd in commands) is not existing
    assert any('create' in cmd for cmd in commands) is (environment is None)
    assert not (tmp_path/'.local/share/bactomics/install.lock').exists()
    assert existing_data.read_text() == 'original workspace'
    assert not (tmp_path/'.local/share/bactologix-dev').exists()
    assert not any('bactologix-dev' in arg for cmd in commands for arg in cmd)


def test_download_integrity_and_interruption(tmp_path, monkeypatch):
    import io, hashlib
    payload = b'verified fixture'
    asset = {'name':'fixture','size':len(payload),'digest':'sha256:'+hashlib.sha256(payload).hexdigest(),'browser_download_url':'https://github.com/example/repo/releases/download/v1/fixture'}
    monkeypatch.setattr(installer, 'urlopen', lambda *a,**k:io.BytesIO(payload))
    target = tmp_path/'download'
    installer.download(asset, target)
    assert target.read_bytes() == payload
    with pytest.raises(ValueError, match='verification failed'):
        installer.download({**asset,'size':1},target)
    monkeypatch.setattr(installer, 'urlopen', lambda *a,**k:io.BytesIO(b'corrupt'))
    with pytest.raises(ValueError, match='verification failed'):installer.download(asset,target)
    def interrupted(*a,**k):raise OSError('connection interrupted')
    monkeypatch.setattr(installer, 'urlopen', interrupted)
    with pytest.raises(OSError):installer.download(asset,target)


@pytest.mark.parametrize('has_conda', [False, True])
@pytest.mark.parametrize('check', [False, True])
def test_preflight_low_disk_has_no_install_side_effects(tmp_path, monkeypatch, check, has_conda):
    from types import SimpleNamespace
    monkeypatch.setattr(installer.Path,'home',lambda:tmp_path)
    monkeypatch.setattr(installer.os,'geteuid',lambda:1000)
    monkeypatch.setattr(installer.platform,'system',lambda:'Linux')
    monkeypatch.setattr(installer.platform,'machine',lambda:'x86_64')
    monkeypatch.setattr(installer.shutil,'disk_usage',lambda p:SimpleNamespace(free=1))
    monkeypatch.setattr(installer,'find_conda',lambda:'/fixture/conda' if has_conda else None)
    monkeypatch.setattr(installer.sys,'argv',['installer']+(['--check'] if check else []))
    monkeypatch.setattr(installer,'get_json',lambda *a:pytest.fail('Network request during failed preflight'))
    if check:assert installer.main()==1
    else:
        with pytest.raises(SystemExit):installer.main()
    assert not (tmp_path/'.local').exists()
