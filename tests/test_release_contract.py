import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from bactologix import catalog, release_contract as rc
from bactologix.store import Store


def review():
    return {'tool':'basis','repository':catalog.PIPELINES['basis']['repo'], 'commit':'a'*40,
        'tag':'v99.0.0','channel':'stable','adapter_interface':1,'configuration_schema':'basis-v1',
        'entry_points':{'workflow':'BaSIS.smk','environment':'environment.yml'},
        'runner':{'python':'3.11','snakemake':'9.6.2'},'tested':True,
        'test_run':'https://github.com/crackberryq/Bactologix/actions/runs/40906609530',
        'files_sha256':{'BaSIS.smk':'b'*64,'environment.yml':'c'*64}}


def test_refresh_approval_and_offline_preservation(tmp_path, monkeypatch):
    from bactologix.ui_backend import dispatch
    store=Store(tmp_path); r=review()
    listing={'live':True,'checkedAt':'2026-10-29','versions':[{'id':'release:v99.0.0','kind':'release',
        'ref':r['tag'],'commit':r['commit'],'prerelease':False,'publishedAt':'2026-10-29'}]}
    monkeypatch.setattr(catalog,'fetch_versions',lambda tool:copy.deepcopy(listing))
    monkeypatch.setattr(catalog,'api',lambda path:{'content':base64.b64encode(json.dumps({'schema_version':1,'releases':[r]}).encode()).decode()})
    assert not catalog.supported('basis',r['commit'],store)
    result = dispatch(store, 'refresh_versions', {'tool': 'basis'})
    assert result['refreshed_catalog'] == 'basis' and result['keep_modal']
    assert catalog.supported('basis',r['commit'],Store(tmp_path))
    assert catalog.versions(store,'basis')['recommended_release']=='v99.0.0'
    old=store.get('catalog','basis')
    monkeypatch.setattr(catalog,'api',lambda path: (_ for _ in ()).throw(OSError('offline')))
    with pytest.raises(OSError):rc.refresh(store,'basis')
    failure = dispatch(store, 'refresh_versions', {'tool': 'basis'})
    assert 'refreshed_catalog' not in failure and 'Refresh failed' in failure['message']
    assert store.get('catalog','basis')==old
    assert catalog.supported('basis',r['commit'],store)
    assert catalog.supported('basis',catalog.PIPELINES['basis']['previous_commits'][0],store)


@pytest.mark.parametrize('key,value,expected',[
 ('adapter_interface',2,'Adapter update'),('configuration_schema','basis-v2','schema'),
 ('entry_points',{},'entry point'),('runner',{},'pin'),('tested',False,'successful'),
 ('files_sha256',{},'checksums'),('commit','main','immutable')])
def test_incompatible_contracts(key,value,expected):
    r=review();r[key]=value
    assert expected in rc.reason('basis',r)


def test_source_preflight_checks_hashes_and_runner(tmp_path):
    store=Store(tmp_path/'workspace');source=tmp_path/'source';source.mkdir()
    r=review()
    for name,text in [('BaSIS.smk','rule all:\n    input: []\n'),('environment.yml','dependencies: [python=3.11, snakemake=9.6.2]\n')]:
        (source/name).write_text(text);r['files_sha256'][name]=hashlib.sha256(text.encode()).hexdigest()
    store.put('release_approvals','basis:'+r['commit'],r)
    rc.preflight(store,'basis',r['commit'],source)
    (source/'BaSIS.smk').write_text('modified')
    with pytest.raises(ValueError,match='preflight failed'):rc.preflight(store,'basis',r['commit'],source)


def test_published_registry_valid():
    doc=json.loads((Path(__file__).parents[1]/'compatibility/releases-v1.json').read_text())
    assert doc['schema_version']==1
    for row in doc['releases']:assert rc.reason(row['tool'],row) is None


@pytest.mark.parametrize('key,value', [('interface', 2), ('entry_point', 'Other.smk'),
    ('screening_schema', 'future'), ('parent_commits', ['main']), ('scopes', ['anything']),
    ('scopes', []), ('scopes', 'amrfinder-review')])
def test_followup_contract_fails_closed(key, value):
    r = review()
    r['files_sha256']['Followup.smk'] = 'd' * 64
    r['followup'] = {'interface': 1, 'entry_point': 'Followup.smk',
        'screening_schema': 'basis-snakemake-run-v1', 'parent_commits': ['b' * 40],
        'scopes': ['amrfinder-review', 'all-supported']}
    assert rc.reason('basis', r) is None
    r['followup'][key] = value
    assert rc.reason('basis', r) is not None


@pytest.mark.parametrize('changed', [{'prerelease':True}, {'ref':'v99.0.1'}, {'kind':'branch'}])
def test_discovery_does_not_approve_mismatched_release(tmp_path, monkeypatch, changed):
    store=Store(tmp_path);r=review()
    row={'kind':'release','ref':r['tag'],'commit':r['commit'],'prerelease':False}
    row.update(changed)
    monkeypatch.setattr(catalog,'fetch_versions',lambda tool:{'checkedAt':'test','versions':[row]})
    monkeypatch.setattr(catalog,'api',lambda path:{'content':base64.b64encode(json.dumps({'schema_version':1,'releases':[r]}).encode()).decode()})
    rc.refresh(store,'basis')
    assert not catalog.supported('basis',r['commit'],store)
