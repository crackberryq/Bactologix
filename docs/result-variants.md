# Result variants (alpha)

A new draft starts independently. **Create variant** branches from a successfully
completed managed run, retaining eligible intermediate results in independent
copies. Original selections are inherited; change only the settings or tool
selections you want to vary. The parent source, configuration, results and job
history remain unchanged. Creating a variant never launches analyses.

Open a completed run → **Create variant** → edit settings → **Review drafts** →
**Create variant**. Inspect **Result inheritance**, then use **Validate run** to
review the native DAG. New jobs may be checkpoint restoration or native cache
verification jobs; these do not imply that the original analysis tool ran again.

## Current reviewed coverage

| Pipeline | Reuse mechanism | Current boundary |
| --- | --- | --- |
| HybAs `780ee7086c15c49f2825800ec463327f009f14a6` | Completed Snakemake checkpoints, copied and SHA256-checked; concrete import rules supply them to the native DAG | Reviewed changes: Racon rounds, polishing mode, Medaka model, Polypolish/BUSCO/Prokka/MultiQC selection and BUSCO lineage. Other changes conservatively invalidate checkpoints. |
| BaSIS `14f161a1f252a712053892961edf2275469c8dab` | Native verified completion caches, copied into the child and rebound to its paths | Native completion-cache modules, including the RGI database dependency when available. Every native job still checks its full execution identity before accepting a cache hit. PathogenFinder2 uses its older completed-evidence/checksum interface, described below. DeepSafe and other outputs without a reviewed reuse interface still recompute. |
| MICP Scope a3–a5 | The native `protein_properties` multi-output checkpoint | Same inputs/configuration; report generation remains native. A changed/unrecognized setting conservatively recalculates properties. |

For **Racon 1 → 2**, valid trimming, Kraken, AWARE, assembly and round-1 artifacts
remain usable. Round 2 and dependent final/assessment/report work are scheduled.
A second branch can reuse checkpoints inherited through the first branch, even
when an earlier checkpoint did not need to be materialized in that branch.

For **BaSIS A → A+B**, the child offers A's verified cache to the native runner;
B executes normally. If A's inputs, settings, executable, environment, resource
identity or relevant source differ, BaSIS recomputes A according to its existing
rules. Unticking A excludes its cache from import and lets the native workflow
record its excluded scope. No interpretation thresholds are changed.

## Completed PathogenFinder2 results

Starting with Bactologix a52, a completed PathogenFinder2 assessment can be
retained from the reviewed BaSIS v0.4.0a2 commit without updating or rerunning
that parent. The adapter checks the native normalization completion record,
recorded input and artifact SHA256 hashes, per-module settings, resource
receipts and inventories, recorded classifier weights and environment records.
It copies the recorded embeddings, classification artifacts and parsed result;
only run-local paths in copied JSON documents are rebound. Original documents
and evidence remain in `variant.json`. Native normalization and reporting run
for the child; historical inference and runtime are not claimed as new work.

Adding another module does not invalidate this completed bundle. Unticking
PathogenFinder2 excludes it. Changes to its settings or shared configuration,
missing provenance, modified bytes or changed environments prevent reuse and
are explained under Result inheritance. This is completed-result reuse, not
partial-stage recovery: an incomplete PathogenFinder2 assessment must finish
normally before it can be inherited. No models, thresholds or interpretation
rules are changed. Existing variants keep their frozen behavior; create a new
variant after updating the application to use this adapter.

Full resource checksum verification can take time for large model files. The
copy needs space for both its immutable snapshot and materialized outputs.
Files merely present on disk are never treated as completed results.

## Provenance and integrity

- Branches keep the parent's exact pipeline commit; cross-version reuse is not
  supported. A newer compatible release is not automatically a reviewed reuse
  contract. Unsupported commits explain why result branching is unavailable.
- Only a successful completed parent is eligible. Drafts, failed/interrupted
  runs and native incomplete markers cannot be promoted into completed results.
- Snakemake format-6 completion records establish checkpoint eligibility. Stored
  input checksums are checked when available. Historical large-file content
  hashes are not invented: older native records primarily establish completion
  and timestamps. New snapshot bytes and original inputs are SHA256-bound.
- Changed native dependencies invalidate downstream checkpoints. Unknown config
  changes fall back to recomputation and are listed in the variant overview.
- Shared environment history and selected database file metadata are checked for
  changes. Database size/mtime inventories are change detectors, **not** full
  historical database-content verification. A new environment can be added
  without changing a previously captured environment.
- Copies use distinct files, never writable links or hard links to parent
  outputs. BaSIS verifies original receipts and payload inventories, then retains
  original records inside the imported cache provenance. Native cache hits still
  require the newly computed execution identity to match exactly.
- A variant uses its own BaSIS result store even if the parent used an external
  store. Resource/database locations are retained. Failed attempts, logs,
  benchmarks and old HTML reports are not imported as new results or telemetry.
- `variant.json` records the parent, changed settings, candidate checkpoints,
  inventories and native provenance. The Results manifest download includes it;
  Methods exports distinguish inheritance from new execution.
- A process-bound copy lock prevents concurrent managed jobs on the parent.
  Failed copies remove the unpublished child. A process crash releases the OS
  lock; a later action can discard the stale database lock row.

Checkpoint snapshots take disk space; HybAs/MICP copies reserve room for both
snapshot storage and materialized outputs. Missing, modified or linked
checkpoints may require native recomputation. Linked directory contents without
a native cache adapter are not assumed safe to inherit. BaSIS candidates with
unknown cache dependencies are not imported.

## Validation boundaries

Software tests execute text-only Snakemake fixtures, including two generations
of Racon branching and a multi-output MICP fixture. The real pinned HybAs DAG is
parsed with synthetic records; no native analysis tool is executed. BaSIS tests
use its actual result-store implementation with synthetic payloads and callbacks
that fail if a supposedly reused tool executes.

Full scientific-tool/workstation acceptance, large datasets, disk usage on the
user's Windows/WSL volumes, and native tool outputs outside the documented reuse
interfaces remain outstanding. No existing user sample analysis is launched or
repeated by these tests. This is an alpha implementation, not a claim that every
pipeline step already supports cross-run reuse.
