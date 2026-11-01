# Changelog

## 0.1.0a60 — Put the recommended pipeline release first

- Show the recommended compatible release at the top of the pipeline version picker, followed by other releases and legacy snapshots. Mark the installed source separately and remove the misleading recommendation text from legacy snapshot names.
- After a successful catalogue refresh, update the automatic default selection to the newly recommended release. Preserve explicit user choices, failed-refresh selections and release/source filters. Refreshing does not install a pipeline or change any saved run.
- Add browser regression coverage for the stale-catalogue refresh, manual legacy selection, unavailable releases, stable-only filtering and the exact selected installation request.

## 0.1.0a59 — Reviewed BaSIS follow-up versions

- Fix stale follow-up reuse across separate drafts: share Snakemake provenance for their shared outputs, bootstrap missing capture metadata once, and preserve normal changed-request and unchanged-resume decisions.

- Register BaSIS v0.4.0a4 after cross-version adapter checks. Refresh the BaSIS catalogue to select it for new runs or reviewed follow-up from v0.4.0a2 screening.

- Select a compatible installed workflow for follow-up while retaining the original screening source and evidence. Record both commits, workflow checksums, scope and reviewed compatibility contract. Validate source, input and transition integrity before execution or resume.
- Expose AMRFinder reported review cues as an explicit scope with a default ceiling of five unique candidates per isolate. Clarify that AWARE presets do not change native AMRFinder thresholds. Unassessed and deferred evidence retains review obligations.
- Extend release contracts with tested follow-up entry points, parent versions and scopes so future compatible releases can be registered without another hard-coded client allowlist. Existing runs remain frozen.
- Add source-transition, unchanged-screening, native workflow/report and browser coverage. Real AFB01/isolate1 follow-up and scientific validation remain beta acceptance work.

## 0.1.0a58 — Tolerate desktop control-file contention

- Retry brief Windows sharing/access conflicts when atomically replacing desktop control files. Use unique temporary names, preserve the last complete JSON on failure, clean temporary files and include the path/Windows error in persistent failures.
- Read control files with delete sharing so the Windows supervisor does not block WSL status-file replacement.
- Require ten seconds of sustained browser absence before requesting session shutdown; a single missed or empty browser response no longer closes the session. Heartbeats continue during this grace period.
- Add Windows tests with real conflicting file handles, bounded failure/preservation checks and transient browser-response recovery. Physical maximize/minimize interaction and full WSL workstation acceptance remain outstanding. No user analyses are run.

## 0.1.0a57 — Pass WSL options correctly

- Leave simple command-line options and distribution names unquoted. WSL can treat a quoted `--list` as a Linux command, causing `/bin/bash: --list: command not found` despite Ubuntu being installed. Keep escaping for spaces, quotes and other complex values.
- Apply the fix to discovery, prerequisite checks, path conversion, setup and desktop startup through their shared process launcher.
- Add native Windows `wsl.exe --help` coverage, exact WSL option routing assertions and expanded argument round-trip tests. Physical Windows/Ubuntu installation and native pipeline acceptance remain workstation checks.

## 0.1.0a56 — Recognize existing Ubuntu installations

- Decode WSL distribution listings as UTF-8 or UTF-16, with or without a byte-order mark, instead of assuming one Windows output format. Preserve versioned Ubuntu names.
- Stop with actionable diagnostics when WSL discovery fails; never interpret a failed command as proof that Ubuntu is absent.
- Add Windows subprocess encoding fixtures and regression coverage for discovery failures. Existing environments and analyses remain unchanged. Physical workstation acceptance remains required.

## 0.1.0a54 — Windows desktop setup and owned-session shutdown

