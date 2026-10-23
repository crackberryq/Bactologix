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
