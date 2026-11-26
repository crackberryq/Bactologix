"""One OS lock serializes setup, native workflows and shared environment writes."""
import argparse
from contextlib import nullcontext
import fcntl
import sqlite3
import time
import traceback
from . import __version__
from .store import Store, now
from .engine import Cancelled, Context, execute
from .runtime import environment_lock
from .updates import assert_current
from .supervision import MonitoringInterrupted


def work(store, desktop_owner=None):
    from .desktop import job_stop_channel, session_stopped
    with (store.home / "worker.lock").open("a+") as lock:
        # Waiting launchers also close the idle-shutdown race: a job submitted as
        # the previous worker exits always has a worker waiting to acquire the lock.
        while True:
            if session_stopped(store, desktop_owner):
                return
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.25)
        # Native children inherit the lock. Recovery cannot overlap a surviving child.
        from .queue import recover_interrupted
        recover_interrupted(store)
        idle = 0
        processed = False
        while idle < 20:
            if session_stopped(store, desktop_owner):
                return
            job = store.claim()
            if not job:
                if not processed:
                    return
                idle += 1
                time.sleep(0.25)
                continue
            idle = 0
            processed = True
            ctx = Context(store, job, lock.fileno())
            status = 'running'
            try:
                with job_stop_channel(store, job):
                    ctx.check_cancel()
                    ctx.write("Bactologix started " + now())
                    ctx.write(f"Bactologix worker version {__version__}")
                    with environment_lock(exclusive=job["kind"] == "app_update") as environment_fd:
                        ctx.lock_fds = (lock.fileno(), environment_fd)
                        assert_current(store)
                        # Keeping one idle connection open prevents each short
                        # telemetry/control transaction from becoming SQLite's
                        # last WAL connection and triggering close/reopen cleanup.
                        workspace = store.keep_wal_open() if ctx.setup_control or ctx.workflow_control else nullcontext()
                        with workspace:
                            execute(ctx)
                    ctx.check_cancel()
                status = 'succeeded'
                store.update_job(job["id"], status="succeeded", finished=now())
            except Cancelled as exc:
                ctx.write(str(exc))
                status = 'cancelled'
                store.update_job(job["id"], status="cancelled", finished=now(), error=str(exc))
            except (MonitoringInterrupted, sqlite3.Error) as exc:
                ctx.write(str(exc))
                confirmed = getattr(exc, 'cleanup_confirmed', True)
                status = 'interrupted' if confirmed else 'running'
                ctx.monitor_state('interrupted', str(exc), cleanup_confirmed=confirmed)
                from .queue import monitoring_interrupted
                monitoring_interrupted(store, job['id'], str(exc), confirmed)
            except Exception as exc:
                ctx.write(traceback.format_exc())
                status = 'failed'
                store.update_job(job["id"], status="failed", finished=now(), error=str(exc))
            finally:
                try:
                    if ctx.disk_guard is not None:
                        ctx.disk_guard.finish(status)
                    ctx.observe("finish", status)
                    if status == 'succeeded' and getattr(ctx, 'completed_delivery_run', None):
                        from .result_delivery import after_success
                        try:
                            after_success(ctx, ctx.completed_delivery_run)
                        except Exception as exc:
                            ctx.write('Delivery status unavailable: ' + str(exc))
                finally:
                    ctx.log.close()
            if status in {'interrupted', 'running'}:
                # Never progress the queue after monitoring loss. Recovery checks
                # native identities and pauses the queue before any future claim.
                return
            if job["kind"] == "app_update":
                # Never claim more work with modules loaded before pip replaced them.
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument('--desktop-owner')
    args = parser.parse_args()
    work(Store(args.home), args.desktop_owner)
