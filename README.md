# Bactologix 0.1.0a9 — alpha candidate

Created by **Jimi Goldstein · Human Link**. ORCID: https://orcid.org/0000-0002-7829-0090.

Bactologix is a Python/Streamlit application for local HybAs and standalone BaSIS setup, version selection, configuration, sample registration, isolated run drafts, durable jobs, and HTML reports. This version executes real operations. Published wheels and source archives are listed in [GitHub Releases](https://github.com/crackberryq/Bactologix/releases). Bactologix is not published to PyPI.

**Alpha release:** the approved HTML interface now runs inside Streamlit with working Python controls. Software checks, real Chromium-to-Streamlit checks and native workflow dry-runs passed; full biological analyses and target-workstation browser testing remain outstanding. Read [VALIDATION.md](VALIDATION.md) and the alpha boundaries below before running samples.

This branch contains the **a9 candidate**. It is not a published GitHub release yet. To test this branch in an existing application environment:

```bash
conda activate bactomix
python -m pip install --upgrade "git+https://github.com/crackberryq/Bactologix.git@fix/run-integrity-and-aware-adapters"
python -m bactologix.cli
```

Use an exact commit from the pull request when a reproducible installation is required. The wheel commands below apply after the corresponding release is published or when installing a locally built wheel.

## Install on Linux or WSL2

Install Python 3.11+ and Git. Download the wheel, then use its actual path:

```bash
pipx install ./bactologix-0.1.0a9-py3-none-any.whl
bactologix
```

Your base Python can be any supported Python 3.11+ (including 3.13). The separate pipeline runners install their own Python 3.11. If pipx is missing in your Conda base, install it first:

```bash
python -m pip install pipx
python -m pipx ensurepath
python -m pipx install --force "https://github.com/crackberryq/Bactologix/releases/download/v0.1.0a9/bactologix-0.1.0a9-py3-none-any.whl"
"$HOME/.local/bin/bactologix"
```

To use a Conda environment named `bactomix`, create it once if it does not already exist:

```bash
conda create -n bactomix python=3.11 pip -y
conda activate bactomix
python -m pip install --upgrade "https://github.com/crackberryq/Bactologix/releases/download/v0.1.0a9/bactologix-0.1.0a9-py3-none-any.whl"
python -m bactologix.cli
```

For an alternative dedicated Python environment:

```bash
python3 -m venv ~/.venvs/bactologix
~/.venvs/bactologix/bin/python -m pip install ./bactologix-0.1.0a9-py3-none-any.whl
~/.venvs/bactologix/bin/bactologix
```

Open http://localhost:8501. The launcher runs headlessly, so WSL does not try to launch a Linux browser. Automatic source watching is disabled for the packaged app, preventing pip from triggering a rerun halfway through an update. Use `python -m bactologix.cli --port 8502` and http://localhost:8502 when using a different local port. On Windows, run these commands inside WSL2; use Linux paths such as `/home/jimi/...` or `/mnt/c/...` in the app. This app cannot operate the user's workstation from ChatGPT: install it on the machine containing the samples. Native Windows pipeline execution is not supported.

The launcher binds to `127.0.0.1` by default. If Windows-to-WSL localhost forwarding fails while the WSL health check succeeds, use `bactologix --host <WSL-private-IP> --port 8502` and open that same IP and port in Windows. Obtain the current WSL address with `hostname -I`; it may change after restarting WSL. This is a local, single-user tool, not a public server. `--home /absolute/workspace` selects a workspace; the default is the platform user data directory. `BACTOLOGIX_HOME` also selects it. Jobs persist in SQLite and logs remain on disk. Closing a browser does not stop a job; stop it from Jobs & progress.

## Update Bactologix

Use **Update Bactologix** in the top bar or **Install & update**. The dialog retrieves published GitHub releases, including prereleases, and shows the running version, release notes and application environment. Select the version you want, then click **Install v…**. Older releases are labelled. A release needs the expected wheel and GitHub SHA-256 digest to be installable.

Finish or cancel queued/running jobs first. The updater checks the asset again, verifies its size, checksum, package identity and Python requirement, then runs pip through the exact Python interpreter serving Bactologix. The job log records installation progress. Once installation starts it cannot be cancelled from the app. An environment lock also prevents an update from overlapping another Bactologix worker using that Python environment. These locks do not coordinate unrelated external pip/Conda commands.

After it finishes, press **Ctrl+C** in the Bactologix terminal and rerun the same launch command, including `--host`, `--port` and `--home` if used. Refresh the browser after restart. A banner reminds you to restart, and new work is blocked in the old app process. This first updater does not automatically restart Streamlit. Pipeline runners, databases, settings, cached tool environments and run folders are separate from the application package. Pip may update application dependencies when required by the selected release.

For **v0.1.0a5 and earlier**, install this release once using the wheel command above; the update button becomes available after restarting. If BaSIS `f66dfedd` was already downloaded, open **Install & update → BaSIS → Finish setup**. This uses the downloaded source and creates or reuses its runner. The runner environment definition is unchanged from the earlier supported BaSIS revision. Read **Setup log** if setup fails; **Retry setup** reuses an existing runner, whereas **Repair runner** creates a separate replacement.

Compatible snapshots remain selectable even after a branch moves. Unsupported downloads say **Version not supported** and link to the app updater; failed setup says **Setup incomplete** with its job log. Downloading a supported version normally completes runner setup in the same job.

## First use

1. **Install & update:** select your Conda executable, or install workspace-local Miniforge. Use **Connect existing** for a local checkout, or choose a version to download and set up. Both supported runners use Python 3.11 and Snakemake 9.6.2. Streamlit is isolated from these environments.
2. **Samples:** choose **Add sample → Browse folders**, navigate to the sample folder and select it, or import CSV/TSV columns `sample_id,input_folder,read_mode`. HybAs uses `raw/illumina` and `raw/nanopore`. BaSIS needs `annotation/<sample_id>.fna`, `.faa`, `.gff`, plus `.gbk` when enabled modules require it.
3. **Configuration:** import existing native YAML or edit the complete template. Search all pinned settings in the setting editor. Choose database/model locations in **Databases & models**; advanced YAML editing remains available. Saving defaults affects only new drafts.
4. **Samples & variants → Create run drafts:** choose samples and create drafts. Each draft copies workflow source, references only input files, writes a frozen resolved config, and has independent outputs and scheduler state. Run **Check / dry-run** first. Read its actual log, then prepare analysis tool environments or **Run / resume**.
5. **Results & reports:** native MultiQC/Safety reports are discovered by expected path, or register any existing local HTML. Embedded reports run in an isolated inner frame. Self-contained reports work best; use Open original for relative assets.
6. **Overview → Run live smoke test:** executes a real tiny Snakemake workflow using an installed controller and creates an HTML software-test report. It performs no biological analysis.

Diagnostic commands: `bactologix doctor`, `bactologix jobs`, and `bactologix smoke-test`.

## Browse samples and connect existing tools

**Add sample → Browse folders** opens a folder explorer on the machine running Bactologix. Use Home, Workspace, Computer, or a mounted Windows drive shortcut; navigate into the sample folder and click **Select this folder**. Folder name and read subfolders suggest the sample ID and read mode, which remain editable. No reads are uploaded or copied by this picker. It is an in-app explorer and requires no desktop GUI toolkit. Windows drive paths (`C:\Users\...`) and paths to the active WSL distribution can also be pasted into the picker.

Under **Install & update → HybAs/BaSIS → Connect existing**:

1. Browse to the pipeline Git checkout and **Inspect folder**, or expand **Search this machine** and scan. The default scan covers Home, the launch directory and `/opt` to depth four, with time/directory limits. It skips data/cache directories and symlinks. Choose a search root for other locations, including `/mnt/c` or another mounted disk. A quick scan is not a full-disk inventory.
2. Choose a detected Conda environment, browse to one, or leave **Create / reuse a Bactologix runner** selected. Environment discovery reads Conda metadata; connecting an existing runner executes the native Python/Snakemake/import checks before marking it ready. An incompatible runner is not activated or modified.
3. Optionally choose an existing writable Snakemake Conda cache, typically `<checkout>/.snakemake/conda`. An available cache at that location is suggested after inspection. Snakemake decides which environments match and may create missing ones during preparation; reuse of every old environment is not guaranteed.
4. Click **Connect installation** and inspect the job log. Bactologix copies the selected committed source locally and uses independent run outputs. It does not alter the original checkout or its local settings. Import other native settings in **Configuration**, then connect existing resources in **Databases & models**.

Only the reviewed source commits below can be connected for managed runs. Modified working-tree files are excluded from the source snapshot; the dialog flags tracked edits. ZIP-only copies without Git metadata need a Git checkout. Existing results can be registered as HTML reports; existing external runs are not adopted for in-place resume. Repair creates a managed runner and leaves a selected external environment intact.

## Check and install tool environments

In **Install & update**, open **Check / install environments** on a ready installation. Select the environments to inspect and click **Check selected**. The worker asks Snakemake for the exact cache paths, checks deployment completion and runs each installed environment's native executable/version probes. Missing, incomplete and failed environments remain distinct from verified ones.

**Environment cache folder → Browse → Use this cache** selects an existing cache for maintenance and future drafts. Existing run drafts retain their original cache. Snakemake 9.6.2 invokes deployment scripts with an unquoted cache path, so choose a cache path without spaces or shell characters; Bactologix checks this before requesting installation. Source, workspace and database destination folders may contain spaces.

**Install selected** deploys only the selected environment recipes, then checks them. No samples, analyses or database download targets are scheduled. Optional environments remain optional for analysis. Compatible environments are reused from the installation's configured cache. A completed environment whose tools fail checks is retained for diagnosis; this control does not delete it. Follow progress in **Jobs & progress**, then reopen the environment panel for status and check time.

## Manage databases without editing YAML

Discovery includes registered Conda prefixes, pipeline resource folders, hidden Snakemake caches and bounded directory symlink traversal. The scan lists recognizable but incomplete locations with the missing files. Broad scans still have time, folder and depth limits; supply a specific folder if needed.

Open **Databases & models**, choose **HybAs** or **BaSIS**, and click **Auto scan**. Every selectable resource has its own name, location, **Browse**, and **Check location** controls. Detected installations appear in a dropdown. Add a specific scan folder to include another disk or a deeper directory; Windows Explorer paths work in WSL.

The scan preselects a preferred candidate for each unchanged field. It keeps a valid saved location first, then prefers a matching configured BUSCO lineage or a completed BaSIS resource receipt. Remaining ties use a stable path order, not a claim that a particular release is scientifically best or newest. A choice you edited yourself is retained. Review the selected release and lineage, then click **Save locations**. The server rechecks every changed selection before saving; stale configuration revisions and incomplete locations are rejected. **Reload locations** discards unsaved resource choices. **Use pipeline default** resets only that resource's owned path/metadata fields when saved.

- **HybAs:** Kraken2 and BUSCO may live on different disks. Bactologix creates a separate local link layout for each selection, writes the native shared-root configuration, and preserves earlier layouts for frozen run drafts. Database contents are neither copied nor downloaded.
- **BaSIS:** named entries cover AMRFinderPlus, CARD/RGI, VFDB, BacMet, antiSMASH, geNomad, MOB-suite, MLST/PubMLST, CRISPRCasFinder/CASFinder, PROSITE, ProtT5/classifier models, and the deep reference bundle. Folder selection resolves native file-backed resources such as CARD JSON and DIAMOND indices. Completed resource receipts retain allowlisted companion paths and recorded identities; executable overrides and analysis switches are not imported. Model/reference bundles require their companion files; a filename alone does not establish their identity.
- **Tool-managed resources:** Prokka, IntegronFinder, ISEScan and ICEscreen use resources inside their tool environments in the pinned adapters. They are labelled separately because a custom folder would not redirect those tools. MinCED needs no external database.

Discovery is a quick structural check, not a full checksum verification or compatibility guarantee. It scans configured locations, registered pipeline sources/caches, workspace resources and common folders, with limits of 5,000 directories, 3,000 entries per folder, seven levels and an eight-second traversal budget. The result reports reached limits and inaccessible folders. Directory symlinks are inspected without recursive traversal; hidden directories are skipped unless included as explicit roots. This is not an exhaustive inventory of every disk. The native workflow still performs its resource checks during preparation.

Saving affects future run drafts and preserves other analysis settings. Existing drafts keep their frozen paths; independently changing database contents at those paths is outside this selection mechanism. Missing BaSIS resources continue to follow the saved `auto`/`manual` policy. Scanning and saving do not install environments or download databases. Use the separate Download database control to request preparation.

## Download databases to a chosen location

Each selectable database card has **Download database**. Choose a writable parent folder with **Browse**, set threads/memory, and select the lineage for BUSCO. Bactologix creates a fresh subfolder, freezes a separate administrative configuration and invokes only the pinned pipeline's native database preparation target. Kraken2 uses the native standard database build, which can require substantial memory, storage and time. BaSIS uses its resource provider and required dependencies. Tool-managed resources are prepared through their tool environment instead.

Downloads require the current recommended supported source snapshot and a ready runner. They do not use sample inputs or change saved analysis defaults. Native commands, receipts and provider logs remain available on disk; **Download log** includes recent provider output. A stopped or failed download offers **Resume download**, preserving its original destination, configuration and source identity. The native provider determines which partial work can be reused.

When preparation succeeds, click **Use downloaded**, then **Save locations**. Paths are checked again at selection and saving. Earlier run drafts keep their frozen paths.

## Version and environment policy

The UI refreshes GitHub release/branch lists, resolves release tags to exact commits, and installs immutable source checkouts. The bundled catalogue is explicitly date-labelled when offline. Compatible immutable snapshots from this app release are always included, separately from moving branch entries. The alpha's reviewed execution adapters accept these source commits:

| Pipeline | Branch | Commit |
|---|---|---|
| HybAs | HybAs_v9.0.0-AWARE (recommended) | 780ee7086c15c49f2825800ec463327f009f14a6 |
| HybAs | Earlier compatible snapshot | 9477fd3b6578e23a84d6524e1c89e65e01590f03 |
| BaSIS AWARE | basis-aware / v0.4.0a1 (recommended) | 1d2d96b266e98d956254d1a518ff5b437ab7a10a |
| BaSIS | snakemake-standalone (earlier compatible snapshot) | f66dfedd46da05ae79bf769e7e6fada91270d4d1 |
| BaSIS | snakemake-standalone (earlier compatible snapshot) | d2d2e7806df4d2f586dc4a11d5c1f980deb06857 |

Other listed versions can be downloaded; managed execution is withheld until their adapter contract is reviewed. Branch updates to a new commit therefore require a Bactologix adapter update before execution. Choosing a different installed version affects future drafts only.

Controller environments are keyed by pipeline, environment-file bytes and platform. Existing controllers are health-checked before reuse. Repair prepares a fresh controller and retains the old one. Existing drafts use the repaired controller for the same source commit, and require a fresh native dry-run before preparation or execution. Their original controller path remains recorded as creation provenance. Snakemake uses a stable absolute `--conda-prefix` per pipeline outside version-specific source folders; its native content/pin/deploy-script hashing governs rule environment reuse. No environment or old run is routinely deleted. Database/model storage is separate. BaSIS's configured `resources.policy: auto` can download missing resources during an actual run. HybAs database provisioning remains the upstream explicit workflow/configuration responsibility.

One OS lock serializes jobs in each Bactologix workspace, including environment preparation. Child processes inherit the lock. The worker marks abandoned running jobs interrupted on recovery. Run/resume uses normal Snakemake metadata and `--rerun-incomplete`; Bactologix never fabricates completion records, deletes scheduler locks, or weakens rerun triggers. The generated sample sheet and exact staged input-link mapping are verified before every launch. Input size/mtime changes invalidate a draft; these are metadata checks, not full input content hashes. Older drafts reconstruct their expected link mapping from recorded source identities, never from the current staged links; unverifiable drafts are rejected. Comparison clones must retain the parent’s exact input snapshot, including its file inventory. A failed execution remains failed after later successful dry-runs, and discovered reports show their source run status. Native workflow provenance may provide additional content checksums.

## Reviewed BaSIS AWARE follow-up

New BaSIS defaults leave DeepSafe disabled. Existing saved defaults and run configurations are preserved, so review them before creating new drafts after upgrading. Choose the supported BaSIS AWARE source under Install & update for new screening runs; older standalone snapshots remain available for their existing runs.

After a managed BaSIS AWARE screening run completes, open its results, then **Run details → Review follow-up**. Review the follow-up configuration, explicitly enable it, and confirm that you reviewed the screening report. This creates a separate frozen follow-up request with its own source copy, configuration, job history and native dry-run. It invokes upstream `Followup.smk`, which verifies saved screening evidence and requires prepared resources; it has no screening or resource-download rules. Resource paths must be absolute. Bactologix fingerprints the saved screening prerequisite files and rejects changed evidence before launch.

The native follow-up entry point writes into the screening run’s `safety/deepsafe` and `safety/deepsafe-report` folders. Multiple follow-up requests therefore share those outputs; they are not independent output variants. The latest executed follow-up request owns the displayed follow-up report. Earlier requests retain their configurations and job logs. A failed latest attempt is labelled failed even if a report from an earlier attempt remains on disk. Screening reports remain separate.

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

`src/bactologix`: HTML/JS/CSS component, Streamlit action bridge, CLI, SQLite store, GitHub catalogue, version-specific adapters, detached worker. `tests`: subprocess/queue, immutable source/config and input contracts, report containment, and Streamlit component trigger checks. The packaged HTML component preserves the original prototype’s design; its production data and actions come from Python, with no seeded examples or simulated workflow completion. `VALIDATION.md` records the checks performed for the supplied build. GitHub Actions runs Python 3.11/3.12 tests, pinned upstream dry-runs, an empty saved-screening follow-up, package checks, and a separate real Chromium-to-Streamlit test. No native biological analysis is performed by CI.

Credits and RIS/BibTeX/CFF/plain-text citations are in the UI and package data. GitHub's **Cite this repository** entry uses the root [CITATION.cff](CITATION.cff). Cite the pipelines, tools and databases used as well as Bactologix.

## License

Bactologix is released under the [MIT License](LICENSE), copyright (c) 2026 Jimi Goldstein. The license is included in the wheel and source distributions. Upstream templates, pipelines, tools and databases retain their respective licenses; bundled upstream notices are in `src/bactologix/data/LICENSE.*`.
