# Low-disk protection during a run

A fresh measurement below 5 GiB on the Linux root filesystem or the Windows volume holding the active WSL distribution pauses a running workflow. Exactly 5 GiB meets the reserve. Dedicated swap and other volumes are informational.

If a required reading is delayed or unavailable, the run continues and Bactologix retries the check. Check Ubuntu storage manually and pause the run if necessary.

After a measured low-space pause, free space on the affected filesystem. Choose **Resume run** once a fresh check confirms at least 5 GiB on both protected filesystems. Processes suspended during the run retain their memory and outputs.

A measured low-space event during managed setup can still trigger a workspace storage stop.

## After a workspace storage stop

1. Open **Jobs & progress** and read the storage notice. Identify the drive
   that reached the threshold and wait for active tools to exit.
2. Make space on the affected drive. Keep the files and logs for any run you
   intend to resume. Removing files inside Ubuntu may leave the Windows virtual
   disk at its previous size.
3. Choose **Recheck and clear storage stop**. Bactologix measures the original
   storage locations again. Each protected filesystem must have more than 5 GiB available to clear a workspace stop.
4. Review the saved run and choose **Run / resume** when ready. The queue
   remains paused after a successful recheck, and earlier waiting jobs stay
   held. Release the jobs you want to run and explicitly resume the queue.
   An interrupted tool step may need to run again. Completed outputs and prior
   attempt logs remain available.

## Scope and limits

The monitor samples free space. In-progress writes and activity from other
applications can consume space between samples. Standalone Snakemake sessions,
other Bactologix workspaces and unrelated applications control their own jobs.
Bactologix keeps previously written outputs and logs when it stops owned
processes. Inspect the stopped attempt before retrying.
