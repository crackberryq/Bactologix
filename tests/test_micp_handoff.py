import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import yaml
from bactologix.store import Store
from bactologix.catalog import PIPELINES
from bactologix.micp_handoff import create, database_identity
from bactologix.runs import check_run_files


def database(root):
    root.mkdir()
    for name, content in {'proteins.faa':'>p\nACDE\n', 'proteins.dmnd':'fixture', 'diamond-version.txt':'fixture',
                          'reference-manifest.json':json.dumps({'schema_version':'1.0'})}.items():
        (root/name).write_text(content)
    (root/'checksums.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}))
    (root/'completed.json').write_text(json.dumps({'status':'completed','checksums_sha256':hashlib.sha256((root/'checksums.json').read_bytes()).hexdigest()}))
    return root


def test_handoff_creates_isolated_draft_with_exact_provenance(tmp_path):
    store=Store(tmp_path/'app')
    source=tmp_path/'source';source.mkdir();(source/'envs').mkdir()
    (source/'MICPscope.smk').write_text('rule all:\n    input: []\n')
    (source/'envs/reference-search.yml').write_text('dependencies: [diamond]\n')
    subprocess.run(['git','init',str(source)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(source),'add','.'],check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture'],check=True,capture_output=True)
    sha=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    item=dict(id='micp',source=str(source),ready=True,version={'commit':sha},controller=str(tmp_path/'controller'))
    store.put('installs','micp',item);store.put('settings','active-micpscope','micp')
    db=database(tmp_path/'db');store.put('configs','micpscope',yaml.safe_dump({'reference_database':str(db)}))
    out=tmp_path/'hybas-output';(out/'annotation').mkdir(parents=True);(out/'work/final').mkdir(parents=True)
    (out/'annotation/demo.faa').write_text('>p\nACDE\n')
    (out/'annotation/demo.gff').write_text('##gff-version 3\n')
    (out/'work/final/assembly.final.fasta').write_text('>contig\nACGT\n')
    parent=dict(id='parent',tool='hybas',sample={'id':'demo','mode':'illumina_only'},output=str(out),
                commit='a'*40,config='{}',completed='yes',label='Source',cores=1,memory_mb=2048)
    store.put('runs','parent',parent)
    with patch('bactologix.runs.check_run_files'), patch('bactologix.runs.dry_run_current',return_value=False), patch.dict(PIPELINES,{'micpscope':dict(PIPELINES['micpscope'],commit=sha)}):
        child=create(store,'parent')
    assert child['parent'] is None
    assert child['sample']['micp_handoff']['source_run']=='parent'
    assert len(child['inputs'])==3
    assert Path(child['output'],'annotation/demo.fna').resolve()==out/'work/final/assembly.final.fasta'
    assert yaml.safe_load(child['config'])['reference_database_sha256']==database_identity(db)
    check_run_files(child)
    (db/'proteins.dmnd').write_text('changed')
    with pytest.raises(ValueError,match='checksum'):check_run_files(child)


def test_incomplete_source_rejected(tmp_path):
    store=Store(tmp_path/'app')
    with pytest.raises(ValueError,match='completed'):create(store,'missing')


def test_database_identity_rejects_tampering(tmp_path):
    db=database(tmp_path/'db')
    with pytest.raises(ValueError,match='changed'):database_identity(db,'wrong')
    (db/'proteins.faa').write_text('tampered')
    with pytest.raises(ValueError,match='checksum'):database_identity(db)
