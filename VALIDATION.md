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
