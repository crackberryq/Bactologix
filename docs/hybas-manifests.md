# HybAs manifest inputs — Bactologix 0.1.0a55 Alpha

In Samples, choose **Import HybAs manifest**, enter the local JSON/YAML path,
and preview the declared libraries. Relative read paths resolve beside the
manifest. Import registers the reviewed sample plans and file identities.
Changing declarations or files after preview requires another preview. Existing
sample IDs are never overwritten.

Creating a run requires a reviewed immutable HybAs release with the `hybas-v2`
configuration contract. The application update alone does not activate an
unpublished workflow. Select a compatible alpha from Install & update once its
release contract is registered. The installer uses the source's pinned controller
requirements; the new tool recipes appear in environment setup.

New v2 workspace defaults use Auto for supported resources, modules, input mode
and polishing. CPU/memory budgets in the draft remain scheduling ceilings.
Saved defaults and explicit overrides are preserved. Auto does not infer library
preparation, read orientation, chemistry or scientific thresholds. Review those
in the manifest; use the native dry-run before tool preparation or execution.

Every draft freezes a single-sample manifest alongside its configuration and
source. Resume checks that frozen manifest and each input's size/mtime. Editing
or deleting the original manifest cannot silently change an existing run.
Input-content hashing remains the workflow's provenance responsibility; size/mtime
checks are not content hashes. Changed reads require a new registration/draft.

The HybAs research report is listed with existing reports. Open its folder to
use local TSV/JSON exports and links to computational history. Biological
qualification and the reviewed HybAs release remain separate from these software
adapter checks. Old runs keep their original settings, runner and cached tools.
