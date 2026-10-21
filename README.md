# Bactologix 0.1.0a4 — native alpha

Created by **Jimi Goldstein · Human Link**. ORCID: https://orcid.org/0000-0002-7829-0090.

Bactologix is a Python/Streamlit application for local HybAs and standalone BaSIS setup, version selection, configuration, sample registration, isolated run drafts, durable jobs, and HTML reports. This version executes real operations. Download the wheel and source archive from [GitHub Releases](https://github.com/crackberryq/Bactologix/releases). Bactologix is not published to PyPI.

**Alpha release:** the approved HTML interface now runs inside Streamlit with working Python controls. Software checks, component browser checks and native workflow dry-runs passed; full biological analyses and target-workstation browser testing remain outstanding. Read [VALIDATION.md](VALIDATION.md) and the alpha boundaries below before running samples.

## Install on Linux or WSL2

Install Python 3.11+ and Git. Download the wheel, then use its actual path:

```bash
pipx install ./bactologix-0.1.0a4-py3-none-any.whl
bactologix
```

Your base Python can be any supported Python 3.11+ (including 3.13). The separate pipeline runners install their own Python 3.11. If pipx is missing in your Conda base, install it first:

```bash
python -m pip install pipx
python -m pipx ensurepath
python -m pipx install --force "https://github.com/crackberryq/Bactologix/releases/download/v0.1.0a4/bactologix-0.1.0a4-py3-none-any.whl"
"$HOME/.local/bin/bactologix"
```

For an alternative dedicated Python environment:

```bash
python3 -m venv ~/.venvs/bactologix
~/.venvs/bactologix/bin/python -m pip install ./bactologix-0.1.0a4-py3-none-any.whl
~/.venvs/bactologix/bin/bactologix
```

Open http://localhost:8501. On Windows, run these commands inside WSL2; use Linux paths such as `/home/jimi/...` or `/mnt/c/...` in the app. This app cannot operate the user's workstation from ChatGPT: install it on the machine containing the samples. Native Windows pipeline execution is not supported.

The launcher binds to `127.0.0.1`. This is a local, single-user tool, not a public server. `--home /absolute/workspace` selects a workspace; the default is the platform user data directory. `BACTOLOGIX_HOME` also selects it. Jobs persist in SQLite and logs remain on disk. Closing a browser does not stop a job; stop it from Jobs & progress.

## First use

1. **Install & update:** select your Conda executable, or install workspace-local Miniforge. Select a version and download/set up its runner. Both supported runners use Python 3.11 and Snakemake 9.6.2. Streamlit is isolated from these environments.
2. **Samples:** register an existing sample folder, or import CSV/TSV columns `sample_id,input_folder,read_mode`. HybAs uses `raw/illumina` and `raw/nanopore`. BaSIS needs `annotation/<sample_id>.fna`, `.faa`, `.gff`, plus `.gbk` when enabled modules require it.
3. **Configuration:** import existing native YAML or edit the complete template. Search all pinned settings in the setting editor. Set database/model paths to absolute paths. Saving defaults affects only new drafts.
4. **Samples & variants → Create run drafts:** choose samples and create drafts. Each draft copies workflow source, references only input files, writes a frozen resolved config, and has independent outputs and scheduler state. Run **Check / dry-run** first. Read its actual log, then prepare analysis tool environments or **Run / resume**.
5. **Results & reports:** native MultiQC/Safety reports are discovered by expected path, or register any existing local HTML. Embedded reports run in an isolated inner frame. Self-contained reports work best; use Open original for relative assets.
6. **Overview → Run live smoke test:** executes a real tiny Snakemake workflow using an installed controller and creates an HTML software-test report. It performs no biological analysis.

Diagnostic commands: `bactologix doctor`, `bactologix jobs`, and `bactologix smoke-test`.

## Version and environment policy

The UI refreshes GitHub release/branch lists, resolves release tags to exact commits, and installs immutable source checkouts. The bundled catalogue is explicitly date-labelled when offline. The alpha's reviewed execution adapters accept these source commits:

| Pipeline | Branch | Commit |
|---|---|---|
| HybAs | HybAs_v9.0.0-AWARE | 9477fd3b6578e23a84d6524e1c89e65e01590f03 |
| BaSIS | snakemake-standalone | d2d2e7806df4d2f586dc4a11d5c1f980deb06857 |

Other listed versions can be downloaded; managed execution is withheld until their adapter contract is reviewed. Branch updates to a new commit therefore require a Bactologix adapter update before execution. Choosing a different installed version affects future drafts only.

Controller environments are keyed by pipeline, environment-file bytes and platform. Existing controllers are health-checked before reuse. Repair prepares a fresh controller and retains the old one. Snakemake uses a stable absolute `--conda-prefix` per pipeline outside version-specific source folders; its native content/pin/deploy-script hashing governs rule environment reuse. No environment or old run is routinely deleted. Database/model storage is separate. BaSIS's configured `resources.policy: auto` can download missing resources during an actual run. HybAs database provisioning remains the upstream explicit workflow/configuration responsibility.

One OS lock serializes jobs in each Bactologix workspace, including environment preparation. Child processes inherit the lock. The worker marks abandoned running jobs interrupted on recovery. Run/resume uses normal Snakemake metadata and `--rerun-incomplete`; Bactologix never fabricates completion records, deletes scheduler locks, or weakens rerun triggers. Input size/mtime changes invalidate a draft; these are metadata checks, not full input content hashes. Native workflow provenance may provide additional content checksums.

## Alpha boundaries

- HybAs and BaSIS run in separate controller environments; no analysis tools or databases are installed by pip.
- Batch drafts are supported; jobs execute sequentially. Parallel analysis and remote/HPC execution are not implemented.
- AWARE-free and polishing variants inherit full settings and parent identity, but **start independent workflows from original reads**. Efficient post-AWARE checkpoint reuse and polishing-only execution are not implemented. They must not be described as validated compute-saving clones.
- Existing reports can be opened; existing runs are not adopted for in-place resume. A managed run can resume its own native outputs.
- A version switch never rewrites prior run configuration. Changed YAML requires a new draft.
- Automatic report discovery indicates an existing file, not scientific validity. User-registered reports are explicitly user-selected.
- Repair checks the controller; damaged rule environments require diagnosis using their native logs. The app does not automatically delete failed environments.
- Actual analysis requires valid input data and relevant resource databases. A software smoke test does not validate biological outputs or every external tool.

## Development

```bash
python -m pip install -e . pytest build
python -m pytest
python -m build
```

`src/bactologix`: HTML/JS/CSS component, Streamlit action bridge, CLI, SQLite store, GitHub catalogue, version-specific adapters, detached worker. `tests`: subprocess/queue, immutable source/config and input contracts, report containment, and Streamlit component trigger checks. The packaged HTML component preserves the original prototype’s design; its production data and actions come from Python, with no seeded examples or simulated workflow completion. `VALIDATION.md` records the checks performed for the supplied build.

Credits and RIS/BibTeX/CFF/plain-text citations are in the UI and package data. GitHub's **Cite this repository** entry uses the root [CITATION.cff](CITATION.cff). Cite the pipelines, tools and databases used as well as Bactologix.

## License

Bactologix is released under the [MIT License](LICENSE), copyright (c) 2026 Jimi Goldstein. The license is included in the wheel and source distributions. Upstream templates, pipelines, tools and databases retain their respective licenses; bundled upstream notices are in `src/bactologix/data/LICENSE.*`.
