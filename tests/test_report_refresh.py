import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from bactologix.store import Store
from bactologix import report_refresh as r


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    source = tmp_path / 'renderer'
    package = source / 'src/micpscope'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'report.py').write_text('def render_report(data):\n    return "<html><body><main><h1>Saved fixture</h1></main></body></html>"\n')
    output = tmp_path / 'output'
    (output / 'micpscope').mkdir(parents=True)
    evidence = output / 'micpscope/results.json'
    evidence.write_text(json.dumps({'schema_version':'1.3','software_version':'old-analysis','proteins':[], 'provenance':{}}))
    (output / 'micpscope/report.html').write_text('original report')
    run = dict(id='run-fixture', tool='micpscope', output=str(output), sample={'id':'fixture'}, commit='a'*40)
    store.put('runs',run['id'],run)
    store.put('installs','renderer',dict(id='renderer',tool='micpscope',ready=True,controller='/fixture/controller',source=str(source),version={'ref':'new-renderer','commit':'b'*40}))
    store.put('settings','active-micpscope','renderer')
    monkeypatch.setattr(r,'supported',lambda *args:True)
    monkeypatch.setattr(r,'preflight',lambda *args:None)
    monkeypatch.setattr('bactologix.engine.find_conda',lambda *args:'/fixture/conda')
    return store, run, evidence, source


def context(store, plan, jid):
    def command(argv, **kwargs):
        assert argv[:6] == ['/fixture/conda','run','--no-capture-output','-p','/fixture/controller','python']
        assert 'snakemake' not in argv
        subprocess.run([sys.executable, *argv[6:]], check=True, **kwargs)
    return SimpleNamespace(store=store,job={'id':jid,'payload':plan},write=lambda s:None,check_cancel=lambda:None,command=command)


def test_refresh_keeps_originals_and_records_two_independent_editions(fixture):
    store, run, evidence, _ = fixture
    before=evidence.read_bytes()
    for jid in ('first','second'):
        plan=r.inspect(store,run['id'])
        assert plan['available'] and plan['mode']=='report-only'
        assert plan['analysis_version']=='old-analysis'
        assert any('No saved gene coordinates' in n for n in plan['notes'])
        r.execute(context(store,plan,jid))
        edition=store.get('report_refreshes',jid)
        assert edition['source_unchanged'] and edition['report_only']
        content=Path(edition['path']).read_text()
        assert 'old-analysis' in content and 'new-renderer' in content
        assert 'Saved fixture' in content
        assert store.get('reports',jid)['run']==run['id']
    assert evidence.read_bytes()==before
    assert (evidence.parent/'report.html').read_text()=='original report'
    assert store.get('runs',run['id'])==run
    assert store.get('reports','first')['path'] != store.get('reports','second')['path']


@pytest.mark.parametrize('change',['evidence','renderer'])
def test_stale_preview_never_starts_rendering(fixture,change):
    store,run,evidence,source=fixture
    plan=r.inspect(store,run['id'])
    if change=='evidence': evidence.write_text(evidence.read_text()+'\n')
    else: (source/'src/micpscope/report.py').write_text('# changed')
    with pytest.raises(ValueError,match='changed'):
        r.request(store,run['id'],plan['evidence_sha256'],plan['renderer_sha256'])
    with pytest.raises(ValueError,match='changed'):
        r.execute(context(store,plan,'stale'))
    assert not store.all('reports')


def test_missing_invalid_symlink_and_unsupported_inputs_are_explained(fixture):
    store,run,evidence,_=fixture
    evidence.unlink()
    assert not r.inspect(store,run['id'])['available']
    evidence.write_text('{}')
    assert 'Unsupported GeneScope' in r.inspect(store,run['id'])['reasons'][0]
    actual=evidence.with_suffix('.saved');evidence.rename(actual);evidence.symlink_to(actual)
    assert not r.inspect(store,run['id'])['available']
    run['tool']='hybas';store.put('runs',run['id'],run)
    assert 'not yet' in r.inspect(store,run['id'])['reasons'][0]


def test_basis_contract_and_renderer_use_saved_bundle(fixture):
    store,run,_,source=fixture
    run['tool']='basis';store.put('runs',run['id'],run)
    install=store.get('installs','renderer');install['tool']='basis';store.put('installs','renderer',install)
    store.put('settings','active-basis','renderer')
    package=source/'basis';package.mkdir();(package/'__init__.py').write_text('')
    (package/'report.py').write_text('def write_report(run,samples,records,deep_records,outdir):\n    (outdir / "report.html").write_text("<html><body><main>Saved BaSIS fixture</main></body></html>")\n')
    path=Path(run['output'])/'safety';path.mkdir()
    evidence=path/'evidence.json';evidence.write_text(json.dumps(dict(schema_version='basis-report-1',run={'version':'basis-old'},samples=[],records=[],deep_records=[])))
    plan=r.inspect(store,run['id']);assert plan['available']
    r.execute(context(store,plan,'basis-edition'))
    assert 'Saved BaSIS fixture' in Path(store.get('reports','basis-edition')['path']).read_text()


