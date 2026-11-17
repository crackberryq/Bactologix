# Run monitoring and resource recovery

These controls preserve frozen configurations and generated outputs. They do
not establish analytical validation of a pipeline or reconstruct missing
execution evidence from older runs.

## Monitoring is distinct from the native workflow

Brief SQLite control-read contention is retried with bounded waits. Persistent
control failure interrupts supervision and attempts to stop the owned native
process tree. Only verified native exit permits a terminal interruption record.
Unknown or surviving recorded process identities keep the job busy and prevent
queue recovery, relaunch, unlock and cleanup. The queue pauses for review.

The run page displays monitoring retry, recovery or interruption separately from
native tool failure. A stop attempt with unconfirmed exit is shown explicitly;
the UI does not promise that another Stop click can regain process ownership.
Logs and process identity records remain available. Restarting the interface does
not make a historical failed run complete.

Explicit Stop and desktop lease changes retry brief database contention within
a bounded interval. A stale heartbeat cannot clear a confirmed stop request.
If control storage stays unavailable, the launcher still runs cleanup for its
owned UI process and reports shutdown as unconfirmed; it does not report zero jobs or
successful workflow shutdown without evidence.

The older a105 monitoring exception could leave a native child working after the
application marked its job failed. This was reproduced with a harmless writer.
Historical records may lack the PID creation identity needed for safe recovery;
this update does not automatically adopt or signal such processes. Inspect their
actual state before starting another attempt or deleting files. Do not remove
SQLite WAL/SHM files or workflow locks as a generic repair.

## Repair a stale VFDB reference

Open the saved BaSIS run and choose **Repair VFDB reference** when its explicit
external paths are missing or invalid. Review the old paths and exact changes,
then choose **Create revised draft**. The original run is retained. The new draft
clears the external VFDB database, reference FASTA, metadata and version fields
and selects native preparation. Module enablement, search thresholds, compute
allocation and resource directory are preserved.

Changing a manual resource policy to Auto also permits preparation of other
enabled modules without explicit resources; the preview states this consequence.
Creating the draft does not download a database or run analysis. Validate the new
draft, prepare its environments if needed, and run it explicitly. A newly fetched
VFDB snapshot may differ from the old one: review its actual provider provenance
and citations. Neither file proximity nor a directory name establishes a matched
FASTA/index bundle or database version.

Revised drafts use independent output directories. Verified checkpoint reuse
from a failed parent, including reuse across pipeline source versions, remains
a separate recovery feature; this update does not establish that an older
Racon assembly or other checkpoint is eligible for reuse.

On older application versions the same revision can be made through **Edit
settings**: set **Databases & storage → Policy** to `auto`, and leave **Module:
VFDB → Database / Database version / Metadata / Reference fasta** blank. Do not
type `null` into those string controls. Use a revised draft, never edit frozen
run files in place.

Database preparation availability is checked against the actual installed
source's reviewed capability. The UI gives the unavailable reason and last check
time; starting a preparation job always performs fresh source checks.

## Graphics dependencies

NanoPlot 1.46.2 declares Kaleido >=1.0.0; MultiQC 1.32 declares Kaleido 0.2.1.
Forcing the latter into their shared environment is not a supported dependency
repair. Complete existing environments are retained when the application cannot
apply a supported repair. Checks do not silently download a browser. For a reviewed split environment, choosing Install or Repair can prepare a missing browser and then verify rendering; a normal analysis cannot opt into that download.

The new HybAs Dev candidate separates QC and report rendering environments and
checks actual HTML/PNG generation. Native CI must pass full recipe installs and
render checks before that candidate is approved. Chrome/browser identity is
recorded separately from Conda packages. A setup check establishes capability at
check time, not that every plot was produced by every later sample run.

## Medaka choices

Only a source with the reviewed model-selection contract exposes the new guided
review controls. Old frozen workflows cannot acquire this behavior merely by
updating the application. Automatic selection requires exact recognized
basecaller metadata and an installed mapping; unknown version suffixes cannot
fall back to a shorter model-family name. Raw ONT metadata is inspected before
expensive assembly, and actual processed reads are rechecked before Medaka.

An unmatched explicit choice requires acknowledgement, a reason and binding to
that selected model. Changing the model requires a new acknowledgement. This
records a researcher decision, not model suitability. Requested/resolved model,
read metadata, model hash, tool version and failure diagnostics are retained.
Methods exports distinguish recorded model review from scientific validation
when it is attributable to an executed Medaka stage in the saved run summary.
Standalone failed-preflight receipts and graphics-runtime observations remain
native output files; they are not automatically imported into Methods evidence
or its reproducibility ZIP.
Medaka is not silently skipped or replaced with a historical default model.

## Kraken capacity

The selected database's core-file footprint is compared with physical RAM using
bounded file metadata checks. Swap may permit scheduling while execution remains
severely limited by paging. File sizes and headroom estimates are not measured
resident memory or progress percentages. This update does not change the selected
database, classification thresholds or loading policy. Memory mapping and
alternative databases require their own runtime and result comparisons.

## Verification limits

Software tests use synthetic inputs, real harmless child processes, injected
control failures and retained fixture outputs. They do not qualify a complete
WSL biological workload, kernel blocked-I/O behavior, or processes that evade
discovery entirely. The operating system and active storage must still be checked
on the target workstation. Outputs are never automatically deleted by these
recovery controls.
