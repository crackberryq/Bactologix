"""Version-pinned adapters with isolated outputs and immutable configurations."""
from __future__ import annotations
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import yaml
from .catalog import DATA, PIPELINES, supported
from .store import identifier, now
from . import __version__


def parse_config(text):
    if len(text.encode()) > 2 * 1024 * 1024:
        raise ValueError("Configuration exceeds 2 MB")
    cfg = yaml.safe_load(text)
    if not isinstance(cfg, dict) or not all(isinstance(k, str) for k in cfg):
        raise ValueError("Configuration must be a YAML mapping with string keys")
    # Avoid recursive aliases, non-JSON scalars and infinite nesting in saved state.
    try:
        encoded = json.dumps(cfg, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Use ordinary YAML mappings, lists and scalar values; no recursive aliases or special values") from exc
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("Expanded configuration exceeds 4 MB")
    return cfg


def template(tool):
    return (DATA / (tool + ".config.yaml")).read_text()


def valid_id(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value) or value in {".", ".."}:
        raise ValueError("Sample ID must use letters, numbers, underscores, dots or dashes (up to 100 characters)")
    return value


def absolute_path(value):
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an absolute Linux path (or ~/…). Windows users should use WSL paths.")
    path = path.resolve()
    if any(ch in str(path) for ch in "{}\n\r\t"):
        raise ValueError("Paths cannot contain braces, tabs or newlines")
    return path


def register_sample(store, sid, path, mode="hybrid"):
    valid_id(sid)
    folder = absolute_path(path)
    if not folder.is_dir():
        raise ValueError("Sample folder does not exist: " + str(folder))
    if mode not in {"hybrid", "ont_only", "illumina_only"}:
        raise ValueError("Unsupported read mode")
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        matches = [json.loads(row[0]) for row in db.execute("SELECT body FROM items WHERE kind='samples'")]
        matches = [s for s in matches if s['id'].casefold() == sid.casefold()]
        if matches:
            if matches[0]['path'] == str(folder) and matches[0]['mode'] == mode:
                return matches[0]
            raise ValueError('That sample ID is already registered with different inputs')
        row = {'id': sid, 'path': str(folder), 'mode': mode, 'created': now()}
        db.execute("INSERT INTO items(kind,id,body) VALUES ('samples',?,?)", (sid, json.dumps(row)))
    return row


def remove_sample(store, sid, expected):
    """Unregister a sample and archive its idle runs without changing run records or files."""
    if not isinstance(sid, str) or not isinstance(expected, dict):
        raise ValueError("Choose a registered sample to remove.")
    valid_id(sid)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT body FROM items WHERE kind='samples' AND id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("This sample is no longer registered. Refresh the sample list.")
        if json.loads(row[0]) != expected:
            raise ValueError("This sample registration changed. Refresh the sample list before removing it.")
        active = _active_run_ids(db)
        archived, kept = [], []
        for row in db.execute("SELECT body FROM items WHERE kind='runs'").fetchall():
            run = json.loads(row[0])
            sample = run.get("sample", {})
            if sample.get("id") != sid or sample.get("path") != expected["path"]:
                continue
            if run["id"] in active:
                kept.append(run["id"])
            elif not _is_archived(db, run["id"]):
                _set_archive(db, run["id"], True, "sample removed")
                archived.append(run["id"])
        db.execute("DELETE FROM items WHERE kind='samples' AND id=?", (sid,))
    return {"archived": archived, "active": kept}


def _active_run_ids(db):
    rows = db.execute("SELECT payload FROM jobs WHERE kind='workflow' AND status IN ('queued','running')").fetchall()
    return {json.loads(row[0]).get("run_id") for row in rows}


def _is_archived(db, rid):
    row = db.execute("SELECT body FROM items WHERE kind='run_visibility' AND id=?", (rid,)).fetchone()
    return bool(row and json.loads(row[0]).get("archived"))


def _set_archive(db, rid, archived, reason):
    body = {"id": rid, "archived": archived, "changed": now(), "reason": reason}
    db.execute("INSERT INTO items VALUES ('run_visibility',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
               (rid, json.dumps(body)))


def archive_runs(store, ids, archived=True):
    """Change list visibility atomically; frozen provenance, reports and files are retained."""
    if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(rid, str) for rid in ids):
        raise ValueError("Select between one and 500 saved runs.")
    ids = list(dict.fromkeys(ids))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        active = _active_run_ids(db)
        for rid in ids:
            if not db.execute("SELECT 1 FROM items WHERE kind='runs' AND id=?", (rid,)).fetchone():
                raise ValueError("The selected run no longer exists.")
            if archived and rid in active:
                raise ValueError("Queued or running jobs stay visible. Wait for them to finish before archiving.")
        for rid in ids:
            _set_archive(db, rid, archived, "removed from list" if archived else "restored")
    return ids