def test_followup_cannot_refresh_another_runs_evidence(fixture):
    store,run,_,_=fixture
    run.update(tool='basis',run_type='followup',parent='parent');store.put('runs',run['id'],run)
    assert 'no longer owns' in r.inspect(store,run['id'])['reasons'][0]


def test_installed_genescope_renderer_from_saved_json(tmp_path):
    import os
    source=os.environ.get('BACTOLOGIX_MICPSCOPE_SOURCE')
    if not source:
        pytest.skip('Installed GeneScope source is supplied by adapter CI')
    script=Path(r.__file__).parent/'data/render_saved_report.py'
    data={'schema_version':'1.3','software_version':'original-analysis','sample_id':'synthetic',
          'proteins':[], 'evidence':[], 'context':{}, 'knowledge_catalog':{},
          'provenance':{'input_sha256':'0'*64,'created_utc':'original-date'}}
    saved=tmp_path/'results.json';saved.write_text(json.dumps(data));before=saved.read_bytes()
    plan=dict(tool='micpscope',source=source,analysis_version='original-analysis',analysis_commit='a'*40,
              renderer_version='selected-renderer',renderer_commit='b'*40,evidence_sha256=r.digest(saved)[1],notes=[])
    planfile=tmp_path/'plan.json';planfile.write_text(json.dumps(plan))
    subprocess.run([sys.executable,'-B',str(script),str(saved),str(planfile),str(tmp_path/'edition')],check=True)
    assert saved.read_bytes()==before
    content=(tmp_path/'edition/report.html').read_text()
    assert 'Report edition provenance' in content and 'selected-renderer' in content
    assert 'original-analysis' in content


def test_installed_basis_renderer_from_saved_json(tmp_path):
    import os
    source=os.environ.get('BACTOLOGIX_BASIS_SOURCE')
    if not source:
        pytest.skip('Installed BaSIS source is supplied by adapter CI')
    script=Path(r.__file__).parent/'data/render_saved_report.py'
    data=dict(schema_version='basis-report-1',run={'run_id':'synthetic','version':'original-analysis'},
              samples=[],records=[],deep_records=[])
    saved=tmp_path/'evidence.json';saved.write_text(json.dumps(data));before=saved.read_bytes()
    plan=dict(tool='basis',source=source,analysis_version='original-analysis',analysis_commit='a'*40,
              renderer_version='selected-renderer',renderer_commit='b'*40,evidence_sha256=r.digest(saved)[1],notes=[])
    planfile=tmp_path/'plan.json';planfile.write_text(json.dumps(plan))
    subprocess.run([sys.executable,'-B',str(script),str(saved),str(planfile),str(tmp_path/'edition')],check=True)
    assert saved.read_bytes()==before
    content=(tmp_path/'edition/report.html').read_text()
    assert 'Report edition provenance' in content and 'selected-renderer' in content


def test_changes_during_render_do_not_register_a_completed_edition(fixture):
    store,run,evidence,_=fixture
    ctx=context(store,r.inspect(store,run['id']),'changed-during-render')
    original=ctx.command
    def command(*args,**kwargs):
        original(*args,**kwargs)
        evidence.write_text(evidence.read_text()+'\n')
    ctx.command=command
    with pytest.raises(ValueError,match='changed while rendering'):
        r.execute(ctx)
    assert not store.all('reports')
    assert not store.all('report_refreshes')


def test_ui_dispatch_only_queues_server_built_plan(fixture, monkeypatch):
    from bactologix.ui_backend import dispatch
    store,run,_,_=fixture
    monkeypatch.setattr('bactologix.updates.assert_current',lambda s:None)
    monkeypatch.setattr('bactologix.updates.status',lambda s:{'active_job':None})
    captured=[]
    monkeypatch.setattr('bactologix.ui_backend.enqueue',lambda s,k,p:captured.append((k,p)) or 'job-edition')
    plan=dispatch(store,'preview_report_refresh',{'id':run['id']})['refresh_plan']
    result=dispatch(store,'regenerate_report',{'id':run['id'],'evidence_sha256':plan['evidence_sha256'],
                   'renderer_sha256':plan['renderer_sha256'],'source':'untrusted-ignored','tool':'untrusted-ignored'})
    assert result['navigate']=='jobs'
    assert captured[0][0]=='report_refresh'
    assert captured[0][1]['source']==plan['source']
