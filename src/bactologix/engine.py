"""Explicit operations executed by a detached, single-workspace worker."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from .catalog import PIPELINES, api, supported
from .controllers import controller_requirements, controller_check_code
from .store import Store, now, transient_lock
from .supervision import MonitoringInterrupted


SETUP_CONTROL_GRACE_SECONDS = 30.0
WORKFLOW_CONTROL_GRACE_SECONDS = 30.0
CONTROL_HOLD_SECONDS = 300.0


class Cancelled(Exception):
    pass


def find_conda(store):
    if store.channel == "dev":
        p = store.home / "tools/miniforge/bin/conda"
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    configured = store.get("settings", "conda", "")
    candidates = [configured, os.environ.get("CONDA_EXE"), shutil.which("conda"),
                  store.home / "tools/miniforge/bin/conda"]
    candidates += [Path.home() / p / "bin/conda" for p in ("miniforge3", "miniconda3", "anaconda3", "mambaforge")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    return None


def doctor(store):
    conda = find_conda(store)
    disk = shutil.disk_usage(store.home)
    return {"channel": store.channel, "application_environment": sys.prefix, "system": platform.system(), "architecture": platform.machine(), "python": platform.python_version(),
            "git": shutil.which("git"), "conda": conda, "workspace": str(store.home),
            "free_gb": round(disk.free / 2**30, 1), "cpus": os.cpu_count(),
            "pipeline_platform_supported": platform.system() == "Linux"}


def launch_worker(store):
    if platform.system() != "Linux":
        raise RuntimeError("Pipeline execution requires Linux or WSL2. Launch Bactologix inside WSL.")
    with (store.home / "logs/worker.log").open("ab") as log:
        env = dict(os.environ)
        owner = env.pop('BACTOLOGIX_DESKTOP_SESSION', None)
        args = ['--desktop-owner', owner] if owner else []
        subprocess.Popen([sys.executable, "-m", "bactologix.worker", "--home", str(store.home), *args],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=env)


def _check_queue_platform(store):
    if platform.system() != "Linux":
        raise RuntimeError("Run this app inside Linux or WSL2 to execute jobs.")
    from .updates import assert_current
    assert_current(store)


def enqueue_workflows(store, payloads):
    """Check the whole selection before enqueueing, binding each saved draft revision."""
    from .runs import validate_launch
    _check_queue_platform(store)
    checked = []
    for payload in payloads:
        run = validate_launch(store, payload["run_id"], payload["action"], full_inputs=False)
        checked.append((payload, run.get("draft_generation", 0)))
    queued = []
    try:
        for payload, generation in checked:
            queued.append(store.submit("workflow", payload, expected_draft_generation=generation))
    except Exception as exc:
        if queued:
            raise ValueError(f"Queued {len(queued)} job(s). {exc}") from exc
        raise
    finally:
        if queued:
            # A single workspace worker claims the queue in order, including
            # any attempts saved before a later submission failed.
            launch_worker(store)
    return queued


def enqueue(store, kind, payload):
    if kind == "workflow":
        return enqueue_workflows(store, [payload])[0]
    if kind not in {"install", "connect", "bootstrap", "check", "smoke", "workstation", "app_update", "environments", "database_download", "database_redownload", "database_build", "report_refresh", "storage_cleanup", "factory_reset", "isolate_export"}:
        raise ValueError("Unknown operation")
    _check_queue_platform(store)
    jid = store.submit(kind, payload)
    launch_worker(store)
    return jid


class Context:
    def __init__(self, store, job, lock_fd=None, environment_fd=None):
        self.store, self.job, self.lock_fd = store, job, lock_fd
        self.lock_fds = tuple(fd for fd in (lock_fd, environment_fd) if fd is not None)
        self.log = store.log_path(job["id"]).open("a", buffering=1)
        self.cancel_reason = None
        self.verified_desktop = None
        self.setup_control = (job['kind'] in {'install', 'connect', 'bootstrap', 'environments',
                                              'database_download', 'database_redownload', 'database_build'} or
                              (job['kind'] == 'check' and (job['payload'].get('setup') or job['payload'].get('repair'))))
        if job['kind'] == 'report_refresh' and job['payload'].get('mode') == 'measure-validation':
            self.setup_control = True
        self.workflow_control = job['kind'] == 'workflow'
        self.control_deadlines = {}
        self.control_owner = None
        self.control_held_at = None
        self.controls_restored_at = None
        self.monitor = {'id': job['id'], 'state': 'healthy', 'retry_count': 0}
        self.native_record = None
        run_id = job['payload'].get('run_id')
        self.source_commit = (store.get('runs', run_id, {}) if run_id else {}).get('commit')
        self.observer = None
        self.observer_warned = False
        try:
            from .telemetry import Recorder
            self.observer = Recorder(store, job)
        except Exception:
            self.observation_warning()
        self.disk_guard = None
        if self.setup_control or self.workflow_control:
            from .channels import environment
            from .disk_pause import Guard
            self.disk_guard = Guard(store, job, self.check_cancel_only, self.write,
                                    initial_env=environment(store.home), control_operation=self.control_operation)

        if self.observer is not None:
            self.observer.monitor_sources = self.monitor_sources

    def monitor_sources(self):
        disk = self.disk_guard.record if self.disk_guard else {}
        beat = None
        if self.verified_desktop:
            from .desktop import _lease_records
            proof, pulse = _lease_records(self.store, self.verified_desktop[0])
            beat = pulse.get('heartbeat') or proof.get('heartbeat')
        return disk, beat

    def observation_warning(self):
        if not self.observer_warned:
            self.observer_warned = True
            try:
                self.write("Resource observation is unavailable or incomplete; job execution continues.")
            except Exception:
                pass

    def observe(self, method, *args):
        # Observation must never change a command's result or prevent cancellation.
        if self.observer is not None:
            try:
                getattr(self.observer, method)(*args)
            except Exception:
                try:
                    self.observer.record['observer_errors'] = True
                except Exception:
                    pass
                self.observation_warning()

    def write(self, message):
        self.log.write(str(message) + "\n")

    @contextmanager
    def observe_download(self, name):
        """Count response bytes read directly by this worker between native commands."""
        self.observe("begin_download", name)
        try:
            yield lambda count: self.observe("received_download_bytes", count)
        finally:
            self.observe("end_download")

    def check_cancel(self):
        self.check_cancel_only()
        if self.disk_guard is not None:
            self.disk_guard.checkpoint()

    def check_cancel_only(self):
        if self.refresh_desktop_cancel():
            raise Cancelled(self.cancel_reason)

    def control_operation(self, operation, *args, poll=False, op_key=None, reconcile_final=False):
        """Keep supervising the native process during brief SQLite contention.

        A polling call can defer to the next tick. At a command boundary a real
        result is required. Track each operation separately: a working reader
        cannot establish that a blocked writer has recovered.
        """
        interrupted = self.monitor['state'] == 'interrupted'
        if not (self.setup_control or self.workflow_control) or (interrupted and not reconcile_final):
            return operation(*args)
        key = operation if op_key is None else op_key
        grace = SETUP_CONTROL_GRACE_SECONDS if self.setup_control else WORKFLOW_CONTROL_GRACE_SECONDS
        deadline = self.control_deadlines.get(key)
        if deadline is None:
            deadline = time.monotonic() + grace
        while True:
            try:
                result = operation(*args)
            except (sqlite3.Error, MonitoringInterrupted) as exc:
                cause = exc.__cause__ if isinstance(exc, MonitoringInterrupted) else exc
                if not isinstance(cause, sqlite3.Error) or not transient_lock(cause):
                    raise
                # Cancellation reads already report short retries through the
                # store callback. Other control reads may only report exhaustion.
                if not interrupted and (isinstance(exc, sqlite3.Error) or self.monitor['state'] != 'degraded'):
                    self.monitor_retry(exc, 1)
                self.control_deadlines[key] = deadline
                self.controls_restored_at = None
                remaining = deadline - time.monotonic()
                # A disk-status write publishes a reading that the guard
                # has already enforced. A busy writer must not suspend a
                # running process (or obstruct its shutdown). At command
                # boundaries we still require the current state to be saved.
                if poll and op_key == 'disk-status' and (self.control_owner is not None or self.cancel_reason):
                    return None
                # The owner was verified with SQLite before this command was
                # released. The caller checks its independent stop and live
                # desktop records on every deferred read, including this one.
                if (poll and op_key == 'job and desktop controls'
                        and self.control_owner is not None and self.verified_desktop is not None):
                    return None
                if remaining <= 0:
                    if poll and self.control_owner is not None and CONTROL_HOLD_SECONDS > 0:
                        self.hold_for_control(op_key or operation.__name__, cause)
                        return None
                    if isinstance(exc, sqlite3.Error):
                        raise MonitoringInterrupted('Workspace control persistence failed: ' + str(exc)) from exc
                    raise
                if poll:
                    return None
                time.sleep(min(.25, remaining))
            else:
                self.control_deadlines.pop(key, None)
                return result

    def hold_for_control(self, key, error):
        if self.control_held_at is not None:
            return
        try:
            self.verify_control_freeze()
        except MonitoringInterrupted as exc:
            raise MonitoringInterrupted(f'{exc} Blocked workspace operation: {key}.',
                                        cleanup_confirmed=False) from exc
        self.control_held_at = time.monotonic()
        if self.disk_guard is not None:
            self.disk_guard.control_suspended = True
        detail = 'Tool processes suspended while Bactologix retries workspace controls.'
        self.monitor_state('degraded', detail, control_hold=True, control_operation=key,
                           last_error=str(error))
        self.write(detail + ' ' + str(error))

    def verify_control_freeze(self):
        from .disk_process import STORAGE_SUSPEND_SECONDS, SuspensionError
        import psutil
        try:
            self.control_owner.tree.freeze(timeout=STORAGE_SUSPEND_SECONDS)
            self.control_owner.refresh()
        except (SuspensionError, psutil.Error) as exc:
            raise MonitoringInterrupted('Could not verify suspension during workspace control recovery: '
                                        + str(exc), cleanup_confirmed=False) from exc

    def service_control_hold(self):
        if self.control_held_at is None:
            return
        if time.monotonic() - self.control_held_at >= CONTROL_HOLD_SECONDS:
            raise MonitoringInterrupted('Could not verify workspace controls while tool processes were suspended: '
                                        + str(self.monitor.get('last_error', 'control access unavailable')))
        # Keep the same verified process tree stopped, including any newly
        # observed descendants. The independent EOF watcher still owns cleanup.
        self.verify_control_freeze()
        if self.control_deadlines:
            return
        guard = self.disk_guard
        if guard is not None and self.controls_restored_at is None:
            self.controls_restored_at = time.monotonic()
            guard.samples.wake.set()
            return
        if guard is not None and not guard.control_recovery_ready(self.controls_restored_at):
            return
        # The SQLite stop/desktop lease must be checked after disk sampling.
        # A newly deferred read keeps the processes suspended.
        if self.refresh_desktop_cancel(poll=True) or self.control_deadlines:
            return
        if guard is not None and not guard.control_recovery_ready(self.controls_restored_at):
            return
        storage_paused = guard is not None and guard.record['state'] == 'paused'
        if not storage_paused:
            from .disk_process import SuspensionError
            import psutil
            try:
                self.control_owner.tree.resume()
            except (SuspensionError, psutil.Error) as exc:
                raise MonitoringInterrupted('Could not resume the owned processes after workspace control recovery: '
                                            + str(exc), cleanup_confirmed=False) from exc
        if guard is not None:
            guard.control_suspended = False
        self.control_held_at = self.controls_restored_at = None
        detail = ('Workspace controls recovered; the storage pause remains active.' if storage_paused
                  else 'Workspace controls recovered; the same tool processes resumed.')
        self.monitor_state('recovered', detail, control_hold=False)
        self.write(detail)

    def refresh_desktop_cancel(self, *, poll=False):
        from . import desktop, disk_stop
        if self.cancel_reason is not None:
            return True
        self.cancel_reason = disk_stop.stop_reason(self.store, self.job)
        if not self.cancel_reason and self.job['kind'] != 'app_update':
            self.cancel_reason = desktop.job_stop_reason(self.store, self.job['id'])
        if self.cancel_reason:
            return True
        def read_reason():
            state = self.store.control_state(self.job['id'], self.monitor_retry)
            if state['cancel']:
                # A desktop close sets the same durable job flag.
                reason = 'A stop request was recorded for this job.'
                if state['owner'] and self.job['kind'] != 'app_update':
                    detail = desktop.recorded_session_stop(state['session'])
                    if detail:
                        reason += ' ' + detail
                return reason
            if self.job['kind'] != 'app_update' and state['owner']:
                reason = desktop.session_stop_reason(state['session'], store=self.store, owner=state['owner'],
                                                     job_id=self.job['id'])
                if not reason:
                    self.verified_desktop = (state['owner'], state['session'])
                return reason or ''
            self.verified_desktop = None
            return ''

        reason = self.control_operation(read_reason, poll=poll, op_key='job and desktop controls')
        if reason is None:
            if self.verified_desktop is not None:
                owner, session = self.verified_desktop
                self.cancel_reason = desktop.cached_session_stop_reason(self.store, owner, session)
            return bool(self.cancel_reason)
        if self.monitor['state'] == 'degraded' and not self.control_deadlines and self.control_held_at is None:
            self.monitor_state('recovered', 'Workspace control access recovered; native execution remained owned.')
        self.cancel_reason = reason or None
        return self.cancel_reason is not None

    def monitor_state(self, state, detail, **fields):
        from .supervision import save
        self.monitor.update(state=state, detail=detail, updated_at=now(), **fields)
        try:
            save(self.store, self.job['id'], self.monitor)
        except OSError:
            pass  # Diagnostics cannot disable the native-process cleanup path.

    def monitor_retry(self, exc, attempt):
        if self.monitor['state'] != 'degraded':
            try:
                self.write('Workspace control data is unavailable; retrying while retaining native process ownership: ' + str(exc))
            except OSError:
                pass
        detail = (self.monitor['detail'] if self.control_held_at is not None
                  else 'Retrying workspace control access; native work is still supervised.')
        self.monitor_state('degraded', detail,
                           retry_count=self.monitor['retry_count'] + 1, last_error=str(exc))

    def remember_processes(self, proc, argv, guard):
        """Record identities before opening the native exec gate and as children appear."""
        import psutil
        from .supervision import save
        if self.native_record is None:
            run_id = self.job['payload'].get('run_id')
            try:
                created, pgid = psutil.Process(proc.pid).create_time(), os.getpgid(proc.pid)
            except (psutil.NoSuchProcess, ProcessLookupError):
                # An ungated short command can finish before identity collection.
                # Popen is the authority for its own child's exit, never PID absence.
                if guard is not None or proc.poll() is None:
                    raise MonitoringInterrupted('Cannot establish the native process identity.', cleanup_confirmed=False)
                created, pgid = None, None
            self.native_record = {'pid': proc.pid, 'created': created,
                'pgid': pgid, 'job_id': self.job['id'], 'run_id': run_id,
                'source_commit': self.source_commit, 'started': now(), 'cleanup_confirmed': False,
                'ownership_complete': True,
                'command_sha256': hashlib.sha256(json.dumps([str(x) for x in argv]).encode()).hexdigest()}
        identities = ([{'pid': pid, 'created': created} for pid, created in sorted(guard.tree.members)]
                      if guard is not None and guard.tree is not None else
                      ([{'pid': self.native_record['pid'], 'created': self.native_record['created']}]
                       if self.native_record['created'] is not None else []))
        if self.native_record.get('identities') != identities:
            self.native_record['identities'] = identities
            save(self.store, self.job['id'], self.native_record, 'process')

    def complete_process_record(self, proc, confirmed, observed_returncode):
        from .supervision import save
        if self.native_record is None:
            return
        self.native_record.update(cleanup_confirmed=confirmed, finished=now() if confirmed else None,
                                  returncode=proc.returncode, observed_returncode=observed_returncode)
        save(self.store, self.job['id'], self.native_record, 'process')
        value = dict(self.native_record, pid=None if confirmed else self.native_record['pid'])
        # Confirmed cleanup can be reconciled independently of a control failure
        # that interrupted execution. It never clears that interruption.
        self.control_operation(self.store.put_process, self.job['id'], value,
                               op_key='final_process_record', reconcile_final=confirmed)

    def command(self, argv, cwd=None, extra_env=None, stdout=None, *, timeout=None, stop_after=None):
        import shlex
        self.check_cancel()
        self.write("$ " + shlex.join([str(x) for x in argv]))
        env = os.environ.copy()
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1", "HYBAS_PREPARE_BROWSER": "0"})
        env.update(extra_env or {})
        from .channels import environment
        env.update(environment(self.store.home))
        guard = self.disk_guard
        if guard is not None:
            guard.before_command(cwd, env)
        started_stop = None
        stop_reason = None
        command_started = time.monotonic()
        timed_out = False
        actual_argv = [str(x) for x in argv]
        from . import disk_process
        owner = guard if guard is not None else disk_process.ProcessOwner()
        gate_read, gate_write = os.pipe()
        actual_argv = [sys.executable, str(Path(disk_process.__file__).resolve()), '--exec', str(gate_read), *actual_argv]
        pass_fds = (*self.lock_fds, gate_read)
        try:
            proc = subprocess.Popen(actual_argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=stdout if stdout is not None else self.log,
                                stderr=self.log if stdout is not None else subprocess.STDOUT, start_new_session=True,
                                pass_fds=pass_fds)
        except BaseException:
            if gate_write is not None:
                os.close(gate_write)
            raise
        finally:
            if gate_read is not None:
                os.close(gate_read)
        failed = True
        cleanup_confirmed = False
        monitoring_error = None
        owned_tree = None
        native_started = False
        self.native_record = None
        try:
            owner.attach(proc)
            owned_tree = owner.tree
            self.remember_processes(proc, argv, owner)
            self.control_operation(self.store.put_process, self.job['id'], self.native_record)
            if (self.setup_control or self.workflow_control) and self.monitor['state'] == 'degraded':
                self.check_cancel_only()  # A stop may have arrived while the exec gate was closed.
            if guard is not None:
                # Recording ownership can outlast a storage reading. Recheck before releasing the command.
                guard.checkpoint()
            os.write(gate_write, b'1')
            native_started = True
            os.close(gate_write)
            gate_write = None
            self.control_owner = owner
            self.observe("begin_command", proc, argv[0])
            while proc.poll() is None:
                cancelled = self.refresh_desktop_cancel(poll=True)
                elapsed_command = time.monotonic() - command_started
                timed_out = timed_out or (timeout is not None and elapsed_command >= timeout)
                if timed_out:
                    owner.signal(signal.SIGKILL)
                if cancelled or (stop_after is not None and elapsed_command >= stop_after):
                    if started_stop is None:
                        started_stop = time.monotonic()
                        self.control_owner = None  # Nothing may suspend the command during teardown.
                        stop_reason = self.cancel_reason if cancelled else 'The command reached its scheduled stop.'
                        owner.stop()
                        self.write("Stopping owned processes gracefully (SIGINT)… " + stop_reason)
                    elapsed = time.monotonic() - started_stop
                    if elapsed > 30:
                        owner.signal(signal.SIGKILL if elapsed > 60 else signal.SIGTERM)
                if guard is not None and started_stop is None and not timed_out:
                    guard.tick(command_running=True)
                else:
                    owner.refresh()
                if started_stop is None and not timed_out:
                    self.service_control_hold()
                self.remember_processes(proc, argv, owner)
                self.observe("tick")
                time.sleep(0.25)
            if timed_out:
                self.check_cancel_only()  # A concurrent user stop keeps its cancellation status.
                raise TimeoutError("Software check command exceeded its time limit; its process group was stopped.")
            if started_stop is not None:
                raise Cancelled(stop_reason)
            if proc.returncode:
                raise RuntimeError(f"Command exited with code {proc.returncode}; see the job log")
            failed = False
        except MonitoringInterrupted as exc:
            monitoring_error = exc
            self.monitor_state('interrupted', str(exc), cleanup_confirmed=False)
            raise
        except TimeoutError:
            raise  # A native command timeout is distinct from control storage loss.
        except (sqlite3.Error, OSError) as exc:
            self.monitor_state('interrupted', 'Workspace control persistence failed: ' + str(exc), cleanup_confirmed=False)
            monitoring_error = MonitoringInterrupted(self.monitor['detail'])
            raise monitoring_error from exc
        finally:
            self.control_owner = None
            if self.control_held_at is not None:
                self.control_held_at = self.controls_restored_at = None
                if self.monitor['state'] == 'degraded':
                    detail = ('The stop request was received. Bactologix is closing the tool processes.'
                              if started_stop is not None else
                              'The command ended while Bactologix was retrying workspace controls.')
                    self.monitor_state('degraded', detail, control_hold=False)
                else:
                    self.monitor['control_hold'] = False
                if guard is not None:
                    guard.control_suspended = False
            primary_error = sys.exception() if failed else None
            # Capture an exit observed before teardown. The gated launcher or
            # a process we had to stop can report a different status afterwards.
            observed_returncode = proc.poll() if native_started and not timed_out and started_stop is None else None
            if gate_write is not None:
                os.close(gate_write)
            try:
                # Capture the latest descendants before detach releases the in-memory tree.
                if owner.tree is not None:
                    try:
                        owner.tree.refresh()
                        self.remember_processes(proc, argv, owner)
                    except Exception:
                        pass  # Never skip signals because inspection/persistence failed.
                if failed:
                    if owner.tree is None and proc.poll() is None:
                        proc.kill()  # The gated launcher has not spawned native work.
                owner.detach(failed=failed)
            finally:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                self.observe("end_command")
                from .supervision import verify_exit
                identities = (self.native_record or {}).get('identities', [])
                ownership_complete = not native_started or (owned_tree is not None and not owned_tree.inspection_failed)
                if self.native_record is not None:
                    self.native_record['ownership_complete'] = ownership_complete
                cleanup_confirmed = ownership_complete and proc.poll() is not None and (not identities or verify_exit(identities))
                if monitoring_error is not None:
                    monitoring_error.cleanup_confirmed = cleanup_confirmed
                details = [str(primary_error)] if primary_error is not None else []
                if observed_returncode is not None and (not failed or monitoring_error is not None):
                    details.append(f'Native command exited with code {observed_returncode}.')
                if not cleanup_confirmed:
                    details.append('Native cleanup could not be confirmed; execution remains locked for review.')
                if not cleanup_confirmed or self.monitor['state'] == 'interrupted':
                    self.monitor_state('interrupted', ' '.join(details), command_returncode=observed_returncode,
                                       cleanup_confirmed=cleanup_confirmed)
                try:
                    self.complete_process_record(proc, cleanup_confirmed, observed_returncode)
                except Exception as exc:
                    details.append('Final native process record could not be saved: ' + str(exc))
                    self.monitor_state('interrupted', ' '.join(details), command_returncode=observed_returncode,
                                       cleanup_confirmed=cleanup_confirmed, persistence_error=str(exc))
                    raise MonitoringInterrupted(self.monitor['detail'], cleanup_confirmed=cleanup_confirmed) from exc
                if not cleanup_confirmed:
                    raise MonitoringInterrupted(self.monitor['detail'], cleanup_confirmed=False)
        if (self.setup_control or self.workflow_control) and self.monitor['state'] == 'degraded':
            self.check_cancel_only()  # Reconcile control before advancing past a completed command.


def controller_key(tool, content):
    context = "|".join((tool, platform.system(), platform.machine(), "conda-controller-v1")).encode()
    return hashlib.sha256(context + b"\0" + content).hexdigest()[:20]


def check_controller(ctx, conda, prefix, tool, requirements):
    if not conda or not prefix:
        raise RuntimeError("Set up this pipeline's runner before checking it.")
    code = controller_check_code(tool, requirements)
    ctx.command([conda, "run", "--no-capture-output", "-p", str(prefix), "python", "-c", code])
    ctx.command([conda, "run", "--no-capture-output", "-p", str(prefix), "snakemake", "--version"])


def inherited_cache(store, tool):
    previous = store.get('installs', store.get('settings', 'active-' + tool), {})
    return previous.get('rule_cache') or str(store.home / 'envs' / tool)


def install(ctx):
    store, payload = ctx.store, ctx.job["payload"]
    tool, version = payload["tool"], payload["version"]
    spec = PIPELINES[tool]
    commit = version["commit"]
    if store.channel == "dev" and not supported(tool, commit, store):
        raise ValueError("Select an exact build in the development catalog.")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Installation requires an exact 40-character Git commit")
    if not shutil.which("git"):
        raise RuntimeError("Git is missing. Install Git through your Linux distribution and retry.")
    key = tool + "-" + commit
    target = store.home / "sources" / key
    if not target.exists():
        temporary = Path(tempfile.mkdtemp(prefix=key + "-", dir=store.home / "sources"))
        try:
            ctx.command(["git", "init", str(temporary)])
            ctx.command(["git", "-C", str(temporary), "remote", "add", "origin", f'https://github.com/{spec["repo"]}.git'])
            ctx.command(["git", "-C", str(temporary), "fetch", "--depth", "1", "origin", commit])
            ctx.command(["git", "-C", str(temporary), "checkout", "--detach", "FETCH_HEAD"])
            if subprocess.check_output(["git", "-C", str(temporary), "rev-parse", "HEAD"], text=True).strip() != commit:
                raise RuntimeError("Downloaded source does not match the selected commit")
            temporary.rename(target)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    if subprocess.check_output(["git", "-C", str(target), "status", "--porcelain"], text=True).strip():
        raise RuntimeError("Installed source has local changes; preserve them and choose a clean installation.")
    from .setup_state import mode
    setup_mode = mode(store, tool, commit)
    record = store.get("installs", key, {"id": key, "tool": tool, "source": str(target), "version": version,
                                        "created": now(), "controller": None, "ready": False,
                                        "rule_cache": inherited_cache(store, tool)})
    record.setdefault('setup_mode', setup_mode)
    record.setdefault('setup_phase', 'source')
    record.setdefault('rule_cache', str(store.home / 'envs' / tool))
    store.put("installs", key, record)
    if not supported(tool, commit, store):
        ctx.write("Source downloaded. This version needs an adapter compatibility review before managed execution.")
        return
    from .release_contract import preflight
    preflight(store, tool, commit, target)
    record['setup_phase'] = 'runner'
    store.put('installs', key, record)
    prepare_controller(ctx, record, repair=payload.get("repair", False))
    if record.get('setup_mode') == 'first_install' and not record.get('setup_completed'):
        prepare_first_install(ctx, record)


def prepare_first_install(ctx, record):
    from . import maintenance
    rows = maintenance.recipes(record)
    if not rows:
        raise RuntimeError('No tool environment recipes found; first setup cannot be completed.')
    record['setup_phase'] = 'environments'
    ctx.store.put('installs', record['id'], record)
    ctx.write('First installation: preparing and checking tool environments. Databases remain a separate setup step.')
    original = ctx.job['payload']
    try:
        ctx.job['payload'] = maintenance.environment_request(ctx.store, record['id'], [r['id'] for r in rows], 'install')
        maintenance.execute_environments(ctx)
    finally:
        ctx.job['payload'] = original
    record.update(setup_phase='complete', setup_completed=now())
    ctx.store.put('installs', record['id'], record)
    ctx.write('First installation complete. Review databases and validate a run before execution.')


def prepare_controller(ctx, record, repair=False):
    store = ctx.store
    tool, target = record["tool"], Path(record["source"])
    spec = PIPELINES[tool]
    record.update(ready=False)
    store.put("installs", record["id"], record)
    content = (target / spec["environment"]).read_bytes()
    requirements = controller_requirements(content)
    record["controller_requirements"] = requirements
    store.put("installs", record["id"], record)
    ctx.write(f"Required runner: Python {requirements['python']}; Snakemake {requirements['snakemake']}.")
    conda = find_conda(store)
    if not conda:
        ctx.write("Installing workspace-local Conda to prepare the selected release's runner.")
        bootstrap(ctx)
        conda = find_conda(store)
        if not conda:
            raise RuntimeError("Local Conda setup did not produce an executable. Inspect the setup log.")
    digest = controller_key(tool, content)
    base = store.home / "controllers" / (tool + "-" + digest)
    prefix = Path(record["controller"]) if record.get("controller") and record.get("controller_digest") == digest else base
    if repair and prefix.exists():
        prefix = Path(str(base) + "-repair-" + ctx.job["id"][4:])
        ctx.write("Preparing a fresh controller; the previous environment is retained.")
    if not prefix.exists():
        ctx.write("Installing the selected release's Python and Snakemake in a separate runner.")
        ctx.command([conda, "env", "create", "--yes", "--prefix", str(prefix), "--file", str(target / spec["environment"])])
    else:
        ctx.write("Checking the existing controller before reuse.")
    try:
        check_controller(ctx, conda, prefix, tool, requirements)
    except (Cancelled, MonitoringInterrupted):
        raise
    except Exception:
        record.update(ready=False)
        store.put("installs", record["id"], record)
        raise RuntimeError(f"Runner check failed for Python {requirements['python']} / Snakemake {requirements['snakemake']}. "
                           "Inspect the log; use Repair to create a separate controller.") from None
    record.update(controller=str(prefix), conda=conda, controller_digest=digest, controller_origin="managed", ready=True, checked=now())
    store.put("installs", record["id"], record)
    store.put("settings", "active-" + tool, record["id"])
    ctx.write("Runner ready. Matching tool environments are reused automatically. Use Install environments in Install & update to prepare missing tools.")


def connect_existing(ctx):
    """Snapshot committed source locally; never edit the user's checkout or env."""
    from .local import inspect_installation, local_path
    store, payload = ctx.store, ctx.job["payload"]
    tool = payload["tool"]
    info = inspect_installation(payload["path"], tool, store)
    if info["commit"] != payload["commit"]:
        raise ValueError("The checkout changed since inspection. Inspect it again before connecting.")
    if not info["supported"]:
        raise ValueError("This source commit needs an adapter review before it can be connected for runs.")
    key = tool + "-local-" + hashlib.sha256((info["path"] + info["commit"]).encode()).hexdigest()[:20]
    target = store.home / "sources" / key
    if not target.exists():
        temporary = Path(tempfile.mkdtemp(prefix=key + "-", dir=store.home / "sources"))
        try:
            ctx.command(["git", "init", str(temporary)])
            ctx.command(["git", "-C", str(temporary), "fetch", "--depth", "1", "--no-tags", "--no-recurse-submodules",
                         info["path"], info["commit"]])
            ctx.command(["git", "-c", "core.hooksPath=/dev/null", "-C", str(temporary), "checkout", "--detach", "FETCH_HEAD"])
            temporary.rename(target)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    actual = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(target), "status", "--porcelain"], text=True).strip()
    if actual != info["commit"] or dirty:
        raise ValueError("The local source snapshot changed. Choose a clean installation.")
    from .release_contract import preflight
    preflight(store, tool, info["commit"], target)
    previous = store.get("installs", key)
    prefix = local_path(payload["controller"]) if payload.get("controller") else None
    cache = local_path(payload["rule_cache"]) if payload.get("rule_cache") else None
    from .channels import owned
    if prefix: owned(store, prefix, "Controller")
    if cache: owned(store, cache, "Environment cache")
    if prefix and (not (prefix / "conda-meta").is_dir() or not (prefix / "bin/python").is_file()):
        raise ValueError("Select an existing Linux Conda environment folder, containing conda-meta and bin/python.")
    if cache and (not cache.is_dir() or not os.access(cache, os.W_OK)):
        raise ValueError("The selected Snakemake environment cache must be an existing writable folder.")
    record = {"id": key, "tool": tool, "source": str(target), "original_source": info["path"], "origin": "local",
              "version": {"id": "local:" + key, "ref": info["ref"], "commit": info["commit"], "kind": "local"},
              "created": previous["created"] if previous else now(), "controller": str(prefix) if prefix else None,
              "rule_cache": str(cache) if cache else inherited_cache(store, tool), "ready": False}
    if previous and previous.get("ready"):
        raise ValueError("This installation is already connected. Select it under Install & update.")
    store.put("installs", key, record)
    ctx.write("Source copied from local Git history; original configs, databases and results are untouched.")
    if info["modified"]:
        ctx.write("The original checkout has local edits. This snapshot uses its committed version. Import your settings in Configuration.")
    if prefix:
        conda = find_conda(store)
        if not conda:
            raise ValueError("Choose your Conda executable before connecting an environment.")
        requirements = controller_requirements((target / PIPELINES[tool]["environment"]).read_bytes())
        check_controller(ctx, conda, prefix, tool, requirements)
        record.update(ready=True, conda=conda, checked=now(), controller_origin="external",
                      controller_requirements=requirements)
        store.put("installs", key, record)
        store.put("settings", "active-" + tool, key)
        ctx.write("Connected: existing runner verified and reused. No environment was installed or updated.")
    else:
        prepare_controller(ctx, record)


