"""Sample-free MICP Scope reference builds launched from setup."""
import hashlib
import json
from pathlib import Path
import shutil
import yaml
from . import maintenance
from .local import local_path
from .runs import tree_digest
from .store import identifier, now


def available(store):
    item = store.get('installs', store.get('settings','active-micpscope'))
    return bool(item and item.get('ready') and (Path(item['source'])/'DatabaseBuilder.smk').is_file())


def validate(store, text):
    if not isinstance(text,str) or len(text)>2_000_000:
        raise ValueError('Provide a YAML/JSON specification up to 2 MB.')
    spec=yaml.safe_load(text)
    if not isinstance(spec,dict) or spec.get('schema_version')!='1.0' or not isinstance(spec.get('proteins'),list) or not spec['proteins']:
        raise ValueError('Provide schema_version: "1.0" and at least one protein record.')
    import re
    for key in ('name','version'):
        if not isinstance(spec.get(key),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',spec[key]):
            raise ValueError(key+' must be a nonempty name using letters, numbers, dots, underscores or hyphens.')
    if not isinstance(spec.get('regulatory',[]),list): raise ValueError('regulatory must be a list')
    assets={}
    for row in spec['proteins']+spec.get('regulatory',[]):
        if not isinstance(row,dict): raise ValueError('Sequence records must be mappings')
        if 'path' in row:
            p=local_path(row['path'])
            if not p.is_file(): raise ValueError('Input FASTA not found: '+str(p))
            row['path']=str(p); assets[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    return spec,assets


def request(store,text,cores=2,memory_mb=2048):
    item=maintenance.installation(store,store.get('settings','active-micpscope'))
    if item['tool']!='micpscope' or not available(store):
        raise ValueError('Update MICP Scope to a version with Database Builder and finish runner setup.')
    maintenance.check_deployment_path(store,item)
    spec,assets=validate(store,text)
    key=identifier('reference')
    folder=store.home/'maintenance'/key
    folder.mkdir(parents=True)
    # Freeze supplied files so edits to the user's originals cannot alter a queued build.
    inputs=folder/'inputs'; inputs.mkdir()
    for n,(original,digest) in enumerate(assets.items()):
        target=inputs/(str(n)+'.fasta'); shutil.copyfile(original,target)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise ValueError('Input changed during snapshot: '+original)
        for row in spec['proteins']+spec.get('regulatory',[]):
            if row.get('path')==original: row['path']=str(target)
    specpath=folder/'references.yaml'; specpath.write_text(yaml.safe_dump(spec,sort_keys=False))
    shutil.copytree(item['source'],folder/'workflow',ignore=shutil.ignore_patterns('.git','.snakemake','__pycache__','*.pyc'))
    destination=store.home/'resources/micpscope'/key
    payload={'id':key,'tool':'micpscope','installation':item['id'],'commit':item['version']['commit'],
             'folder':str(folder),'destination':str(destination),'name':spec['name'],'version':spec['version'],
             'input_sha256':assets,'snapshot_sha256':tree_digest(inputs),'spec_sha256':hashlib.sha256(specpath.read_bytes()).hexdigest(),
             'workflow_sha256':tree_digest(folder/'workflow'),
             'cores':maintenance._number(cores,'Threads',1024),'memory_mb':maintenance._number(memory_mb,'Memory (MB)',10000000)}
    store.put('database_builds',key,{**payload,'status':'queued','created':now()})
    return payload


def _execute(ctx):
    p=ctx.job['payload']; store=ctx.store; folder=Path(p['folder'])
    item=maintenance.installation(store,p['installation'])
    if item['version']['commit']!=p['commit']: raise ValueError('Installation changed; submit a new build')
    if tree_digest(folder/'workflow')!=p['workflow_sha256'] or tree_digest(folder/'inputs')!=p['snapshot_sha256'] or hashlib.sha256((folder/'references.yaml').read_bytes()).hexdigest()!=p['spec_sha256']:
        raise ValueError('Frozen build files changed; submit a new build')
    destination=Path(p['destination'])
    destination.mkdir(parents=True,exist_ok=False)
    cfg={'reference_spec':str(folder/'references.yaml'),'build_dir':str(destination),'threads':p['cores'],'memory_mb':p['memory_mb']}
    config=folder/'build.yaml'; config.write_text(yaml.safe_dump(cfg))
    row=store.get('database_builds',p['id']); row.update(status='running',job=ctx.job['id']); store.put('database_builds',p['id'],row)
    argv=maintenance.snake_command(store,item,folder,folder/'workflow/DatabaseBuilder.smk')
    argv[argv.index('--cores')+1]=str(p['cores'])
    argv+=['--configfile',str(config),'--resources','mem_mb='+str(p['memory_mb'])]
    try:
        ctx.write('Database Builder: validating references, retrieving sequences, indexing, packaging and verifying. No sample analysis.')
        ctx.command(argv,cwd=folder); ctx.check_cancel()
        result=destination/'database'
        receipt=json.loads((result/'completed.json').read_text())
        if receipt.get('status')!='completed' or receipt.get('checksums_sha256')!=hashlib.sha256((result/'checksums.json').read_bytes()).hexdigest():
            raise ValueError('Completed database receipt did not verify')
        row.update(status='completed',result_path=str(result),finished=now())
        ctx.write('Database registered: '+str(result))
    except BaseException as exc:
        row.update(status='failed',error=str(exc)); raise
    finally: store.put('database_builds',p['id'],row)


def execute(ctx):
    try:
        _execute(ctx)
    except BaseException as exc:
        from .engine import Cancelled
        row=ctx.store.get('database_builds',ctx.job['payload']['id'])
        if row:
            row.update(status='cancelled' if isinstance(exc,Cancelled) else 'failed', error=str(exc), job=ctx.job['id'], finished=now())
            ctx.store.put('database_builds',row['id'],row)
        raise