- Add a downloadable Windows setup bundle with visible stage progress, setup logs, prerequisite guidance and a branded desktop shortcut. Reuse the existing Linux installer and Conda environment rather than introducing a separate installation layout.
- Launch WSL invisibly through the shortcut and open a dedicated Edge/Chrome profile. Closing its last window requests shutdown, asks before stopping active jobs, and waits visibly for cleanup. App updates finish safely before exit.
- Tag jobs by desktop session, gate submissions while closing, and reuse native cancellation. Lost supervisor/launcher leases cancel owned work. Independent Ubuntu work and other sessions remain untouched; no WSL distribution shutdown is used.
- Share the b-dot logo across the application, favicon, installer and shortcut. Preserve the traditional terminal launcher and its documented lifetime.
- Add Windows PowerShell/argument/shortcut/rendering/browser-isolation CI, Linux owned-process shutdown tests and deterministic release-bundle checks. Physical Windows/WSL first-install, reboot/UAC, browser policies and native-analysis interruption/resume remain workstation acceptance checks. No user sample analyses are run.

## 0.1.0a53 — DeepSafe follow-up from completed variants

- Fix reviewed follow-up requests created from completed BaSIS result variants. Verify the parent variant's content before creating the request, then use `Followup.smk` with the follow-up's own saved-evidence fingerprints. Screening variant manifests and restore commands remain with the parent.
- Preserve parent results, configuration and provenance. Existing ordinary screening runs retain their follow-up route; previously broken follow-up drafts can be replaced by a new reviewed request from the completed parent.
- Cover native-cache and completed-result variants, unchanged parent files, changed input content and changed screening evidence with software regression tests. Native DeepSafe execution on a workstation remains a separate acceptance check.

## 0.1.0a52 — preserve completed PathogenFinder2 results in variants

- Reuse completed PathogenFinder2 result bundles from the reviewed BaSIS v0.4.0a2 release when recorded inputs, settings, artifacts, resources and environment provenance still agree. Rebind copied artifact paths to the child; retain original evidence and checksums.
- Keep changed or unverified results out of reuse and explain why. Unticking the module excludes it; original runs remain unchanged. DeepSafe reuse remains outside the reviewed interfaces.
- Fix native BaSIS cache imports to locate the nested workflow libraries in actual managed run folders. Regression fixtures now reproduce that layout.
- Test opaque artifact restoration without invoking original stages, changed-input/resource rejection, repeated branching, the native normalization interface and the real BaSIS DAG. Native scientific-tool and physical Windows/WSL acceptance remain outstanding; no user analyses are run.

## 0.1.0a51 — isolated result variants

- Add Create variant for completed managed HybAs, BaSIS and MICP Scope runs, preserving the parent and recording inherited results separately from new execution.
- Reuse verified intermediate checkpoints on reviewed pipeline commits. HybAs Racon 1→2 retains eligible upstream results and computes the added round and affected downstream work. Changed dependencies require recomputation.
- Import selected BaSIS native result-cache candidates with full native identity validation. Unticked modules are excluded; PathogenFinder2 and DeepSafe outputs without native cache support still recompute.
- Verify copied artifacts, retain immutable provenance, protect parent runs during snapshots, and roll back failed variant creation.
- Validation passed on Python 3.11 and 3.12 (623 tests each), Chromium (18 tests), and sample-free environment deployment (2 tests). Synthetic workflows verify incremental reuse; native scientific-tool and physical Windows/WSL acceptance remain outstanding. No existing user sample analyses were launched.

## 0.1.0a50 — compact MICP Scope reports

- Recommend `micpscope-v0.1.0a5`: collapsible categories, filtered exports, composition and recorded genomic/protein context.
- Automatically stage optional sample GFF3 and InterProScan TSV inputs and track their identity with the frozen run.
- Preserve older supported sources and the shared pipeline flow.

## 0.1.0a49 — named MICP Scope release and categorized reports

- Recommend `micpscope-v0.1.0a4` with a cited JSON catalog and categorized gene/function reports.
- Keep previous compatible snapshots, preserve the reviewed-release contract system, and label development sources explicitly.


## 0.1.0a48 — reviewed releases and consistent pipeline flow

