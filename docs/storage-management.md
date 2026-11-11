# Storage cleanup and recovery

Bactologix separates storage measurement from permanent cleanup. Open **Storage**
to see the registered cleanup candidates. Opening the page does not start a drive
scan or delete anything, and age alone never triggers deletion.

During local analysis, [low-disk protection](low-disk-protection.md) monitors
required Linux and WSL host volumes. A storage-paused run retains its locks;
stop it before in-app cleanup or exports that require an idle workspace.
No storage threshold automatically authorizes file removal.

## Restore an archived run

Archiving hides a run while keeping its configuration, files and reports. Use
**Runs → Archived → Restore**, or the **Restore run** action for an archived run
in Storage or on its run page. Restoration only changes list visibility. It
does not delete files, remove workflow locks, resume the queue or start analysis.

If a permanent-cleanup preview reports a workflow lock, close the preview or use
its separate restore action to bring the archived run back. Restore does not
require unlocking first. Then open the failed job and use **Check workflow lock**.
Unlock is available only when the ownership checks allow it; see
[workflow lock recovery](workflow-locks.md). A blocked cleanup preview does not
mean that the run has been deleted.

## Choose what to remove

| Cleanup item | What it removes | What remains protected |
| --- | --- | --- |
| Run results | The reviewed run-owned files and associated registration records | Original input files and dependencies used by retained runs |
| Sample registration | The selected registration record | All input files; this frees no data space |
| Sample data | Only the explicitly enumerated known input files and their selected registration | The containing folder and unrelated files |
| Pipeline version | An unchanged managed source snapshot and its installation entry | Connected original checkouts and files still needed by retained runs |
| Pipeline runner or tool environment | An eligible recorded managed environment | Shared, external or unverified environments and retained dependencies |

Archived runs still own files and count as retained dependencies. Review their
results before selecting them for permanent cleanup. Removing results makes that
saved run unavailable for normal reuse; keep the full output and required evidence
elsewhere if you will need them later.

Environments can occupy far more space than source code. Select the unused pipeline
installation and its eligible runner/tool environments together when the preview
requires it. Retiring the current pipeline selection clears that selection; it does
not automatically choose a different version. Reinstall a suitable version before
creating new drafts for that pipeline.

## Review and confirm

1. Transfer the files you want to retain and verify that the copies are usable.
   An individual report download or Methods export is not a full run backup.
2. Optionally choose **Measure storage**. Include registered external locations
   when you want those paths measured. Measured candidates are ordered by size;
   unmeasured selections are measured during preview.
3. Select the items under **Reclaim disk space**, then choose
   **Preview selected cleanup**. Read every listed path and any protection reason.
   The preview itself does not remove files.
4. Confirm that you verified transferred copies or intentionally discard the files,
   type **DELETE**, and choose **Permanently delete selected**.
5. Follow the queued cleanup in **Jobs & progress**. Review the receipt in
   **Cleanup history**, then measure storage again to see current availability.

Bactologix records your acknowledgment; it does not verify a transfer or backup.
Closing the preview abandons that confirmation. There is no undo for completed
file deletion.

## References and concurrent work

Cleanup rechecks the selected files and recorded dependencies before removal.
Retained drafts, runs, follow-ups and shared resources can block a selection.
Finish or cancel queued/running jobs before cleanup, and finish any draft or
variant operation that is currently copying files. New work cannot start while
cleanup is queued or running.

Known workspace references are checked, including the separate regular and Dev
workspaces when discoverable. This is not a global inventory of every application
or custom workspace on the PC. Keep files needed by other software or unregistered
workspaces. Symbolic links do not grant ownership of their targets, and a familiar
folder name alone does not make a directory eligible for deletion.

## Incomplete cleanup

A failed or interrupted deletion may leave some files removed and others retained.
Its receipt records that outcome. Review **Incomplete cleanup** and its remaining
paths before confirming a retry; do not assume a failed job restored deleted files.
Resolve incomplete cleanup before starting new work in the affected workspace.

File sizes in the preview are estimates, not a promise of reclaimed physical disk
space. Hard links, compression, snapshots and WSL virtual disks can affect the
change visible to Windows. The storage measurement reports current filesystem
availability separately. Cleanup does not compact a WSL virtual disk or remove
unregistered files elsewhere on the drive.
