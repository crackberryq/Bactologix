import hashlib
import json
import yaml
from pathlib import Path

from bactologix import reference_catalog, run_config
from bactologix.runs import bind_paths
from bactologix.store import Store


def package(root, name='MICP', version='1.0'):
    root.mkdir(parents=True)
    app=dict(id='test',name=name,version=version,description='Synthetic application fixture')
    files={'proteins.faa':'>p\nAVIL\n','proteins.dmnd':'fixture', 'diamond-version.txt':'fixture',
           'reference-manifest.json':json.dumps({'schema_version':'1.0','specification':{'schema_version':'2.0','application':app}})}
    for name, value in files.items(): (root/name).write_text(value)
    checksums={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
    (root/'checksums.json').write_text(json.dumps(checksums))
    (root/'completed.json').write_text(json.dumps({'status':'completed','checksums_sha256':hashlib.sha256((root/'checksums.json').read_bytes()).hexdigest()}))
    return root


def test_choices_include_all_completed_builds_and_deduplicate_registration(tmp_path):
    store=Store(tmp_path/'workspace');one=package(tmp_path/'one');two=package(tmp_path/'two', 'Other application','2.0')
    store.put('database_builds','one',{'status':'completed','result_path':str(one)})
    store.put('database_builds','running',{'status':'running','result_path':str(tmp_path/'unfinished')})
    reference_catalog.register(store,one);reference_catalog.register(store,two)
    rows=reference_catalog.choices(store)
    assert len(rows)==2 and all(r['available'] for r in rows)
    assert {r['name'] for r in rows}=={'MICP','Other application'}
    assert rows[0]['description']=='Synthetic application fixture'


def test_missing_and_changed_packages_visible_but_unavailable(tmp_path):
    store=Store(tmp_path/'workspace');root=package(tmp_path/'database')
    reference_catalog.register(store,root)
    (root/'proteins.dmnd').unlink()
    assert not reference_catalog.choices(store)[0]['available']
    (root/'proteins.dmnd').write_text('fixture')
    (root/'reference-manifest.json').write_text('{}')
    assert 'metadata checksum' in reference_catalog.choices(store)[0]['error']


def test_current_and_previous_run_paths_are_listed_without_default_mutation(tmp_path):
    store=Store(tmp_path/'workspace');root=package(tmp_path/'database')
    text=yaml.safe_dump({'reference_database':str(root)})
    store.put('runs','old',{'tool':'micpscope','config':text})
    assert reference_catalog.choices(store)[0]['path']==str(root)
    assert store.get('configs','micpscope') is None
    assert store.get('runs','old')['config']==text


def test_typed_selection_and_none_are_real_yaml_values():
    for text in ['samples: []\n', 'reference_database: null\n', 'reference_database: /old\n']:
        fields=run_config.editor('micpscope',text)['fields']
        row=next(r for r in fields if r['name']=='reference_database')
        assert row['kind']=='reference_database'
        selected=run_config.compile_config('micpscope',text,{row['key']:'/path with spaces/database'})
        assert yaml.safe_load(selected)['reference_database']=='/path with spaces/database'
        cleared=run_config.compile_config('micpscope',selected,{row['key']:''})
        assert yaml.safe_load(cleared)['reference_database'] is None


def test_clearing_database_clears_old_managed_identity(tmp_path):
    cfg={'reference_database':None,'reference_database_sha256':'old'}
    assert bind_paths(cfg,'micpscope',Store(tmp_path),{})=={'reference_database':None}
    assert cfg['reference_database_sha256']=='old'
