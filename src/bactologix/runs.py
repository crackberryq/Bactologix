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
    matches = [s for s in store.all("samples") if s["id"].casefold() == sid.casefold()]
    if matches:
        if matches[0]["path"] == str(folder) and matches[0]["mode"] == mode:
            return matches[0]
        raise ValueError("That sample ID is already registered with different inputs")
    return store.put("samples", sid, {"id": sid, "path": str(folder), "mode": mode, "created": now()})


def import_samples(store, text):
    text = text.lstrip("\ufeff")
    delimiter = "\t" if "\t" in text.partition("\n")[0] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not {"sample_id", "input_folder"} <= set(reader.fieldnames or []):
        raise ValueError("Sample sheet requires sample_id and input_folder; read_mode is optional")
    rows = list(reader)
    seen = {s["id"].casefold() for s in store.all("samples")}
    prepared = []
    for row in rows:
        sid = valid_id(row["sample_id"].strip())
        if sid.casefold() in seen:
            raise ValueError("Duplicate sample ID: " + sid)
        seen.add(sid.casefold())
        path = absolute_path(row["input_folder"].strip())
        mode = row.get("read_mode", "hybrid").strip() or "hybrid"
        if not path.is_dir() or mode not in {"hybrid", "ont_only", "illumina_only"}:
            raise ValueError("Invalid folder/read mode for " + sid)
        prepared.append({"id": sid, "path": str(path), "mode": mode, "created": now()})
    with store.connect() as db:
        for row in prepared:
            db.execute("INSERT INTO items(kind,id,body) VALUES ('samples',?,?)", (row["id"], json.dumps(row)))
    return prepared


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


def create_run(store, tool, sample, config_text, cores=4, memory_mb=16000, parent=None, label=None, installation=None):
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
        cfg["threads"] = cores
        if tool == "hybas":
            cfg.update(base_dir=str(folder / "outputs"), sample_sheet=str(folder / "samples.tsv"), setup_only=False, admin_mode=False, ci_mode=False)
            cfg.pop("isolate", None)
            (folder / "samples.tsv").write_text("isolate\n" + sample["id"] + "\n")
        else:
            cfg.update(bactomics_root=str(folder / "outputs"), samples=str(folder / "samples.tsv"), memory_mb=memory_mb, isolates=[])
            cfg.pop("isolate", None)
            with (folder / "samples.tsv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["sample_id", "isolate_dir", "hybas_dir", "input_mode"], delimiter="\t")
                writer.writeheader()
                hybas_layout = Path(sample["path"]).name == sample["id"]
                writer.writerow({"sample_id": sample["id"], "isolate_dir": str(output),
                                 "hybas_dir": sample["path"] if hybas_layout else "",
                                 "input_mode": "auto" if hybas_layout else "external"})
        text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
        (folder / "config.yaml").write_text(text)
        (folder / "submitted.yaml").write_text(config_text)
        run = {"id": rid, "tool": tool, "sample": copy.deepcopy(sample), "folder": str(folder), "output": str(output),
               "installation": install_id, "controller": installed["controller"], "commit": installed["version"]["commit"],
               "rule_cache": installed.get("rule_cache") or str(store.home / "envs" / tool),
               "config": text, "submitted": config_text, "config_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "workflow_sha256": tree_digest(folder / "workflow"), "inputs": inputs, "cores": cores, "memory_mb": memory_mb,
               "parent": parent, "label": label or sample["id"], "created": now(), "dry_run_ok": False,
               "reuse": "input-files-only; workflow outputs are independent"}
        (folder / "bactologix-run.json").write_text(json.dumps(run, indent=2) + "\n")
        store.put("runs", rid, run)
        return run
    except BaseException:
        shutil.rmtree(folder)
        raise


def clone_runs(store, parent_id, aware_free=False, strategies=None):
    parent = store.get("runs", parent_id)
    if not parent or parent["tool"] != "hybas":
        raise ValueError("Choose a HybAs parent run")
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
                                  parent["cores"], parent["memory_mb"], parent_id, label, parent["installation"]))
    return created


