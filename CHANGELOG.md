# Bactologix releases

## 0.1.0a17 — automatic release-specific runners

- Read the required Python and exact Snakemake versions from each reviewed pipeline release's environment file instead of enforcing one global runner version.
- Install workspace-local Conda automatically when managed setup needs it, then create or check the release's isolated runner before activation. Preserve older runners, external environments, tool caches and saved runs.
- Show verified requirements in the installation status and setup log. Reject unpinned/conflicting requirements, keep failed setup unready and preserve the previous active version when a new installation fails.

## 0.1.0a16 — reuse environments and uninstall unused versions

- Add **Reuse existing environments** and the selected cache path directly to each ready installation. List known same-pipeline caches, allow browsing to another cache, and open the native environment checker after selection.
- New pipeline installations inherit the previous active version’s custom tool cache; retries and existing saved runs keep their own cache. Native recipe matching determines which tools can be reused.
- Add a reviewed **Uninstall version** action for unused managed source snapshots. Preserve runners, tool caches, databases, results, logs and original checkouts; block active versions, saved-run dependencies, unfinished downloads, pending jobs and modified or unowned sources.
- Revalidate source identity and dependencies during removal. Coordinate activation, maintenance requests, cache changes and new-draft registration with uninstall; restore the source if registry removal fails.
- Add lifecycle regression checks, Chromium reuse/uninstall coverage and real sample-free cross-version environment reuse verification.

## 0.1.0a15 — guided workspace configuration

- Configuration now uses the draft editor's category tabs, mode cards, switches and searchable Advanced sections. Full YAML, import/export and validation are retained.
- Pipeline tabs keep independent unsaved edits. Save defaults changes future drafts only; existing runs keep their frozen configuration.
- Defaults saves are revision-checked in a transaction so another window or newer database selection cannot be silently overwritten. A clean editor refreshes changed saved settings, and Reload saved defaults discards local edits.

## 0.1.0a14 — guided settings for saved runs

- Redesign the saved-run details dialog with Overview and pipeline-specific setting tabs, readable saved values, resolved database paths and a compact execution summary. Keep raw commands and frozen YAML under Advanced.
- Add Edit settings to saved runs, using the same guided controls as new drafts. Start from the run’s frozen configuration, retain custom keys and database paths, and review CPU/memory settings before creating a separate revised draft.
- Preserve the original run, input identity, pipeline installation, tool cache and saved global defaults. Block edits to archived, active, follow-up or changed-input/source runs; require a new native dry-run for each revision.
- Extend backend and real-browser checks through saved-value viewing, cancel/discard, guided revision, YAML round trips and final configuration persistence.

## 0.1.0a13 — clear run lists and a guided draft builder

- Publish checked application wheels and SHA-256 sums to GitHub Releases so the in-app updater can discover them. This release includes the database, environment, sample and draft-configuration improvements from the a8–a12 development candidates.
- Remove sample now also archives its inactive saved runs, matching both sample ID and input folder. Queued/running jobs stay visible. Frozen configuration, files, reports and job history are retained.
- Label the upper panel Run drafts & history with Current runs and Archived tabs, individual/batch Remove from list and Restore. Block stale launch requests for archived runs under the job queue transaction.
- Replace the generic draft configuration landing page with a Samples → Run settings → Review flow. Add category tabs, Kraken/polishing mode cards, quality switches and BaSIS module cards. Keep every native setting and custom YAML under Advanced.
- Preserve independent pipeline edits, saved defaults and native algorithms. Extend browser coverage through archive/restore, guided controls, mobile layout, YAML round trips and final draft settings.

## 0.1.0a12 — configure each run before creating drafts (candidate)

- Add Samples, Run settings and Full YAML tabs to Create run drafts, with searchable configuration sections, switches, dropdowns, numeric controls and structured-value editors for both pipelines.
- Start from a snapshot of saved defaults and apply edits only to this request's drafts. Keep independent edits while switching pipelines; preserve custom keys and existing database paths.
- Keep Kraken on/off and its Off/Manual/Auto/AWARE decision mode consistent. Expose polishing, quality/annotation, resource limits, BaSIS modules, AWARE and DeepSafe settings without changing native decision algorithms or presets.
- Check input types and common invalid combinations before draft creation. Keep output/input locations and top-level CPU/memory assignment under the existing run adapter. Open a single created draft for review and a native dry-run.
- Add application, pinned native dry-run and Chromium acceptance coverage for run-local configuration propagation and unchanged saved defaults.