- Order all shared pipeline controls as HybAs AWARE, BaSIS AWARE, then MICP Scope. Keep the database heading, tabs and resource-card layout for pipelines without external databases.
- Refresh a versioned, maintainer-reviewed release contract registry alongside GitHub releases. Compatible tested releases can be approved without changing the application package; unreviewed commits stay source-only.
- Validate adapter interface, configuration schema, entry points, exact commit, pinned runner and source checksums. Persist immutable approvals for older runs and retain dated cached catalogues when refresh fails. Add stable/prerelease filters and installed/latest/recommended version information.
- Bring validation notes up to date for a44–a47 and document the new registry and workstation limits.

## 0.1.0a47 — MICP Scope display name

- Display **MICP Scope** consistently in pipeline selectors, setup, input guidance, reports and documentation.
- Pin the matching report-name update while retaining support for existing Snakemake MICPscope runs. Repository names, workflow filenames and commands remain unchanged.

## 0.1.0a46 — retain installation history after updates

- Includes MICPscope integration and first-time setup from a45.
- Keep completed updates classified as existing installations when older source versions have been removed.
- Distinguish incomplete updates from incomplete first installations in setup status.

## 0.1.0a45 — MICPscope and first-time setup

- Add the native Snakemake MICPscope pipeline to setup, run drafts, configuration and reports.
- Detect first-time installations across all three pipelines and prepare tool environments after runner setup.
- Persist incomplete first setup for retries; retain existing environments, databases and outputs.


## 0.1.0a44 — BaSIS readable-report release

- Support BaSIS AWARE v0.4.0a2 at its exact release commit, including the readable screening report and ISEScan zero-result parser fix. New runs generate the report automatically.
- Update the recommended installation, bundled source metadata and adapter CI to the released commit. Earlier BaSIS installations and existing run bindings remain supported.
- Retain Methods defaults for v0.4.0a1 through an explicit equivalence entry; the relevant analysis defaults are unchanged. Follow-up remains available for both reviewed releases.
- Update Bactologix first, restart it, then refresh the BaSIS catalogue under Install & update and install/select v0.4.0a2. Complete its existing setup/check flow and create a new test draft; earlier drafts stay bound to their recorded version.

## 0.1.0a43 — report viewer space and folder access

- Fill the report dialog with the report, removing the fixed-height blank area and redundant helper text.
- Add Open report folder and replace the embedded BaSIS metadata/files.html Run files shortcut without rewriting saved HTML.
- Request Windows Explorer under WSL or the host desktop file manager using the recorded report location.

## 0.1.0a42 — saved report navigation

- Keep report section links inside the embedded report instead of navigating to an empty application page. Open enclosing details when jumping to a section.
- Allow native report printing and Save PDF while retaining origin isolation. Existing saved HTML files are unchanged.
- Add a Chromium regression for Run files, Screening calls, Cite BaSIS and native printing.

## 0.1.0a41 — installer preflight and lock fixes

- Fail read-only preflight on insufficient disk even when Conda is present.
- Release the setup lock before launching Bactologix, including reused installations.
- Expand installer coverage to 16 tests; physical Windows/WSL acceptance remains outstanding.

## 0.1.0a40 — beginner installation

- Add Windows/WSL/Ubuntu preparation instructions and a copy-paste installer. Discover Conda, optionally install verified Miniforge, and create bactomics or reuse legacy bactomix.
- Verify release downloads, preserve existing application installations and analysis data, and launch the existing guided setup. Include local preflight, installation lock and setup log.
- Clean-machine Windows/WSL and native Miniforge installation remain user acceptance checks. Interrupted downloads restart from scratch; incomplete Conda locations require inspection.

## 0.1.0a39 — aligned statuses and default app mode

- Align job statuses in a consistent column regardless of action count.
- Launch in app mode by default; provide `--browser` for explicit regular-browser launch.
- Add Workspace tools → Restart as browser, opening the current server address without stopping jobs. The existing app window can then be closed.
- Close workspace tools when clicking elsewhere in the interface.
- Exclude hidden and disabled controls from dialog keyboard focus cycling.

## 0.1.0a38 — dialog keyboard navigation

