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
    run['tool']='unsupported';store.put('runs',run['id'],run)
    assert 'does not have' in r.inspect(store,run['id'])['reasons'][0]


@pytest.mark.parametrize("layout", ["basis", "workflow/vendor/basis"])
def test_basis_contract_and_renderer_use_saved_bundle(fixture, layout):
    store,run,_,source=fixture
    run['tool']='basis';store.put('runs',run['id'],run)
    install=store.get('installs','renderer');install['tool']='basis';store.put('installs','renderer',install)
    store.put('settings','active-basis','renderer')
    package=source/layout;package.mkdir(parents=True);(package/'__init__.py').write_text('')
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


@pytest.mark.parametrize('legacy',[True,False])
def test_hybas_saved_report_and_legacy_summary(fixture, legacy):
    import os
    import shutil
    store,run,_,source=fixture
    real=os.environ.get('BACTOLOGIX_HYBAS_REPORT_SOURCE')
    if not real:
        pytest.skip('HybAs report source supplied by integration CI')
    package=source/'workflow/lib';package.mkdir(parents=True)
    for name in ('hybas_report.py','hybas_report_assets.py'):
        shutil.copyfile(Path(real)/'workflow/lib'/name,package/name)
    run['tool']='hybas';store.put('runs',run['id'],run)
    install=store.get('installs','renderer');install['tool']='hybas';store.put('installs','renderer',install)
    store.put('settings','active-hybas','renderer')
    summary={'isolate':run['sample']['id'],'workflow_version':'HybAs-old','assembly_bp':'4000000','run_mode':'hybrid'}
    doc=summary if legacy else {'schema_version':'hybas-report-1','summary':summary}
    path=Path(run['output'])/('metadata/run_summary.json' if legacy else 'reports/hybas/results.json')
    path.parent.mkdir(parents=True);path.write_text(json.dumps(doc));before=path.read_bytes()
    plan=r.inspect(store,run['id']);assert plan['available'],plan
    assert plan['analysis_version']=='HybAs-old'
    r.execute(context(store,plan,'hybas-edition'))
    report=Path(store.get('reports','hybas-edition')['path']).read_text()
    assert '4,000,000' in report and 'Assembly overview' in report
    assert 'HybAs-old' in report and path.read_bytes()==before


def set_evidence(evidence, **values):
    doc=json.loads(evidence.read_text());doc.update(values)
    evidence.write_text(json.dumps(doc))


def test_wrong_sample_is_rejected_before_rendering(fixture):
    store,run,evidence,_=fixture
    set_evidence(evidence,sample_id='OTHER-SAMPLE')
    plan=r.inspect(store,run['id'])
    assert not plan['available'] and 'sample identity' in plan['reasons'][0]
    assert not store.all('reports')


@pytest.mark.parametrize('field,value', [('run_id','other-run'),('commit','c'*40),('config_sha256','d'*64)])
def test_same_sample_conflicting_application_provenance_rejected(fixture,field,value):
    store,run,evidence,_=fixture
    run.update(config_sha256='e'*64,workflow_sha256='f'*64);store.put('runs',run['id'],run)
    set_evidence(evidence,sample_id=run['sample']['id'],provenance={'bactologix':{field:value}})
    assert not r.inspect(store,run['id'])['available']


@pytest.mark.parametrize('field', ['source_commit','workflow_commit','config_sha256','input_sha256'])
def test_native_content_identity_conflict_rejected(fixture,field):
    store,run,evidence,_=fixture
    run.update(config_sha256='b'*64,inputs=[{'path':'/data/sample.faa','sha256':'c'*64}]);store.put('runs',run['id'],run)
    set_evidence(evidence,sample_id=run['sample']['id'],provenance={field:'different'})
    plan=r.inspect(store,run['id'])
    assert not plan['available'] and 'does not match' in plan['reasons'][0]