def import_samples(store, text):
    from .sample_imports import commit
    return commit(store, text)


def snapshot_files(paths):
    result = []
    for path in sorted(set(Path(p).resolve() for p in paths)):
        stat = path.stat()
        result.append({"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return result


def check_inputs(run):
    for expected in run["inputs"]:
        path = Path(expected["path"])
        try:
            stat = path.stat()
        except OSError as exc:
            raise ValueError("Input is no longer accessible: " + str(path)) from exc
        if stat.st_size != expected["size"] or stat.st_mtime_ns != expected["mtime_ns"]:
            raise ValueError("Input changed since this draft was created: " + str(path) + ". Create a new draft.")


def input_layout(output, tool):
    """Record the paths actually consumed by the workflow, including link targets."""
    output = Path(output)
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Staged input directories must not be replaced with links")
    roots = ("raw/illumina", "raw/nanopore") if tool == "hybas" else ("annotation",)
    layout = []
    for rel in roots:
        root = output / rel
        if root.is_symlink() or root.parent.is_symlink() or output.is_symlink():
            raise ValueError("Staged input directories must not be replaced with links")
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            used = re.search(r"\.(fastq|fq)(\.gz)?$", path.name, re.I) if tool == "hybas" else path.suffix in {".fna", ".faa", ".gff", ".gbk"}
            if used:
                if not path.is_symlink():
                    raise ValueError("Staged input is no longer its original link: " + str(path))
                layout.append({"path": str(path.relative_to(output)), "target": str(path.resolve())})
    return sorted(layout, key=lambda row: row["path"])


def sample_sheet(sample, tool, output):
    if tool == "hybas":
        return "isolate\n" + sample["id"] + "\n"
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=["sample_id", "isolate_dir", "hybas_dir", "input_mode"], delimiter="\t")
    writer.writeheader()
    hybas_layout = Path(sample["path"]).name == sample["id"]
    writer.writerow({"sample_id": sample["id"], "isolate_dir": str(output),
                     "hybas_dir": sample["path"] if hybas_layout else "",
                     "input_mode": "auto" if hybas_layout else "external"})
    return handle.getvalue()


def check_run_files(run):
    folder = Path(run["folder"])
    if hashlib.sha256((folder / "config.yaml").read_bytes()).hexdigest() != run["config_sha256"]:
        raise ValueError("Saved configuration changed. Create a new draft for changed settings.")
    if run.get("run_type") == "followup":
        check_inputs(run)
        if input_layout(run["output"], run["tool"]) != run["input_layout"]:
            raise ValueError("Staged screening inputs changed")
        for expected in run["followup_sources"]:
            path = Path(expected["path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
                raise ValueError("Saved screening prerequisite changed: " + str(path))
        return
    expected_sheet = sample_sheet(run["sample"], run["tool"], run["output"]).encode()
    if (folder / "samples.tsv").read_bytes() != expected_sheet:
        raise ValueError("Saved sample sheet changed. Create a new draft.")
    check_inputs(run)
    expected = run.get("input_layout")
    if expected is None:
        # Older drafts have resolved source identities but no staged-link manifest.
        # Reconstruct only from those identities; never trust the current staged links.
        source = Path(run["sample"]["path"])
        paths = list((source / "raw").glob("*/*")) if run["tool"] == "hybas" else list((source / "annotation").glob("*"))
        recorded = {row["path"] for row in run["inputs"]}
        expected = sorted(({"path": str(p.relative_to(source)), "target": str(p.resolve())}
                           for p in paths if p.is_file() and str(p.resolve()) in recorded), key=lambda row: row["path"])
        if not expected:
            raise ValueError("Cannot verify this older draft's input layout. Create a revised draft.")
    if input_layout(run["output"], run["tool"]) != expected:
        raise ValueError("Staged inputs changed since this draft was created. Create a new draft.")


def current_controller(store, run):
    installed = store.get("installs", run["installation"])
    if not installed or not installed.get("ready") or not installed.get("controller"):
        raise ValueError("The pipeline runner needs setup or repair")
    if installed["tool"] != run["tool"] or installed["version"]["commit"] != run["commit"]:
        raise ValueError("The installation no longer matches this run's source version")
    return installed["controller"]


def dry_run_current(store, run):
    try:
        return bool(run.get("dry_run_ok") and run.get("validated_controller", run["controller"]) == current_controller(store, run))
    except ValueError:
        return False


def run_status(run, jobs, checked):
    related = [j for j in jobs if j["kind"] == "workflow" and j["payload"].get("run_id") == run["id"]]
    active = next((j for j in related if j["status"] in {"queued", "running"}), None)
    if active:
        return active["status"]
    execution = next((j for j in related if j["payload"].get("action") == "run"), None)
    failures = {"failed", "cancelled", "interrupted"}
    if execution and execution["status"] in failures:
        return execution["status"]
    if related and related[0]["status"] in failures:
        return related[0]["status"]
    return "completed" if run.get("completed") else "checked" if checked else "draft"


def stage_inputs(sample, tool, destination):
    original = Path(sample["path"])
    sid = sample["id"]
    inputs = []
    destination.mkdir(parents=True)
    if tool == "hybas":
        (destination / "raw").mkdir()
        modes = {"hybrid": ("illumina", "nanopore"), "ont_only": ("nanopore",), "illumina_only": ("illumina",)}
        for mode in modes[sample["mode"]]:
            source = original / "raw" / mode
            files = sorted(p for p in source.glob("*") if p.is_file() and re.search(r"\.(fastq|fq)(\.gz)?$", p.name, re.I))
            if not files:
                raise ValueError(f"No FASTQ files found in {source}. Expected raw/illumina and/or raw/nanopore.")
            # Link each input file into a new directory; native outputs never point at an original run.
            output = destination / "raw" / mode
            output.mkdir()
            for file in files:
                (output / file.name).symlink_to(file.resolve())
            inputs.extend(files)
    else:
        output = destination / "annotation"
        output.mkdir()
        for extension in ("fna", "faa", "gff", "gbk"):
            source = original / "annotation" / (sid + "." + extension)
            if not source.is_file():
                if extension == "gbk":
                    continue
                raise ValueError("Missing annotation input: " + str(source))
            (output / source.name).symlink_to(source.resolve())
            inputs.append(source)
        inputs.extend(p for p in (original / "metadata").glob("*") if p.is_file())
        final = original / "work/final/assembly.final.fasta"
        if final.is_file():
            inputs.append(final)
    return snapshot_files(inputs)


def tree_digest(folder):
    digest = hashlib.sha256()
    for path in sorted(Path(folder).rglob("*")):
        rel = path.relative_to(folder)
        if any(part in {".git", ".snakemake", "__pycache__"} for part in rel.parts) or path.suffix == ".pyc":
            continue
        if path.is_file():
            digest.update(str(rel).encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def bind_paths(cfg, tool, store, installed):
    # Relative user resource paths would otherwise change when making isolated copies.
    cfg = copy.deepcopy(cfg)
    if tool == "hybas":
        raw = cfg.get("db_root", "db")
        if Path(str(raw)).expanduser().is_absolute():
            cfg["db_root"] = str(absolute_path(raw))
        elif str(raw) == "db":
            cfg["db_root"] = str(store.home / "resources/hybas/db")
        else:
            raise ValueError("Set db_root to an absolute path to your shared databases")
    else:
        resources = cfg.setdefault("resources", {})
        if not isinstance(resources, dict):
            raise ValueError("resources must be a mapping")
        raw = resources.get("directory", "resources")
        if str(raw) == "resources":
            resources["directory"] = str(store.home / "resources/basis")
        else:
            resources["directory"] = str(absolute_path(raw))
        storage = cfg.get("storage", {})
        if not isinstance(storage, dict):
            raise ValueError("storage must be a mapping")
        if "native_results_directory" in storage:
            storage["native_results_directory"] = str(absolute_path(storage["native_results_directory"]))
        # These keys are sourced from the standalone BaSIS PATH_KEYS contract.
        keys = {"database", "database_manifest", "result", "profiles_dir", "reference_fasta", "reference_metadata",
                "prot_t5_path", "weights_model", "embeddings", "embeddings_manifest", "db_proteins", "metadata",
                "domain_database", "tree_review_registry", "motif_rules", "prosite_snapshot", "placement_registry", "placement_cache_dir"}
        for section in list(cfg.get("modules", {}).values()) + [cfg.get("deep", {})]:
            if isinstance(section, dict):
                for key in keys:
                    if section.get(key):
                        section[key] = str(absolute_path(section[key]))
    return cfg


def create_run(store, tool, sample, config_text, cores=4, memory_mb=16000, parent=None, label=None, installation=None, expected_inputs=None):
    spec = PIPELINES[tool]
    install_id = installation or store.get("settings", "active-" + tool)
    installed = store.get("installs", install_id)
    if not installed or not installed.get("ready") or not supported(tool, installed["version"]["commit"]):
        raise ValueError("Set up a supported pipeline runner first")
    cores, memory_mb = int(cores), int(memory_mb)
    if cores < 1 or memory_mb < 512:
        raise ValueError("Choose at least one core and 512 MB memory")
    cfg = bind_paths(parse_config(config_text), tool, store, installed)
    rid = identifier("run")
    folder = store.home / "runs" / rid
    folder.mkdir()
    try:
        source = Path(installed["source"])
        actual_commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip()
        if actual_commit != installed["version"]["commit"] or dirty:
            raise ValueError("Installed source changed. Use a clean source installation.")
        shutil.copytree(source, folder / "workflow", ignore=shutil.ignore_patterns(".git", ".snakemake", "__pycache__", "*.pyc"))
        output = folder / "outputs" / sample["id"]
        inputs = stage_inputs(sample, tool, output)
        if expected_inputs is not None and inputs != expected_inputs:
            raise ValueError("Original inputs differ from the parent run. Register changed data as a new run, not a comparison variant.")
        cfg["threads"] = cores
        if tool == "hybas":
            cfg.update(base_dir=str(folder / "outputs"), sample_sheet=str(folder / "samples.tsv"), setup_only=False, admin_mode=False, ci_mode=False)
            cfg.pop("isolate", None)
        else:
            cfg.update(bactomics_root=str(folder / "outputs"), samples=str(folder / "samples.tsv"), memory_mb=memory_mb, isolates=[])
            if 'execution' in cfg:
                if not isinstance(cfg['execution'], dict):
                    raise ValueError('The execution settings must be a mapping.')
                cfg['execution'].update(cores=cores, memory_mb=memory_mb)
            cfg.pop("isolate", None)
        (folder / "samples.tsv").write_bytes(sample_sheet(sample, tool, output).encode())
        text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
        (folder / "config.yaml").write_text(text)
        (folder / "submitted.yaml").write_text(config_text)
        run = {"id": rid, "tool": tool, "sample": copy.deepcopy(sample), "folder": str(folder), "output": str(output),
               "installation": install_id, "controller": installed["controller"], "commit": installed["version"]["commit"],
               "rule_cache": installed.get("rule_cache") or str(store.home / "envs" / tool),
               "config": text, "submitted": config_text, "config_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "workflow_sha256": tree_digest(folder / "workflow"), "inputs": inputs, "input_layout": input_layout(output, tool), "cores": cores, "memory_mb": memory_mb,
               "parent": parent, "label": label or sample["id"], "created": now(), "dry_run_ok": False,
               "bactologix_version": __version__,
               "reuse": "input-files-only; workflow outputs are independent"}
        (folder / "bactologix-run.json").write_text(json.dumps(run, indent=2) + "\n")
        store.register_run(run)
        return run
    except BaseException:
        shutil.rmtree(folder)
        raise


def clone_runs(store, parent_id, aware_free=False, strategies=None):
    parent = store.get("runs", parent_id)
    if not parent or parent["tool"] != "hybas":
        raise ValueError("Choose a HybAs AWARE parent run")
    check_run_files(parent)
    cfg = parse_config(parent["submitted"])
    if aware_free:
        cfg.update(kraken_decision_mode="off", run_kraken=False, require_target_taxid_for_kraken=False)
    created = []
    for strategy in strategies or [cfg.get("polish_mode", "tripolish")]:
        if strategy not in {"none", "racon", "medaka", "tripolish"}:
            raise ValueError("Unsupported polishing strategy")
        if parent["sample"]["mode"] == "illumina_only" and strategy not in {"none", "tripolish"}:
            raise ValueError("Illumina-only variants support Baseline or Polypolish (the native tripolish route)")
        cfg["polish_mode"] = strategy
        label = parent["sample"]["id"] + (" · AWARE-free" if aware_free else " · clone") + " · " + strategy
        created.append(create_run(store, "hybas", parent["sample"], yaml.safe_dump(cfg, sort_keys=False),
                                  parent["cores"], parent["memory_mb"], parent_id, label, parent["installation"], expected_inputs=parent["inputs"]))
    return created


def validate_launch(store, rid, action, check_busy=True):
    if action not in {"dry-run", "prepare", "run", "check-environments"}:
        raise ValueError("Unsupported workflow action")
    run = store.get("runs", rid)
    if not run:
        raise ValueError("Unknown run")
    if store.get("run_visibility", rid, {}).get("archived"):
        raise ValueError("Restore this run from Archived before starting a job.")
    if check_busy and any(j["kind"] == "workflow" and j["payload"]["run_id"] == rid and j["status"] in {"queued", "running"} for j in store.jobs()):
        raise ValueError("This run already has a queued or active job")
    current_controller(store, run)
    if not supported(run["tool"], run["commit"]):
        raise ValueError("This source version has no reviewed execution adapter")
    if action != "dry-run" and not dry_run_current(store, run):
        raise ValueError("A successful native dry-run with the current runner is required before preparing tools or running")
    check_run_files(run)
    from .preflight import assert_launch
    assert_launch(run, action)
    return run


def command(store, run, action):
    from .engine import find_conda
    conda = find_conda(store)
    if not conda:
        raise ValueError("Conda is missing")
    folder = Path(run["folder"])
    entry = "Followup.smk" if run.get("run_type") == "followup" else PIPELINES[run["tool"]]["snakefile"]
    argv = [conda, "run", "--no-capture-output", "-p", current_controller(store, run), "snakemake",
            "--snakefile", str(folder / "workflow" / entry),
            "--directory", str(folder / "workflow"), "--configfile", str(folder / "config.yaml"),
            "--cores", str(run["cores"]), "--resources", "mem_mb=" + str(run["memory_mb"]), "kraken_jobs=1",
            "--use-conda", "--conda-prefix", run.get("rule_cache") or str(store.home / "envs" / run["tool"]), "--rerun-incomplete"]
    if action == "dry-run":
        argv.append("--dry-run")
    elif action == "check-environments":
        argv.append("--list-conda-envs")
    elif action == "prepare":
        argv.append("--conda-create-envs-only")
    elif action == "run":
        pos = argv.index('snakemake')
        argv[pos:pos] = ['python', str(DATA / 'run_existing.py')]
    return argv


def execute_workflow(ctx):
    payload, store = ctx.job["payload"], ctx.store
    run = validate_launch(store, payload["run_id"], payload["action"], check_busy=False)
    if tree_digest(Path(run["folder"]) / "workflow") != run["workflow_sha256"]:
        raise ValueError("The run's workflow source changed; create a new draft")
    if payload["action"] == "check-environments":
        from .readiness import check_environments
        check_environments(ctx, run)
        return
    ctx.write("Run " + run["id"] + " · source " + run["commit"])
    ctx.write("Input identity uses file size/mtime checks. Workflow provenance may add content checksums.")
    if payload['action'] == 'run':
        ctx.write('Environment policy: reuse matching installed environments only; automatic installation and repair are disabled.')
    if payload["action"] == "dry-run":
        run["dry_run_ok"] = False
    elif payload["action"] == "run":
        if run.get("completed"):
            run["previous_completed"] = run.pop("completed")
        if run.get("run_type") == "followup":
            store.put("followup_output_owner", run["parent"], run["id"])
    run["last_controller"] = current_controller(store, run)
    store.put("runs", run["id"], run)
    from . import results
    results.observe(ctx, run, 'before')
    try:
        ctx.command(command(store, run, payload["action"]), cwd=Path(run["folder"]) / "workflow")
    finally:
        results.observe(ctx, run, 'after')
    run["last_action"] = payload["action"]
    if payload["action"] == "dry-run":
        run["dry_run_ok"] = True
        run["validated"] = now()
        run["validated_controller"] = run["last_controller"]
    elif payload["action"] == "run":
        run["completed"] = now()
        from . import methods
        try:
            methods.capture(store, run, ctx.job['id'])
        except Exception as exc:
            # Export metadata must never turn a successful workflow into a failed job.
            ctx.write('Methods metadata could not be captured: ' + str(exc))
    store.put("runs", run["id"], run)


def report_candidates(store, run):
    if run.get("run_type") == "followup":
        owned = store.get("followup_output_owner", run["parent"]) == run["id"]
        entries = [("Follow-up report", "safety/deepsafe-report/report.html")] if owned else []
    else:
        entries = [("MultiQC", "reports/multiqc/multiqc_report.html"), ("Safety report", "safety/report.html")]
        if run["tool"] == "basis" and not store.get("followup_output_owner", run["id"]):
            entries.append(("Follow-up report", "safety/deepsafe-report/report.html"))
    return entries


def reports(store):
    result = []
    jobs = store.jobs()
    for run in store.all("runs"):
        output = Path(run["output"])
        entries = report_candidates(store, run)
        for name, rel in entries:
            path = output / rel
            if path.is_file() and path.stat().st_size:
                result.append({"id": run["id"] + "-" + name, "name": name, "path": str(path.resolve()), "run": run["id"], "sample": run["sample"]["id"],
                               "run_status": run_status(run, jobs, dry_run_current(store, run))})
    for item in store.all("reports"):
        if Path(item["path"]).is_file():
            result.append(item)
    for item in store.all("smoke"):
        if Path(item["report"]).is_file():
            result.append({"id": item["id"], "name": "Live test report", "path": item["report"], "run": item["id"], "sample": "Software smoke test"})
    return result
