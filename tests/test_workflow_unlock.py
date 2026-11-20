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
import yaml
from bactologix import workflow_unlock as unlock
from bactologix import runs
from bactologix.store import Store
from bactologix.runs import tree_digest
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample


@pytest.fixture
def low_swap_locked(workspace, installation, sample, monkeypatch):
    """Exercise real launch validation with the reported maintenance shortfall."""
    from bactologix import compute_memory, hardware
    run = runs.create_run(workspace, 'hybas', sample,
                          compute_memory.with_swap('{}', 98160), 12, 46080)
    run['dry_run_ok'] = True
    workspace.put('runs', run['id'], run)
    directory = Path(run['folder']) / 'workflow'
    locks = directory / '.snakemake/locks'
    locks.mkdir(parents=True)
    (locks / '0.input.lock').write_text('/software/fixture\n')
    output = Path(run['output']) / 'preserved-result.txt'
    output.write_text('retained output')
    inventory_calls = []

    def capacity():
        inventory_calls.append(True)
        return {'total_mb': 49152, 'available_mb': 49004,
                'swap_mb': 98304, 'swap_available_mb': 73728,
                'combined_total_mb': 147456, 'combined_available_mb': 122732}

    monkeypatch.setattr(hardware, 'memory_capacity', capacity)
    monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/fixture/conda')
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda _: None)
    monkeypatch.setattr(unlock.psutil, 'process_iter', lambda: [])
    return workspace, run, locks, output, inventory_calls


def test_low_swap_unlock_preserves_run_and_keeps_analysis_capacity_checks(low_swap_locked):
    store, run, locks, output, inventory_calls = low_swap_locked
    original = copy.deepcopy(run)
    folder = Path(run['folder'])
    preserved = [output, folder / 'config.yaml', folder / 'samples.tsv',
                 folder / 'bactologix-run.json',
                 *Path(run['sample']['path']).glob('raw/illumina/*.fastq')]
    contents = {path: path.read_bytes() for path in preserved}

    for action in ('dry-run', 'run'):
        with pytest.raises(ValueError, match='Pre-run memory checks:') as error:
            runs.validate_launch(store, run['id'], action)
        assert '98,160 MB swap allowance exceeds the 72,704 MB usable free swap' in str(error.value)
        assert '144,240 MB scheduling budget exceeds the 119,251 MB' in str(error.value)
    assert inventory_calls == [True, True]

    plan = unlock.preview(store, run['id'])
    assert plan['allowed'], plan['message']
    jid = unlock.request(store, run['id'], plan['signature'])
    job = store.claim()
    assert job['id'] == jid and job['kind'] == 'workflow_unlock'
    executed = []

    def native(argv, **kwargs):
        executed.append(argv)
        start = argv.index('snakemake') + 1
        result = subprocess.run([sys.executable, '-m', 'snakemake', *argv[start:]],
                                cwd=kwargs['cwd'], capture_output=True, text=True,
                                timeout=kwargs['timeout'])
        assert result.returncode == 0, result.stdout + result.stderr

    with unlock.ownership(store):
        unlock.execute(SimpleNamespace(store=store, job=job, write=lambda _: None,
                                       command=native))
    assert len(executed) == 1 and executed[0][-1] == '--unlock'
    assert 'mem_mb=144240' in executed[0]
    assert not {'--dry-run', '--forcerun', '--use-conda', '--rerun-incomplete'}.intersection(executed[0])
    assert not locks.exists() or not list(locks.iterdir())
    assert inventory_calls == [True, True]  # Preview, queue and worker skip only admission.
    assert store.get('runs', run['id']) == original
    assert all(path.read_bytes() == content for path, content in contents.items())
    assert store.queue_snapshot()['paused']
    store.update_job(jid, status='succeeded')
    with pytest.raises(ValueError, match='Pre-run memory checks:'):
        runs.validate_launch(store, run['id'], 'run')
    assert inventory_calls == [True, True, True]