def test_legacy_missing_identity_is_visible_and_does_not_acquire_source(fixture):
    store,run,_,_=fixture
    plan=r.inspect(store,run['id'])
    assert plan['available'] and plan['evidence_identity']['state']=='unverified'
    assert plan['analysis_commit'] is None and plan['analysis_status']=='unknown'
    r.execute(context(store,plan,'legacy'))
    content=Path(store.get('reports','legacy')['path']).read_text()
    assert 'Evidence attribution unverified' in content
    assert 'unverified / not recorded' in content and run['commit'] not in content
    assert store.get('reports','legacy')['analysis_status']=='unknown'


@pytest.mark.parametrize('status',['failed','cancelled','interrupted'])
def test_diagnostic_refresh_preserves_failed_or_cancelled_execution(fixture,status):
    store,run,evidence,_=fixture
    set_evidence(evidence,sample_id=run['sample']['id'])
    jid=store.submit('workflow',{'run_id':run['id'],'action':'run'});store.update_job(jid,status=status)
    plan=r.inspect(store,run['id']);assert plan['available']
    r.execute(context(store,plan,'diagnostic'))
    row=store.get('reports','diagnostic')
    assert row['analysis_status']==status and row['render_status']=='completed'
    assert row['assessment']['state']=='unknown'
    assert f'Latest computational execution: <strong>{status}</strong>' in Path(row['path']).read_text()


def recorded_evidence(fixture):
    from bactologix import report_evidence
    store,run,evidence,_=fixture
    run.update(config_sha256='c'*64,workflow_sha256='d'*64);store.put('runs',run['id'],run)
    original=evidence.read_bytes();evidence.unlink()
    jid=store.submit('workflow',{'run_id':run['id'],'action':'run'})
    ctx=SimpleNamespace(store=store,job=store.job(jid),write=lambda _:None)
    report_evidence.observe(ctx,run,'before')
    evidence.write_bytes(original)
    report_evidence.observe(ctx,run,'after')
    store.update_job(jid,status='succeeded')
    return jid


def test_content_receipt_binds_attempt_and_retains_later_failure(fixture):
    store,run,evidence,_=fixture
    first=recorded_evidence(fixture)
    plan=r.inspect(store,run['id'])
    assert plan['evidence_identity']['job_id']==first and plan['analysis_commit']==run['commit']
    later=store.submit('workflow',{'run_id':run['id'],'action':'run'});store.update_job(later,status='failed')
    plan=r.inspect(store,run['id'])
    assert plan['analysis_status']=='failed' and plan['analysis_job_id']==later
    assert plan['evidence_identity']['attempt_status']=='succeeded'
    assert any('earlier attempt' in n for n in plan['notes'])


def test_changed_same_sample_content_cannot_borrow_execution_receipt(fixture):
    store,run,evidence,_=fixture
    recorded_evidence(fixture)
    set_evidence(evidence,sample_id=run['sample']['id'],software_version='replacement')
    plan=r.inspect(store,run['id'])
    assert not plan['available'] and 'no longer matches' in plan['reasons'][0]


def test_unchanged_legacy_file_does_not_gain_source_attribution(fixture):
    from bactologix import report_evidence
    store,run,_,_=fixture
    jid=store.submit('workflow',{'run_id':run['id'],'action':'run'})
    ctx=SimpleNamespace(store=store,job=store.job(jid),write=lambda _:None)
    report_evidence.observe(ctx,run,'before');report_evidence.observe(ctx,run,'after')
    store.update_job(jid,status='succeeded')
    plan=r.inspect(store,run['id'])
    assert plan['available'] and plan['evidence_identity']['state']=='unverified'
    assert plan['analysis_commit'] is None


def test_changed_execution_after_preview_requires_new_preview(fixture):
    store,run,_,_=fixture
    plan=r.inspect(store,run['id'])
    jid=store.submit('workflow',{'run_id':run['id'],'action':'run'});store.update_job(jid,status='failed')
    with pytest.raises(ValueError,match='changed since preview'):
        r.execute(context(store,plan,'stale-status'))


