# Bactologix 0.1.0a27 — alpha

### Job progress and resource-use records

**Jobs & progress** and a saved run's **Activity** show elapsed time, sampled CPU
and peak sampled RAM. Recognized Snakemake log messages add active steps and
completed tasks in the current plan. Checkpoints can change the total; a task
percentage is neither an ETA nor evidence of complete biological results.

Choose **Resource use** to view CPU/RAM charts or download JSON, CSV, or a ZIP
containing both formats and measurement notes. Every job attempt has a separate
record, including failed and cancelled jobs; a resumed run never overwrites the
earlier attempt. Recording starts with jobs executed in a27. Historical usage
cannot be reconstructed for older jobs.

Measurements sample the command and discoverable descendants approximately every
two seconds. 100% CPU represents one logical CPU. CPU seconds are observed
user/system counter increments; RAM is the sum of process RSS in MiB, which can
count shared memory more than once. Brief processes, detached children and
between-sample peaks can be missed. Unavailable/stale readings and interrupted
records are explicit. Charts save at most every 30 seconds and at command/job
completion; long histories are combined into intervals preserving observed CPU
seconds and sampled RSS peaks. These are local diagnostic measurements, not
complete scheduler accounting; GPU usage, energy and remote jobs are not measured.
Observation errors do not change command results or prevent cancellation. Exports
include job/run IDs and source/configuration hashes, but no raw logs, command
arguments or input sequences. No data is uploaded automatically.

### Guided setup and protected run editing

Start in **Install & update → Guided setup**. Choose a pipeline, review PC
capacity, connect or install its runner, check/install/repair environments,
select database locations, then review setup before creating a draft. Steps
remain accessible in any order. Installation and download actions still require
their explicit buttons. Setup status does not replace validation of a saved run.

**Create run drafts** and **Edit settings** now open full pages: Samples → Run
settings → Review. Unfinished run edits are retained in this browser tab, scoped
to the workspace, and recovered after a refresh. **Save & exit** leaves the editor
without creating a run; use **Resume editing** to return. **Discard edits** asks
before clearing the unfinished editor. Browser storage may be unavailable or
cleared when a tab closes; the editor reports recovery failures. Creating a draft
stores it durably in the workspace and clears its browser recovery copy. This
recovery feature applies to the run editor, not unsaved global defaults.

Saved-run checks now show destination permissions, free space for results and
working folders, and CPU/RAM budget warnings. Checks read directory permissions
without creating files. Missing parents are checked at the closest existing
directory. Unusable destinations and less than 1 MB free space block the relevant
job, both at submission and worker start. Less than 5 GB is an advisory warning,
not an output-size estimate. CPU/RAM oversubscription and explicit tool-memory
limits above the run budget are advisory; no settings are silently adjusted.
These checks cannot predict analysis storage/memory requirements, disk quotas,
future disk growth, or every checkpoint-dependent environment. Native validation
and runtime checks remain required. GPU detection now explicitly treats absent
NVIDIA results as unknown availability, including on WSL.


### Run-specific Methods exports

Open a saved run and choose **Methods**, or use **Results → Methods & downloads**.
Review the generated prose, then download plain text, Markdown, printable HTML,
or a reproducibility ZIP. The ZIP includes the frozen run YAML, settings with
their origin, source/configuration hashes, retained execution metadata and
available software citation files. Text and HTML can be copied into a manuscript;
HTML can also be printed to PDF from a browser.

Drafts describe planned methods. Completed runs incorporate available native
execution records, tool versions and automatic decisions; workflow completion
does not imply that every requested module completed. New executions retain a
completion-time metadata snapshot. Older completed runs can use their available
output metadata, clearly labelled as read at export. Unknown versions remain
unknown, and older/unreviewed source versions receive a configuration-only
export instead of borrowing current defaults. HybAs read modes, Kraken/AWARE,
polishing, quality and annotation switches, and BaSIS module exclusions, AWARE
and follow-up settings drive the prose. Follow-up exports distinguish inherited
screening from new execution.

