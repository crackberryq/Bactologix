# Validation update: 0.1.0a39 (2026-10-28)

- All 15 installed-wheel browser tests passed. Coverage includes clicking outside workspace tools and cycling focus past controls inside hidden containers. Package builds and JavaScript syntax passed.
- Full Python, browser and environment CI remain release gates. This change affects interface interactions only.

# Validation update: 0.1.0a38 (2026-10-28)

- All 15 browser checks passed with the dialog focus restoration. The final packaged fallback refinement passed the focused mobile navigation/workspace-tools regression. JavaScript syntax and package builds passed.
- Full Python, browser and environment CI remain release gates. This change affects keyboard focus only.

# Validation update: 0.1.0a37 (2026-10-28)

- The final installed-wheel Python 3.12 suite passed **543 tests**, with 41 separately gated checks skipped. All **15 browser tests passed** in the final complete run.
- Audited 31 main views, configuration/saved-run tabs and common dialogs at 1440, 1024 and 390 pixels (93 captures). The initial audit found phone-width overflow in all five Install & update views; the final audit reported none and no JavaScript errors. Additional browser acceptance covers the wizard, folder picker, reports, resource-use dialogs, import review and maintenance controls.
- New browser coverage exercises responsive installation tabs, mobile drawer visibility, keyboard focus cycling and Escape, navigation dismissal, workspace tools, app-window help and the skip link. Existing saved-run, maintenance and support flows use their new control locations. Early browser failures exposed a decorative arrow in the support button's accessible name and incomplete request handling in the new test fixture; both were corrected before the final green run.
- App-window regressions cover validated URLs, Windows/WSL PowerShell arguments and timeouts, executable paths with spaces, detached native windows, failed-browser fallback, headless hosts and mutually exclusive CLI flags. The existing real-server readiness and restart checks still pass. Actual Windows/WSL desktop window appearance is not physically verified; browser policy and platform support may affect app mode.
- Wheel/source builds, all **59 runtime package files**, JavaScript syntax and dependency checks passed. Python 3.11/3.12 pinned-adapter, Chromium and real sample-free Conda suites remain mandatory release gates. This update changes presentation and optional browser launch, not scientific execution or saved settings.

# Validation update: 0.1.0a36 (2026-10-28)

- The installed-wheel Python 3.12 suite passed **537 tests**, with 40 separately gated checks skipped. Import coverage includes BOM/CRLF, quoted comma-containing folders, TSV, blank/default modes, duplicate and existing IDs, wrong field counts, malformed CSV, duplicate/unknown headers, invalid modes/folders and size/row limits.
- Review is read-only. Tests reject missing/stale UI review tokens, changed text/registrations, removed folders and retargeted symlinks. A failing SQLite trigger verifies whole-batch rollback. Concurrent bulk and single-registration writers cannot create case-only duplicate IDs; original files and existing records remain intact.
- All **14 browser tests passed** locally. The import flow covers invalid rows, escaped text, stale-review hiding after typing/upload, 25-row pages, problems-only filtering, a conflicting registration after preview, mandatory re-preview, full successful import and fresh review after reopening. Desktop/mobile layouts and review-table scrolling were inspected. Successful re-preview clears obsolete error toasts.
- Wheel/source builds, all **59 runtime package files**, JavaScript syntax and dependency checks passed. Python 3.11/3.12 pinned-adapter, Chromium and real sample-free Conda suites remain mandatory release gates.
- Registration validates table fields and directory existence only; it does not scan/validate input contents, certify readable sequence files, create runs or start workflows. Filesystem contents can change after directory checks. Physical-workstation/WSL/GPU and full biological-analysis acceptance remain outside these software checks.

# Validation update: 0.1.0a35 (2026-10-28)

- The installed-wheel Python 3.12 suite passed **521 tests**, with 39 separately gated checks skipped. New comparison tests cover missing/null/empty values, boolean/integer/float identity, list order, mapping-order independence, literal dotted paths, non-string nested keys, missing subtrees, scalar replacements, archived records and reordered references across four runs.
- Invalid selections, missing/invalid saved configurations, recursive aliases, unsupported YAML scalars and size/depth limits fail explicitly. Tests verify exact YAML versus typed-value hashes, unchanged stored runs, no subprocess/submission/mutation and identical refreshed UI/export documents during read-only update access.
- All **13 browser tests passed** locally. New Chromium coverage includes two/three-run selections, reference changes, stale-result hiding, typed cells, search/section/difference filters, 50-row paging, missing values, explicit long-value shortening and full native JSON downloads containing filtered/unchanged rows. Desktop/mobile views were inspected; mobile download-button wrapping was corrected.
- Wheel/source builds, all **58 runtime package files**, JavaScript syntax and dependency checks passed. Python 3.11/3.12 pinned-adapter, Chromium and real sample-free Conda suites remain mandatory release gates.
- Comparison reads stored records only; it does not validate on-disk YAML, inputs, environments, source integrity or results. Defaults are not inferred and similarly named keys across pipelines need not be equivalent. Physical-workstation/WSL/GPU and full biological-analysis acceptance remain outside these software checks.

# Validation update: 0.1.0a34 (2026-10-28)

- The installed-wheel Python 3.12 suite passed **502 tests**, with 38 separately gated checks skipped. This release changes browser presentation only; backend job records, queue transactions and scientific execution are unchanged.
- All **12 browser tests passed** locally across the suite and a focused recheck. The first full run passed 11 tests but timed out in an existing configuration-editor transition; that test passed unchanged on an isolated rerun. Clean full browser CI remains a release gate.
- New Chromium acceptance exercises 31 historical attempts plus active work, 25-item pagination, combined filters, ordering, archived and missing-run records, installation-derived pipeline attribution, all-page JSON download, omitted payload data and missing timestamps. A refreshed snapshot moves completed work into history without resetting filters; saved-run navigation and read-only record assertions pass.
- Desktop/mobile views were inspected, including wrapping and horizontal overflow. Wheel/source builds, all **57 runtime package files**, JavaScript syntax and dependency checks passed. Python 3.11/3.12 pinned-adapter, Chromium and real sample-free Conda suites remain mandatory release gates.
- History export reflects the displayed workspace snapshot and is not a live check or backup. Pagination bounds rendered history cards, not the backend snapshot size. Physical-workstation/WSL/GPU and complete-analysis acceptance remain outside these software checks.