@pytest.mark.parametrize('failure', ['working_directory', 'configuration', 'lock_redirect', 'worker_lease'])
def test_low_swap_unlock_retains_non_memory_guards(low_swap_locked, monkeypatch, tmp_path, failure):
    from bactologix import preflight
    store, run, locks, output, inventory_calls = low_swap_locked
    if failure == 'working_directory':
        access = preflight.os.access
        directory = Path(run['folder']) / 'workflow'
        monkeypatch.setattr(preflight.os, 'access',
                            lambda path, mode: False if Path(path) == directory else access(path, mode))
        expected = 'Pre-run checks:'
    elif failure == 'configuration':
        Path(run['folder'], 'config.yaml').write_text('changed: true\n')
        expected = 'Saved configuration changed'
    elif failure == 'lock_redirect':
        target = tmp_path / 'redirected-locks'
        locks.rename(target)
        locks.symlink_to(target, target_is_directory=True)
        expected = 'Lock location is redirected'
    else:
        with unlock.ownership(store):
            plan = unlock.preview(store, run['id'])
        assert not plan['allowed'] and 'currently in use' in plan['message']
        assert inventory_calls == [] and store.jobs() == []
        return
    plan = unlock.preview(store, run['id'])
    assert not plan['allowed'] and expected in plan['message']
    assert inventory_calls == [] and store.jobs() == []
    assert output.read_text() == 'retained output'


@pytest.mark.parametrize('stage', ['preview', 'request', 'execute'])
def test_low_swap_unlock_rechecks_active_processes(low_swap_locked, monkeypatch, stage):
    store, run, locks, output, inventory_calls = low_swap_locked
    if stage != 'preview':
        plan = unlock.preview(store, run['id'])
        assert plan['allowed']
    if stage == 'execute':
        jid = unlock.request(store, run['id'], plan['signature'])
        job = store.claim()
        assert job['id'] == jid
    process = SimpleNamespace(pid=123, cmdline=lambda: ['python', '-m', 'snakemake'],
                              uids=lambda: SimpleNamespace(real=os.getuid(),
                                                           effective=os.getuid(), saved=os.getuid()))
    monkeypatch.setattr(unlock.psutil, 'process_iter', lambda: [process])
    if stage == 'preview':
        plan = unlock.preview(store, run['id'])
        assert not plan['allowed'] and 'Snakemake process 123 is active' in plan['message']
    elif stage == 'request':
        with pytest.raises(ValueError, match='Snakemake process 123 is active'):
            unlock.request(store, run['id'], plan['signature'])
    else:
        with unlock.ownership(store), pytest.raises(ValueError, match='Snakemake process 123 is active'):
            unlock.execute(SimpleNamespace(store=store, job=job,
                                           command=lambda *_a, **_k: pytest.fail('Active work must prevent native unlock')))
    assert (locks / '0.input.lock').exists()
    assert output.read_text() == 'retained output'
    assert inventory_calls == []
    if stage != 'execute':
        assert store.jobs() == []


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
    monkeypatch.setattr(unlock,'command',lambda *a: [sys.executable,'-m','snakemake','--snakefile',str(directory/'Snakefile'),'--directory',str(directory),'--configfile',str(config),'--resources','mem_mb=16000','kraken_jobs=1','--dry-run','--forcerun','unwanted_analysis'])
    monkeypatch.setattr('bactologix.engine.launch_worker',lambda store: None)
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda: [])
    return store,run,locks,output


@pytest.mark.parametrize('with_pam', [False, True])
def test_native_unlock_is_maintenance_only_and_keeps_queue_paused(locked,monkeypatch,with_pam):
    store,run,locks,output=locked
    if with_pam:
        child=pam_fixture(monkeypatch)
        monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[child])
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


