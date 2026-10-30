"""Real Snakemake v2 DAGs from immutable source; no sequence tools are invoked."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from bactologix import manifest_inputs
from bactologix.catalog import PIPELINES
from bactologix.runs import create_run, check_run_files
from bactologix.store import Store
from test_manifest_inputs import approve


@pytest.mark.parametrize('platform,chemistry,short', [('pacbio','hifi',False),('pacbio','clr',True),('ont',None,True)])
def test_manifest_native_dag(tmp_path, platform, chemistry, short):
    supplied = os.environ.get('BACTOLOGIX_HYBAS_V2_SOURCE')
    if not supplied:
        pytest.skip('Immutable v2 checkout not supplied')
    source = Path(supplied).resolve()
    commit = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    assert commit == '8c7bdd792259b5e540727a2283c8300b893b66c3'
    store = Store(tmp_path/'workspace')
    installed = {'id':'v2','tool':'hybas','source':str(source),'controller':sys.prefix,'ready':True,
                 'version':{'commit':commit,'ref':'v2 software test'}}
    store.put('installs','v2',installed);store.put('settings','active-hybas','v2')
    approve(store, installed)
    reads = tmp_path/'long.fq';reads.write_text('@read\nACGT\n+\nIIII\n')
    lib={'library_id':'long','platform':platform,'read_groups':[{'read_group_id':'a','run_id':'run1','reads':str(reads)}]}
    if chemistry:lib['chemistry']=chemistry
    libraries=[lib]
    if short:
        left,right=tmp_path/'a.fq',tmp_path/'b.fq'
        for path in (left,right):path.write_text('@pair\nACGT\n+\nIIII\n')
        libraries.insert(0,{'library_id':'short','platform':'illumina','library_type':'paired_end','orientation':'fr',
            'read_groups':[{'read_group_id':'a','run_id':'run1','r1':str(left),'r2':str(right)}]})
    manifest=tmp_path/'inputs.json';manifest.write_text(json.dumps({'schema_version':1,'samples':[{'sample_id':'example','libraries':libraries}]}))
    review=manifest_inputs.review(manifest);manifest_inputs.commit(store,manifest,review['token'])
    cfg={'threads':'auto','total_mem_mb':2048,'assembly_mem_mb':1024,'spades_memory_gb':1,'polishing_mem_mb':512,
         'run_kraken':False,'kraken_decision_mode':'off','run_busco':False,'run_prokka':False,'run_multiqc':True,'polish_mode':'auto'}
    run=create_run(store,'hybas',store.get('samples','example'),yaml.safe_dump(cfg),cores=2,memory_mb=2048)
    check_run_files(run)
    folder=Path(run['folder'])
    result=subprocess.run([sys.executable,'-m','snakemake','-s',str(folder/'workflow/Hybas.smk'),
        '--configfile',str(folder/'config.yaml'),'--cores','2','--resources','mem_mb=2048','--dry-run'],
        cwd=folder/'workflow',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
    assert result.returncode==0,result.stdout[-16000:]
    assert ('flye_pacbio' if platform=='pacbio' else 'spades_libraries') in result.stdout
    assert 'research_report' in result.stdout
    assert not Path(run['output'],'work').exists()