# Validation update: 0.1.0a33 (2026-10-28)

- The installed-wheel Python 3.12 suite passed **502 tests**, with 37 separately gated checks skipped. Compatibility regressions cover exact current/earlier/unreviewed commits, moving branch labels, dated catalogue origin, missing active registrations, missing/mismatched installation bindings, replaced and revalidated runner paths, legacy validation records and archived runs.
- Tests distinguish recorded requirements/check dates, latest explicit check status and failed execution outcomes. Missing/invalid application package requirements remain unknown, and a running/installed version mismatch requires restart. Read-only assertions prohibit external subprocess/network/enqueue operations and verify preserved installation/run records. JSON export is identical to the freshly returned overview.
- All **11 browser tests passed** locally. Real Chromium acceptance covers the Compatibility route and reload, filters, archived visibility, native JSON download, refreshed source mismatch, escaped labels, saved-run navigation and mobile overflow. Desktop/mobile views were inspected. Local browser uses Chromium 153; CI retains its pinned Playwright image.
- Wheel/source builds, all **57 runtime package files**, JavaScript syntax and dependency checks passed. Full Python 3.11/3.12 pinned-adapter, Chromium and real sample-free Conda suites remain release gates.
- This is a local-record overview, not a fresh runner/package probe or launch-readiness check. It does not verify source/configuration integrity, input files, databases, tool environments or biological results; it does not fetch live releases or migrate saved runs. Physical-workstation/WSL/GPU and complete-analysis acceptance remain outside these software checks.

# Validation update: 0.1.0a32 (2026-10-28)

- The installed-wheel Python 3.12 suite passed **489 tests**, with 36 separately gated checks skipped. Storage regressions cover explicit traversal, metadata-only reads, unchanged files/settings/jobs, persistence, exact JSON exports, nested/shared references, archived runs, hard-link deduplication, sparse-file blocks and filesystem-capacity deduplication.
- Tests enforce external-location opt-in, linked root/parent refusal, symbolic-link/FIFO skipping, missing/inaccessible paths, stale registration rejection and entry/time/depth/location limits. A simulated cross-device entry verifies skip accounting; a discovered backup at the location limit produces a partial measurement instead of incorrect category attribution. Tests prohibit unlink, rename and directory removal during scans.
- All **10 browser tests passed** locally. The new real Chromium/Streamlit flow verifies no scan on opening Storage, explicit workspace/external measurements, retained repair accounting, native JSON download, category filtering, saved measurement after refresh, stale-scope warning, review navigation and desktop/mobile overflow. Mobile download-header wrapping was inspected and corrected. Local browser uses Chromium 153; CI retains its pinned Playwright image.
- Wheel/source builds, all **56 runtime package files**, JavaScript syntax and dependency checks passed. Full Python 3.11/3.12 pinned-adapter, Chromium and real sample-free Conda checks remain mandatory release gates.
- Measurements are live metadata observations, not atomic snapshots, ownership proofs or reclaimable-space estimates. Directory/filesystem overhead and shared physical extents from reflinks, compression or snapshots are not measured. Same-device bind mounts are not distinguished. Slow filesystem calls can exceed the cooperative traversal time budget. Real mounted-disk/WSL filesystem performance and physical-workstation acceptance remain unverified by these software fixtures. No biological analysis or cleanup is performed.

# Validation update: 0.1.0a31 (2026-10-28)

- The installed-wheel Python 3.12 suite passed **473 tests**, with 35 separately gated checks skipped. Result regressions cover exact frozen YAML, legacy/changed reports, separate execution and validation status, repeated attempts, failed/cancelled/interrupted observations, missing-before and missing-after records, unavailable logs/metrics and shared follow-up ownership.
- Real sample-free subprocesses exercise report observations around the workflow command path on both success and nonzero exit. Observation and diagnostic-write failures cannot mask workflow results. No native biological analysis is used by these tests.
- All **9 browser tests passed** locally, including search, archived-run visibility, per-run navigation, actual YAML/HTML/log/manifest downloads, report iframe, Methods/resource links, refresh persistence, changed external reports and mobile overflow. The installed-wheel results flow was rechecked after final observation edge-case changes; desktop/mobile views were inspected. Local browser is Chromium 153; CI retains its pinned Playwright image.
- Downloads recheck regular-file identity and size through no-follow directory/file descriptors. Tests reject symbolic links, linked parents, FIFO/directory paths, missing/empty/oversized reports, changed-during-read files and foreign report IDs. Per-run inspection only examines known report paths and selected-run log metadata, without recursive output scanning or implicit workflow execution.
- Wheel/source builds, all **55 runtime package files**, JavaScript syntax and dependency checks passed. Python 3.11/3.12 pinned-adapter checks, the full browser suite and real sample-free Conda deployment remain mandatory release gates.
- Report observations are metadata associations, not content hashes, proof of a producing process or completion of all requested scope. Historical HTML copies are not retained. Physical workstation/WSL reboot, GPU computation and complete analysis acceptance remain outside these software checks.

# Validation update: 0.1.0a30 (2026-10-28)

- Queue regressions cover legacy SQLite/FIFO compatibility, saved payload preservation, persistent pause/holds/order, new-job append order, stale and replayed browser requests, transactional claim/control races, terminal-job rejection, held-job protections and application-update interlocks.
- Real sample-free subprocess workers exercise stop-after-current behavior, skipped held jobs and interrupted-record recovery. Failed/cancelled current attempts also leave the queue paused. Recovery is lock-protected and pauses for review; it does not automatically retry interrupted attempts.
- Chromium acceptance covers hold/release, reordering, refresh persistence, the global pause banner, interrupted-worker recovery, explicit resume, actual worker completion, retained prior attempts and desktop/mobile layout. Local verification uses Chromium 153 because this host could not download Playwright's pinned browser; CI retains its pinned browser image.
- The installed-wheel Python 3.12 suite passed **451 tests**, with 34 separately gated checks skipped. Wheel/source builds, JavaScript syntax, all 54 runtime package files and dependency checks passed locally. Python 3.11/3.12 application/pinned-adapter checks, the complete browser suite and sample-free real-Conda deployment remain release gates.
- These checks do not establish physical-workstation reboot recovery, Windows browser launching, GPU computation or complete analysis acceptance. No scientific algorithms, frozen run settings, input data or output files are changed by queue controls.