def validate_launch(store, rid, action, check_busy=True):
    if action not in {"dry-run", "prepare", "run"}:
        raise ValueError("Unsupported workflow action")
    run = store.get("runs", rid)
    if not run:
        raise ValueError("Unknown run")
    if check_busy and any(j["kind"] == "workflow" and j["payload"]["run_id"] == rid and j["status"] in {"queued", "running"} for j in store.jobs()):
        raise ValueError("This run already has a queued or active job")
    installed = store.get("installs", run["installation"])
    if not installed or not installed.get("ready"):
        raise ValueError("The pipeline runner needs setup or repair")
    if not supported(run["tool"], run["commit"]):
        raise ValueError("This source version has no reviewed execution adapter")
    if action != "dry-run" and not run.get("dry_run_ok"):
        raise ValueError("A successful native dry-run is required before preparing tools or running")
    cfg_path = Path(run["folder"]) / "config.yaml"
    if hashlib.sha256(cfg_path.read_bytes()).hexdigest() != run["config_sha256"]:
        raise ValueError("Saved configuration changed. Create a new draft for changed settings.")
    check_inputs(run)
    return run


def command(store, run, action):
    from .engine import find_conda
    conda = find_conda(store)
    if not conda:
        raise ValueError("Conda is missing")
    folder = Path(run["folder"])
    argv = [conda, "run", "--no-capture-output", "-p", run["controller"], "snakemake",
            "--snakefile", str(folder / "workflow" / PIPELINES[run["tool"]]["snakefile"]),
            "--directory", str(folder / "workflow"), "--configfile", str(folder / "config.yaml"),
            "--cores", str(run["cores"]), "--resources", "mem_mb=" + str(run["memory_mb"]), "kraken_jobs=1",
            "--use-conda", "--conda-prefix", run.get("rule_cache") or str(store.home / "envs" / run["tool"]), "--rerun-incomplete"]
    if action == "dry-run":
        argv.append("--dry-run")
    elif action == "prepare":
        argv.append("--conda-create-envs-only")
    return argv


def execute_workflow(ctx):
    payload, store = ctx.job["payload"], ctx.store
    run = validate_launch(store, payload["run_id"], payload["action"], check_busy=False)
    if tree_digest(Path(run["folder"]) / "workflow") != run["workflow_sha256"]:
        raise ValueError("The run's workflow source changed; create a new draft")
    ctx.write("Run " + run["id"] + " · source " + run["commit"])
    ctx.write("Input identity uses file size/mtime checks. Workflow provenance may add content checksums.")
    if payload["action"] == "dry-run":
        run["dry_run_ok"] = False
        store.put("runs", run["id"], run)
    ctx.command(command(store, run, payload["action"]), cwd=Path(run["folder"]) / "workflow")
    run["last_action"] = payload["action"]
    if payload["action"] == "dry-run":
        run["dry_run_ok"] = True
        run["validated"] = now()
    elif payload["action"] == "run":
        run["completed"] = now()
    store.put("runs", run["id"], run)


def reports(store):
    result = []
    for run in store.all("runs"):
        output = Path(run["output"])
        for name, rel in (("MultiQC", "reports/multiqc/multiqc_report.html"), ("Safety report", "safety/report.html")):
            path = output / rel
            if path.is_file() and path.stat().st_size:
                result.append({"id": run["id"] + "-" + name, "name": name, "path": str(path.resolve()), "run": run["id"], "sample": run["sample"]["id"]})
    for item in store.all("reports"):
        if Path(item["path"]).is_file():
            result.append(item)
    for item in store.all("smoke"):
        if Path(item["report"]).is_file():
            result.append({"id": item["id"], "name": "Live test report", "path": item["report"], "run": item["id"], "sample": "Software smoke test"})
    return result