def bootstrap(ctx):
    store = ctx.store
    existing = find_conda(store)
    if existing:
        ctx.write("Existing Conda found: " + existing)
        return
    machine = {"x86_64": "x86_64", "aarch64": "aarch64"}.get(platform.machine())
    if platform.system() != "Linux" or not machine:
        raise RuntimeError("Automatic Miniforge setup supports Linux x86_64/aarch64. Configure an existing Conda executable.")
    release = api("repos/conda-forge/miniforge/releases/latest")
    name = "Miniforge3-" + release["tag_name"] + "-Linux-" + machine + ".sh"
    assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
    if name not in assets or name + ".sha256" not in assets:
        raise RuntimeError("Official release has no matching installer/checksum")
    destination = store.home / "tools/miniforge"
    if destination.exists():
        raise RuntimeError("An incomplete Miniforge folder exists. Inspect it or configure another Conda executable; it was not removed.")
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        installer = Path(tmp) / name
        checksum_request = Request(assets[name + ".sha256"], headers={"User-Agent": "Bactologix"})
        with ctx.observe_download("Miniforge checksum") as received, urlopen(checksum_request, timeout=60) as response:
            checksum = response.read(4096)
            received(len(checksum))
            expected = checksum.decode().split()[0].lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise RuntimeError("Official Miniforge checksum file has an unexpected format")
        for attempt in range(1, 4):
            ctx.check_cancel()
            ctx.write(f"Downloading official installer, attempt {attempt}/3")
            req = Request(assets[name], headers={"User-Agent": "Bactologix"})
            with (ctx.observe_download("Miniforge installer") as received,
                  urlopen(req, timeout=60) as response,
                  installer.open("wb") as out):
                while chunk := response.read(1024 * 1024):
                    received(len(chunk))
                    ctx.check_cancel()
                    out.write(chunk)
            actual = hashlib.sha256(installer.read_bytes()).hexdigest()
            if actual == expected:
                break
            ctx.write(f"Download did not match its checksum ({installer.stat().st_size} bytes). Retrying a fresh download.")
        else:
            raise RuntimeError("Miniforge checksum verification failed after three downloads; installer was not executed")
        ctx.write("Official Miniforge " + release["tag_name"] + "; SHA-256 " + actual)
        ctx.command(["bash", str(installer), "-b", "-p", str(destination)])
    store.put("settings", "conda", str(destination / "bin/conda"))
    ctx.write("Local Conda installed. No shell initialization was changed.")


