"""Explicit native lock recovery. Never remove files or launch analysis targets."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import hashlib
import json
import os
import psutil
from .runs import command, validate_launch, tree_digest


@contextmanager
def ownership(store):
    with (store.home / 'worker.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Workflow currently in use: a worker or surviving child owns this workspace.')
        yield


def check_processes(directory):
    # Conservatively block ANY external Snakemake, including other directories.
    # Inaccessible process identity means ownership cannot be verified.
    for proc in psutil.process_iter():
        try:
            if proc.pid == os.getpid():
                continue
            args = proc.cmdline()
            if any(Path(arg).name in {'snakemake', 'snakemake.exe'} for arg in args):
                raise ValueError('Workflow currently in use: Snakemake process %s is active. Wait for it to finish before checking again.' % proc.pid)
            if args and proc.uids().real == os.getuid():
                cwd = Path(proc.cwd()).resolve()
                if cwd == Path(directory) or Path(directory) in cwd.parents:
                    raise ValueError('Workflow currently in use: process %s is working in this directory. Wait for it to finish.' % proc.pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            raise ValueError('Ownership could not be verified: process %s cannot be inspected. Unlock is blocked.' % proc.pid)


def details(store, rid, own_job=None):
    if any(j['status'] in {'queued', 'running'} and j['id'] != own_job for j in store.jobs()):
        raise ValueError('Finish or cancel other queued/running jobs before unlocking.')
    run = validate_launch(store, rid, 'dry-run')
    source = Path(run['folder']) / 'workflow'
    if tree_digest(source) != run['workflow_sha256']:
        raise ValueError('Saved workflow source changed. Unlock is blocked.')
    original = command(store, run, 'dry-run')
    directory = Path(original[original.index('--directory') + 1]).resolve()
    locks = directory / '.snakemake' / 'locks'
    if locks.is_symlink() or (directory / '.snakemake').is_symlink():
        raise ValueError('Lock location is redirected. Unlock is blocked.')
    files = sorted(locks.iterdir()) if locks.exists() else []
    if not files:
        raise ValueError('No workflow lock remains. Review the run before Run / resume.')
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError('Unrecognized lock entries. Unlock is blocked.')
    records = [(p.name, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]
    argv = original[:original.index('snakemake') + 1]
    for option in ('--snakefile', '--directory', '--configfile'):
        argv.extend([option, original[original.index(option) + 1]])
    argv += ['--cores', '1', '--unlock']
    signature = hashlib.sha256(json.dumps([run, argv, records], sort_keys=True).encode()).hexdigest()
    return {'id': rid, 'directory': str(directory), 'commit': run['commit'], 'signature': signature,
            'lock_count': len(files), 'command': argv}


def preview(store, rid):
    try:
        with ownership(store):
            from .queue import recover_interrupted
            recover_interrupted(store)
            plan = details(store, rid)
            check_processes(plan['directory'])
        return {**{k:v for k,v in plan.items() if k != 'command'}, 'allowed': True,
                'message': 'Leftover lock found; no active Snakemake process was detected. Ownership will be checked again before unlocking. Outputs are preserved and no analysis starts.'}
    except (ValueError, OSError) as exc:
        return {'id': rid, 'allowed': False, 'message': str(exc)}


def request(store, rid, signature):
    with ownership(store):
        plan = details(store, rid)
        check_processes(plan['directory'])
        if not isinstance(signature, str) or signature != plan['signature']:
            raise ValueError('The run or lock changed. Check the workflow lock again.')
        from .queue import control, save_control
        # Pause before submission while holding the worker lease. Submission
        # retains the standard desktop ownership and application-update guards.
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
                raise ValueError('The queue changed. Check the workflow lock again.')
            state = control(db)
            state.update(paused=True, pause_reason='workflow_unlock')
            save_control(db, state)
        payload = {'run_id': rid, 'signature': signature, 'action': 'unlock'}
        jid = store.submit('workflow_unlock', payload)
    from .engine import launch_worker
    launch_worker(store)
    return jid


def execute(ctx):
    # The worker and native command inherit the workspace lock throughout.
    plan = details(ctx.store, ctx.job['payload']['run_id'], ctx.job['id'])
    check_processes(plan['directory'])
    if plan['signature'] != ctx.job['payload']['signature']:
        raise ValueError('The run or lock changed since review. Check the workflow lock again.')
    ctx.write('Explicit workflow unlock · source ' + plan['commit'])
    ctx.write('Directory: ' + plan['directory'])
    ctx.write('Maintenance only: no analysis, output deletion or automatic resume.')
    ctx.command(plan['command'], cwd=plan['directory'], timeout=120)
    locks = Path(plan['directory']) / '.snakemake/locks'
    if locks.exists() and any(locks.iterdir()):
        raise ValueError('Snakemake returned, but lock entries remain. Inspect the unlock log.')
    ctx.write('Workflow unlocked. Queue remains paused. Review the saved run before Run / resume.')