# Validation update: 0.1.0a29 (2026-10-28)

- The local Python 3.12 application suite passed **428 tests**, with 33 separately gated checks skipped. Added regressions cover permission/disk failures, bounded log evidence and escaped rendering, unknown causes, refused log symlinks, interrupted workers, duplicate checks, frozen runner selection, user cancellation, probe timeouts and retained per-attempt history.
- Real sample-free subprocess checks exercised child output, SIGINT cancellation and timeout cleanup. A detached worker recovered an interrupted record and completed a new check. A tiny native Snakemake job ran through an existing test Python environment using a Conda-command wrapper fixture; this is not a fresh real-Conda deployment claim.
- All **7 browser tests passed** locally. Real Chromium-to-Streamlit acceptance covers the explicit workstation action, actual queued worker completion, report iframe, native JSON download, user-reported confirmations and clearing, refresh persistence and mobile overflow. Local browser verification uses Chromium 153 because this host could not download Playwright's pinned browser; release CI retains its pinned Chromium image. Existing browser acceptance and release gates remain required.
- Pinned adapter checks passed 26 tests locally; the existing empty-follow-up execution fixture failed in this host with Snakemake/psutil `NoSuchProcess`. The same fixture fails on unchanged a28 source in this host. No workflow or scientific settings were changed to bypass it; clean CI adapter checks remain required before release.
- Wheel/source builds, JavaScript syntax and dependency checks passed locally. Physical WSL/Windows browser launch, real GPU computation, all external output destinations, real machine reboot recovery and complete analyses remain outside these checks. Browser/report/download confirmations describe user observations, not automatic verification.

# Validation update: 0.1.0a28 (2026-10-27)

- Support-export tests cover opt-in selected-job logs, older-run versus active-source provenance, omitted sample/configuration/payload fields, cached capacity allowlists, credential/path/identifier patterns, unknown evidence, bounded excerpts, and refused symlink/FIFO reads.
- Exact text/ZIP assertions verify that manual edits survive, checksums match and subsequent job/log changes cannot append hidden diagnostics. Invalid review flags, selections, formats, Unicode byte limits and empty/null-containing text are rejected. Preview generation does not launch health probes or persist an export.
- Chromium acceptance covers preview generation, opt-in log inclusion, review invalidation after edits/selection changes, escaped editable content, actual text/ZIP downloads and mobile overflow. Existing Python matrices, native adapter fixtures, browser flows and sample-free Conda checks remain release gates.
- Redaction is deliberately best-effort and can over-redact diagnostic detail or miss private prose. A reviewed snapshot is not anonymous by guarantee, not a fresh environment health check, and not a complete log. No support-service connection or automatic sharing is implemented.

# Validation update: 0.1.0a27 (2026-10-27)

- Software tests cover changing checkpoint totals, parallel steps, clipped/truncated logs, process identity reuse, child-counter double counting, unavailable readings, bounded history compaction, per-attempt provenance, stale/interrupted records and validated JSON/CSV/ZIP exports.
- Real software subprocess tests exercise stdout redirection, nonzero exit codes, cancellation and observer failures. Worker tests verify terminal statuses and recovery. A sample-free Snakemake checkpoint fixture checks actual progress messages; no biological analysis is used to test observation.
- Chromium acceptance covers task counts, live/paused/partial states, checkpoint replanning, chart rendering, real ZIP/CSV downloads and narrow layouts. The full existing Python 3.11/3.12, pinned-adapter, browser and sample-free Conda suites remain publication gates.
- Process sampling is best-effort, not exact scheduler accounting or unique physical RAM. Target-workstation WSL/GPU and complete analysis acceptance remain outside these software checks; the release remains alpha.

# Validation update: 0.1.0a26 (2026-10-27)

- Host-check regressions cover nonexistent output parents, broken links, file/directory conflicts, denied permissions, read-only filesystems, exhausted/low disk space, resource oversubscription, disabled tool limits, additional native output destinations and read-only environment-cache reuse. Assertions verify that checks preserve frozen configuration and do not create probe directories.
- Chromium acceptance covers the setup guide for both pipelines, full-page draft/revision editing, per-pipeline settings, raw invalid-YAML recovery after a real browser refresh, preserved CPU budgets, navigation away and resume, explicit discard confirmation, exact configuration creation and mobile overflow checks. Existing saved-run, Methods, environment, report and automatic restart tests remain required.
- Release publication requires Python 3.11/3.12 application and pinned-adapter tests, real Chromium/Streamlit interaction and sample-free Conda deployment. These are software checks, not full analysis validation or tests on the user's physical Ubuntu/WSL workstation. The package remains alpha pending target-machine/lab beta acceptance.

# Validation update: 0.1.0a25 (2026-10-27)

- Methods tests cover frozen configuration isolation, read-mode/polishing routes, disabled steps, per-sample BaSIS exclusions, partial scope, version provenance, source/configuration drift, linked/oversized metadata, exact-YAML ZIP downloads and HTML escaping.
- Pinned-source checks cover both current pipeline versions. Browser acceptance checks the Methods preview, actual ZIP contents and mobile overflow. Existing application, native-adapter, browser and real Conda checks remain release gates.
- Completion metadata is captured separately from immutable run settings; older runs are explicitly identified as legacy metadata read at export. Unknown source templates use configuration-only output. Exports are writing aids requiring researcher review and complete tool/database citations; no biological analyses are executed by this feature or its software tests.

# Validation update: 0.1.0a24 (2026-10-27)