def test_completion_only_methods_snapshot_does_not_prove_legacy_producer(fixture):
    from bactologix import report_evidence
    store,run,evidence,_=fixture
    run.update(config_sha256='c'*64,workflow_sha256='d'*64);store.put('runs',run['id'],run)
    set_evidence(evidence,sample_id=run['sample']['id'])
    jid=store.submit('workflow',{'run_id':run['id'],'action':'run'})
    ctx=SimpleNamespace(store=store,job=store.job(jid),write=lambda _:None)
    report_evidence.observe(ctx,run,'before');report_evidence.observe(ctx,run,'after')
    store.update_job(jid,status='succeeded')
    store.put('methods_evidence',run['id'],{'run_id':run['id'],'job_id':jid,
              'commit':run['commit'],'config_sha256':run['config_sha256'],'workflow_sha256':run['workflow_sha256'],
              'evidence':{'files':[{'path':'micpscope/results.json','sha256':r.digest(evidence)[1]}]}})
    plan=r.inspect(store,run['id'])
    assert plan['available'] and plan['analysis_commit'] is None
    assert plan['evidence_identity']['state']=='unverified'


def test_missing_native_version_never_borrows_selected_run_version(fixture):
    store,run,evidence,_=fixture
    run['version']='Selected-run-version';store.put('runs',run['id'],run)
    doc=json.loads(evidence.read_text());doc.pop('software_version');evidence.write_text(json.dumps(doc))
    plan=r.inspect(store,run['id'])
    assert plan['analysis_version']=='Not recorded' and plan['analysis_commit'] is None
    r.execute(context(store,plan,'unknown-version'))
    content=Path(store.get('reports','unknown-version')['path']).read_text()
    assert run['version'] not in content and run['commit'] not in content


def test_execution_changed_during_render_does_not_register_edition(fixture):
    store,run,_,_=fixture
    ctx=context(store,r.inspect(store,run['id']),'stale-render-status')
    original=ctx.command
    def command(*args,**kwargs):
        original(*args,**kwargs)
        jid=store.submit('workflow',{'run_id':run['id'],'action':'run'});store.update_job(jid,status='failed')
    ctx.command=command
    with pytest.raises(ValueError,match='changed while rendering'):
        r.execute(ctx)
    assert not store.all('reports')


@pytest.mark.parametrize('source',['samples','records','deep_records','input_validations','module_decisions'])
def test_basis_foreign_sample_cannot_hide_in_nested_evidence(fixture,source):
    from bactologix.evidence_identity import validate_native
    _,run,_,_=fixture
    run['tool']='basis'
    doc=dict(samples=[{'sample_id':'fixture'}],records=[],deep_records=[],run={})
    if source in ('input_validations','module_decisions'):
        doc['run'][source]={'another-sample':{}}
    else:
        doc[source]=[{'sample_id':'another-sample'}]
    with pytest.raises(ValueError,match='sample identity'):
        validate_native(run,doc)


def test_native_basis_run_id_keeps_its_own_namespace(fixture):
    from bactologix.evidence_identity import validate_native
    _,run,_,_=fixture
    run['tool']='basis'
    doc=dict(samples=[{'sample_id':'fixture'}],records=[],deep_records=[],run={'run_id':'native-derived-id'})
    assert 'sample identity' in validate_native(run,doc)['checks']


def test_hybas_foreign_sample_and_contradictory_config_rows_reject(fixture):
    from bactologix.evidence_identity import validate_native
    _,run,_,_=fixture
    run.update(tool='hybas',config_sha256='c'*64)
    with pytest.raises(ValueError,match='sample identity'):
        validate_native(run,{'isolate':'wrong-sample'})
    doc={'schema_version':'hybas-report-1','summary':{'isolate':'fixture'},'tables':{'workflow_provenance':[
        {'field':'config_sha256','value':'c'*64},{'field':'config_sha256','value':'d'*64}]}}
    with pytest.raises(ValueError,match='configuration hash'):
        validate_native(run,doc)


def test_recorded_evidence_cannot_follow_changed_reviewed_decision(fixture):
    store,run,_,_=fixture
    recorded_evidence(fixture)
    run['identification']={'decision_sha256':'new-decision'};store.put('runs',run['id'],run)
    plan=r.inspect(store,run['id'])
    assert not plan['available'] and 'different run, source or configuration' in plan['reasons'][0]