def smoke(ctx):
    """Real orchestration check. No biological data or analysis tools."""
    store = ctx.store
    prefix = ctx.job["payload"].get("controller")
    conda = find_conda(store)
    if not prefix:
        ready = [item for item in store.all("installs") if item.get("ready")]
        if ready:
            prefix = ready[0]["controller"]
    if not prefix or not conda:
        raise RuntimeError("Set up a pipeline runner before the Snakemake smoke test.")
    folder = store.home / "runs" / ("smoke-" + ctx.job["id"][4:])
    folder.mkdir()
    (folder / "Snakefile").write_text('rule all:\n    input: "report.html"\n\nrule report:\n    output: "report.html"\n    run:\n        from pathlib import Path\n        Path(output[0]).write_text("<html><body><h1>Bactologix live test passed</h1><p>A real Snakemake job created this report. No sample analysis was performed.</p></body></html>")\n')
    ctx.command([conda, "run", "--no-capture-output", "-p", prefix, "snakemake", "--cores", "1", "--directory", str(folder), "--snakefile", str(folder / "Snakefile")], cwd=folder)
    if not (folder / "report.html").is_file():
        raise RuntimeError("Smoke workflow returned without its expected report")
    store.put("smoke", ctx.job["id"], {"id": ctx.job["id"], "report": str(folder / "report.html"), "finished": now()})
    ctx.write("PASS — actual Snakemake process, durable log and HTML output verified.")


