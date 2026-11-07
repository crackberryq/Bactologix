"""AWARE bundle registration and immutable handoff with synthetic read files."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml
from bactologix import aware_outputs as aware
from bactologix.runs import create_run, check_run_files, run_status, command
from bactologix.catalog import PIPELINES
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample
from test_manifest_inputs import approve


def bundle(root, sample_id='derived'):
    root.mkdir(parents=True,exist_ok=True)
    reads=root/'unexpected_layout/retained.fastq';reads.parent.mkdir()
    reads.write_text('@read\nACGT\n+\nIIII\n')
    group=dict(id='species_11',name='Example species',category='provisional_species_assignment',units={'pacbio':1},
        manifest={'schema_version':1,'samples':[dict(sample_id=sample_id,libraries=[dict(library_id='pb',platform='pacbio',chemistry='hifi',read_groups=[dict(run_id='original_run',read_group_id='original_rg',reads='unexpected_layout/retained.fastq')])])]})
    doc=dict(schema=aware.SCHEMA,isolate='sample',context='test',groups=[group],files=[dict(path='unexpected_layout/retained.fastq',size=reads.stat().st_size,sha256=aware.sha(reads))])
    path=root/'manifest.json';path.write_text(json.dumps(doc))
    return path,reads


def test_portable_import_and_metadata(workspace,tmp_path):
    path,reads=bundle(tmp_path/'original')
    shutil.copytree(path.parent,tmp_path/'moved')
    shutil.rmtree(path.parent)
    path=tmp_path/'moved/manifest.json'
    view=aware.review(path)
    assert view['rows'][0]['mode']=='pacbio_only'
    rows=aware.commit(workspace,path,view['token'],['species_11'])
    lib=rows[0]['input_manifest']['libraries'][0]
    assert lib['chemistry']=='hifi' and lib['read_groups'][0]['run_id']=='original_run'
    assert rows[0]['aware_provenance']['parent_sample']=='sample'
    with pytest.raises(ValueError,match='already registered'):
        aware.commit(workspace,path,view['token'],['species_11'])


@pytest.mark.parametrize('change',['bytes','traversal','symlink','unrecorded','ambiguous'])
def test_rejects_invalid_import(workspace,tmp_path,change):
    path,reads=bundle(tmp_path/'bundle');doc=json.loads(path.read_text());view=aware.review(path)
    if change=='bytes':reads.write_text('changed')
    elif change=='traversal':doc['files'][0]['path']='../outside.fastq'
    elif change=='symlink':
        copy=tmp_path/'elsewhere';copy.write_bytes(reads.read_bytes());reads.unlink();reads.symlink_to(copy)
    elif change=='unrecorded':doc['files']=[]
    else:doc['groups'][0]['category']='ambiguous'
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):aware.commit(workspace,path,view['token'],['species_11'])
    assert workspace.all('samples')==[]


def test_derived_runs_skip_second_filter_and_hash_content(workspace,installation,tmp_path):
    approve(workspace,installation)
    path,reads=bundle(tmp_path/'bundle');view=aware.review(path)
    sample=aware.commit(workspace,path,view['token'],['species_11'])[0]
    run=create_run(workspace,'hybas',sample,'kraken_decision_mode: aware\nrun_kraken: true\npolish_mode: none')
    cfg=yaml.safe_load(run['config'])
    assert cfg['kraken_decision_mode']=='off' and cfg['run_kraken'] is False
    check_run_files(run)
    import os
    stat=reads.stat();reads.write_text(reads.read_text().replace('ACGT','TGCA'));os.utime(reads,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    with pytest.raises(ValueError,match='Derived AWARE input changed'):aware.verify_sample(run['sample'])


def test_continuation_preserves_configuration(workspace,installation,sample,monkeypatch):
    source=Path(installation['source']);(source/'app').mkdir();(source/'app/aware-outputs.json').write_text('{"interface":1}')
    subprocess.run(['git','-C',str(source),'add','.'],check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@localhost','commit','-qm','aware fixture'],check=True)
    commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    installation['version']['commit']=commit;workspace.put('installs',installation['id'],installation);monkeypatch.setitem(PIPELINES['hybas'],'commit',commit)
    run=create_run(workspace,'hybas',sample,'aware_run_until: aware\nkraken_decision_mode: aware')
    path,_=bundle(Path(run['output'])/'reports/aware')
    doc=json.loads(path.read_text());doc['groups'][0].update(id='primary',category='primary');path.write_text(json.dumps(doc))
    aware.capture(run,'job');workspace.put('runs',run['id'],run)
    assert run_status(run,[],True)=='aware_complete'
    original=run['config'];view=aware.review(path)
    updated=aware.continue_assembly(workspace,run['id'],view['token'])
    assert updated['config']==original and not updated['dry_run_ok']
    monkeypatch.setattr('bactologix.engine.find_conda',lambda store:'/conda')
    argv=command(workspace,updated,'dry-run')
    assert argv.count('--config')==1
    assert any(x.startswith('aware_assembly_continuation=') for x in argv)
    check_run_files(updated)
    Path(updated['aware']['continuation_path']).write_text('{}')
    with pytest.raises(ValueError,match='continuation changed'):check_run_files(updated)


def test_new_options_are_one_group():
    from bactologix import run_config
    fields=run_config.editor('hybas','kraken_decision_mode: aware')['fields']
    assert all(f['group']=='Kraken & AWARE' for f in fields if f['name'].startswith('aware_'))
    with pytest.raises(ValueError):run_config.validate('hybas','aware_preserve_groups: auto')