@pytest.mark.parametrize('ram_mb,swap_mb', [(28672, 98304), (131072, 0)])
def test_native_unlock_keeps_saved_resources_over_profile_defaults(locked, monkeypatch, ram_mb, swap_mb):
    """Unlock parses native guards but must never execute an analysis target."""
    store, run, locks, output = locked
    directory = Path(run['folder']) / 'workflow'
    (directory / 'Hybas.smk').write_text('''
budget = workflow.global_resources.get("mem_mb")
budget = int(getattr(budget, "value", budget))
if budget < config["reservation_mb"]:
    raise ValueError(f"fixture reservation exceeds scheduler budget: {budget}")
rule all:
    input: "unexpected-analysis.txt"
rule analysis:
    output: "unexpected-analysis.txt"
    shell: "echo should-never-run > {output}"
''')
    profile = directory / 'profiles/default'
    profile.mkdir(parents=True)
    (profile / 'config.yaml').write_text('resources:\n  mem_mb: 16000\n')
    config = {'reservation_mb': 113868, 'assembly_mem_mb': 8192}
    if swap_mb:
        config['bactologix_compute'] = {'swap_mb': swap_mb}
    frozen = yaml.safe_dump(config).encode()
    (directory.parent / 'config.yaml').write_bytes(frozen)
    run.update(tool='hybas', cores=12, memory_mb=ram_mb, config=frozen.decode(),
               workflow_sha256=tree_digest(directory))
    store.put('runs', run['id'], run)
    original_run = copy.deepcopy(run)
    monkeypatch.setattr(unlock, 'command', runs.command)
    monkeypatch.setattr(runs, 'current_controller', lambda *_: sys.prefix)
    monkeypatch.setattr('bactologix.engine.find_conda', lambda *_: '/fixture/conda')

    def native(argv):
        # Exercise the app-generated native argv without installing or invoking
        # a Conda environment. The disposable workflow contains no real data.
        start = argv.index('snakemake') + 1
        return subprocess.run([sys.executable, '-m', 'snakemake', *argv[start:]],
            cwd=directory, capture_output=True, text=True, timeout=60)

    planned = unlock.details(store, run['id'])['command']
    assert f'mem_mb={ram_mb + swap_mb}' in planned
    assert 'kraken_jobs=1' in planned and planned[-1] == '--unlock'
    assert planned[planned.index('--cores') + 1] == '1'
    # The former unlock command omitted resources and loaded the 16,000 MB
    # profile default. Prove that this prevents even maintenance from parsing.
    legacy = planned[:]
    start = legacy.index('--resources')
    del legacy[start:legacy.index('--unlock')]
    failed = native(legacy)
    assert failed.returncode != 0
    assert 'fixture reservation exceeds scheduler budget: 16000' in failed.stdout + failed.stderr
    assert (locks / '0.input.lock').exists()
    assert not (directory / 'unexpected-analysis.txt').exists()

    plan = unlock.preview(store, run['id'])
    assert plan['allowed']
    jid = unlock.request(store, run['id'], plan['signature'])
    job = store.claim()
    assert job['id'] == jid
    seen = []
    def execute(argv, **kwargs):
        seen.append(argv)
        result = native(argv)
        assert result.returncode == 0, result.stdout + result.stderr
    with unlock.ownership(store):
        unlock.execute(SimpleNamespace(store=store, job=job, write=lambda _: None, command=execute))
    assert len(seen) == 1 and '--dry-run' not in seen[0] and '--forcerun' not in seen[0]
    assert '--use-conda' not in seen[0] and '--rerun-incomplete' not in seen[0]
    assert not locks.exists() or not list(locks.iterdir())
    assert not (directory / 'unexpected-analysis.txt').exists()
    assert output.read_text() == 'retained output'
    assert (directory.parent / 'config.yaml').read_bytes() == frozen
    assert store.get('runs', run['id']) == original_run
    assert store.queue_snapshot()['paused']


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
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[SimpleNamespace(pid=123,name=lambda:'software-task',cmdline=args,uids=lambda:SimpleNamespace(real=os.getuid(),effective=os.getuid(),saved=os.getuid()))])
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
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[SimpleNamespace(pid=343,name=lambda:'software-task',uids=owners,cmdline=cmdline)])
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



def pam_fixture(monkeypatch, mode='valid'):
    uid=os.getuid()
    identities=lambda:SimpleNamespace(real=uid,effective=uid,saved=uid)
    parent=SimpleNamespace(pid=342,uids=identities,cmdline=lambda:['/usr/lib/systemd/systemd','--user'],
        exe=lambda:'/usr/lib/systemd/systemd',is_running=lambda:True)
    child=SimpleNamespace(pid=344,uids=identities,name=lambda:'(sd-pam)',parent=lambda:parent,
        ppid=lambda:parent.pid,is_running=lambda:True,
        cmdline=lambda:['(sd-pam)'],cwd=lambda:(_ for _ in ()).throw(psutil.AccessDenied(344)))
    base='/user.slice/user-%s.slice/user@%s.service' % (uid,uid)
    monkeypatch.setattr(unlock,'_manager_cgroups',lambda pid:[base if pid==344 else base+'/init.scope'])
    monkeypatch.setattr(unlock,'_trusted_systemd_executable',lambda value:value=='/usr/lib/systemd/systemd')
    if mode=='name_only': parent.exe=lambda:'/tmp/systemd'
    if mode=='not_user_manager': parent.cmdline=lambda:['/usr/lib/systemd/systemd','--system']
    if mode=='other_name': child.name=lambda:'my-job'
    if mode=='app_cgroup': monkeypatch.setattr(unlock,'_manager_cgroups',lambda pid:[base+'/app.slice/my-job.service'])
    if mode=='missing_cgroup': monkeypatch.setattr(unlock,'_manager_cgroups',lambda pid:[])
    if mode=='denied_parent': parent.exe=lambda:(_ for _ in ()).throw(psutil.AccessDenied(342))
    if mode=='changed_parent': child.ppid=lambda:999
    if mode=='gone_parent': parent.is_running=lambda:False
    if mode=='mixed_uid': child.uids=lambda:SimpleNamespace(real=uid,effective=uid,saved=uid+1)
    if mode=='cmdline_denied': child.cmdline=lambda:(_ for _ in ()).throw(psutil.AccessDenied(344))
    if mode=='known_snakemake': child.cmdline=lambda:['python','-m','snakemake']
    return child