- Added software tests for CPU affinity, cgroup v1/v2 ancestor quotas and memory usage, WSL capacity, NVIDIA driver/toolkit distinctions, unknown capacity, preset rounding and system reserves, atomic defaults with stale-write rejection, and new-draft budget propagation. Existing runs and non-resource fields are preserved.
- Added repair regressions for read-only rendering checks, the recognized QC dependency conflict with package receipts, healthy reuse, unknown failures, symlink rejection, and preservation/restoration of incomplete environments on failed or cancelled deployment.
- The actual NanoPlot 1.46.2 plotting module generated valid scatter/density HTML and PNG files locally using Plotly 6.1.2 and Kaleido 0.2.1, with synthetic numeric arrays only. This is a runtime workaround, not full resolution of upstream declared dependencies.
- Chromium acceptance covers the PC capacity tab, explicit re-detection, saving a preset for one pipeline, CPU/RAM propagation into a new draft, preserving budget edits across pipeline switches, narrow layouts and the scoped Repair dialog. The real Conda fixture now checks healthy repair reuse and recreation of an incomplete environment with a retained backup.
- Publication remains gated on both Python matrices, Chromium and real sample-free Conda deployment. Target-workstation WSL/GPU behavior and full analyses are outside these software checks.

# Validation update: 0.1.0a22 (2026-10-27)

- Setup cards expose separate installation and read-only checking actions. Chromium covers each confirmation from a workspace with no samples or runs, selection clearing, payload scope, collapsed storage controls, and blocking deployment until folder edits are saved.
- Server tests verify that setup requests queue only selected software environments and do not create samples, run drafts or analysis jobs. Existing source/configuration, database, launcher and immutable-run checks remain required.
- The real sample-free Conda fixture now inherits the old active version's cache using the same function as pipeline updates, without a manual cache-selection action, and checks that the existing prefix and Conda history remain unchanged after installation is requested for the new source version.
- Publication remains gated by both Python matrices, real Chromium and real sample-free Conda deployment. Full biological analyses and target-workstation WSL behavior are outside these software checks.

# Validation update: 0.1.0a21 (2026-10-27)

- Local source and separately installed wheel suites each passed **298 tests**, with native/browser/deployment checks enabled separately. All 44 runtime package files match source; license and installed dependency checks pass.
- Software regressions verify distinct saved Kraken modes in list data without rewriting run records. Existing run/configuration/database/revision, environment reuse and launcher checks remain required.
- Chromium acceptance covers separate Samples/Runs, removal and archive/restore, run search and pipeline/status filters, explicit hidden selections, the unified comparison view, parent selection, dedicated run pages, browser Back/Forward/reload, native report overlays and unknown-run recovery. Existing guided configuration and revised-draft preservation checks remain in the same end-to-end flow.
- A controlled Chromium component test pauses a real background-poll response, submits a foreground sample-registration action, and verifies that the dialog and inputs survive, the action is queued exactly once, and no native backend mutation occurs in the fixture.
- Run Activity tests use injected job evidence and an actual request through an intentionally failing software-only Conda stub. They do not run biological analyses. The real supervisor restart fixture checks that the current workspace route survives a browser reload.
- Publication requires both Python matrices with pinned native adapters, Chromium and real sample-free Conda deployment. Target-workstation WSL interaction and full biological analyses are outside these checks.

# Validation update: 0.1.0a20 (2026-10-27)

- Local application and separately installed wheel suites each passed **293 tests**, with 25 native/browser/deployment checks enabled separately. All 44 packaged runtime files match source, the license is included and installed dependency checks pass.
- New software regressions cover scoped run checks, changed inputs/configuration/source/runner, current native environment path parsing, missing/incomplete completion markers, stale receipts, failed checks, immutable saved runs/defaults and bounded failure-log guidance. Native inventory parsing fails closed on unexpected paths, containers or malformed rows.
- Chromium acceptance adds unsaved-draft labels, mobile navigation names, five run checks, missing/available environment states, database preparation disclosure, readable installation status, polling outside Jobs, preservation of focused edits and actionable failure guidance. The existing real launcher restart/browser refresh flow remains required. Environment and job state fixtures exercise the interface without running sample analyses.
- Screenshot review moved execution controls above check details on mobile, restored the narrow-layout draft save label and corrected toast width/expiry across component refreshes.
- The real sample-free Conda acceptance job adds native `--list-conda-envs` checks under Snakemake 9.6.2 and 9.27.1. It verifies that listing missing or incomplete environments does not create/delete/repair those environments or execute a rule; existing reuse and execution-guard checks remain required.
- Publication is gated on Python 3.11/3.12 application and pinned workflow adapter tests, Chromium and real Conda deployment. Inventory availability means completed folders, not executable verification or complete prediction of checkpoint-dependent environments. Database-path display does not claim full database compatibility. No biological analyses or target-workstation WSL browser interaction are performed.

# Validation update: 0.1.0a19 (2026-10-27)

- Local application suite: **275 passed**, with 25 native/browser/deployment checks enabled separately. Software regressions cover completed versus failed/in-progress/unrelated update handoffs, fresh interpreter verification, same-address restart, preserved workspace state and rejection of missing or incomplete environments without deleting them. The launcher test starts and replaces a real Streamlit process using a simulated verified updater result.
- Chromium acceptance arms the open tab during an update, waits for real launcher replacement, verifies an actual browser reload and a changed server identity on the same URL, and checks that samples, runs, defaults and installations survive. The fixture does not perform a pip self-update.
- The sample-free Conda job checks the guard against Snakemake 9.6.2 and 9.27.1, runs a tiny checkpoint and Python-version rule in an existing environment, verifies unchanged Conda history and rejects missing/incomplete environments. It explicitly checks that the guard reaches the checkpoint subprocess.
- Publication requires both Python matrices, Chromium and real Conda deployment. No biological analyses or target-workstation WSL browser interaction are performed by these checks. Installing this release from an older running launcher needs one manual restart before automatic updates are available.

# Validation update: 0.1.0a18 (2026-10-27)

- Local application suite: **254 passed**, with 25 native/browser/deployment checks enabled separately. Launcher coverage includes WSL NAT interface selection, mirrored loopback, missing network tools, explicit and automatic ports, IPv6/wildcard URLs, invalid hosts, Windows browser commands, headless operation and startup failure.
- Real installed `bactologix` console-command checks start Streamlit, wait for its listening message and health endpoint, invoke a controlled browser helper only after readiness, and verify that Ctrl+C/SIGTERM stop the web server. The ordinary command is exercised with no flags; the explicit host/port and `--no-browser` path is also checked. Deliberately invalid proxy settings confirm that local health checks bypass proxies.
- Chromium acceptance launches the installed console command and exercises the existing application flow. Windows browser invocation and WSL network detection use software fixtures; opening the Windows browser on a physical WSL workstation still requires user confirmation. No browser is installed and no Windows network settings are changed.
- Publication requires both Python matrices, Chromium and sample-free environment deployment. This update changes the application launcher only; pipeline execution, databases and scientific settings are unchanged.