## 0.1.0a11 — remove samples from selection (candidate)

- Add **Remove sample** to Registered samples and the Create run drafts picker. Removal unregisters the sample, clears its selection and keeps the picker open.
- Preserve original files, frozen runs, jobs and reports; allow re-registering the folder. Reject stale removal requests when the registration has changed.

## 0.1.0a10 — apply saved databases to existing runs (candidate)

- Add **Use saved databases** in run details and **Use saved databases for runs** on the database page, with current/saved paths and per-resource selection.
- Create independent revised drafts with the chosen saved paths in the actual native command configuration; retain input identity, pipeline version, cache, resource budgets and other run settings. Open a single revised draft immediately for its required fresh dry-run.
- Recheck saved locations and original run integrity before applying; reject stale previews, incomplete resources and active runs. Preserve historical runs and unselected database paths.
- Add regression coverage for the reported missing-BUSCO failure, BaSIS setting preservation, batch revisions, and the real browser save/apply flow.

## 0.1.0a9 — database downloads and tool environments (candidate)

- Discover registered Conda prefixes, hidden Snakemake caches and linked database folders; list incomplete matches with actionable reasons.
- Add native database preparation with a destination picker, resource budgets, BUSCO lineage selection, provider log tails and resumable jobs. Preserve existing resources and require explicit selection/save after completion.
- Add per-installation environment selection, real executable/version checks and Snakemake-managed installation without sample runs. Keep missing, incomplete and failed states separate.
- Add a configurable launcher listen address for WSL private-IP access.
- Add native preparation-plan tests, browser coverage and a real Conda deployment acceptance job.

## 0.1.0a8 — run integrity, repair recovery and BaSIS AWARE

- Verify generated sample sheets and staged input links before execution, including older drafts reconstructed from their recorded source identities.
- Reject comparison variants when original input contents or file inventories differ from their parent.
- Route existing drafts through repaired controllers for the same source commit; require a fresh dry-run and retain original controller provenance.
- Preserve failed execution status across subsequent checks and label reports with their source run status. Require completed HybAs runs before managed handoff.
- Add reviewed BaSIS AWARE 0.4.0a1 compatibility and current HybAs source compatibility while retaining previously supported snapshots.
- Use screening-first defaults for new BaSIS configurations. Preserve existing saved preferences. Add an explicit reviewed saved-screening follow-up request and separate follow-up report discovery.
- Track shared follow-up output ownership so older requests do not claim a later request's report.
- Prevent editing form controls while a server request is pending, including the folder picker’s initial load, so late responses cannot overwrite newly entered paths.
- Add regression, pinned-source integration and real-browser tests, with GitHub Actions on Python 3.11/3.12 and package builds.

## 0.1.0a7 — database folders and automatic discovery

- Replace the resource-configuration shortcut with named HybAs and BaSIS database/model cards, folder browsing, checks, detected-installation selectors and saved locations.
- Add bounded, read-only discovery with visible scan scope/limits and explained preferred choices. Keep valid configured locations and manually edited choices; prefer the configured BUSCO lineage when available.
- Map independent HybAs Kraken2/BUSCO folders through separate managed link layouts, keeping earlier run paths intact.
- Resolve BaSIS file-backed databases and complete resource bundles using native layouts and allowlisted receipt fields. Label environment-bundled resources accurately instead of offering ineffective overrides.
- Revalidate paths on save, reject stale configuration revisions, preserve unrelated settings and existing frozen drafts, and support explicit per-resource resets.
- Reuse the WSL-compatible folder explorer, including return-to-page behavior and an existing-parent fallback for absent database locations.

## 0.1.0a6 — BaSIS setup and Bactologix updates