def execute(ctx):
    kind = ctx.job["kind"]
    if kind == 'isolate_export':
        from .isolate_export import execute as export_isolate
        export_isolate(ctx)
    elif kind == 'storage_cleanup':
        from .storage_cleanup import execute as cleanup_storage
        cleanup_storage(ctx)
    elif kind == 'factory_reset':
        from .factory_reset import execute as reset_workspace
        reset_workspace(ctx)
    elif kind == 'database_redownload':
        from .database_redownload import execute as redownload_database
        redownload_database(ctx)
    elif kind == "app_update":
        if ctx.job['payload'].get('update_all'):
            from .update_all import execute as update_everything
            update_everything(ctx)
        else:
            from .updates import install_update
            install_update(ctx)
    elif kind == "install":
        install(ctx)
    elif kind == "connect":
        connect_existing(ctx)
    elif kind == "bootstrap":
        bootstrap(ctx)
    elif kind == "check":
        record = ctx.store.get("installs", ctx.job["payload"]["installation"])
        if not record:
            raise ValueError("Installation no longer exists")
        try:
            if not supported(record["tool"], record["version"]["commit"], ctx.store):
                raise ValueError("This source version needs an adapter review.")
            from .release_contract import preflight
            preflight(ctx.store, record["tool"], record["version"]["commit"], record["source"])
            if ctx.job["payload"].get("repair") or ctx.job["payload"].get("setup"):
                prepare_controller(ctx, record, repair=ctx.job["payload"].get("repair", False))
                return
            requirements = controller_requirements((Path(record["source"]) / PIPELINES[record["tool"]]["environment"]).read_bytes())
            check_controller(ctx, find_conda(ctx.store), record["controller"], record["tool"], requirements)
            record.update(ready=True, checked=now(), controller_requirements=requirements)
        except Exception:
            record["ready"] = False
            raise
        finally:
            ctx.store.put("installs", record["id"], record)
    elif kind == "workstation":
        from .workstation import execute_check
        execute_check(ctx)
    elif kind == "smoke":
        smoke(ctx)
    elif kind == "workflow_unlock":
        from .workflow_unlock import execute as unlock
        unlock(ctx)
    elif kind == "report_refresh":
        from .report_refresh import execute as refresh_report
        refresh_report(ctx)
    elif kind == "workflow":
        from .runs import execute_workflow
        execute_workflow(ctx)
    elif kind == "database_build":
        from .database_builder import execute as build_database
        build_database(ctx)
    elif kind in {"environments", "database_download"}:
        from .maintenance import execute_environments, execute_download
        (execute_environments if kind == "environments" else execute_download)(ctx)
    else:
        raise ValueError("Unsupported job")