Review the Methods text, database/model identities and tool/database citations
before submission. Exports contain sample identifiers and local paths. Exporting
does not run tools, change settings, install environments or download databases.

Created by **Jimi Goldstein · Human Link**. ORCID: https://orcid.org/0000-0002-7829-0090.

Bactologix is a Python/Streamlit application for local HybAs AWARE and standalone BaSIS AWARE setup, version selection, configuration, sample registration, isolated run drafts, durable jobs, and HTML reports. This version executes real operations. Published wheels and source archives are listed in [GitHub Releases](https://github.com/crackberryq/Bactologix/releases). Bactologix is not published to PyPI.

**Alpha release:** the approved HTML interface now runs inside Streamlit with working Python controls. Software checks, real Chromium-to-Streamlit checks and native workflow dry-runs passed; full biological analyses and target-workstation browser testing remain outstanding. Read [VALIDATION.md](VALIDATION.md) and the alpha boundaries below before running samples.

Use **Update Bactologix → Refresh releases** to see published updates, including alpha versions. Development commits and pull requests are not releases and do not appear in that list. Check [GitHub Releases](https://github.com/crackberryq/Bactologix/releases) for available installable versions.

## Install on Linux or WSL2

Install Python 3.11+ and Git. Download the wheel, then use its actual path:

```bash
pipx install ./bactologix-0.1.0a27-py3-none-any.whl
bactologix
```

Your base Python can be any supported Python 3.11+ (including 3.13). The separate pipeline runners install their own Python 3.11. If pipx is missing in your Conda base, install it first:

```bash
python -m pip install pipx
python -m pipx ensurepath
python -m pipx install --force "https://github.com/crackberryq/Bactologix/releases/download/v0.1.0a27/bactologix-0.1.0a27-py3-none-any.whl"
"$HOME/.local/bin/bactologix"
```

To use a Conda environment named `bactomix`, create it once if it does not already exist:

```bash
conda create -n bactomix python=3.11 pip -y
conda activate bactomix
python -m pip install --upgrade "https://github.com/crackberryq/Bactologix/releases/download/v0.1.0a27/bactologix-0.1.0a27-py3-none-any.whl"
bactologix
```

For an alternative dedicated Python environment:

```bash
python3 -m venv ~/.venvs/bactologix
~/.venvs/bactologix/bin/python -m pip install ./bactologix-0.1.0a27-py3-none-any.whl
~/.venvs/bactologix/bin/bactologix
```

After installation, activate the environment containing Bactologix and run:

```bash
conda activate bactomix
bactologix
```

The installed `bactologix` command starts the application, waits for the server to report readiness and pass its health check, then opens the browser. Keep the terminal open; **Ctrl+C** stops the web UI. On WSL it opens the Windows default browser and detects the current private address, avoiding stale IP addresses and broken localhost forwarding. Mirrored WSL networking uses loopback. On a Linux desktop or macOS it uses the default browser. If automatic opening is unavailable, the terminal prints the address and the server remains available.

The default port is 8501, with automatic selection through 8520 if occupied. `bactologix --no-browser` starts without opening a browser. `--host` and `--port` override the detected address and port; an occupied explicit port produces an error. The launcher does not kill other processes or modify Windows network/browser settings. Starting the command again starts another UI server on a free port; close the previous one with Ctrl+C when manually starting a replacement session.

Automatic source watching is disabled for the packaged app, preventing pip from triggering a rerun halfway through an update. `python -m bactologix.cli` remains available as an equivalent entry point. On Windows, run the command inside WSL2; use Linux paths such as `/home/jimi/...` or `/mnt/c/...` in the app. Native Windows pipeline execution is not supported. Windows browser opening requires WSL interoperability; if it is unavailable, open the printed URL manually.

This is a local, single-user tool, not a public server. Ordinary Linux/macOS launches bind to `127.0.0.1`; WSL NAT launches bind to the detected private interface rather than all interfaces. `--home /absolute/workspace` selects a workspace; the default is the platform user data directory. `BACTOLOGIX_HOME` also selects it. Jobs persist in SQLite and logs remain on disk. Closing the browser or stopping the UI does not cancel a background job; use Jobs & progress to cancel it.

## Update Bactologix

Use **Update Bactologix** in the top bar or **Install & update**. Click **Refresh releases** when checking for a new update. The dialog retrieves published GitHub releases, including prereleases, and shows the running version, release notes and application environment. Select the version you want, then click **Install v…**. Older releases are labelled. A release needs the expected wheel and GitHub SHA-256 digest to be installable.

Finish or cancel queued/running jobs first. The updater checks the asset again, verifies its size, checksum, package identity and Python requirement, then runs pip through the exact Python interpreter serving Bactologix. The job log records installation progress. Once installation starts it cannot be cancelled from the app. An environment lock also prevents an update from overlapping another Bactologix worker using that Python environment. These locks do not coordinate unrelated external pip/Conda commands.

After a successful, verified update, the `bactologix` launcher restarts the application on the **same address and port**. Keep the terminal and browser tab open: the tab refreshes automatically after the new server identifies itself and passes its health check. The launcher waits for the update job to succeed and the queue to become idle. Failed installation or fresh-import verification keeps the old server available with the log and manual restart guidance. A restart does not open a duplicate browser tab.

**One-time transition from a18 or earlier:** the already-running launcher cannot gain this feature until restarted. After installing a19 or later, press **Ctrl+C**, then run **`bactologix`** once (retain custom `--home`, `--host` or `--port` options). Later updates use automatic restart and refresh. Manually launched Streamlit sessions still show manual restart instructions. Downgrading to a release without the new server identity requires a manual browser refresh.

Pipeline runners, databases, settings, cached tool environments and run folders are separate from the application package. Pip may update application dependencies when required by the selected release.

For **v0.1.0a5 and earlier**, install this release once using the wheel command above; the update button becomes available after restarting. If BaSIS `f66dfedd` was already downloaded, open **Install & update → BaSIS → Finish setup**. This uses the downloaded source and creates or reuses its runner. The runner environment definition is unchanged from the earlier supported BaSIS revision. Read **Setup log** if setup fails; **Retry setup** reuses an existing runner, whereas **Repair runner** creates a separate replacement.

Compatible snapshots remain selectable even after a branch moves. Unsupported downloads say **Version not supported** and link to the app updater; failed setup says **Setup incomplete** with its job log. Downloading a supported version normally completes runner setup in the same job.

## PC capacity and power presets

Open **Install & update → PC capacity**. Detection reports resources visible to the Linux process, including WSL/container limits, logical/physical CPU counts where available, RAM and available memory, swap, workspace disk space, NVIDIA GPUs/VRAM and driver/toolkit details. **Detect again** refreshes the snapshot. NVIDIA's driver CUDA capability is separate from the `nvcc` toolkit version; neither guarantees compatibility with a tool environment. PCI graphics information is included when `lspci` is available. Probes time out and unavailable information is labelled.

Choose one pipeline or both, then **Use High power**, **Use Medium**, or **Use Low**. These buttons save resource values in the default YAML and in the CPU/RAM budgets used by future run drafts. High uses all available CPU threads (up to 512). RAM starts from the lower of total capacity and currently available RAM, reserves at least 1 GB or 5% of capacity, and rounds down to whole GB within the app limit of 2,000,000 MB. Medium uses half the threads and 60% of that RAM budget; Low uses a quarter of the threads and 30%, with a 1 GB minimum when available. The UI shows the exact budget before applying it.

Presets set execution budgets; they do not guarantee full utilization or that every database/tool will fit. HybAs assembly, SPAdes and polishing budgets are aligned with the selected RAM. Existing database-derived requirements, GPU settings, enabled tools and scientific thresholds are preserved. GPU details are informational: there is no generic CUDA toggle that makes every tool GPU-capable. Saved runs keep their original configuration; **Edit run settings** creates a revised draft when needed. Ordinary Configuration edits made in another window are protected by revision checks.

## Check and repair tool environments

Each supported pipeline in **Install & update → Pipelines & environments** has **Install environments**, **Check environments**, and **Repair environments**. No sample or run draft is required. Checking is read-only and includes the native executable checks plus actual NanoPlot scatter/density HTML and PNG rendering when NanoPlot is part of the recipe.

Repair checks each selected environment first. Healthy environments are retained. Missing environments are deployed using the reviewed native recipe. Incomplete environments are retained under `<cache>/.bactologix-repair-backups/<job>/<recipe>` before native deployment; a failed or cancelled rebuild restores the previous incomplete environment. Backups are not automatically removed. Unknown failures in complete environments are reported for diagnosis without deleting those environments.

For the known shared QC conflict, repair recognizes **NanoPlot 1.46.2 + MultiQC 1.32** and runs a constrained Conda transaction for **Plotly 6.1.2 + python-kaleido 0.2.1**, then repeats the native and rendering checks. Package versions before and after the attempt are saved in `<workspace>/maintenance/<job>/<recipe>-repair.json`. This locally tested rendering workaround retains MultiQC's required Kaleido version; NanoPlot's upstream declared Kaleido requirement still differs, so the native QC recipe needs a future upstream dependency split. Other tool versions are not silently upgraded or patched.

Repairs run through the serialized job worker. Shared runs using the repaired cache see those package changes; frozen source and YAML files are preserved. Do not run external workflows or Conda mutations against that cache while repair is active. Normal **Run / resume** still never installs or repairs environments automatically.

## First use

1. **Install & update:** use **Connect existing** for a local checkout, or choose a version to download and set up. Bactologix reads the selected release's pinned Python and Snakemake requirements from its environment file, creates or reuses a separate runner, and verifies it before activation. If Conda is missing, managed setup first installs workspace-local Miniforge automatically. Streamlit is isolated from these environments.
2. **Samples:** choose **Add sample → Browse folders**, navigate to the sample folder and select it, or import CSV/TSV columns `sample_id,input_folder,read_mode`. HybAs uses `raw/illumina` and `raw/nanopore`. BaSIS needs `annotation/<sample_id>.fna`, `.faa`, `.gff`, plus `.gbk` when enabled modules require it.
   **Remove sample** is available in Registered samples and in the run-draft sample picker. It unregisters the folder and moves its inactive saved runs from **Current runs** to **Archived**. Queued/running runs remain visible. Files, frozen configuration, jobs and reports are preserved. Use **Restore** to bring a run back, or **Add sample** to register the folder again. Individual runs also have **Remove from list**.
3. **Configuration:** use the same category tabs, mode cards and switches as the draft editor. **Advanced** provides searchable sections and **Full YAML** for custom keys; YAML import/export and **Check settings** remain available. Each pipeline retains its own unsaved edits while switching tabs. **Save defaults** applies the configuration to future drafts and preserves existing runs. **Reload saved defaults** discards local edits. A stale editor cannot overwrite newer saved settings or database selections; download your edits before reloading if you need to keep them. Choose database/model locations in **Databases & models**.
4. **Runs → Create run drafts:** select samples, open **Run settings**, and customize the new drafts before creating them. Use category tabs and mode cards for Kraken/AWARE, Assembly, Quality and Databases; BaSIS has Modules, AWARE, DeepSafe and Resources. **Advanced** holds the searchable full field editor and **Full YAML** for custom keys. Finish in **Review** to check the selected samples and key settings; **Check settings** validates the editor input. Switching pipelines retains independent edits. **Reload saved defaults** resets this dialog to the currently saved configuration. Selected samples receive the same settings; create separate requests for different configurations. Saved defaults and existing runs are preserved. Each **saved run page** has the same category tabs for reading its saved values. Choose **Edit settings** to use the guided editor, then review and create a separate revised draft; the original run remains intact. Raw commands and YAML are under **Advanced**. Each draft copies workflow source, references only input files, writes a frozen resolved config, and has independent outputs and scheduler state. CPU/memory budgets are set above the editor; generated input/output paths are shown as managed settings. Native defaults shown for omitted settings are only added when edited, so opening the editor does not enable extra modules. Run **Validate run** first. Read its actual log, then prepare analysis tool environments or **Run / resume**.
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

## Find your work

- **Overview** shows active jobs, recent failures, recent runs and pipeline setup. It suggests a first setup/sample/draft action when needed.
- **Samples** contains registered input folders. Search by sample or path, create a draft for one sample, or open that sample's runs. Removing a sample retains its files and archives inactive saved runs.
- **Runs** contains current and archived drafts/history. Search by name, sample or run ID and filter by pipeline/status. Batch actions use the selected runs; selections outside the current filter are explicitly counted. Reset filters does not clear your selection.
- **Comparisons** combines saved-setting comparisons and variant creation. Select runs to compare their configuration and provenance, or choose a HybAs parent for independent variant drafts. Native reports remain the source of scientific results.

Open a run to use its dedicated page. Settings categories, **Activity** and **Results** stay with that run, and the URL records the selected run and section. Browser Back, Forward and refresh restore that location; unknown run links show a recovery action. Single-run validation/preparation/execution opened from this page switches to its Activity section. Batch jobs open Jobs & progress. Settings edits still create a separate revised draft, preserving the parent.

URLs contain only the page, run ID and section, not configuration or input paths. Search filters, unsaved editor changes and comparison selections are session state, not saved in the URL. A run link works only in a workspace that contains that run. **Run software check** is available in Install & update.

## Run checks and environment availability

Saved-run **Overview → Run checks** separates inputs/saved files, pipeline/runner setup, native validation, frozen database paths and tool environment availability. The panel reports the scope and time of each check. **Runner set up** on an installation card refers to the runner only; it does not mean every analysis tool or database is ready. **Validate run** performs the native dry-run. Saved database paths remain visible under **Databases**, independently of later default changes.

After validation, **Check environments** asks the native workflow for its current environment inventory and checks completed folders in this run's own cache. This action never installs, deletes or repairs tool environments. The receipt is specific to the frozen configuration, source, runner and cache; completion markers are rechecked when the panel refreshes. It does not run executable probes or guarantee environments that later checkpoints may discover. Use the installation-level checker below for executable probes. Known missing/incomplete environments direct you to **Install environments**; normal execution retains its existing-environments-only guard.

Jobs show focused recovery guidance for recognized missing database, input-change and missing-environment failures, with the original full log still available. Active job counts refresh across workspace pages without interrupting focused fields or editing dialogs.

## Check and install tool environments

Each ready pipeline card in **Install & update** has two direct actions:

- **Install environments** opens the software selection. Choose the tools, then click **Install selected**. No sample or run draft is required. Existing matching environments are reused automatically; missing or changed requirements are prepared using the native recipes.
- **Check environments** opens a read-only check. Choose the tools, then click **Check selected**. Snakemake identifies the exact paths, checks deployment completion and runs the installed tools' executable/version probes. Missing, incomplete and failed environments remain distinct from verified ones.

**Reuse is automatic.** New pipeline installations inherit the previous active version's environment folder; retries retain their existing folder. Snakemake determines whether the environments match. There is no separate reuse button and no broad scan of unrelated Conda installations. The selected location remains visible on the setup card. Use **Check environments** for fresh evidence of which tools work.

**Normal runs never create, repair or replace Snakemake tool environments.** They use matching completed environments in the saved cache. If an updated recipe has no matching environment, the run stops with its missing path and an explicit installation choice. Use **Install environments** on a saved run or **Install selected** in the environment panel to authorize deployment. Dry-runs remain available before installation. Snakemake's native environment hashes still determine compatibility; a different recipe is not forced into an old environment. This policy also applies to environments discovered by checkpoints and Snakemake subprocesses.

**Advanced: environment storage → Environment cache folder → Browse → Save folder** changes storage or selects an external Snakemake cache. This section is collapsed by default. Save any folder change before checking or installing; unsaved changes disable those actions. Existing run drafts retain their original cache. Snakemake 9.6.2 invokes deployment scripts with an unquoted cache path, so choose a cache path without spaces or shell characters; Bactologix checks this before requesting installation. Source, workspace and database destination folders may contain spaces.

**Install selected** deploys only the selected environment recipes, then checks them. No samples, analyses or database download targets are scheduled. Optional environments remain optional for analysis. Compatible environments are reused from the installation's configured cache. A completed environment whose tools fail checks is retained for diagnosis; this control does not delete it. Follow progress in **Jobs & progress**, then reopen the environment panel for status and check time.

## Uninstall an older pipeline version

In **Install & update**, click **Uninstall version** and review the exact source folder and retained content before confirming. Only an inactive, unused managed source snapshot and its installation entry are removed. Shared runners, tool caches, databases, saved runs, results, logs and original connected checkouts remain on disk. This action does not reclaim their storage.

Versions referenced by any saved run (including archived runs) or unfinished database download cannot be removed. Queued/running jobs also block uninstall. Modified, untracked or ignored source files and sources containing retained caches or checkouts must be preserved first. The app rechecks these conditions at confirmation; stale previews cannot remove a changed installation.

## Manage databases without editing YAML

Discovery includes registered Conda prefixes, pipeline resource folders, hidden Snakemake caches and bounded directory symlink traversal. The scan lists recognizable but incomplete locations with the missing files. Broad scans still have time, folder and depth limits; supply a specific folder if needed.

Open **Databases & models**, choose **HybAs** or **BaSIS**, and click **Auto scan**. Every selectable resource has its own name, location, **Browse**, and **Check location** controls. Detected installations appear in a dropdown. Add a specific scan folder to include another disk or a deeper directory; Windows Explorer paths work in WSL.

The scan preselects a preferred candidate for each unchanged field. It keeps a valid saved location first, then prefers a matching configured BUSCO lineage or a completed BaSIS resource receipt. Remaining ties use a stable path order, not a claim that a particular release is scientifically best or newest. A choice you edited yourself is retained. Review the selected release and lineage, then click **Save for future runs**. The server rechecks every changed selection before saving; stale configuration revisions and incomplete locations are rejected. **Reload locations** discards unsaved resource choices. **Use pipeline default** resets only that resource's owned path/metadata fields when saved.

- **HybAs:** Kraken2 and BUSCO may live on different disks. Bactologix creates a separate local link layout for each selection, writes the native shared-root configuration, and preserves earlier layouts for frozen run drafts. Database contents are neither copied nor downloaded.
- **BaSIS:** named entries cover AMRFinderPlus, CARD/RGI, VFDB, BacMet, antiSMASH, geNomad, MOB-suite, MLST/PubMLST, CRISPRCasFinder/CASFinder, PROSITE, ProtT5/classifier models, and the deep reference bundle. Folder selection resolves native file-backed resources such as CARD JSON and DIAMOND indices. Completed resource receipts retain allowlisted companion paths and recorded identities; executable overrides and analysis switches are not imported. Model/reference bundles require their companion files; a filename alone does not establish their identity.
- **Tool-managed resources:** Prokka, IntegronFinder, ISEScan and ICEscreen use resources inside their tool environments in the pinned adapters. They are labelled separately because a custom folder would not redirect those tools. MinCED needs no external database.

Discovery is a quick structural check, not a full checksum verification or compatibility guarantee. It scans configured locations, registered pipeline sources/caches, workspace resources and common folders, with limits of 5,000 directories, 3,000 entries per folder, seven levels and an eight-second traversal budget. The result reports reached limits and inaccessible folders. Directory symlinks are inspected without recursive traversal; hidden directories are skipped unless included as explicit roots. This is not an exhaustive inventory of every disk. The native workflow still performs its resource checks during preparation.

Saving affects future run drafts and preserves other analysis settings. To update an existing draft, open its details and click **Use saved databases**, or click **Update saved drafts** on this page to choose several runs. Review current/saved paths, select the valid changes, and confirm. A revised draft keeps the original inputs, pipeline version, environment cache, resource budgets and other settings; the selected paths are written into the configuration used by its command. A single revised draft opens immediately: click **Validate run** before preparing tools or running it. Active runs and reviewed follow-up requests cannot use this action.

Original drafts keep their frozen paths; independently changing database contents at those paths is outside this selection mechanism. Missing BaSIS resources continue to follow the saved `auto`/`manual` policy. Scanning and saving do not install environments or download databases. Use the separate download/preparation control to request preparation.

## Download databases to a chosen location

Each selectable database card has **Download and prepare**, or **Download and build** for Kraken2. Choose a writable parent folder with **Browse**, set threads/memory, and select the lineage for BUSCO. Bactologix creates a fresh subfolder, freezes a separate administrative configuration and invokes only the pinned pipeline's native database preparation target. Kraken2 uses the native standard database build, which can require substantial memory, storage and time. BaSIS uses its resource provider and required dependencies. Tool-managed resources are prepared through their tool environment instead.

The review dialog explains that native preparation may first install required tool environments. Downloads require the current recommended supported source snapshot and a ready runner. They do not use sample inputs or change saved analysis defaults. Native commands, receipts and provider logs remain available on disk; **Download log** includes recent provider output. A stopped or failed download offers **Resume download**, preserving its original destination, configuration and source identity. The native provider determines which partial work can be reused.

When preparation succeeds, click **Select downloaded location**, then **Save for future runs**. Paths are checked again at selection and saving. Earlier run drafts keep their frozen paths.

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

Controller environments are keyed by pipeline, environment-file bytes and platform. Each reviewed release must explicitly pin Python (minor series or patch) and a complete Snakemake version in its Conda environment file. Setup installs those requirements, rather than whatever happens to be the newest Snakemake. The verified versions appear on the installation card and in the setup log. A changed definition creates a separate runner; a failed installation/check never activates the new version. Existing controllers are health-checked against the selected release before reuse. Repair prepares a fresh controller and retains the old one. Existing drafts use the repaired controller for the same source commit, and require a fresh native dry-run before preparation or execution. Their original controller path remains recorded as creation provenance. Snakemake uses a stable absolute `--conda-prefix` per pipeline outside version-specific source folders; its native content/pin/deploy-script hashing governs rule environment reuse. No environment or old run is routinely deleted. Database/model storage is separate. BaSIS's configured `resources.policy: auto` can download missing resources during an actual run. HybAs database provisioning remains the upstream explicit workflow/configuration responsibility.

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

## Publishing application updates

After a version bump and changelog entry reach `main`, the software workflow publishes the wheel and source archive only after both Python matrices, Chromium and environment deployment succeed. The publication job uses the tested Python 3.11 artifact, uploads to a draft, verifies GitHub asset digests using the application updater’s acceptance rules, and publishes it with alpha/beta/RC versions marked as prereleases. Existing published assets are never replaced; a new update needs a new package version. Workflow dispatch on `main` can retry a failed publication. Development branches do not publish releases.
