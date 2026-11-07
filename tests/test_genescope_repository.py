import base64
import copy
import json

import pytest
from bactologix import catalog, release_contract as rc
from bactologix.store import Store

OLD = 'crackberryq/Bactomics_MICPscope'
NEW = 'crackberryq/Bactomics_GeneScope'


def contract(repository):
    spec = catalog.PIPELINES['micpscope']
    return dict(tool='micpscope', repository=repository, commit='a'*40,
        tag='genescope-v99.0.0', channel='prerelease', adapter_interface=1,
        configuration_schema='micpscope-v1', entry_points={'workflow':spec['snakefile'],'environment':spec['environment']},
        runner={'python':'3.11','snakemake':'9.6.2'}, tested=True,
        test_run='https://github.com/crackberryq/Bactologix/actions/runs/41817753488',
        files_sha256={spec['snakefile']:'b'*64,spec['environment']:'c'*64})


def test_new_remote_and_cached_urls_preserve_original_records(tmp_path):
    store=Store(tmp_path)
    cached={'live':True,'versions':[dict(id='old-release',kind='release',ref='old-tag',name='old name',
        commit=catalog.PIPELINES['micpscope']['previous_commits'][0],url='https://github.com/'+OLD+'/releases/tag/old-tag')]}
    store.put('catalog','micpscope',cached)
    assert catalog.PIPELINES['micpscope']['repo']==NEW
    result=catalog.versions(store,'micpscope')
    assert next(x for x in result['versions'] if x['id']=='old-release')['url']=='https://github.com/'+NEW+'/releases/tag/old-tag'
    assert store.get('catalog','micpscope')==cached
    assert all(catalog.supported('micpscope',sha) for sha in catalog.PIPELINES['micpscope']['previous_commits'])
    assert catalog.canonical_repository_url('https://github.com/'+OLD+'-unrelated/tree/main')=='https://github.com/'+OLD+'-unrelated/tree/main'
    assert catalog.canonical_repository_url('https://example.com/'+OLD)=='https://example.com/'+OLD


def test_old_approval_survives_canonical_refresh_but_other_changes_fail(tmp_path, monkeypatch):
    store=Store(tmp_path);old=contract(OLD);new=contract(NEW)
    key='micpscope:'+old['commit'];store.put('release_approvals',key,old)
    assert rc.reason('micpscope',old) is None
    assert rc.approval(store,'micpscope',old['commit'])==old
    bad=dict(new,repository='someone-else/Bactomics_GeneScope')
    assert rc.reason('micpscope',bad)
    listing={'live':True,'versions':[dict(kind='release',ref=new['tag'],commit=new['commit'],prerelease=True)]}
    monkeypatch.setattr(catalog,'fetch_versions',lambda tool:copy.deepcopy(listing))
    def api(path):
        return {'content':base64.b64encode(json.dumps({'schema_version':1,'releases':[new]}).encode()).decode()}
    monkeypatch.setattr(catalog,'api',api)
    rc.refresh(store,'micpscope')
    assert store.get('release_approvals',key)==old
    new['files_sha256']=dict(new['files_sha256'], **{catalog.PIPELINES['micpscope']['snakefile']:'d'*64})
    with pytest.raises(ValueError,match='changed'):rc.refresh(store,'micpscope')
    assert store.get('release_approvals',key)==old
