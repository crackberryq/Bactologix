# Workflow lock recovery

Use **Check workflow lock** on the failed job, review the result, and choose **Unlock workflow** when enabled. This runs native Snakemake `--unlock` using the frozen run's runner, workflow and configuration. It does not start an analysis. The queue remains paused after unlocking; review the run before resuming.

If the run is archived, restore it first using **Runs → Archived → Restore** or
**Restore run** in Storage. Restoration is available even when native locks
remain. It changes visibility only; the lock and all saved data stay intact until
the separate guarded unlock operation succeeds. Permanent cleanup is not needed
to restore or unlock a run.

## Memory settings during unlock

Starting with Bactologix 0.1.0a102 (0.1.0a102.dev1 for Dev), native unlock keeps
this run's saved scheduling resources, including any explicit disk-swap
allowance. Workflows can check their resource declarations while parsing the
unlock request. Dropping those settings could otherwise load a lower profile
default and fail before unlocking. Passing the saved budget does not allocate
that memory or execute analysis tools.

This change does not establish that a particular lock is stale. Use the guarded
**Check workflow lock** review first. If it reports an active process or cannot
verify ownership, wait or investigate that message; do not remove lock files
manually. After a successful unlock, review the saved run, resume the queue when
ready, and use **Run / resume**. Original outputs and frozen settings are kept.

## Protected systemd PAM helpers

On Ubuntu/WSL, `(sd-pam)` can have the same real, effective and saved UID as the application while denying access to its working directory. A process-inspection permission error does not itself establish ownership of a Snakemake lock.

Bactologix 0.1.0a70 recognizes this helper only for private, user-owned workflow/lock directories and after checking all of:

- Exact `(sd-pam)` process name and matching real/effective/saved UIDs.
- A live parent running the OS systemd executable with `--user`, with matching UIDs.
- A root-owned systemd binary and ancestors that are not group/world writable.
- Kernel-reported systemd control-group membership in the user's manager service or its init scope, rather than an application service.
- An unchanged live parent relationship at the end of inspection.

Recognition applies only to an otherwise inaccessible process. A visible Snakemake command always blocks, even if its process name resembles the helper. Missing or inaccessible verification evidence continues to block unlocking. The helper is never killed or modified. The review and maintenance log identify excluded helper PIDs.

The workspace worker lease, queue checks, workflow hashes, lock fingerprints, and execution-time ownership recheck remain enforced. Multiple failed attempts on the same run do not mean multiple independent workflow locks.

Implementation background: [systemd v249 PAM session helper](https://github.com/systemd/systemd/blob/v249/src/core/execute.c), `setup_pam()`. This documentation describes the supported software recovery path, not a claim that any particular user's lock has already been removed.
