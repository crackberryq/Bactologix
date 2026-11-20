# Low-disk protection during a run

Bactologix checks the physical space available for writes to the workspace, run
outputs, workflow folders and temporary directories. Under WSL it also checks
the Windows drives holding the Ubuntu virtual disk and active swap storage.
The monitor covers local workflows and managed environment and database setup.
A storage stop applies to the current Bactologix workspace.

| Measured free space | What happens |
| --- | --- |
| Below 20 GiB and above 10 GiB on any monitored drive | Bactologix warns and work continues. |
| At or below 10 GiB on any monitored drive | Bactologix stops active work, blocks new starts and retains outputs and logs. **Jobs & progress** shows the affected drive and its last reading. |
| At least 15 GiB on every monitored drive after a storage stop | You can ask Bactologix to check and clear the stop. You decide which work to resume. |

The monitor uses measured free bytes on each drive. High RAM usage and high use
of an authorized swap allowance can occur during large jobs. The storage stop
triggers on actual free disk space. Bactologix does not deduct the swap allowance
from that measurement. Limits use binary GiB (1 GiB = 1,073,741,824 bytes).

## After a workspace storage stop

1. Open **Jobs & progress** and read the storage notice. Identify the drive
   that reached the threshold and wait for active tools to exit.
2. Make space on the affected drive. Keep the files and logs for any run you
   intend to resume. Removing files inside Ubuntu may leave the Windows virtual
   disk at its previous size.
3. Choose **Recheck and clear storage stop**. Bactologix measures the original
   storage locations again. Every monitored drive needs at least 15 GiB free.
4. Review the saved run and choose **Run / resume** when ready. The queue
   remains paused after a successful recheck, and earlier waiting jobs stay
   held. Release the jobs you want to run and explicitly resume the queue.
   An interrupted tool step may need to run again. Completed outputs and prior
   attempt logs remain available.

## When a storage reading is unavailable

A missing or stale reading can pause an individual workflow while Bactologix
tries to verify space. During an active command, **Rechecking storage** appears
briefly if the last complete measurement showed at least 20 GiB free on every
monitored drive. If the new reading arrives in time and confirms sufficient
capacity, the command continues. A stale reading alone cannot trigger a
workspace-wide low-space stop.

A workflow waiting before a command has no active tool to suspend. Once a
running command is suspended, its processes retain memory and workspace locks.
The job log records the failed check. When the run page shows that storage has
recovered and every required drive has at least 15 GiB free, choose **Resume
run** to continue the same attempt. **Stop run** ends the attempt and retains
its outputs and logs. Older attempts may also show this per-run pause after a
low-space reading.

## Windows and WSL

A WSL job can have ample space visible inside Ubuntu while Windows has too
little space on its backing or swap drive. Bactologix checks each location
individually. If Ubuntu reports an `Exec format error` for `powershell.exe`,
the Windows Dev launcher can provide measurements for its own distribution.
During a brief reporting gap, Bactologix can also read verified mounted Windows
drives directly. Open **View log** for the affected location and check.

After changing Windows WSL swap settings, stop WSL and restart it before
launching new runs. The configured swap path may differ from the one still in
use by an older WSL session.

## Scope and limits

The monitor samples free space. In-progress writes and activity from other
applications can consume space between samples. Standalone Snakemake sessions,
other Bactologix workspaces and unrelated applications control their own jobs.
Bactologix keeps previously written outputs and logs when it stops owned
processes. Inspect the stopped attempt before retrying.
