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
from bactologix import release_contract
from bactologix.store import Store


@pytest.mark.parametrize('endpoint', ['assembly', 'aware'])
@pytest.mark.parametrize('pause', [False, True])
@pytest.mark.parametrize('platform,chemistry,short', [('pacbio','hifi',False),('pacbio','clr',True),('ont',None,True)])
def test_manifest_native_dag(tmp_path, platform, chemistry, short, pause, endpoint):
    supplied = os.environ.get('BACTOLOGIX_HYBAS_V2_SOURCE')
    if not supplied:
        pytest.skip('Immutable v2 checkout not supplied')
    source = Path(supplied).resolve()
    commit = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    registry = json.loads((Path(__file__).parents[1] / 'src/bactologix/data/development-v1.json').read_text())
    preferred = [row for row in registry['builds'] if row['tool'] == 'hybas' and row.get('preferred')]
    assert len(preferred) == 1
    review = preferred[0]
    assert commit == review['commit']
    assert not subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
    assert release_contract.reason('hybas', review) is None
    store = Store(tmp_path/'workspace')
    installed = {'id':'v2','tool':'hybas','source':str(source),'controller':sys.prefix,'ready':True,
                 'version':{'commit':commit,'ref':'v2 software test'}}
    store.put('installs','v2',installed);store.put('settings','active-hybas','v2')
    store.put('release_approvals', 'hybas:' + commit, review)
    release_contract.preflight(store, 'hybas', commit, source)
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
    cfg["aware_run_until"] = endpoint
    if pause or endpoint == "aware":
        database=tmp_path/'db'/'standard';database.mkdir(parents=True)
        for name in ('hash.k2d','opts.k2d','taxo.k2d'):(database/name).write_text('software fixture')
        cfg.update(kraken_review_before_filtering=pause,run_kraken=True,kraken_decision_mode='aware',db_root=str(tmp_path/'db'))
    run=create_run(store,'hybas',store.get('samples','example'),yaml.safe_dump(cfg),cores=2,memory_mb=2048)
    check_run_files(run)
    folder=Path(run['folder'])
    result=subprocess.run([sys.executable,'-m','snakemake','-s',str(folder/'workflow/Hybas.smk'),
        '--configfile',str(folder/'config.yaml'),'--cores','2','--resources','mem_mb=2048','--dry-run'],
        cwd=folder/'workflow',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
    assert result.returncode==0,result.stdout[-16000:]
    if pause:
        assert 'identification_review' in result.stdout
        assert 'flye_pacbio' not in result.stdout and 'spades_libraries' not in result.stdout
    elif endpoint == "aware":
        assert "aware_complete" in result.stdout
        assert "flye_pacbio" not in result.stdout and "spades_libraries" not in result.stdout
    else:
        assert ('flye_pacbio' if platform=='pacbio' else 'spades_libraries') in result.stdout
        assert 'research_report' in result.stdout
    assert not Path(run['output'],'work').exists()