# Validation update: 0.1.0a17 (2026-10-27)

- Runner requirements now come from each reviewed source's pinned environment definition. Unit/integration fixtures cover distinct release versions, mismatches, malformed pins, automatic local Conda bootstrap, compatible reuse, failed activation and cancellation.
- Local software suite: 229 passed, 25 skipped (native upstream, browser and Conda acceptance gates). The new native runner-deployment acceptance test is gated by `BACTOLOGIX_ENVIRONMENT_TESTS=1` and installs/verifies Snakemake 9.27.1 separately from the application's test environment.
- GitHub CI must pass its Python matrices, Chromium and real Conda deployment jobs before publication. Local mocked provisioning does not prove solver/network availability on a workstation.
- The supported pipeline commit catalogue is unchanged. This app update does not enable the unfinished HybAs v10 execution adapters.

# Validation update: 0.1.0a16 (2026-10-26)

- Local application suite: **201 passed**, with 24 native/browser/deployment checks enabled separately. New regression cases verify previous-cache inheritance, retry preservation, cache discovery, removal of unused snapshots, preservation of shared/original content, active and archived-run dependency blocks, modified/unowned sources, stale reviews, transaction rollback, incomplete cleanup reporting and maintenance/cache/draft registration races.
- Chromium acceptance selects a previous version’s cache through the new visible control, checks persistence and unchanged frozen runs, refuses active-version removal, reviews and removes an unused managed copy, and verifies that the selected cache and original source remain. The `ui-previews` artifact retains screenshots of the chooser, uninstall review and installation controls.
- The real sample-free Conda job installs a small Python environment, checks it, creates a later source commit with unchanged recipes, selects the previous cache and verifies the same environment path and unchanged Conda history after preparation. Existing failed-tool checks remain covered.
- Publication requires the Python 3.11/3.12, Chromium and real environment jobs to pass. This change affects application lifecycle and UI only; native algorithms and presets are unchanged. No biological analyses are executed by these checks.

# Validation update: 0.1.0a15 (2026-10-26)

- Local application suite: **179 passed**, with 24 native/browser/deployment checks enabled separately. New regression cases cover read/check/export without saving, future-run inheritance, unchanged frozen history, nested/custom values, invalid edits and stale saves including a competing database update during compilation.
- Configuration shares the guided controls and Advanced renderer with the draft/revision editors, while maintaining independent pipeline edits, category selection and YAML state. The existing draft/revision acceptance flow is retained.
- Chromium acceptance adds configuration category switching, Advanced search, narrow layout, YAML parse-error recovery, file import/export, save/reload, unchanged existing runs, refresh after database selection and refusal to overwrite a newer saved configuration. Screenshots are retained in the `ui-previews` Actions artifact. The release workflow requires the Python 3.11/3.12, browser and sample-free environment jobs to pass before publication.
- Only UI and workspace configuration persistence are changed; native algorithms and presets are unchanged. These checks do not execute biological analyses.

# Validation update: 0.1.0a14 (2026-10-26)

- Local application suite and separately installed wheel: **169 passed**, with 24 native/browser/deployment checks enabled separately. All 36 packaged runtime files and the license match source; dependency checks and JavaScript syntax checks pass.
- Saved-run inspection exposes the actual frozen settings through an Overview and pipeline category tabs. The command, YAML and complete field catalogue are collapsed under Advanced. Guided revisions start from the selected run's resolved configuration rather than today's defaults.
- Regression checks cover HybAs and BaSIS revisions, nested/custom values, resolved database paths, CPU/memory edits, original input identities, installation/cache inheritance, new output folders, fresh dry-run requirements, unregistered samples and unchanged parent files/defaults. Changed inputs/source, stale editor signatures, invalid configuration, active jobs and archived runs are rejected before creating a revision.
- Chromium acceptance covers saved-run tabs, collapsed YAML, cancelling edits, editing saved settings, YAML round trips, budget changes and opening the new revision. The `ui-previews` artifact contains the saved-run overview and revision editor for visual inspection. Exact-commit CI runs this flow alongside the existing Python 3.11/3.12 adapter checks and sample-free environment deployment; publication requires every job to pass.
- This update changes presentation and draft orchestration. Native algorithms and pipeline presets are unchanged; no biological analysis is run by these checks.

# Validation update: 0.1.0a13 (2026-10-26)

- Publication checks add five regression cases for draft-to-public asset verification, checksum rejection, wrong tag/commit rejection, wrong wheel identity and immutable published releases. The publisher uses the actual app updater acceptance rules; publication is gated behind all software, browser and environment checks on `main`.
- Local application suite and separately installed wheel: **151 passed**, with 24 native/browser/deployment checks enabled separately. All 35 packaged runtime files match source; dependency checks pass.
- Archive regression checks cover unregistering a sample while retaining active jobs, matching the original input folder, preserving frozen files, restoring runs, and refusing stale launch requests at validation and queue insertion. Batch archive rejects an active or missing run without partially hiding other runs.
- Chromium acceptance now exercises both run lists, restoration and individual removal, guided Kraken/polishing cards, quality switches, BaSIS category tabs, pipeline switching, narrow layout, Advanced search, YAML recovery and the Review step. Screenshots are retained in the `ui-previews` Actions artifact for visual inspection.
- The first browser pass identified the decorative step numbers being included in navigation button names. The numbers are now hidden from assistive labels. Screenshot review also tightened the module switches and mobile budget fields.
- Python 3.11/3.12 pinned adapter checks and real sample-free Conda deployment passed on the initial implementation. The final candidate's exact-commit CI reruns those checks and the full browser flow; see the PR checks for the result. This update does not change native algorithms or validate biological outputs.

# Validation update: 0.1.0a12 candidate (2026-10-26)

