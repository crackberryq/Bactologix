"""Explicit operations executed by a detached, single-workspace worker."""
from __future__ import annotations
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
from .store import Store, now
from .supervision import MonitoringInterrupted


class Cancelled(Exception):
    pass


def find_conda(store):
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
    return {"system": platform.system(), "architecture": platform.machine(), "python": platform.python_version(),
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


def enqueue(store, kind, payload):
    if kind not in {"install", "connect", "bootstrap", "check", "workflow", "smoke", "workstation", "app_update", "environments", "database_download", "database_build", "report_refresh", "storage_cleanup", "factory_reset", "isolate_export"}:
        raise ValueError("Unknown operation")
    if platform.system() != "Linux":
        raise RuntimeError("Run this app inside Linux or WSL2 to execute jobs.")
    from .updates import assert_current
    assert_current(store)
    if kind == "workflow":
        from .runs import validate_launch
        validate_launch(store, payload["run_id"], payload["action"])
    jid = store.submit(kind, payload)
    launch_worker(store)
    return jid


class Context:
    def __init__(self, store, job, lock_fd=None, environment_fd=None):
        self.store, self.job, self.lock_fd = store, job, lock_fd
        self.lock_fds = tuple(fd for fd in (lock_fd, environment_fd) if fd is not None)
        self.log = store.log_path(job["id"]).open("a", buffering=1)
        self.cancel_requested = False
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
        if job['kind'] == 'workflow' and job['payload'].get('action') in {'run', 'continue'}:
            from .disk_pause import Guard
            self.disk_guard = Guard(store, job, self.check_cancel_only, self.write)

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

    def check_cancel(self):
        self.check_cancel_only()
        if self.disk_guard is not None:
            self.disk_guard.checkpoint()

    def check_cancel_only(self):
        if self.refresh_desktop_cancel():
            raise Cancelled("Stopped by user")

    def refresh_desktop_cancel(self):
        from .desktop import session_value_stopped
        if self.cancel_requested:
            return True
        state = self.store.control_state(self.job['id'], self.monitor_retry)
        if self.monitor['state'] == 'degraded':
            self.monitor_state('recovered', 'Workspace control reads recovered; native execution remained owned.')
        self.cancel_requested = bool(state['cancel'] or (self.job['kind'] != 'app_update' and state['owner'] and session_value_stopped(state['session'])))
        return self.cancel_requested

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
                self.write('Workspace control read interrupted; retrying while retaining native process ownership: ' + str(exc))
            except OSError:
                pass
        self.monitor_state('degraded', 'Retrying workspace control reads; native work is still supervised.',
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

    def complete_process_record(self, proc, confirmed):
        from .supervision import save
        if self.native_record is None:
            return
        self.native_record.update(cleanup_confirmed=confirmed, finished=now() if confirmed else None,
                                  returncode=proc.returncode)
        save(self.store, self.job['id'], self.native_record, 'process')
        value = dict(self.native_record, pid=None if confirmed else self.native_record['pid'])
        self.store.put_process(self.job['id'], value)

    def command(self, argv, cwd=None, extra_env=None, stdout=None, *, timeout=None, stop_after=None):
        import shlex
        self.check_cancel()
        self.write("$ " + shlex.join([str(x) for x in argv]))
        env = os.environ.copy()
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1"})
        env.update(extra_env or {})
        guard = self.disk_guard
        if guard is not None:
            guard.before_command(cwd, env)
        started_stop = None
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
            self.store.put_process(self.job['id'], self.native_record)
            os.write(gate_write, b'1')
            native_started = True
            os.close(gate_write)
            gate_write = None
            self.observe("begin_command", proc, argv[0])
            while proc.poll() is None:
                cancelled = self.refresh_desktop_cancel()
                elapsed_command = time.monotonic() - command_started
                timed_out = timed_out or (timeout is not None and elapsed_command >= timeout)
                if timed_out:
                    owner.signal(signal.SIGKILL)
                if cancelled or (stop_after is not None and elapsed_command >= stop_after):
                    if started_stop is None:
                        started_stop = time.monotonic()
                        owner.stop()
                        self.write("Stopping owned processes gracefully (SIGINT)…")
                    elapsed = time.monotonic() - started_stop
                    if elapsed > 30:
                        owner.signal(signal.SIGKILL if elapsed > 60 else signal.SIGTERM)
                if guard is not None and started_stop is None and not timed_out:
                    guard.tick()
                else:
                    owner.refresh()
                self.remember_processes(proc, argv, owner)
                self.observe("tick")
                time.sleep(0.25)
            if timed_out:
                self.check_cancel_only()  # A concurrent user stop keeps its cancellation status.
                raise TimeoutError("Software check command exceeded its time limit; its process group was stopped.")
            if started_stop is not None:
                raise Cancelled("Stopped by user; inspect the log before resuming")
            if proc.returncode:
                raise RuntimeError(f"Command exited with code {proc.returncode}; see the job log")
            failed = False
        except MonitoringInterrupted as exc:
            monitoring_error = exc
            self.monitor_state('interrupted', str(exc), command_returncode=proc.poll(), cleanup_confirmed=False)
            raise
        except TimeoutError:
            raise  # A native command timeout is distinct from control storage loss.
        except (sqlite3.Error, OSError) as exc:
            self.monitor_state('interrupted', 'Workspace control persistence failed: ' + str(exc), command_returncode=proc.poll(), cleanup_confirmed=False)
            monitoring_error = MonitoringInterrupted(self.monitor['detail'])
            raise monitoring_error from exc
        finally:
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
                if not cleanup_confirmed:
                    self.monitor_state('interrupted', 'Native cleanup could not be confirmed; execution remains locked for review.', cleanup_confirmed=False)
                elif self.monitor['state'] == 'interrupted':
                    self.monitor_state('interrupted', self.monitor['detail'], cleanup_confirmed=True)
                try:
                    self.complete_process_record(proc, cleanup_confirmed)
                except Exception as exc:
                    self.monitor_state('interrupted', 'Native execution ended but its control record could not be saved: ' + str(exc), cleanup_confirmed=cleanup_confirmed)
                    raise MonitoringInterrupted(self.monitor['detail'], cleanup_confirmed=cleanup_confirmed) from exc
                if not cleanup_confirmed:
                    raise MonitoringInterrupted(self.monitor['detail'], cleanup_confirmed=False)


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
    except Cancelled:
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
        with urlopen(Request(assets[name + ".sha256"], headers={"User-Agent": "Bactologix"}), timeout=60) as response:
            expected = response.read(4096).decode().split()[0].lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise RuntimeError("Official Miniforge checksum file has an unexpected format")
        for attempt in range(1, 4):
            ctx.check_cancel()
            ctx.write(f"Downloading official installer, attempt {attempt}/3")
            req = Request(assets[name], headers={"User-Agent": "Bactologix"})
            with urlopen(req, timeout=60) as response, installer.open("wb") as out:
                while chunk := response.read(1024 * 1024):
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
    elif kind == 'factory_reset':
        from .factory_reset import execute as reset_workspace
        reset_workspace(ctx)
    elif kind == 'storage_cleanup':
        from .storage_cleanup import execute as cleanup_storage
        cleanup_storage(ctx)
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