- Restore keyboard focus after closing dialogs, including the workspace tools menu and mobile navigation. If the original control is no longer available, focus the workspace.
- Preserve the original return destination across nested folder dialogs.

## 0.1.0a37 — interface cleanup and optional app window

- Align shared cards, forms, tabs, action rows and dialogs; fix phone-width overflow across all five Install & update views. Simplify the header and group workspace tools.
- Add a labeled mobile navigation drawer with keyboard focus handling. Keep saved-run result/activity tabs prominent and group configuration and maintenance controls. Collapse lengthy supporting explanations while retaining status and primary actions.
- Add `bactologix --app-window` for installed Chromium-family browsers, including Windows/WSL detection and ordinary-browser fallback after the server is ready. Keep normal launch behavior as the default.

## 0.1.0a36 — reviewed sample-table import

- Preview CSV/TSV sample tables with resolved folders, effective read modes, row-level errors, paging and a problems-only filter. Bound input to 500 rows / 2 MB and reject malformed tables, duplicate/unknown columns and case-insensitive ID conflicts.
- Require a matching error-free review before importing through the UI. Recheck table text, resolved folders and registrations at commit; invalidate stale previews and register the entire table atomically.
- Check duplicate IDs inside the same SQLite write transaction for both bulk and single-sample registration, preventing concurrent case-only collisions. Preserve existing registrations and original files.
- No input-content scan, run creation, workflow launch or scientific setting changes.

## 0.1.0a35 — full saved configuration comparison

- Compare 2–4 saved runs against an explicit reference using stored YAML values and recorded provenance. Preserve custom keys, separate literal dotted keys, and distinguish missing/null/empty values, scalar types and list ordering without importing runtime defaults.
- Add differences-only, setting-path and section filters, 50-row pages, exact-YAML/typed-value hashes and explicit long-cell shortening. Hide stale tables after selection/reference changes.
- Refresh and export all comparison rows as JSON, including full values hidden by filters. Bound comparison size/depth; report missing or invalid saved configurations instead of inventing values.
- Keep saved records, pipeline execution, environments and scientific settings unchanged. Cross-pipeline keys are not assumed semantically equivalent.

## 0.1.0a34 — searchable job history

- Separate always-visible active jobs and queue controls from past attempts. Add job/run/sample/action search, status/pipeline/type filters, submission ordering and 25-attempt pages, including archived-run history.
- Link successful and unsuccessful attempts to their saved runs; retain missing-run IDs and distinguish app/unassigned jobs. Preserve filters across navigation and refreshed snapshots.
- Download all matching pages as an explicit JSON snapshot containing selected job metadata only. Exclude logs, full payloads and configurations; preserve missing timestamps and stored jobs.
- Keep worker execution, queue order, saved settings, environments and scientific processing unchanged.

## 0.1.0a33 — version compatibility overview

- Add Install & update → Compatibility with app/runtime versions, exact supported commits, dated catalogue origin and installed runner records. Familiar branch/release names never grant support to an unreviewed commit.
- Distinguish active versions for new drafts from each saved run’s recorded installation. Flag missing/mismatched bindings, missing setup and validation tied to a different runner path; retain earlier supported and archived runs.
- Keep recorded requirements/check dates, latest check-job status and execution outcomes separate from fresh verification. Add search, pipeline/review/archive filters, existing setup/run navigation and a persistent tab route.
- Export the same refreshed overview as JSON. No automatic network lookup, environment probe, job submission, run migration or active-version change.

## 0.1.0a32 — storage overview

- Add explicit Storage measurements across runs, environments/runners, databases/models, retained repair folders, sources and logs. Show registered paths/references, category filters, review navigation, saved timestamps and JSON downloads.
- Keep external registered locations opt-in. Flag changed registrations, active work, missing/unavailable paths, scan limits and partial accounting; no implicit scans or cleanup.
- Count regular-file logical bytes and allocated blocks separately, deduplicating overlapping roots and hard links. Show filesystem capacity once per device, without claiming reclaimable bytes or exclusive ownership.
- Bound metadata traversal, refuse linked roots/parents, skip links/special files and unregistered cross-device entries. Preserve original data, environments, run settings and repair backups.