- Local application suite: **146 passed**, with 24 native/browser/deployment tests enabled separately. New checks cover typed and nested edits, custom keys (including dotted mapping keys), optional resource overrides, managed fields, invalid values, pipeline mismatch, batch drafts, frozen dialog defaults and unchanged saved configurations/history.
- New native integration checks pass for both exact pinned sources: create a draft through the same run-configuration endpoint as the UI, then pass a real Snakemake dry-run with the selected options in its frozen command configuration. No biological tools execute.
- Chromium acceptance covers Kraken on/off and decision-mode synchronization, polishing and BUSCO controls, searching settings, independent pipeline edits, YAML round trips, malformed-YAML recovery and creation of a run with the chosen values while preserving saved defaults.
- The menu exposes the bundled configuration catalogue, recognized resource companion fields and keys present in the supplied YAML. Omitted values remain omitted until edited. Generated input/output locations and top-level CPU/memory assignments retain the existing managed-run contract. No native decision algorithms or scientific presets were changed.
- Exact-commit CI runs Python 3.11/3.12, real Chromium, package builds and sample-free Conda deployment. Native dry-runs remain necessary for source-specific settings, resource availability and selected sample compatibility.

# Validation update: 0.1.0a11 candidate (2026-10-26)

- Local application checks: **130 passed**, with 22 native/browser/deployment checks enabled separately. Sample-removal regression verifies that registration disappears while input files, staged inputs, frozen runs and queued jobs remain intact; the existing run still generates its command. Removed samples cannot be selected by stale draft requests, and the original folder can be registered again.
- Stale removal requests cannot remove a replacement registration with different inputs. Removal operates only on the sample registry and never deletes filesystem content.
- The first browser pass caught an old checkbox value being captured again during rendering after removal. The response now clears the outgoing checkbox as well as saved form state; the re-registration assertion remains in the acceptance test.
- Real Chromium acceptance now exercises removal from both Registered samples and the run-draft picker, keeps other draft-form values, handles removal of the last sample and verifies that re-registration clears the earlier checkbox selection. Exact-commit CI also runs the existing Python 3.11/3.12 native adapters and environment deployment checks.

# Validation update: 0.1.0a10 candidate (2026-10-26)

- Local application regression suite: **128 passed**, with 22 native/browser/deployment checks enabled separately. New cases cover HybAs/BaSIS path propagation, per-resource and batch selection, preservation of the parent and unrelated options, fresh dry-run requirements, missing databases, stale previews, input drift and queued work.
- The new pinned HybAs integration test reproduces the reported missing `bacteria_odb10` failure, saves a structurally complete software fixture under a path containing spaces, applies it to a revised draft, and passes the real Snakemake dry-run using the revised command configuration. No BUSCO analysis or biological database download is performed.
- Real-browser acceptance now covers saving BUSCO, selecting an existing run, reviewing current/saved locations, applying them, opening the new draft and refusing unchanged selections. Python 3.11/3.12, browser, package-build and real Conda deployment checks run in GitHub Actions for this candidate; consult the exact commit's checks for their result.
- This update changes application resource routing and draft creation. It does not validate biological outputs or database contents beyond existing structural checks. Existing runs and their results remain unchanged.

# Validation update: 0.1.0a9 candidate (2026-10-26)

- Local software checks pass: 118 application tests; 21 opt-in native/browser/deployment tests are separate.
- All 14 sample-free database preparation plans pass against the exact pinned HybAs and BaSIS sources, including paths containing spaces. Both generated environment administration DAGs parse all native recipes.
- Regression fixtures cover discovery inside hidden caches and linked directories, incomplete database matches, environment missing/incomplete/verified/failed states, source drift, separate download destinations, failed-download resume, and revalidation before selecting downloaded resources.
- The first a9 CI run passed both Python matrices. Browser persistence worked, but its assertion compared raw YAML wrapping; it now compares the parsed value. Real deployment exposed Snakemake 9.6.2's unquoted post-deploy cache path. A cache-folder picker and preflight now reject unsupported cache paths before deployment; the real installation test uses an explicitly selected cache without spaces.
- Browser acceptance now includes the download destination picker, selecting and saving a prepared database, and environment controls. A separate real Conda acceptance job checks a small software-only environment through missing, install, verified and deliberately broken-tool states. Results for this candidate are recorded in its GitHub Actions run.
- Large reference databases and the full set of biological tool environments are not downloaded in this software test suite. Native providers retain responsibility for download checksums, indexing and preparation receipts. No biological analyses or clinical safety claims are tested here.
- The WSL localhost forwarding issue was isolated on the user's machine: the health endpoint replied in WSL, Windows localhost reset the connection, and binding to the WSL private IP restored access. The launcher now accepts that explicit address with `--host`.

# Validation update: 0.1.0a8 candidate (2026-10-25)

