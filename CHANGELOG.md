# Bactologix releases

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