## 0.1.0a31 — results and download centre

- Add searchable per-run results navigation with pipeline and archived-run filters. Bring native reports, frozen YAML, a JSON results manifest, Methods, logs and resource records together on the saved-run Results tab.
- Separate the latest execution from validation/environment attempts. Keep failed/cancelled/interrupted status, unavailable files and missing resource records explicit; report presence never proves complete scope.
- Record best-effort before/after report-file metadata for new execution attempts. Show matching attempt observations and changed/unchanged metadata without asserting a producing process or reconstructing historical report copies. Preserve shared follow-up ownership.
- Recheck bounded downloads, refusing linked/non-regular files. Keep original outputs, frozen settings, existing records and scientific execution unchanged.

## 0.1.0a30 — persistent queue controls

- Hold/release and reorder waiting jobs in Jobs & progress. Held jobs remain queued, retaining duplicate-run, installation and update protections; existing queues retain FIFO order.
- Pause the queue after the current job, or while idle, and explicitly resume eligible work. Persist holds, order and pause across restarts; reject stale browser changes transactionally.
- After acquiring the worker lock, mark abandoned running jobs interrupted and pause for review. Never automatically retry interrupted attempts or overlap surviving child processes.
- Keep cancellation available, block queue changes during application updates, and require an unpaused empty queue before updating. Preserve job payloads, frozen settings and outputs.

## 0.1.0a29 — guided recovery and workstation checks

- Add an evidence-backed **Why did this fail?** panel with a reported failed step, bounded error excerpt, possible cause and targeted review navigation. Recognize storage/quota, permissions, interrupted jobs and plotting dependency failures alongside existing guidance; unknown causes remain explicit. No automatic repair, retry or scheduler-lock removal.
- Add explicit **Install & update → Workstation check** jobs for workspace file access, durable worker execution, child-process logging, controlled cancellation and an optional existing-runner software workflow. Preserve frozen runner selection, use bounded probe timeouts and install no software or databases.
- Retain dated per-attempt results and partial evidence, with an HTML viewer and JSON download. Keep artifact delivery separate from user-confirmed browser/report/download observations. Preserve previous records and all sample/run settings and outputs.
- Add software and real-browser acceptance coverage. GPU computation, Windows browser launching and physical-workstation reboot recovery remain unverified until checked on target machines.

## 0.1.0a28 — reviewed local support snapshots

- Add **Get support** in the sidebar, footer and job logs: choose a job, optionally include its log excerpt, generate an editable preview, confirm review, then download text or ZIP. Edits clear review confirmation and changed choices require a matching preview.
- Export allowlisted application versions, cached capacity, recorded setup/environment checks and selected-job provenance. Keep a saved run's exact source commit separate from the active installation; do not run new probes or include full configurations, input files, sample lists or environment-variable dumps.
- Keep logs off by default. Bound selected-log reads, reject symbolic links and special files, omit clipped/oversized lines, and conservatively redact common credentials, paths, commands and identifiers. Explicitly require user review because redaction cannot guarantee anonymity.
- Download exactly the reviewed text. ZIP contains that same text, a checksum and a static notice; no fresh/hidden diagnostics, automatic upload, external posting or persistent workspace export. Support remains available during app updates.

## 0.1.0a27 — live job progress and resource-use records

- Show elapsed time, sampled CPU, peak sampled RAM, active workflow steps and recognized current-plan task counts in Jobs, Run activity and job logs. Checkpoint changes, unavailable readings, stale updates and partial records remain explicit.
- Add per-attempt CPU/RAM charts and JSON, CSV and ZIP downloads. Preserve failed/cancelled attempts separately from resumed jobs; retain run/source/configuration provenance without raw logs, command arguments or input sequences.
- Sample only the launched command and discoverable descendants. Bound process counters, log reads and history storage; compact histories while retaining CPU integrals, observed RSS peaks and missing-reading counts. Document shared-memory accounting, brief/detached process gaps and excluded GPU/remote-job measurements.
- Keep observation best-effort and independent of job results, cancellation, output redirection and environment handling. Older jobs have no synthesized usage records.