- The application suite passed **90 tests** locally. Four opt-in integration/browser tests are separate. Regression cases reject sample-sheet edits, retargeted/replaced/missing/added staged inputs, and comparison input drift; they cover older drafts, controller repair, failed-run status, report ownership, and reviewed follow-up requests.
- Real **Snakemake 9.6.2 dry-runs passed** for the pinned HybAs `780ee7086c15c49f2825800ec463327f009f14a6` and BaSIS AWARE `1d2d96b266e98d956254d1a518ff5b437ab7a10a` sources, using Bactologix-generated drafts with paths containing spaces. These checks run Snakemake directly in Python 3.12 and do not deploy Conda tool environments.
- The saved-screening follow-up dry-run passed. An attempted empty native follow-up execution encountered this container's process-monitoring limitation (`psutil.NoSuchProcess` in Snakemake benchmarking); it is not counted as a local execution pass. The unchanged workflow is included in the standard Linux CI suite, with zero findings and no analysis tool commands.
- GitHub Actions now defines Python 3.11/3.12 application/adapter tests, source and wheel builds, installed-package checks, and a separate real Chromium-to-Streamlit acceptance job. **[CI run 40238113167](https://github.com/crackberryq/Bactologix/actions/runs/40238113167) passed:** 93 tests on Python 3.11 and 93 on Python 3.12 (one browser-only test skipped in each matrix job), plus the separate real Chromium acceptance test. The unchanged native empty follow-up completed, produced its separate HTML report with zero analysis commands, resumed without work, and preserved screening files. The browser test covers the actual folder picker, sample registration, configuration persistence and sandboxed report viewer; it caught an initial folder-load/edit race, which was fixed and rechecked.
- The final wheel installed in a separate test environment and passed all 90 application tests (the four opt-in tests run separately). `pip check` found no broken requirements; the CLI reported 0.1.0a8. Every packaged Python/UI/data file matched source, and the MIT license was included. The source and wheel builds passed locally and in CI.
- Production source, screening defaults, follow-up entry point and configuration fields were reviewed against the exact upstream commits. Older supported source commits and existing saved configurations remain available. New defaults do not rewrite existing preferences.
- Full biological analyses, large database/model execution, real Conda deployment on a clean workstation, and WSL browser acceptance remain outside these software checks. The HybAs compatibility refresh does not fix previously identified upstream algorithm/output-reuse issues.

---

# Validation update: 0.1.0a6 (2026-10-24)

- **43 automated tests passed** against the final source and installed wheel. New tests cover supported current/previous snapshots, downloaded-source setup, environment reuse, release asset validation, server-resolved release selection, update/queue/cancellation locks, package-replacement UI caching, checksum/Python rejection, stale release rejection, pip failures and restart handling. Real Streamlit component-trigger tests exercise refresh and update selection with duplicate-event suppression.
- All **217 files** of BaSIS tree `88fcd3b9d0b9ca2dc2f175da5dcb1b1aaeb7f032` were verified against GitHub blob hashes for published commit `f66dfedd46da05ae79bf769e7e6fada91270d4d1`. The runner environment definition is unchanged from the prior supported commit. Review focused on the input/configuration, entry point, environment and output contracts used by Bactologix.
- A real **Snakemake 9.6.2 dry-run** passed on that verified tree using an isolated Bactologix-generated run with software-only annotation files, a sample sheet, frozen configuration and paths containing spaces. The initial DAG had **83 jobs**, with dynamic checkpoints. The workflow source fingerprint and original inputs remained unchanged. A local fixture commit with the exact published tree was used; no published source was edited. This parse/DAG check used the available Python 3.12 test environment without creating Conda tool environments; the unchanged production runner still requires Python 3.11.
- A real app installation ran in a disposable environment: the production a6 updater/worker selected and installed the exact published a5 wheel, verified the fresh-process version and recorded the restart requirement. Only network transport was replaced with the saved GitHub release JSON and wheel; checksum checking, locks, queue state, pip and version verification ran normally. Configuration and sentinel files in runners, tool caches, resources and runs survived. This tests the selected-version installer, including downgrade, rather than claiming a live GitHub HTTP download in the test host.
- Browser checks used the production HTML/CSS/JS component with an isolated static fixture. Finish setup, selected-version payloads, the app release picker, compatible snapshots and the setup layout were checked. Python tests separately exercised real Streamlit callbacks; the static fixture did not install software.
- The final wheel includes all application/UI/configuration files and the exact MIT license. A fresh installation, dependency check and CLI health endpoint were checked before publication.
- Full analysis, rebuilding all pipeline tool environments, target WSL browser-to-worker execution, and a live self-update on the user's workstation remain outside these software checks. No biological analysis was run and no prior alpha limitation was removed.

---

# Validation update: 0.1.0a5 (2026-10-24)

- All 27 automated tests passed, including the 18 existing tests and nine additional filesystem/connection/component tests.
- Folder tests cover pagination, filtering, hidden directories, symlink navigation, missing/relative paths and WSL path translation.
- A real temporary Git checkout was discovered, inspected and copied through the production connection routine. Tests verified that modified local configuration, existing result files and cache contents were retained, while the committed source alone was copied into the workspace.
- Existing-runner connection tests replace the native environment checker with a test double. They verify the selected prefix is checked, no environment creation/update command is issued, failure prevents activation, and new run commands use the selected cache. They do not establish compatibility of a user's environment.
- The real Streamlit component protocol was exercised to browse a folder and register a sample in SQLite. Source selection revalidates the commit and rejects unsupported versions.
- The production HTML/CSS/JavaScript component was checked in a browser using an isolated static QA fixture: folder navigation, selection, automatic sample details, candidate scan results, inspection and existing environment selection. A collapsed scan-results issue found in this check was fixed and rechecked.
- The managed browser preview cannot run the local Python virtual environment. Full browser-to-Streamlit testing on the target WSL workstation remains outstanding. Static fixture interactions are not an end-to-end worker test.
- No full biological workflows were run for this UI/setup update. The reviewed adapter commits and prior alpha limitations remain unchanged.

---

# Bactologix — validation record

## 0.1.0a7 — 24 October 2026

Database management was checked with filesystem-only software fixtures in an isolated Linux workspace.

- **70 automated tests passed**, including all previous tests. New checks cover database layouts and required companions, preferred-location ordering, scan limits and symlink cycles, receipt field allowlists, stale/concurrent default changes, save-time revalidation, resets, managed-link collision protection and unchanged earlier frozen drafts.
- The real Streamlit component protocol browsed a folder, scanned two resource types, checked a selection and saved native configuration through Python/SQLite. The packaged HTML/JavaScript component was exercised in the managed browser preview: pipeline tabs, discovery dropdowns, preferred choices, manual-choice retention, folder selection returning to the page, reload and save-state clearing.
- The exact reviewed BaSIS tree `88fcd3b9d0b9ca2dc2f175da5dcb1b1aaeb7f032` (published commit `f66dfedd46da05ae79bf769e7e6fada91270d4d1`) consumed the generated configuration through its real `SafetyContext`; the chosen MLST directory became an external resource with the expected path. No analysis tool or resource download was invoked.
- HybAs configuration tests resolve the native Kraken2 and BUSCO expressions through separate managed links, including paths containing spaces. Creating later selections leaves earlier frozen run configurations and link targets intact.
- The installed a7 wheel passed all 70 tests (4.98 seconds), including AppTest loading the installed application. Every packaged Python/UI/data file matched the source and the wheel contains the MIT license. `pip check` reported no broken requirements. The real CLI server returned HTTP 200 `ok` on its loopback health endpoint and was stopped cleanly.

The browser preview uses actual component assets and Python-generated filesystem results, with captured commands rather than a live browser-to-Streamlit connection. Full workstation browser testing and real database/tool execution remain user-side checks. Fixture file contents do not establish database integrity or scientific validity; layout discovery intentionally leaves native validation to the pipeline.

## 0.1.0a4 — 22 October 2026

The original HTML design is now the default interface, using Streamlit’s bidirectional component API and the existing durable Python worker.

- **18 automated tests passed**, covering the existing execution contracts and real component-trigger callbacks: configuration saves/edits, unknown-key preservation, duplicate-event suppression, failed actions, server-resolved version selection and report isolation.
- Browser QA rendered the packaged HTML/CSS/JS component in a Shadow DOM harness, using actual workspace snapshots. It checked the dashboard layout, navigation, sample dialog/event payloads, preserved fields after errors, the version picker, and the original smoke-report HTML inside a sandboxed iframe. The harness captures commands rather than installing or running workflows; it is not a replacement for full Streamlit browser integration.
- A real Streamlit component trigger submitted Snakemake smoke job `job-ca5a3c3a299c`. It completed successfully and produced its original HTML report through the existing controller. This check reused the previous runner without installing analysis tools or databases.
- The test process was kept alive through worker completion because the development executor terminates descendant processes when its command exits. An earlier test job was recovered as interrupted; this was an executor-lifetime constraint.
- UI assets, citations and the MIT license are included in the wheel and source distribution. No Node or frontend build is required on the user’s machine.

The browser could not reach the local Streamlit server from its separate environment. Full Streamlit rendering and interaction on the target WSL2 workstation remain unverified. This release does not claim full biological-analysis validation or post-AWARE checkpoint reuse.

## 0.1.0a3 — 22 October 2026

The MIT license packaging release was checked in an isolated Linux workspace using the installed wheel.

- **14 automated tests passed** (2.54 seconds).
- The exact root MIT license was verified inside the wheel, source distribution and installed package. Installed metadata reports `License-Expression: MIT` and `License-File: LICENSE`.
- Every packaged application/data file matched the release source, with no bytecode files in the wheel. The installed launcher reports `0.1.0a3`; `pip check` found no broken requirements.
- Streamlit AppTest loaded the installed app, exercised all eight pages, and found all six citation downloads.
- The installed UI's **Run live smoke test** button queued a real Snakemake job. It completed after navigation to Jobs & logs, and Results & reports rendered its generated HTML component. The existing controller was reused in a fresh test workspace; no analysis environments or databases were installed for this check.
- The actual CLI-launched Streamlit server returned HTTP 200 with `ok` from its health endpoint, then was stopped cleanly.

These checks validate software packaging and orchestration. They do not establish browser visual correctness, target-workstation behavior or biological analysis validity. The alpha limitations below remain applicable.

## 0.1.0a2 — 21 October 2026

Checked on 21 October 2026 in an isolated Linux development workspace. This record distinguishes executed software checks from unperformed analysis and browser checks.

### Passed

- **14 automated tests:** durable queue/cancellation, atomic sample import validation, duplicate sample protection, source/input/output separation, unknown YAML preservation, frozen config enforcement, changed-input rejection, clone inheritance, resource paths, environment identity, literal command arguments, real process interruption, all eight Streamlit pages, editable settings and nested report-frame containment.
- **Live GitHub refresh:** 5 HybAs entries (2 releases and 3 branches); 20 BaSIS entries (9 releases and 11 branches). Release tags resolve to exact commit SHAs.
- **Actual source installation:** both reviewed repositories fetched through Git and checked against the selected SHA.
- **Actual Conda bootstrap:** official Miniforge 26.7.2-0 Linux x86_64 installer, SHA-256 `281b0ac7d550802efc81af633225a5e6116d29ae72f3ab4eae7168c3931a4c05`. It installed inside the test workspace, without shell initialization changes.
- **Actual controller installation:** both upstream environment definitions installed; Python 3.11.17 and the actual Snakemake module/CLI version 9.6.2 were verified. BaSIS dependencies NumPy, Biopython and openpyxl imported successfully.
- **Controller reuse:** subsequent setup checked the existing environment paths successfully without creating replacements.
- **Native HybAs dry-run:** passed using software-only FASTQ fixtures with AWARE, BUSCO, annotation and polishing disabled in the submitted test configuration. A dry-run can list upstream placeholder rules for disabled steps; rule names alone do not establish execution.
- **Native BaSIS dry-run:** passed with the standalone default module configuration and software-only annotation fixtures. The initial DAG contained 68 jobs; dynamic checkpoints can expand that graph during actual execution.
- **Real Snakemake smoke workflow:** executed to completion, wrote an HTML test report, and retained its native log.
- **UI-to-worker integration:** the Streamlit Run live smoke test button created a durable job; it completed after changing pages and rerunning the UI. Results & reports found and rendered the report component.
- **Source integrity after dry-runs:** both per-run source fingerprints remained unchanged.

Two issues discovered during live installation were corrected: Miniforge checksum assets use the versioned installer name, and the Bioconda Snakemake 9.6.2 distribution metadata reports `0.0.0` while its actual module and executable correctly report `9.6.2`. Runner validation now checks the actual installed module and executable. A truncated installer download was rejected; complete retries were checked against the same official checksum before execution.

### Not established by these checks

- No real sample analysis was run. The FASTQ/annotation fixtures are software fixtures, not scientific validation data. The smoke HTML contains only a software-test message.
- Analysis tool environments, large reference databases, GPU tools and end-to-end HybAs/BaSIS biological outputs were not installed or validated here.
- Efficient post-AWARE checkpoint reuse and polishing-only execution remain unimplemented. Clones create independent workflows from original inputs.
- Full browser rendering, native browser downloads and launching a desktop browser were not verified. The managed browser preview service could not start this Python runtime. Streamlit AppTest exercised the actual app and report components, but is not a substitute for browser QA on the target workstation.
- WSL2-specific installation, Windows filesystem paths, aarch64 Miniforge bootstrap and long-running recovery after workstation restart need target-machine testing.

The app, controller environments, test inputs and output folders were separate. Test data, installed tools, credentials and machine-specific workspace state are excluded from the release artifacts.
