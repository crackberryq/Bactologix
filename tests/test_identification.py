"""Persistence, stale-review rejection and immutable continuation contracts."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from bactologix import identification as review
from bactologix.catalog import PIPELINES
from bactologix.runs import create_run, run_status, validate_launch, command
from bactologix.store import Store
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample


@pytest.fixture
def paused(workspace, installation, sample, monkeypatch):
    source=Path(installation['source']);(source/'app').mkdir()
    (source/'app/identification-review.json').write_text('{"interface":1}')
    subprocess.run(['git','-C',str(source),'add','.'],check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@localhost','commit','-qm','review fixture'],check=True)
    sha=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    installation['version']['commit']=sha;workspace.put('installs',installation['id'],installation)
    monkeypatch.setitem(PIPELINES['hybas'],'commit',sha)
    run=create_run(workspace,'hybas',sample,'kraken_review_before_filtering: true\nkraken_decision_mode: manual\ntarget_taxid: ""\n')
    p=review.report_path(run);p.parent.mkdir(parents=True)
    raw=p.parent/'reads.txt';raw.write_text('software fixture')
    doc={'schema':review.SCHEMA,'isolate':sample['id'],'created':'fixture','context':'fixed',
         'files':[{'path':str(raw),'size':raw.stat().st_size,'mtime_ns':raw.stat().st_mtime_ns,'sha256':review.sha(raw)}],
         'candidates':[{'taxid':'11','name':'Example one','rank':'S','illumina_pct':96,'ont_pct':0,'manual_actions':{'illumina':'whitelist_filter','ont':'not_applicable'}},
                       {'taxid':'22','name':'Example two','rank':'S','illumina_pct':4,'ont_pct':0,'manual_actions':{'illumina':'whitelist_filter','ont':'not_applicable'}}],
         'platforms':{'illumina':True,'ont':False},'include_children':True,
         'recommendation':{'taxid':'11','name':'Example one','rank':'S','actions':{'illumina':'passthrough','ont':'not_applicable'}}}
    p.write_text(json.dumps(doc));review.capture(run,'identification-job');run['dry_run_ok']=True
    workspace.put('runs',run['id'],run)
    return workspace,run


def test_review_option_rejects_old_pipeline(workspace,installation,sample):
    with pytest.raises(ValueError,match='does not support'):
        create_run(workspace,'hybas',sample,'kraken_review_before_filtering: true')
    assert not list((workspace.home/'runs').iterdir())


def test_pause_is_not_completion_and_cannot_bypass_review(paused):
    store,run=paused
    assert run_status(run,[],True)=='awaiting_review'
    with pytest.raises(ValueError,match='Awaiting researcher review'):
        validate_launch(store,run['id'],'run')
    with pytest.raises(ValueError,match='finalize'):
        validate_launch(store,run['id'],'continue')


def test_save_later_survives_restart_and_preserves_yaml(paused):
    store,run=paused;before=copy.deepcopy(run)
    view=review.inspect(store,run['id'],'manual','22')
    review.save(store,run['id'],'manual','22',view['signature'])
    reopened=Store(store.home);new=review.inspect(reopened,run['id'])
    assert new['mode']=='manual' and new['taxid']=='22' and new['valid']
    assert reopened.get('runs',run['id'])==before
    assert not reopened.jobs()
    assert Path(run['folder'],'config.yaml').read_text()==run['config']
    with pytest.raises(ValueError,match='changed'):
        review.save(reopened,run['id'],'manual','11',view['signature'])


def test_confirm_decision_is_immutable_and_command_is_recorded(paused,monkeypatch):
    store,run=paused;view=review.inspect(store,run['id'],'manual','22')
    updated=review.save(store,run['id'],'manual','22',view['signature'],confirm=True)
    state=updated['identification'];p=Path(state['decision_path'])
    assert json.loads(p.read_text())['taxid']=='22'
    assert updated['config']==run['config'] and not updated['dry_run_ok']
    monkeypatch.setattr('bactologix.engine.find_conda',lambda s:'/conda')
    argv=command(store,updated,'run')
    assert 'kraken_review_decision='+str(p) in argv
    assert 'kraken_review_decision_sha256='+review.sha(p) in argv
    validate_launch(store,run['id'],'continue')
    with pytest.raises(ValueError,match='already finalized'):
        view=review.inspect(store,run['id']);review.save(store,run['id'],'manual','11',view['signature'],confirm=True)
    p.write_text('{}')
    with pytest.raises(ValueError,match='decision changed'):
        validate_launch(store,run['id'],'continue')


def test_unknown_target_and_changed_evidence_rejected(paused):
    store,run=paused;view=review.inspect(store,run['id'])
    with pytest.raises(ValueError,match='TaxID present'):
        review.save(store,run['id'],'manual','999',view['signature'])
    doc=json.loads(review.report_path(run).read_text());Path(doc['files'][0]['path']).write_text('changed')
    with pytest.raises(ValueError,match='evidence changed'):
        review.inspect(store,run['id'])


def test_save_rejects_active_jobs_and_archived_run(paused):
    store,run=paused;view=review.inspect(store,run['id'])
    jid=store.submit('workflow',{'run_id':run['id'],'action':'dry-run'})
    with pytest.raises(ValueError,match='active work'):
        review.save(store,run['id'],'aware','',view['signature'])
    store.cancel(jid);store.put('run_visibility',run['id'],{'archived':True})
    with pytest.raises(ValueError,match='Restore'):
        review.save(store,run['id'],'aware','',view['signature'])


def test_preview_is_read_only_and_search_is_bounded(paused):
    store,run=paused
    result=dispatch(store,'inspect_identification',{'id':run['id'],'mode':'manual','taxid':'22','query':'two'})['identification']
    assert result['candidate_count']==1 and result['candidates'][0]['taxid']=='22'
    assert result['actions']['illumina']=='whitelist_filter'
    assert store.get('runs',run['id'])==run and not store.jobs()


def test_finalized_choice_is_shown_and_exported_as_effective_settings(paused):
    from bactologix.ui_backend import snapshot
    from bactologix.methods import _configuration
    store,run=paused
    view=review.inspect(store,run['id'],'aware')
    updated=review.save(store,run['id'],'aware','',view['signature'],confirm=True)
    item=next(r for r in snapshot(store)['runs'] if r['id']==run['id'])
    assert item['kraken_mode']=='aware'
    warnings=[]
    original,effective,known=_configuration(updated,warnings)
    assert original['kraken_decision_mode']=='manual'
    assert effective['kraken_decision_mode']=='aware' and effective['target_taxid']==''
    assert any(updated['identification']['decision_sha256'] in warning for warning in warnings)


@pytest.mark.parametrize('available', [False, True])
def test_continuation_worker_validates_and_requires_existing_environments(paused, monkeypatch, available):
    from bactologix.runs import execute_workflow
    store,run=paused
    view=review.inspect(store,run['id'])
    run=review.save(store,run['id'],'aware','',view['signature'],confirm=True)
    monkeypatch.setattr('bactologix.engine.find_conda',lambda s:'/fixture/conda')
    prefix=store.home/'envs/hybas'/('a'*32)
    if available:
        (prefix/'conda-meta').mkdir(parents=True)
        (prefix/'conda-meta/history').touch()
        prefix.with_suffix('.env_setup_done').touch()
    job=store.submit('workflow',{'run_id':run['id'],'action':'continue'})
    store.update_job(job,status='running')
    commands=[]
    class Context:
        def __init__(self): self.store,self.job=store,store.job(job)
        def command(self,argv,*,cwd,stdout=None,**kwargs):
            commands.append(argv)
            assert '--conda-create-envs-only' not in argv
            if stdout is not None:
                stdout.write('environment\tcontainer\tlocation\n'+str(cwd/'config.yaml')+'\t\t'+str(prefix)+'\n')
        def write(self,text): pass
        def check_cancel(self): pass
    if available:
        execute_workflow(Context())
        assert len(commands)==3 and any('run_existing.py' in arg for arg in commands[-1])
        final=store.get('runs',run['id'])
        assert final['completed'] and final['identification']['state']=='completed'
    else:
        with pytest.raises(ValueError,match='Install environments'):
            execute_workflow(Context())
        assert len(commands)==2
        final=store.get('runs',run['id'])
        assert not final.get('completed') and final['identification']['state']=='continuation_ready'
    assert '--dry-run' in commands[0] and '--list-conda-envs' in commands[1]
    assert store.job(job)['payload']['action']=='continue'
    assert Path(run['folder'],'config.yaml').read_text()==run['config']


def test_native_identification_continuation_adapter(tmp_path, monkeypatch):
    """Real Snakemake boundary and command round-trip against the pinned source."""
    import importlib.util
    import os
    import sys
    import yaml
    from bactologix.runs import tree_digest
    from test_adapter_contracts import snake
    source_value=os.environ.get('BACTOLOGIX_HYBAS_REVIEW_SOURCE')
    if not source_value:
        pytest.skip('Pinned identification-review source not supplied')
    source=Path(source_value).resolve()
    expected='7ed09af40942569f1d8d7735f5497ecfc9c0585a'
    assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()==expected
    spec=importlib.util.spec_from_file_location('hybas_review_fixture',source/'tests/test_workflow.py')
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    cfgfile,env=fixture.prepare(tmp_path/'fixture',{'sample':'illumina_only'},polish='none',pp=False,kraken=True)
    monkeypatch.setenv('PATH',env['PATH']);monkeypatch.setenv('HYBAS_TEST_CALLS',env['HYBAS_TEST_CALLS'])
    monkeypatch.setitem(PIPELINES['hybas'],'commit',expected)
    store=Store(tmp_path/'managed workspace')
    install={'id':'review-native','tool':'hybas','source':str(source),'controller':sys.prefix,'ready':True,'version':{'commit':expected,'ref':'pinned-review'}}
    store.put('installs',install['id'],install);store.put('settings','active-hybas',install['id'])
    cfg=yaml.safe_load(cfgfile.read_text());cfg.update(kraken_review_before_filtering=True,
        kraken_decision_mode='manual',require_target_taxid_for_kraken=True,target_taxid='',db_root=str(tmp_path/'fixture/db'))
    run=create_run(store,'hybas',{'id':'sample','path':str(tmp_path/'fixture/sample'),'mode':'illumina_only'},yaml.safe_dump(cfg),cores=2)
    if os.environ.get('HYBAS_TEST_DISABLE_BENCHMARKS')=='1':
        # Same hosted-PID-namespace opt-out as upstream software fixtures; CI keeps benchmarks.
        import re
        p=Path(run['folder'])/'workflow/Hybas.smk'
        p.write_text(re.sub(r'^    benchmark:[^\n]*\n(?:        [^\n]+\n)?','',p.read_text(),flags=re.M))
        run['workflow_sha256']=tree_digest(Path(run['folder'])/'workflow');store.put('runs',run['id'],run)
    snake(store,run,action='run',monkeypatch=monkeypatch)
    review.capture(run,'native-fixture');run['dry_run_ok']=True;store.put('runs',run['id'],run)
    selected=review.inspect(store,run['id'])
    assert selected['valid']
    before=Path(run['folder'],'config.yaml').read_bytes()
    run=review.save(store,run['id'],'aware','',selected['signature'],confirm=True)
    output=snake(store,run,monkeypatch=monkeypatch)
    assert 'unicycler' in output and 'kraken2_illumina' not in output and 'kraken2_nanopore' not in output
    assert Path(run['folder'],'config.yaml').read_bytes()==before