## 0.1.0a26 — guided setup and recoverable run drafts

- Add a five-step setup guide for PC capacity, a selected pipeline, environments, databases and review. Reuse the existing explicit installation/download/repair controls, with scoped status and next actions.
- Move new drafts and run revisions onto full pages. Retain unfinished selections, per-pipeline controls, raw YAML and budgets in this browser tab across navigation and refresh; require confirmation before discarding. Saved runs remain immutable and revisions stay separate.
- Add read-only destination-permission, free-disk-space and CPU/RAM budget checks to saved runs. Recheck blocking destination failures when jobs are queued and when workers start; keep resource warnings advisory and never change frozen settings.
- Clarify that an undetected NVIDIA GPU means availability is unknown, especially under WSL. Setup and hardware detection do not certify a complete run or working GPU environment.
- Preserve explicit environment installation, automatic matching-cache reuse, database selection, Methods exports and application restart/update behavior.

## 0.1.0a25 — run-specific publication Methods

- Add a Methods tab to every saved HybAs AWARE and BaSIS run, with a Results shortcut, reviewable prose and text, Markdown, printable HTML and reproducibility ZIP downloads.
- Generate descriptions from frozen settings and exact reviewed source defaults. Distinguish requested scope from recorded execution, account for read-mode-specific polishing and per-sample module exclusions, and retain unsupported versions as configuration-only exports.
- Capture bounded execution metadata at workflow completion. Preserve historical follow-up records independently of shared outputs; identify legacy metadata read at export and leave missing tool/database versions explicit.
- Include the exact YAML, settings origins, source/configuration hashes and available citation files. Record the app version for new drafts and update the bundled Bactologix citations. Export failures cannot fail an otherwise successful workflow.
- No scientific algorithms, run configurations, environment policies or database preparation behavior changed.

## 0.1.0a24 — workstation presets and environment repair

- Add **Install & update → PC capacity** with automatic Linux/WSL CPU, RAM, swap, workspace disk, NVIDIA GPU/VRAM, driver CUDA capability and CUDA toolkit detection. Honor CPU affinity and cgroup v1/v2 limits; report unavailable probes without inventing capacity.
- Add **High power**, **Medium** and **Low** presets for either pipeline or both. Save CPU and RAM defaults alongside the YAML resource settings used by future run drafts. High uses available CPU threads within app limits and reserves at least 1 GB or 5% of RAM for the system. GPU detection is informational; presets do not enable GPU tools.
- Add **Repair environments** directly to pipeline setup. Reuse healthy environments, install missing ones, retain incomplete copies before rebuilding, and restore them on a failed/cancelled rebuild. Unsupported failures remain visible without deleting complete environments.
- Check actual NanoPlot scatter/density HTML and PNG rendering. Explicit repair recognizes NanoPlot 1.46.2 with MultiQC 1.32 and applies the locally tested Plotly 6.1.2 / Kaleido 0.2.1 workaround, recording package versions before and after. This is a compatibility workaround for the shared QC recipe, not a replacement for upstream environment separation.
- Preserve frozen run configurations and scientific settings. Repaired shared environments affect subsequent executions using that cache; the package repair receipt is retained in the maintenance job folder.

## 0.1.0a23 — consistent pipeline display names

- Display **HybAs AWARE** consistently on pipeline cards, configuration/database tabs, run lists, selectors, help text and credits. Align the workspace header and footer with **HybAs AWARE + BaSIS AWARE**.
- Use the shared pipeline names in run selectors so labels remain consistent. Native identifiers, repository/version names, workflow filenames, stored runs and environment locations are unchanged.

## 0.1.0a22 — simpler environment setup

