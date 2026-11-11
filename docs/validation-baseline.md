# Freeze a validation baseline

A software release is a candidate for validation, not evidence that the assembled
pipeline is analytically validated. Keep regular and Dev testing as separate
baselines. A result applies to the recorded inputs, settings, software,
environments and reference resources; it does not automatically transfer to
another combination.

## Dev source baseline

This document records the **Bactologix 0.1.0a105.dev1** development baseline.
Record the installed application version and the exact commit resolved by its
published release tag in your validation record. Keep Dev testing separate from
[the current-channel baseline](https://github.com/crackberryq/Bactologix/blob/main/docs/validation-baseline.md).

| Pipeline | Dev source candidate |
| --- | --- |
| HybAs | 10.0.0a4 — `7cbd29d942a3d70bc1f6c7be176a75ce4bb7f2a8` |
| BaSIS | 0.4.0a8 — `7c293d36a633a726424d544c6ffc39824537ebe4` |
| GeneScope | 0.1.0a18 — `28bf38c44eb07fe8e4865f67d59ef6b4028f51dc` |

The native-retention review used Flye **2.9.6** (Dev) at upstream commit
`886b8c17412cdf3a2868a28237bca6c5ad1da156` and BUSCO **6.0.0** at upstream commit
`6278721a1916f6da310e03ec9674099028c927a4`. The pipeline's retention adapter checks
its reviewed package sources; other versions require a new compatibility review.

These identify source code only. They do not freeze your computer's installed
packages, databases or models. Older saved runs retain their original workflow;
install the intended pipeline and create a new run to test this candidate.

## Record before execution

1. Define the intended use, input modes and modules being tested, expected native
   outputs, acceptance criteria and comparison method before running. Record
   platform, resources and any limits on the tested scope. File counts alone do
   not establish completeness or analytical correctness.
   Record physical RAM and any explicit [disk-swap allowance](swap-memory.md)
   separately; the scheduler budget is not measured memory consumption.
2. Preserve the new run's exact source, frozen configuration and input content
   identities. Methods records `source_commit`, `config_sha256`, `workflow_sha256`
   and available `input_snapshots`. Legacy file size/mtime records do not establish
   original content identity. Keep the actual input files in the validation
   archive or a separately verified immutable location.
3. Save the **actual resolved environment inventory** for the controller and each
   tool environment, including package versions, builds and channels, alongside
   the archive. Keep an explicit environment specification where supported and
   record any pip-installed packages separately. Environment recipes and version
   constraints are not resolved installations. The application exports available
   observations, but does not bundle software environments.
4. Record every database release/accession, preparation method, file-content
   fingerprint and model identity actually used. Preserve the prepared resources
   or an immutable, verifiable copy where licensing permits. A directory name is
   not a version. Application exports do not bundle database/model contents or
   establish that they stayed fixed on your machine.

## Check the run and preserve its evidence

Compare the observed outputs with the expected artifact list. Check the declared
steps and execution outcomes, native outputs, logs and retained histories.
Exercise retry/interruption and report-only refresh as separate software tests;
verify that replacement preserves the earlier generated files. A hold, failed
attempt or reused result must remain labelled with that outcome. A configured
diagram is not proof that all its tools executed.

Inspect Methods provenance, including `software[].scope`,
`runtime_evidence.versions` and `runtime_evidence.status` when available,
`evidence_origin`, `supplemental_evidence` and `warnings`. Recorded controller versions are distinct
from installed package inventories. Missing or unattributed evidence stays
unresolved; do not fill historical gaps from today's installation.

## Native-retention acceptance for this update

Use a finished test attempt and record which stages actually ran. Unselected or
unreached stages remain untested. Inspect each native output's `native-retained/`
directory; retain pending or failed evidence for review. Paths in the table are
relative to one `native-retained/<session-id>/` directory unless stated otherwise.

| Check | Evidence to inspect | Acceptance record |
| --- | --- | --- |
| Session identity | `session.json`; Flye source/argument metadata; BUSCO `busco-driver.json` with source, adapter hashes, arguments and status | Match the intended tool, source and attempt. A session file alone does not establish completion. |
| Archive completion | Every `events/<id>/manifest.json` and `failure.bin`; preservation events use `archive`/`inventory`, while process-stream events use each `streams` entry's `path` | Event `state` is `complete`, declared files are present, and recorded byte counts/hashes match where supplied. A pending/missing manifest or failure latch `1` is a preservation failure. Latch `0` alone is not proof that analysis finished. |
| Flye, Dev only | Retained consensus and polishing-iteration alignments, bubbles/consensus files, graph-edge alignment and unfiltered polished contigs; earlier `minimap.stderr` and `params.json` generations | Compare the artifacts expected for the stages and iterations actually reached with both active outputs and archive receipts. Do not require an artifact from a stage that did not run. |
| BUSCO logs and jobs | First archived baseline of each pre-existing shared log actually reopened by a reviewed job; unique `native-process-streams` events with `command`, `returncode`, `outcome`, `partial` and `streams` | Prior shared-log bytes survive. Successful and failed jobs keep separate captured stdout/stderr files with their hashes; `captured: false` or partial streams remain explicit evidence gaps. |
| Full isolate export | Exported native directories, receipts and payloads, including failed attempts; export `manifest.json`/`SHA256SUMS` | Verify destination hashes against the reviewed source files and confirm the originals still have the same bytes. Keep native histories with the entire linked-run export. |

A native analysis can fail while preservation completes successfully: for
example, a nonzero BUSCO return code with complete stream/archive receipts.
A preservation error is a separate failure and must block the retention
acceptance result. Pending events can already contain moved originals whose
former paths are absent; keep those events and inspect their recorded archive
paths. Do not treat failed or partial analysis products as completed results.
Native compiled-program spill files, uncaptured streams, kernel scratch and
in-memory states remain outside this capture. No scientific accuracy criterion
or additional stage measurement is introduced by these checks.

## Archive the baseline

After active work finishes, use **Samples → Export isolate → Copy & verify**.
Keep `manifest.json`, `SHA256SUMS`, `EXPORT-COMPLETE.json`,
`records/isolate-inventory.json`, available logs and histories, and each available
`methods/<run-id>/reproducibility.zip`. Keep `methods/<run-id>/UNAVAILABLE.txt`
when Methods could not be generated. Inspect `scope_status`, `missing`,
`warnings` and `input_verification` in `manifest.json`.
`verification: verified-local` establishes local copy integrity, not complete historical scope or completed SharePoint
synchronization. See [Isolate export](isolate-export.md) and
[Output preservation](output-preservation.md) for their scope and limits.

Archive each baseline and attempt separately. A change to input, source,
configuration, environment, database or model identity needs a new comparison
record. Keep **software acceptance**, **native tool integration acceptance** and
**independent analytical validation** as separate outcomes. Passing software
checks or retaining files does not validate scientific interpretations.
