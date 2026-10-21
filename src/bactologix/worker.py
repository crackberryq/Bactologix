"""One OS lock serializes setup, native workflows and shared environment writes."""
import argparse
import fcntl
import time
import traceback
from .store import Store, now
from .engine import Cancelled, Context, execute


def work(store):
    with (store.home / "worker.lock").open("a+") as lock:
        # Waiting launchers also close the idle-shutdown race: a job submitted as
        # the previous worker exits always has a worker waiting to acquire the lock.
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Native children inherit the lock. Recovery cannot overlap a surviving child.
        for old in store.jobs():
            if old["status"] == "running":
                store.update_job(old["id"], status="interrupted", finished=now(),
                                 error="Worker ended unexpectedly. Inspect outputs and logs before resuming.")
        idle = 0
        processed = False
        while idle < 20:
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
            try:
                ctx.write("Bactologix started " + now())
                execute(ctx)
                ctx.check_cancel()
                store.update_job(job["id"], status="succeeded", finished=now())
            except Cancelled as exc:
                ctx.write(str(exc))
                store.update_job(job["id"], status="cancelled", finished=now(), error=str(exc))
            except Exception as exc:
                ctx.write(traceback.format_exc())
                store.update_job(job["id"], status="failed", finished=now(), error=str(exc))
            finally:
                ctx.log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    work(Store(args.home))