- Put distinct **Install environments** and **Check environments** actions directly on supported pipeline setup cards. Software preparation works without a sample or run draft; each dialog has a single explicit confirmation action.
- Remove the separate **Reuse existing environments** control. Updates already inherit the previous active version's environment folder, and Snakemake automatically reuses matching environments. Show this behavior directly in setup.
- Move manual storage changes into collapsed **Advanced: environment storage**, with selection counts and Select all / Clear selection controls. Require folder changes to be saved before checking or installing.
- Preserve frozen run caches and explicit installation policy. Verify automatic cache inheritance with the real Conda reuse fixture; scientific configuration and workflow execution are unchanged.

## 0.1.0a21 — navigation and dedicated run pages

- Separate registered input folders in **Samples** from saved drafts/history in **Runs**, with sample links, search and pipeline/status filters. Make hidden selections explicit before batch actions.
- Replace saved-run overlays with full pages for configuration, checks, Activity and Results. Support direct run links, browser Back/Forward and refresh, plus recovery for unknown run IDs. Single-run launches stay in the run’s Activity view.
- Make Overview focus on active work, recent failures and recent runs. Move the software smoke check into Install & update.
- Combine comparison navigation into **Comparisons**, with saved-run selection and independent variant creation. Display the actual saved Off/Manual/Auto/AWARE Kraken mode in lists and comparisons.
- Preserve existing registration, archive/restore, guided draft/revision dialogs, frozen configurations, environment policy and automatic update restart. Scientific algorithms and pipeline execution semantics are unchanged.

## 0.1.0a20 — clearer run checks and database actions

- Replace broad readiness claims with **Runner set up** on installation cards and five scoped checks on each saved run. Distinguish saved database paths, earlier native validation and current file integrity.
- Add an explicit read-only **Check environments** action for a validated run's current native plan and selected cache. Keep installation deliberate; show missing/incomplete folders separately from available folders, and invalidate inventory receipts after runner/configuration/source/cache changes.
- Clarify database selection versus saving for future runs versus revising existing drafts. Label Kraken preparation **Download and build** and disclose required tool-environment preparation before starting any database download.
- Add recovery guidance for recognized job failures, live queue counts across pages, truthful unsaved-draft labels, clearer report/action wording, stronger text contrast, accessible mobile navigation names and toast placement above save controls.
- Preserve frozen run settings, scientific algorithms, cache reuse and automatic application restart. This first UX pass focuses on clarity; dedicated run pages and the broader navigation redesign remain later work.

## 0.1.0a19 — automatic update restart and explicit environment installation

- Successful app updates restart the supervised application on its existing address after fresh-version verification and job completion. Open tabs wait for the new server identity and health check, then refresh automatically; failed updates preserve the running server.
- Upgrading from a18 or an older launcher needs one final manual restart with `bactologix`. Subsequent updates use the supervisor. Existing manual Streamlit sessions retain clear fallback instructions.
- Normal runs reuse matching, completed Snakemake Conda environments only. Missing or incomplete environments stop the run with an explicit installation choice; no automatic creation, deletion or repair occurs, including checkpoint/subprocess deployment.
- Pipeline updates retain the selected environment cache. The saved-run preparation action is now labelled **Install environments**; **Install selected** remains available for deliberate deployment. Changed requirements still require a matching environment.
- Add real launcher restart, Chromium tab-refresh and sample-free Conda reuse/refusal checks. Scientific settings and workflow algorithms are unchanged.

## 0.1.0a18 — one-command launch with browser opening

- The installed `bactologix` command now opens the browser after its own Streamlit server binds and passes a local health check. Keep the existing `python -m bactologix.cli` entry point.
- Detect the current WSL private address, prefer the default network interface, and open the Windows default browser through WSL interoperability. Mirrored networking and ordinary desktops use loopback. Browser failure leaves the server running with a printable URL.
- Choose an available default port from 8501–8520; preserve explicit host/port overrides and add `--no-browser`. Keep source watching disabled, use the activated environment’s Python, and preserve foreground logs.
- Forward shutdown to the web server without cancelling detached workflow workers. Add real console-command startup, readiness/browser-handoff, proxy bypass, opt-out and Ctrl+C/termination tests, alongside WSL routing and browser-command regression tests.

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


