# Bactologix releases

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