@pytest.mark.parametrize('mode',['valid','cmdline_denied','name_only','not_user_manager','other_name','app_cgroup',
    'missing_cgroup','denied_parent','changed_parent','gone_parent','mixed_uid','known_snakemake','shared_workflow'])
def test_pam_helper_requires_verified_manager_and_scope(locked,monkeypatch,mode):
    store,run,locks,output=locked
    child=pam_fixture(monkeypatch,mode)
    if mode=='shared_workflow':Path(run['folder'],'workflow').chmod(0o777)
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[child])
    plan=unlock.preview(store,run['id'])
    assert plan['allowed']==(mode in {'valid','cmdline_denied'})
    if plan['allowed']:
        assert plan['ignored_session_helpers']==[344]
        assert 'Verified systemd PAM' in plan['message']
    assert output.read_text()=='retained output'
    assert (locks/'0.input.lock').exists() and store.jobs()==[]


def test_verified_pam_helper_does_not_hide_active_workflow(locked,monkeypatch):
    store,run,_,_=locked
    child=pam_fixture(monkeypatch)
    active=SimpleNamespace(pid=345,uids=child.uids,cmdline=lambda:['python','-m','snakemake'])
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[child,active])
    plan=unlock.preview(store,run['id'])
    assert not plan['allowed'] and 'Snakemake process 345' in plan['message']


def test_pam_identity_rechecked_before_native_unlock(locked,monkeypatch):
    store,run,_,_=locked
    child=pam_fixture(monkeypatch)
    monkeypatch.setattr(unlock.psutil,'process_iter',lambda:[child])
    plan=unlock.preview(store,run['id'])
    unlock.request(store,run['id'],plan['signature'])
    job=store.claim()
    child.parent=lambda:None
    with pytest.raises(ValueError,match='working directory'):
        unlock.execute(SimpleNamespace(store=store,job=job,write=lambda _:None,
            command=lambda *a,**k:pytest.fail('must not unlock after ownership proof changes')))


def test_systemd_binary_check_rejects_user_executable(tmp_path):
    binary=tmp_path/'systemd';binary.write_text('fake')
    assert not unlock._trusted_systemd_executable(str(binary))


def test_lock_free_check_skips_source_and_sample_reads(low_swap_locked, monkeypatch):
    store, run, locks, _, _ = low_swap_locked
    (locks / '0.input.lock').unlink()
    monkeypatch.setattr(unlock, 'validate_launch', lambda *a, **kw: pytest.fail('No preflight for an absent lock'))
    monkeypatch.setattr(unlock, 'command', lambda *a: pytest.fail('No command for an absent lock'))
    plan = unlock.preview(store, run['id'])
    assert not plan['allowed'] and 'No workflow lock remains' in plan['message']


def test_checked_lock_preserves_input_validation_in_worker(low_swap_locked, monkeypatch):
    from test_input_identity import replace_preserving_metadata
    store, run, locks, _, _ = low_swap_locked
    fingerprint = runs._fingerprint
    reads = []

    def count_read(path):
        reads.append(path)
        return fingerprint(path)

    monkeypatch.setattr(runs, '_fingerprint', count_read)
    plan = unlock.preview(store, run['id'])
    assert plan['allowed']
    jid = unlock.request(store, run['id'], plan['signature'])
    assert reads == []
    job = store.claim()
    assert job['id'] == jid
    replace_preserving_metadata(Path(run['inputs'][0]['path']))
    with unlock.ownership(store), pytest.raises(ValueError, match='SHA-256 content mismatch'):
        unlock.execute(SimpleNamespace(store=store, job=job,
                                      command=lambda *_a, **_kw: pytest.fail('Changed input must block native unlock')))
    assert reads and (locks / '0.input.lock').is_file()
