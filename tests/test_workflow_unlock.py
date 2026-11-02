"""Lock maintenance uses disposable software fixtures, never sample analyses."""
import copy
import os
import fcntl
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import psutil
import pytest
from bactologix import workflow_unlock as unlock
from bactologix.store import Store
from bactologix.runs import tree_digest
from bactologix.ui_backend import dispatch


@pytest.fixture
def locked(tmp_path, monkeypatch):
    store = Store(tmp_path/'workspace')
    directory = store.home/'runs/run-fixture/workflow'
    directory.mkdir(parents=True)
    (directory/'Snakefile').write_text('rule all:\n    input: []\n')
    config = directory.parent/'config.yaml'
    config.write_text('{}\n')
    locks = directory/'.snakemake/locks'
    locks.mkdir(parents=True)
    (locks/'0.input.lock').write_text('/software/fixture\n')
    output = directory.parent/'preserved.txt'
    output.write_text('retained output')
    run = {'id':'run-fixture','folder':str(directory.parent),'commit':'immutable-fixture','workflow_sha256':tree_digest(directory)}
    store.put('runs', run['id'], run)
    monkeypatch.setattr(unlock,'validate_launch',lambda *a,**k: copy.deepcopy(store.get('runs',run['id'])))
    monkeypatch.setattr(unlock,'command',lambda *a: [sys.executable,'-m','snakemake','--snakefile',str(directory/'Snakefile'),'--directory',str(directory),'--configfile',str(config),'--dry-run','--forcerun','unwanted_analysis'])
    monkeypatch.setattr('bactologix.engine.launch_worker',lambda store: None)
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda: [])
    return store,run,locks,output


def test_native_unlock_is_maintenance_only_and_keeps_queue_paused(locked):
    store,run,locks,output=locked
    original=copy.deepcopy(run)
    plan=unlock.preview(store,run['id'])
    assert plan['allowed'] and plan['lock_count']==1
    jid=unlock.request(store,run['id'],plan['signature'])
    assert store.queue_snapshot()['paused']
    job=store.claim()
    assert job['id']==jid and job['kind']=='workflow_unlock'
    seen=[]
    def command(argv,**kwargs):
        seen.append(argv)
        subprocess.run(argv,check=True,capture_output=True,text=True)
    with unlock.ownership(store):
        unlock.execute(SimpleNamespace(store=store,job=job,write=lambda x:None,command=command))
    assert seen[0][-1]=='--unlock'
    assert '--forcerun' not in seen[0] and '--dry-run' not in seen[0]
    assert not locks.exists() or not list(locks.iterdir())
    assert output.read_text()=='retained output' and store.get('runs',run['id'])==original
    store.update_job(jid,status='succeeded')
    queued=store.submit('smoke',{})
    assert store.claim() is None and store.job(queued)['status']=='queued'
    assert not unlock.preview(store,run['id'])['allowed']


def test_live_lease_blocks_even_without_process_identity(locked):
    store,run,_,_=locked
    with unlock.ownership(store):
        result=unlock.preview(store,run['id'])
        assert not result['allowed'] and 'currently in use' in result['message']


@pytest.mark.parametrize('mode',['active','denied','gone'])
def test_external_process_scan_is_conservative(locked,monkeypatch,mode):
    store,run,_,_=locked
    def args():
        if mode=='denied':raise psutil.AccessDenied(123)
        if mode=='gone':raise psutil.NoSuchProcess(123)
        return ['python','-m','snakemake','--directory','/another/workflow']
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[SimpleNamespace(pid=123,cmdline=args,uids=lambda:SimpleNamespace(real=os.getuid(),effective=os.getuid(),saved=os.getuid()))])
    assert unlock.preview(store,run['id'])['allowed'] == (mode=='gone')


def test_stale_review_and_changed_source_are_rejected(locked):
    store,run,locks,_=locked
    plan=unlock.preview(store,run['id'])
    (locks/'0.input.lock').write_text('changed')
    with pytest.raises(ValueError,match='changed'):unlock.request(store,run['id'],plan['signature'])
    assert store.jobs()==[]
    (Path(run['folder'])/'workflow/Snakefile').write_text('changed source')
    assert not unlock.preview(store,run['id'])['allowed']


def test_worker_rechecks_external_owners_and_never_calls_command(locked,monkeypatch):
    store,run,_,_=locked
    plan=unlock.preview(store,run['id'])
    unlock.request(store,run['id'],plan['signature'])
    job=store.claim()
    monkeypatch.setattr(unlock,'check_processes',lambda *_: (_ for _ in ()).throw(ValueError('busy')))
    with pytest.raises(ValueError,match='busy'):
        unlock.execute(SimpleNamespace(store=store,job=job,command=lambda *a,**k:pytest.fail('must not execute')))


def test_ui_recognizes_lock_error_and_exposes_review(locked):
    from bactologix.readiness import job_guidance
    store,run,_,_=locked
    jid=store.submit('workflow',{'run_id':run['id'],'action':'run'})
    store.update_job(jid,status='failed')
    store.log_path(jid).write_text('LockException:\nError: Directory cannot be locked.\n')
    assert job_guidance(store,store.job(jid))['category']=='workflow_lock'
    result=dispatch(store,'inspect_workflow_lock',{'id':run['id']})
    assert result['unlock_plan']['allowed']
    assert store.jobs()[0]['status']=='failed'


def test_other_same_user_process_in_workflow_directory_blocks_unlock(locked, monkeypatch):
    import os
    store,run,_,_=locked
    process=SimpleNamespace(pid=123,cmdline=lambda:['python','software-task.py'],
                            uids=lambda:SimpleNamespace(real=os.getuid(),effective=os.getuid(),saved=os.getuid()),
                            cwd=lambda:str(Path(run['folder'])/'workflow'))
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[process])
    result=unlock.preview(store,run['id'])
    assert not result['allowed'] and 'working in this directory' in result['message']


@pytest.mark.parametrize('mode', ['protected_foreign', 'protected_same_user', 'unknown_uid', 'foreign_snakemake', 'shared_foreign', 'mixed_uid'])
def test_protected_process_ownership_scope(locked, monkeypatch, mode):
    store,run,_,_=locked
    uid=os.getuid()
    def owners():
        if mode=='unknown_uid': raise psutil.AccessDenied(343)
        return SimpleNamespace(real=uid if mode=='protected_same_user' else uid+1,
                               effective=uid if mode in {'protected_same_user','mixed_uid'} else uid+1,
                               saved=uid if mode=='protected_same_user' else uid+1)
    def cmdline():
        if mode=='foreign_snakemake': return ['python','-m','snakemake']
        raise psutil.AccessDenied(343)
    if mode=='shared_foreign': (Path(run['folder'])/'workflow').chmod(0o777)
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[SimpleNamespace(pid=343,uids=owners,cmdline=cmdline)])
    result=unlock.preview(store,run['id'])
    assert result['allowed'] == (mode=='protected_foreign')
    assert store.jobs()==[]


def test_foreign_protected_process_with_acl_remains_blocked(locked, monkeypatch):
    store,run,_,_=locked
    process=SimpleNamespace(pid=343,uids=lambda:SimpleNamespace(real=os.getuid()+1,effective=os.getuid()+1,saved=os.getuid()+1),
                            cmdline=lambda:(_ for _ in ()).throw(psutil.AccessDenied(343)))
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[process])
    monkeypatch.setattr(unlock.os,'listxattr',lambda path:['system.posix_acl_access'])
    assert not unlock.preview(store,run['id'])['allowed']
