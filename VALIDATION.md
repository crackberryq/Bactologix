# Bactologix 0.1.0a2 — validation record

Checked on 21 October 2026 in an isolated Linux development workspace. This record distinguishes executed software checks from unperformed analysis and browser checks.

## Passed

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

## Not established by these checks

- No real sample analysis was run. The FASTQ/annotation fixtures are software fixtures, not scientific validation data. The smoke HTML contains only a software-test message.
- Analysis tool environments, large reference databases, GPU tools and end-to-end HybAs/BaSIS biological outputs were not installed or validated here.
- Efficient post-AWARE checkpoint reuse and polishing-only execution remain unimplemented. Clones create independent workflows from original inputs.
- Full browser rendering, native browser downloads and launching a desktop browser were not verified. The managed browser preview service could not start this Python runtime. Streamlit AppTest exercised the actual app and report components, but is not a substitute for browser QA on the target workstation.
- WSL2-specific installation, Windows filesystem paths, aarch64 Miniforge bootstrap and long-running recovery after workstation restart need target-machine testing.

The app, controller environments, test inputs and output folders were separate. Test data, installed tools, credentials and machine-specific workspace state are excluded from the release artifacts.