- Support BaSIS `f66dfedd46da05ae79bf769e7e6fada91270d4d1`; retain support for `d2d2e7806df4d2f586dc4a11d5c1f980deb06857` and existing frozen runs.
- Keep compatible source snapshots in the version picker after a GitHub refresh. Prefer the reviewed current snapshot and distinguish source-only downloads.
- Add visible Finish setup / Retry setup actions, informative installation states and setup logs. Already downloaded compatible source can finish setup without another download or forced runner replacement.
- Add Update Bactologix with release selection, current/older-version labels, release notes, SHA-256 and wheel/Python checks, an actual pip installation job in the current app environment, and restart guidance.
- Serialize app updates against queued work and other Bactologix workers sharing the Python environment. Keep the rendered workspace available while pip replaces package files; require restart before further work.
- Refresh bundled BaSIS configuration references from the reviewed source; preserve existing saved defaults. Bind optional native-result storage to an explicit absolute path.
- Launch Streamlit headlessly to avoid WSL's failed automatic `gio` browser-opening attempt; disable source watching so package replacement does not trigger an automatic rerun.

## 0.1.0a5 — browse samples and connect existing installations

- Add a local folder explorer with Home, workspace and mounted Windows drive shortcuts, folder filtering, hidden folders and pagination. Select a sample folder without uploading reads; suggest its sample ID and read mode.
- Add Connect existing to HybAs and BaSIS, with direct folder selection and a bounded local checkout scan. Show exact commit and adapter compatibility before connecting.
- Discover Conda environments and offer an existing runner or a separately managed runner. Existing runners must pass the native health check; no package installation or update is performed in a selected existing runner.
- Copy committed source from local Git without downloading it again or touching the original checkout, configs or outputs. Flag local edits for configuration import.
- Allow an existing Snakemake Conda cache and freeze its path into new run drafts. Native matching determines reuse; missing environments may be added during preparation.
- Keep compatibility checks and independent-output behavior from a4. Existing external runs are not adopted for in-place resume.
- 27 automated tests pass; browser component checks cover folder navigation, selection, detection results and runner selection. Full Streamlit browser testing on the target WSL workstation remains a user-side check.

## 0.1.0a4 — original HTML interface, connected

- Restore the approved HTML design as the default Streamlit interface: sidebar, dashboard cards, comparison views, version picker, configuration editor and dialogs.
- Connect HTML controls to the existing Python/SQLite worker through Streamlit’s bidirectional component API, with duplicate-request protection and visible errors.
- Preserve full YAML editing, sample import, run drafts, batch actions, clones, native execution gates, jobs/logs, reports and citations.
- Embed original HTML reports in sandboxed viewers; retain original-file opening and downloads.
- Bundle all UI assets in the wheel and source distributions; no separate web build or Node installation is needed.
- Keep the existing execution adapters and independent-workflow clone limitations.

Validation: 18 automated tests, browser checks of the HTML component, and a real Snakemake smoke job submitted through the Streamlit component trigger. Full Streamlit browser integration on the target WSL2 workstation remains to be checked.

## 0.1.0a3 — MIT license packaging

- Add the MIT license, copyright (c) 2026 Jimi Goldstein, at the repository root.
- Declare MIT in package and citation metadata and include the license in installable/source distributions.
- Refresh the version in the launcher, citations and installation instructions.
- Preserve the execution behavior and alpha limitations of 0.1.0a2.

## 0.1.0a2 — first native alpha

Created by **Jimi Goldstein · Human Link**.

- Native Streamlit application and `bactologix` launcher for Linux/WSL2.
- GitHub version selection, immutable source downloads, workspace-local Miniforge setup and separate HybAs/BaSIS controllers.
- Environment identity checks and reuse, complete YAML configuration, sample registration/import and frozen per-run configuration.
- Independent run and polishing variants, sequential durable jobs, cancellation, logs and managed run/resume.
- Native HTML report discovery/viewing/downloads and clear credits with downloadable citations.

### Verified

14 automated tests, live source and controller installation, native HybAs/BaSIS dry-runs, controller reuse and a real Snakemake software smoke workflow. See [VALIDATION.md](VALIDATION.md) for the scope and details.

### Alpha limitations

- Efficient post-AWARE checkpoint reuse and polishing-only execution are not implemented. Clones start independent workflows from original inputs.
- Execution adapters are pinned to the two reviewed commits documented in the README; other catalogue versions are source-download only.
- Existing external runs are not adopted for in-place resume. Jobs run sequentially; remote/HPC execution is not implemented.
- Full biological analyses, analysis environments/databases/GPU tools, target-browser behavior and WSL2/aarch64-specific installation have not been validated in this development session.
- This release is distributed through GitHub, not PyPI.
