"""Stable/Dev ownership, catalog, environment and installer separation."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import zipfile
import pytest
from bactologix import channels, dev_catalog, catalog, updates
from bactologix.store import Store, workspace
from bactologix.engine import find_conda

@pytest.fixture
def dev(tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','dev')
    monkeypatch.setenv('BACTOLOGIX_DEV_HOME',str(tmp_path/'dev'))
    return Store()


def test_workspace_and_process_state_are_separate(tmp_path, monkeypatch):
    monkeypatch.delenv('BACTOLOGIX_CHANNEL',raising=False)
    stable=Store(tmp_path/'stable');stable.put('settings','proof','keep')
    monkeypatch.setenv('BACTOLOGIX_HOME',str(stable.home))
    monkeypatch.setenv('BACTOLOGIX_DEV_HOME',str(tmp_path/'dev'))
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','dev')
    dev=Store()
    assert dev.home!=stable.home and dev.get('settings','proof') is None
    dev.put('settings','proof','development')
    assert stable.get('settings','proof')=='keep'
    with pytest.raises(ValueError,match='other'):Store(stable.home)
    with pytest.raises(ValueError,match='nested'):Store(stable.home/'nested')
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','stable')
    with pytest.raises(ValueError,match='other'):Store(dev.home)


def test_dev_rejects_old_workspace_and_escaped_storage(tmp_path, monkeypatch):
    old=tmp_path/'old';old.mkdir();(old/'workspace.sqlite3').touch()
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','dev')
    with pytest.raises(ValueError,match='adopt'):Store(old)
    root=tmp_path/'dev';root.mkdir();(root/'envs').symlink_to(old,target_is_directory=True)
    with pytest.raises(ValueError,match='inside'):Store(root)


def test_dev_cannot_reuse_stable_conda_or_resources(dev, tmp_path, monkeypatch):
    conda=tmp_path/'stable-conda';conda.write_text('fixture');conda.chmod(0o755)
    monkeypatch.setenv('CONDA_EXE',str(conda))
    monkeypatch.setattr('bactologix.engine.shutil.which',lambda _:str(conda))
    assert find_conda(dev) is None
    with pytest.raises(ValueError,match='dedicated'):dev.put('settings','conda',str(conda))
    for field in ('source','controller','rule_cache'):
        with pytest.raises(ValueError,match='inside'):dev.put('installs','bad',{field:str(conda)})
    for cfg in ({'db_root':str(conda)},{'deep':{'profiles_dir':str(conda)}},{'storage':{'native_results_directory':str(conda)}}):
        with pytest.raises(ValueError,match='inside'):channels.validate_resources(dev,cfg)
    channels.validate_resources(dev,{'db_root':str(dev.home/'resources/db')})


def test_runtime_cache_environment_is_owned(dev, tmp_path):
    env=channels.environment(dev.home)
    for key in ('XDG_CACHE_HOME','CONDA_PKGS_DIRS','CONDA_ENVS_PATH','HF_HOME','TORCH_HOME','TMPDIR','CONDARC'):
        assert Path(env[key]).is_relative_to(dev.home)
    assert 'HOME' not in env
    assert env['PYTHONPATH']=='' and env['CONDA_PREFIX']==''
    assert env['CONDA_EXE']==str(dev.home/'tools/miniforge/bin/conda')
    outside=tmp_path/'outside';outside.mkdir()
    (dev.home/'cache/pip').rmdir();(dev.home/'cache/pip').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='inside'):channels.environment(dev.home)


def test_application_environment_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(sys,'prefix',str(tmp_path))
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','dev')
    with pytest.raises(ValueError,match='dedicated'):channels.application_guard()
    (tmp_path/channels.MARKER).write_text(json.dumps({'channel':'dev'}))
    channels.application_guard()
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','stable')
    with pytest.raises(ValueError,match='other'):channels.application_guard()


def test_dev_catalog_contains_only_exact_designated_builds(dev):
    for tool in catalog.PIPELINES:
        rows=catalog.versions(dev,tool)['versions']
        assert rows and all(r['kind']=='development' for r in rows)
        assert all(catalog.supported(tool,r['commit'],dev) for r in rows)
        assert not catalog.supported(tool,'0'*40,dev)
    assert not catalog.supported('hybas',catalog.PIPELINES['hybas']['commit'],dev)
    assert dev_catalog.document(dev)['channel']=='dev'


def test_dev_refresh_rejects_mutation_and_preserves_old_runs(dev, monkeypatch):
    import base64
    doc=dev_catalog.document(dev)
    original=next(row for row in doc['builds'] if row['tool']=='hybas' and row['preferred'])
    def serve(value):monkeypatch.setattr(catalog,'api',lambda path:{'content':base64.b64encode(json.dumps(value).encode()).decode()})
    corrupt=copy.deepcopy(doc)
    next(row for row in corrupt['builds'] if row['tool']=='hybas' and row['commit']==original['commit'])['files_sha256']['Hybas.smk']='0'*64
    serve(corrupt)
    with pytest.raises(ValueError,match='changed'):dev_catalog.refresh(dev,'hybas')
    assert dev.get('dev_catalog','registry') is None
    empty=dict(doc,builds=[]);serve(empty)
    dev_catalog.refresh(dev,'hybas')
    assert dev_catalog.approval(dev,'hybas',original['commit'])


def test_updates_do_not_cross_channels(monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','stable')
    assert updates.channel_accepts('0.1.0a85')
    assert not updates.channel_accepts('0.1.0a86.dev5')
    monkeypatch.setenv('BACTOLOGIX_CHANNEL','dev')
    assert updates.channel_accepts('0.1.0a86.dev5')
    assert not updates.channel_accepts('0.1.0a85')


def test_public_dev_release_asset_and_installer_channel_separation(monkeypatch):
    from test_updates import release
    installer = script('install_bactologix.py')
    version = '0.1.0a86.dev7'
    dev = release(version)
    dev['id'] = 78
    dev['tag_name'] = 'dev-v' + version
    dev['published_at'] = '2099-01-01'
    dev['assets'][0]['name'] = f'Bactologix-Dev-{version}.whl'
    dev['assets'][0]['browser_download_url'] = f'https://github.com/{updates.REPO}/releases/download/{dev["tag_name"]}/{dev["assets"][0]["name"]}'
    regular = release('0.1.0a86')
    regular['published_at'] = '2026-11-09'
    rows = [regular, dev]
    assert installer.select_release(rows, 'prerelease') is regular
    assert installer.select_release(rows, 'dev') is dev
    asset, local_name = installer.release_asset(dev)
    assert asset is dev['assets'][0] and local_name == f'bactologix-{version}-py3-none-any.whl'
    monkeypatch.setattr(updates, 'pages', lambda path: rows)
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'stable')
    assert [r['version'] for r in updates.fetch_releases()['releases']] == ['0.1.0a86']
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
    assert [r['version'] for r in updates.fetch_releases()['releases']] == [version]
    duplicate = copy.deepcopy(dev); duplicate['assets'] *= 2
    assert installer.release_asset(duplicate) is None
    invalid = copy.deepcopy(dev); invalid['tag_name'] = 'dev-v0.1.0a86'
    assert installer.release_asset(invalid) is None


def script(name):
    path=Path(__file__).parents[1]/'scripts'/name
    spec=importlib.util.spec_from_file_location(name.replace('.','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def wheel(tmp_path):
    p=tmp_path/'bactologix-0.1.0a86.dev5-py3-none-any.whl'
    with zipfile.ZipFile(p,'w') as z:z.writestr('bactologix-0.1.0a86.dev5.dist-info/METADATA','Name: bactologix\nVersion: 0.1.0a86.dev5\n')
    return p


def test_windows_dev_bundle_has_own_names_and_pinned_payload(tmp_path):
    builder=script('build_windows_bundle.py');w=wheel(tmp_path)
    result=builder.build(Path(__file__).parents[1],tmp_path/'dev.zip',channel='dev',wheel=w)
    with zipfile.ZipFile(result) as z:
        ps=z.read('Bactologix-Dev/Bactologix-Dev.ps1').decode()
        assert "LOCALAPPDATA 'Bactologix-Dev'" in ps
        assert "'bactologix.dev_cli'" in ps and "'--dev','--install-conda'" in ps
        assert "'Bactologix-Dev.lnk'" in ps
        assert "'dev-package.json'" in ps and w.name in ps
        manifest=json.loads(z.read('Bactologix-Dev/dev-package.json'))
        assert hashlib.sha256(z.read('Bactologix-Dev/'+w.name)).hexdigest()==manifest['sha256']
        assert all(n.startswith('Bactologix-Dev/') for n in z.namelist())


def test_bundled_wheel_verification(tmp_path):
    installer=script('install_bactologix.py');w=wheel(tmp_path)
    manifest=tmp_path/'dev-package.json';manifest.write_text(json.dumps({'wheel':w.name,'sha256':hashlib.sha256(w.read_bytes()).hexdigest()}))
    assert installer.bundled_dev_wheel(manifest)[0]==w
    w.write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):installer.bundled_dev_wheel(manifest)


def test_dev_installer_never_reuses_stable_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    installer=script('install_bactologix.py')
    # Restore environment after this standalone installer mutates its child-process configuration.
    for key in ('CONDA_PKGS_DIRS','CONDA_ENVS_PATH','PIP_CACHE_DIR','XDG_CACHE_HOME','XDG_CONFIG_HOME','TMPDIR','TMP','TEMP','CONDARC','PYTHONNOUSERSITE','BACTOLOGIX_CHANNEL','BACTOLOGIX_DEV_HOME'):
        monkeypatch.setenv(key,os.environ.get(key,''))
    monkeypatch.setattr(installer.Path,'home',lambda:tmp_path)
    monkeypatch.setattr(installer.os,'geteuid',lambda:1000)
    monkeypatch.setattr(installer.platform,'system',lambda:'Linux')
    monkeypatch.setattr(installer.platform,'machine',lambda:'x86_64')
    monkeypatch.setattr(installer.shutil,'disk_usage',lambda p:SimpleNamespace(free=20*2**30))
    monkeypatch.setattr(installer,'find_conda',lambda:pytest.fail('Stable Conda discovery must not run'))
    monkeypatch.setattr(installer.sys,'argv',['installer','--dev','--install-conda','--no-launch'])
    stable=tmp_path/'.local/share/bactomics';stable.mkdir(parents=True);(stable/'keep').write_text('stable')
    w=wheel(tmp_path);(tmp_path/'dev-package.json').write_text(json.dumps({'wheel':w.name,'sha256':hashlib.sha256(w.read_bytes()).hexdigest()}))
    monkeypatch.setattr(installer,'__file__',str(tmp_path/'install_bactologix.py'))
    monkeypatch.setattr(installer,'get_json',lambda url:{'assets':[{'name':'Miniforge3-Linux-x86_64.sh'}]})
    monkeypatch.setattr(installer,'download',lambda asset,p:p.write_text('fixture'))
    commands=[]
    def run(argv, log, capture=False):
        argv=list(map(str,argv));commands.append(argv)
        if argv[0]=='bash':
            prefix=Path(argv[-1]);(prefix/'bin').mkdir(parents=True);(prefix/'bin/conda').write_text('fixture')
        elif 'create' in argv:
            prefix=Path(argv[argv.index('-p')+1]);(prefix/'bin').mkdir(parents=True);(prefix/'bin/python').touch()
        if 'importlib.util' in argv[-1]:return 'False'
        return ''
    monkeypatch.setattr(installer,'run',run)
    assert installer.main()==0
    create=next(c for c in commands if 'create' in c)
    assert create[0]==str(tmp_path/'.local/share/bactologix-dev/tools/miniforge/bin/conda')
    assert create[create.index('-p')+1]==str(tmp_path/'.local/share/bactologix-dev-install/app')
    assert all(str(stable) not in arg for cmd in commands for arg in cmd)
    assert list(stable.iterdir())==[stable/'keep'] and (stable/'keep').read_text()=='stable'


def test_dev_source_contract(dev):
    source=os.environ.get('BACTOLOGIX_DEV_SOURCE')
    tool=os.environ.get('BACTOLOGIX_DEV_TOOL')
    if not source or not tool:pytest.skip('Enabled in the development source-contract CI matrix')
    from bactologix.release_contract import preflight
    rows=catalog.versions(dev,tool)['versions']
    selected=next(r for r in rows if r['preferred'])
    import subprocess
    assert subprocess.check_output(['git','-C',source,'rev-parse','HEAD'],text=True).strip()==selected['commit']
    preflight(dev,tool,selected['commit'],source)


def test_dev_default_workspace_ignores_inherited_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path/'stable-data'))
    monkeypatch.delenv('BACTOLOGIX_DEV_HOME', raising=False)
    assert workspace() == Path.home()/'.local/share/bactologix-dev'


def test_dev_workspace_reopens_after_cache_environment(dev, monkeypatch):
    for key, value in channels.environment(dev.home).items():
        monkeypatch.setenv(key, value)
    reopened = Store(dev.home)
    assert reopened.home == dev.home and reopened.channel == 'dev'
