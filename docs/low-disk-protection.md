# Low-disk protection during a run

Bactologix checks available storage before starting or continuing a local
workflow, during output preservation, and while its tools run. This applies to
HybAs, BaSIS and GeneScope runs launched through Bactologix. It does not control
standalone Snakemake sessions or other applications.

| Available space on any required volume | Behavior |
| --- | --- |
| Below 20 GiB | Show a storage warning. |
| At or below 10 GiB | Hold work before launch, or suspend owned local workflow processes. |
| At least 15 GiB on every required volume | Allow explicit resume after a fresh successful capacity check. |
| A required measurement is unavailable | Hold or suspend until storage monitoring works again. |

Limits use binary GiB (1 GiB = 1,073,741,824 bytes) and available space for the
current user, excluding administrator-reserved space. Checks cover workspace,
run outputs, workflow location and recognized temporary directories.

## Windows and WSL

Free space inside Ubuntu does not establish that Windows has room for its
virtual disk to grow. Under WSL, Bactologix also identifies the Windows volume
holding the current distribution and its configured or default swap location
when swap is enabled. Queries are read-only. Observed Windows-drive mounts are
used where available, including custom automount roots.

Linux and Windows free space are separate constraints, not additive capacity.
A required host check that cannot complete holds the run. Bactologix does not
change `.wslconfig`, resize or compact virtual disks, remove swap, or delete
results to make room.

After changing Windows WSL swap settings, stop WSL and restart it before
launching new runs. The configured swap path may otherwise differ from the
location still active in an older WSL session.

## Recover a paused run

1. Read the run's storage notice for the affected locations and reason.
2. Free space on the affected volume or restore capacity monitoring. Explicitly
   choose which data to transfer or discard. Deleting Linux files does not
   necessarily shrink the Windows virtual-disk file immediately.
3. Wait for fresh measurements: every required volume needs at least 15 GiB.
4. Use the resume control to continue the suspended attempt. Recovery of free
   space does not automatically resume it.

**Stop run** remains available. Stopping retains generated outputs and logs;
a later saved-run resume is separate from continuing suspended processes.

A paused run retains workspace and workflow locks. In-app exports and cleanup
that require an idle workspace remain blocked. Free unrelated storage outside
Bactologix, or stop the run before using those operations. **Pause queue** only
controls subsequent jobs; it is not the storage safety pause.

## Scope and limits

The monitor samples capacity and suspends identity-checked local children.
If it cannot establish suspension, it stops the owned job instead of reporting
that it is safely paused. An independent worker-loss monitor handles owned
processes if the worker disappears; recovery records an interrupted attempt
instead of automatically restarting it.

This reduces disk-exhaustion risk but cannot guarantee that a disk never fills.
In-progress writes, bursts between samples, other applications, quotas, hardware
failure or an unavailable operating system can defeat a capacity margin.
Remote jobs and processes that fully detach before observation are outside
local process ownership. Keep additional headroom and backups of important data.

Automated checks exercise synthetic capacity changes, harmless real writers,
cancellation, worker loss, Windows capacity queries and browser controls.
Windows CI does not execute a full WSL analysis. Validate backing-drive and
swap monitoring on the actual Windows/WSL host before relying on it for long runs.
