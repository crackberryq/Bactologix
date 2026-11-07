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


def _trusted_systemd_executable(value):
    """Accept the OS binary only, including root-owned, non-writable ancestors."""
    path = Path(value).resolve()
    allowed = {Path('/usr/lib/systemd/systemd').resolve(), Path('/lib/systemd/systemd').resolve()}
    if path not in allowed or not path.is_file():
        return False
    return all(p.stat().st_uid == 0 and not (p.stat().st_mode & 0o022)
               for p in (path, *path.parents))


def _manager_cgroups(pid):
    paths = []
    for line in Path('/proc', str(pid), 'cgroup').read_text().splitlines():
        hierarchy, controllers, path = line.split(':', 2)
        if (hierarchy == '0' and not controllers) or 'name=systemd' in controllers.split(','):
            paths.append(path)
    return paths


def protected_session_helper(proc, uid):
    """Identify systemd's protected PAM helper; a process name alone is insufficient.

    Ubuntu's user-manager PAM child drops to the user's UID, then waits for its
    parent to terminate. Reading its cwd can be denied despite matching UIDs.
    Verify the exact name, user-manager parent, OS executable and control groups.
    Unknown or inaccessible evidence retains the normal unlock block.
    """
    try:
        if proc.name() != '(sd-pam)':
            return False
        owners = proc.uids()
        if (owners.real, owners.effective, owners.saved) != (uid, uid, uid):
            return False
        parent = proc.parent()
        if parent is None or proc.ppid() != parent.pid:
            return False
        parent_owners = parent.uids()
        if (parent_owners.real, parent_owners.effective, parent_owners.saved) != (uid, uid, uid):
            return False
        args = parent.cmdline()
        if len(args) != 2 or args[1] != '--user' or not _trusted_systemd_executable(parent.exe()):
            return False
        if Path(args[0]).resolve() != Path(parent.exe()).resolve():
            return False
        base = '/user.slice/user-%s.slice/user@%s.service' % (uid, uid)
        allowed = {base, base + '/init.scope'}
        child_groups, parent_groups = _manager_cgroups(proc.pid), _manager_cgroups(parent.pid)
        if not child_groups or not parent_groups or any(g not in allowed for g in child_groups + parent_groups):
            return False
        return proc.is_running() and parent.is_running() and proc.ppid() == parent.pid
    except (OSError, ValueError, psutil.Error):
        return False


def check_processes(directory):
    """Inspect user-owned work without requiring access to protected OS services.

    Foreign protected processes are outside this user-workspace check only when
    the workflow and lock directories are owned by this user and not writable by
    other users. Known Snakemake processes still block regardless of their UID.
    Unknown UIDs and inaccessible same-user processes block, except verified
    systemd user-manager PAM helpers in a private workflow directory.
    """
    uid = os.getuid()
    private = True
    for path in (Path(directory), Path(directory) / '.snakemake', Path(directory) / '.snakemake/locks'):
        info = path.stat()
        private = private and info.st_uid == uid and not (info.st_mode & 0o022)
        # A named ACL can grant access beyond the traditional permission bits.
        if hasattr(os, 'listxattr'):
            private = private and 'system.posix_acl_access' not in os.listxattr(path)
    ignored_helpers = []
    for proc in psutil.process_iter():
        inspected_field = "user identity"
        try:
            if proc.pid == os.getpid():
                continue
            try:
                owners = proc.uids()
            except psutil.AccessDenied:
                raise ValueError('Ownership could not be verified: the user identity of process %s is unavailable. Unlock is blocked.' % proc.pid)
            same_user = uid in (owners.real, owners.effective, owners.saved)
            inspected_field = "command line"
            try:
                args = proc.cmdline()
            except psutil.AccessDenied:
                if not same_user and private:
                    continue  # Protected foreign service, not a user-owned workflow.
                raise
            if any(Path(arg).name in {'snakemake', 'snakemake.exe'} for arg in args):
                raise ValueError('Workflow currently in use: Snakemake process %s is active. Wait for it to finish before checking again.' % proc.pid)
            if args and (same_user or not private):
                inspected_field = "working directory"
                cwd = Path(proc.cwd()).resolve()
                if cwd == Path(directory) or Path(directory) in cwd.parents:
                    raise ValueError('Workflow currently in use: process %s is working in this directory. Wait for it to finish.' % proc.pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            if private and same_user and protected_session_helper(proc, uid):
                ignored_helpers.append(proc.pid)
                continue
            raise ValueError('Ownership could not be verified: process %s cannot be inspected (%s). Unlock is blocked.' % (proc.pid, inspected_field))
    return ignored_helpers


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
            ignored_helpers = check_processes(plan['directory']) or []
        return {**{k:v for k,v in plan.items() if k != 'command'}, 'allowed': True, 'ignored_session_helpers': ignored_helpers,
                'message': 'Leftover lock found; no relevant active process was detected in the user-workspace check. Ownership will be checked again before unlocking. Outputs are preserved and no analysis starts.' + (' Verified systemd PAM session helpers excluded: ' + ', '.join(map(str, ignored_helpers)) + '.' if ignored_helpers else '')}
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
    ignored_helpers = check_processes(plan['directory']) or []
    if ignored_helpers:
        ctx.write('Verified systemd PAM session helpers excluded from workflow ownership: ' + ', '.join(map(str, ignored_helpers)))
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

