# Follow-up with a reviewed BaSIS release

A completed screening run keeps its original source and results. In its run
page, choose **Review follow-up**, select the installed workflow version, select
**AMRFinder reported review cues**, review the YAML and explicitly enable DeepSafe.
Create the draft, validate it, check its environments, and then run it. No analysis
starts when the draft is created. Missing environments/resources require explicit
setup through the existing controls.

The default five-candidate ceiling applies per isolate. Selection does not imply
resolution: deferred, unassessed and completed-but-unresolved findings remain for
review. AMRFinder review considers reported evidence; it cannot recover alignments
that AMRFinder did not report. Strict/medium/loose are AWARE selection presets,
not native AMRFinder reporting modes.

## Source and release compatibility

Refresh the BaSIS release catalogue and install `v0.4.0a4`. This release is
approved for its own screenings and for saved `v0.4.0a2` screening bundles.
Other historical source versions need their own compatibility checks. A new
follow-up can use that installation only when the official registry approves its
follow-up entry point, screening parent version and scope. General release
approval alone does not authorize a source transition. Existing follow-up drafts
retain their recorded version. The original-workflow choice retains the parent's
implementation and policy; unsupported scopes are rejected.

Optional `followup` capability in `compatibility/releases-v1.json`:

- `interface: 1`, `entry_point: Followup.smk` and its `files_sha256` checksum;
- `screening_schema: basis-snakemake-run-v1`;
- `parent_commits`: exact earlier screening commits exercised by adapter checks;
- `scopes`: supported values from `amrfinder-review` and `all-supported`.

A release is compatible with its own screening commit. Future matching releases
can be registered after their adapter checks without a new hard-coded client
allowlist. Discovery never automatically executes unreviewed code. Approval rows
for an existing immutable commit cannot be rewritten.

The draft snapshots the selected source and binds its runner/cache installation.
`followup-transition.json`, the run Overview and Methods record the screening and
follow-up commits, source checksums, selected scope and reviewed contract. Validation
and resume reject modified snapshots, inputs, screening prerequisites or transition
records. Installing a new release does not rewrite existing runs. Later follow-up
attempts share the isolate's follow-up output folder; only the latest executing
attempt owns that displayed report, and original screening outputs stay separate.

Follow-up drafts share scheduler metadata under the screening run's
`followup-state` directory, while keeping separate frozen workflow/configuration
copies. This lets Snakemake see changes between requests, including a return to
an older request, instead of treating existing outputs as up to date. When these
scheduler records are absent (including migration from an older adapter), the
first validation/execution explicitly recaptures the reviewed request. The dry-run
shows the follow-up work required. After capture finishes, ordinary provenance
tracking supports interruption/resume and unchanged no-op reruns.

## Acceptance

Unit checks cover unsupported versions/scopes, incompatible parents, unfinished
setup, modified sources and preserved screening hashes/timestamps. Pinned workflow
checks use an older synthetic screening bundle and the newer actual `Followup.smk`
through a nonempty selection, explicit unassessed result, report/export and no-op
rerun. Browser checks cover version/scope selection, review reset, draft creation
and mobile layout. These do not run biological tools or validate candidate function.
A real AFB01/isolate1 follow-up, runtime and independent interpretation remain beta
acceptance work.
