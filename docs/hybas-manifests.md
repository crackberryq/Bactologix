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
source. New drafts record a streaming SHA-256 fingerprint for each input. Resume
and reuse check that frozen manifest, each input's size/mtime, and its recorded
content fingerprint. Editing
or deleting the original manifest cannot silently change an existing run.
Quick UI readiness checks use metadata and links without rereading large read
files; full content checks run before launch or reuse. Older drafts without
fingerprints retain explicitly unverified original content identity; the app
does not retroactively invent hashes. A newly created child draft fingerprints
the current files without upgrading its parent's historical provenance.
Changed reads require a new registration/draft.

The HybAs research report is listed with existing reports. Open its folder to
use local TSV/JSON exports and links to computational history. Biological
qualification and the reviewed HybAs release remain separate from these software
adapter checks. Old runs keep their original settings, runner and cached tools.


### Identification review

With a compatible HybAs 10 source, **Pause after identification** is available alongside manifest inputs and Auto settings. The app displays the actual Illumina, ONT or PacBio evidence, saves a target choice for later, and continues only after explicit confirmation. The pause option is boolean; it is not an Auto setting. Source, manifest and evidence identity checks still apply.

This integration is an unreleased development build (`0.1.0a86.dev6`). The older a55 release document records the original development checkpoint, not the current app version.
