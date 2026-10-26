"""Explicit database preparation and sample-free environment administration.

Only reviewed source recipes/providers are used. Snakemake owns environment
hashes and deployment; no substitute cache keys or biological jobs are created.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil

import yaml

from .catalog import PIPELINES, supported
from .engine import Cancelled, find_conda
from .local import git_read, local_path
from .runs import parse_config, template, tree_digest
from .store import identifier, now

RECIPES = {
    "hybas": {"assembly": "Assembly and read preparation", "qc": "QC, Kraken2 and BUSCO",
              "polishing": "Racon and Polypolish", "medaka": "Medaka", "annotation": "Prokka",
              "controller": "Lightweight workflow Python"},
    "basis": {"core-amr": "AMRFinderPlus", "rgi": "CARD / RGI", "diamond": "VFDB and BacMet",
              "antismash": "antiSMASH", "context": "geNomad and IntegronFinder",
              "mobsuite": "MOB-suite", "isescan": "ISEScan", "icescreen": "ICEscreen",
              "crisprcasfinder": "CRISPRCasFinder", "minced": "MinCED", "mlst": "MLST",
              "pathogenfinder2": "PathogenFinder2 models", "deepsafe": "DeepSafe tools"},
}
BASIS_MODULES = ("amrfinder", "rgi", "vfdb", "bacmet", "antismash", "genomad", "mobsuite",
                 "integronfinder", "isescan", "icescreen", "crisprcasfinder", "minced", "mlst", "pathogenfinder2")


def cache_path(store, item):
    return local_path(item.get("rule_cache") or str(store.home / "envs" / item["tool"]))


def installation(store, key):
    item = store.get("installs", key)
    if not item or not item.get("ready") or not item.get("controller"):
        raise ValueError("Finish pipeline runner setup first.")
    if not supported(item["tool"], item["version"]["commit"]):
        raise ValueError("Choose a supported pipeline version.")
    source = Path(item["source"])
    if (git_read(source, "rev-parse", "HEAD") != item["version"]["commit"]
            or git_read(source, "status", "--porcelain")):
        raise ValueError("Pipeline source changed. Connect a clean installation before maintenance.")
    if not find_conda(store):
        raise ValueError("Choose your Conda executable in Install & update.")
    return item


def recipes(item):
    base = Path(item["source"]) / ("workflow/envs" if item["tool"] == "hybas" else "envs")
    rows = []
    for key, label in RECIPES[item["tool"]].items():
        path = base / (key + ".yml")
        hook = path.with_suffix(".post-deploy.sh")
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes() + b"\0" + (hook.read_bytes() if hook.is_file() else b"")).hexdigest()
        rows.append({"id": key, "name": label, "recipe": str(path), "hook": str(hook) if hook.is_file() else "",
                     "definition": digest})
    return rows


def environment_panel(store, item, jobs):
    try:
        rows = recipes(item) if supported(item["tool"], item["version"]["commit"]) else []
        saved = store.get("environment_checks", item["id"], {})
        cache = str(cache_path(store, item))
        for row in rows:
            previous = saved.get(row["id"], {})
            valid = previous.get("definition") == row["definition"] and previous.get("cache") == cache
            row.update({k: previous[k] for k in ("status", "path", "message", "checked_at", "job") if valid and k in previous})
            row.setdefault("status", "Not checked")
            if row.get("path") and not Path(row["path"], "conda-meta/history").is_file():
                row["status"] = "Missing"
        busy = any(j["kind"] == "environments" and j["payload"].get("installation") == item["id"]
                   and j["status"] in {"queued", "running"} for j in jobs)
        return {"rows": rows, "cache": cache, "busy": busy}
    except (OSError, ValueError) as exc:
        return {"rows": [], "cache": "", "busy": False, "error": str(exc)}


def environment_request(store, key, selected, action):
    item = installation(store, key)
    available = {r["id"]: r for r in recipes(item)}
    if action not in {"check", "install"} or not isinstance(selected, list) or not selected:
        raise ValueError("Select at least one environment and choose Check or Install.")
    if any(not isinstance(name, str) or name not in available for name in selected):
        raise ValueError("Unknown environment selection.")
    return {"installation": key, "tool": item["tool"], "action": action,
            "definitions": {name: available[name]["definition"] for name in dict.fromkeys(selected)},
            "cache": str(cache_path(store, item)), "commit": item["version"]["commit"]}


def environment_snakefile(folder, rows):
    """DAG only: install uses --conda-create-envs-only, never these shell bodies."""
    lines = ["rule all:", "    input: " + repr([r["id"] + ".unused" for r in rows])]
    for index, row in enumerate(rows):
        lines += [f"rule environment_{index}:", "    output: " + repr(row["id"] + ".unused"),
                  "    conda: " + repr(row["recipe"]), "    shell: 'exit 99'"]
    path = folder / "Environments.smk"
    path.write_text("\n".join(lines) + "\n")
    return path


def snake_command(store, item, folder, snakefile):
    return [find_conda(store), "run", "--no-capture-output", "-p", item["controller"], "snakemake",
            "--snakefile", str(snakefile), "--directory", str(folder), "--cores", "1",
            "--use-conda", "--conda-prefix", str(cache_path(store, item)), "--rerun-incomplete"]


def parse_environment_list(text, folder, rows, cache):
    expected = {Path(row["recipe"]).resolve(): row["id"] for row in rows}
    result = {}
    for line in text.splitlines():
        columns = line.split("\t")
        if len(columns) != 3 or columns[0] == "environment":
            continue
        recipe, container, location = columns
        path = (folder / recipe).resolve()
        if path not in expected or container:
            continue
        prefix = (folder / location).resolve()
        if prefix.parent != cache.resolve() or not re.fullmatch(r"[a-f0-9]{8}(?:[a-f0-9]{24})?_?", prefix.name):
            raise ValueError("Snakemake returned an unexpected environment cache location.")
        result[expected[path]] = prefix
    if set(result) != {r["id"] for r in rows}:
        raise ValueError("Could not read the complete Snakemake environment inventory. See the job log.")
    return result


def execute_environments(ctx):
    store, payload = ctx.store, ctx.job["payload"]
    item = installation(store, payload["installation"])
    fresh = environment_request(store, item["id"], list(payload["definitions"]), payload["action"])
    if fresh != payload:
        raise ValueError("Environment definitions or cache changed. Submit a new check.")
    rows = [r for r in recipes(item) if r["id"] in payload["definitions"]]
    folder = store.home / "maintenance" / ctx.job["id"]
    folder.mkdir(parents=True)
    snakefile = environment_snakefile(folder, rows)
    argv = snake_command(store, item, folder, snakefile)
    if payload["action"] == "install":
        # Invalidate previous success before deploying, including cancelled jobs.
        saved = store.get("environment_checks", item["id"], {})
        for row in rows:
            saved[row["id"]] = {**row, "cache": payload["cache"], "status": "Installation requested", "job": ctx.job["id"]}
        store.put("environment_checks", item["id"], saved)
        ctx.command([*argv, "--conda-create-envs-only"], cwd=folder)
    listing = folder / "environments.tsv"
    with listing.open("w") as output:
        ctx.command([*argv, "--list-conda-envs"], cwd=folder, stdout=output)
    text = listing.read_text()
    ctx.write(text)
    paths = parse_environment_list(text, folder, rows, cache_path(store, item))
    saved = store.get("environment_checks", item["id"], {})
    failures = []
    for row in rows:
        ctx.check_cancel()
        prefix = paths[row["id"]]
        result = {**row, "path": str(prefix), "cache": payload["cache"], "checked_at": now(), "job": ctx.job["id"]}
        result.update(status="Checking", message="")
        saved[row["id"]] = result
        store.put("environment_checks", item["id"], saved)
        if not (prefix / "conda-meta/history").is_file():
            result.update(status="Missing", message="Install this environment to prepare its tools.")
        elif not prefix.with_suffix(".env_setup_done").is_file():
            result.update(status="Incomplete", message="Snakemake has not marked deployment complete. Install to retry.")
        else:
            try:
                probe = ["bash", row["hook"]] if row["hook"] else ["python", "-c", "import sys; print(sys.version)"]
                ctx.command([find_conda(store), "run", "--no-capture-output", "-p", str(prefix), *probe], cwd=folder)
                result.update(status="Verified", message="Native executable and version checks passed.")
            except Cancelled:
                raise
            except Exception as exc:
                result.update(status="Check failed", message=str(exc))
                failures.append(row["id"])
        ctx.write(row["id"] + ": " + result["status"])
        if payload["action"] == "install" and result["status"] in {"Missing", "Incomplete"}:
            failures.append(row["id"])
        store.put("environment_checks", item["id"], saved)
    if failures:
        raise RuntimeError("Environment checks failed: " + ", ".join(failures) + ". See the job log.")


def download_supported(item):
    return bool(item and item.get("ready") and item["version"]["commit"] == PIPELINES[item["tool"]]["commit"])


def _number(value, label, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(label + " must be a positive integer up to " + str(maximum))
    return value


def download_request(store, tool, key, parent, cores=4, memory_mb=16000, lineage="bacteria_odb10"):
    from .databases import _spec
    _spec(tool, key)
    item = installation(store, store.get("settings", "active-" + tool))
    if not download_supported(item):
        raise ValueError("Select the recommended pipeline snapshot to use database downloads.")
    base = local_path(parent)
    if not base.is_dir() or not os.access(base, os.W_OK):
        raise ValueError("Choose an existing writable download folder.")
    if any(c in str(base) for c in "{}\n\r\t\"'`$\\"):
        raise ValueError("Choose a download folder without quotes, shell characters, braces, tabs or newlines.")
    if key == "busco" and (not isinstance(lineage, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+_odb[0-9]+", lineage)):
        raise ValueError("Enter a BUSCO lineage such as bacteria_odb10.")
    operation = identifier("database")
    return {"id": operation, "tool": tool, "resource": key, "installation": item["id"],
            "commit": item["version"]["commit"], "parent": str(base),
            "destination": str(base / ("bactologix-" + tool + "-" + key + "-" + operation[9:])),
            "cores": _number(cores, "Threads", 1024), "memory_mb": _number(memory_mb, "Memory (MB)", 10000000),
            "lineage": lineage if key == "busco" else ""}


def download_config(payload, folder):
    tool, key = payload["tool"], payload["resource"]
    destination = Path(payload["destination"])
    if tool == "basis":
        modules = {name: {"enabled": False} for name in BASIS_MODULES}
        owner = "pathogenfinder2" if key == "prott5" else key
        if owner in modules:
            modules[owner]["enabled"] = True
        cfg = {"isolates": [], "isolate": "", "samples": "", "sample_module_overrides": {},
               "bactomics_root": str(folder / "no-samples"), "threads": payload["cores"],
               "memory_mb": payload["memory_mb"], "extend": key == "prott5",
               "modules": modules, "deep": {"enabled": key in {"prosite", "deep_catalogue"}},
               "resources": {"policy": "auto", "directory": str(destination)}}
        provider = "card" if key == "rgi" else key
        target = str(destination / provider / "provenance.json")
        result_path = str(destination / provider)
    else:
        cfg = parse_config(template("hybas"))
        cfg.update(admin_mode=True, setup_only=False, sample_sheet="", base_dir=str(folder / "admin-output"),
                   db_root=str(destination), kraken_db_name="standard", threads=payload["cores"],
                   busco_lineage=payload["lineage"] or "bacteria_odb10",
                   busco_download_lineages=[payload["lineage"] or "bacteria_odb10"])
        cfg.pop("isolate", None)
        target = "download_kraken_db" if key == "kraken" else "download_busco_db"
        result_path = str(destination / ("standard" if key == "kraken" else "busco/" + payload["lineage"]))
    return cfg, target, result_path


def execute_download(ctx):
    from .databases import inspect
    store, payload = ctx.store, ctx.job["payload"]
    item = installation(store, payload["installation"])
    if not download_supported(item) or item["version"]["commit"] != payload["commit"]:
        raise ValueError("The selected download adapter changed. Submit a new download.")
    folder = store.home / "maintenance" / payload["id"]
    destination = Path(payload["destination"])
    if destination.parent.resolve() != Path(payload["parent"]):
        raise ValueError("Download parent was replaced by a link. Choose the location again.")
    old = store.get("database_downloads", payload["id"])
    if old:
        if old["request"] != payload:
            raise ValueError("Download request changed; refusing to reuse its folder.")
        if not destination.is_dir() or destination.is_symlink():
            raise ValueError("Download destination is missing or was replaced by a link.")
        if tree_digest(folder / "workflow") != old["workflow_sha256"]:
            raise ValueError("Saved download source changed. Start a new download.")
        row = old
    else:
        folder.mkdir(parents=True)
        destination.mkdir(exist_ok=False)
        shutil.copytree(item["source"], folder / "workflow", ignore=shutil.ignore_patterns(".git", ".snakemake", "__pycache__", "*.pyc"))
        cfg, target, result_path = download_config(payload, folder)
        text = yaml.safe_dump(cfg, sort_keys=False)
        (folder / "config.yaml").write_text(text)
        row = {**payload, "request": payload, "folder": str(folder), "target": target, "result_path": result_path,
               "workflow_sha256": tree_digest(folder / "workflow"), "config_sha256": hashlib.sha256(text.encode()).hexdigest(), "created": now()}
    if hashlib.sha256((folder / "config.yaml").read_bytes()).hexdigest() != row["config_sha256"]:
        raise ValueError("Saved download configuration changed. Start a new download.")
    row.update(job=ctx.job["id"], status="running", error="")
    store.put("database_downloads", row["id"], row)
    argv = snake_command(store, item, folder, folder / "workflow" / PIPELINES[item["tool"]]["snakefile"])
    argv[argv.index("--cores") + 1] = str(payload["cores"])
    argv += [row["target"], "--configfile", str(folder / "config.yaml"), "--resources",
             "mem_mb=" + str(payload["memory_mb"]), "gpu=0", "kraken_jobs=1"]
    try:
        ctx.write("Preparing " + payload["resource"] + " in " + str(destination))
        ctx.write("Only resource preparation is requested. No sample inputs are supplied.")
        ctx.command(argv, cwd=folder)
        ctx.check_cancel()
        result = inspect(store, payload["tool"], payload["resource"], row["result_path"])
        row.update(status="completed", result=result, finished=now())
        ctx.write("Database ready: " + result["path"] + ". Choose Use downloaded, then Save locations.")
    except BaseException as exc:
        row.update(status="cancelled" if isinstance(exc, Cancelled) else "failed", error=str(exc))
        raise
    finally:
        store.put("database_downloads", row["id"], row)


def download_log_tail(store, job):
    row = store.get("database_downloads", job["payload"]["id"])
    if not row:
        return ""
    roots = [Path(row["destination"]) / "logs", Path(row["folder"]) / "admin-output/provenance/databases"]
    tails = []
    for root in roots:
        try:
            with os.scandir(root) as entries:
                paths = []
                for index, entry in enumerate(entries):
                    if index >= 100:
                        break
                    if entry.name.endswith('.log') and entry.is_file(follow_symlinks=False):
                        paths.append(Path(entry.path))
            for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:4]:
                with path.open('rb') as handle:
                    handle.seek(max(0, path.stat().st_size - 12000))
                    text = handle.read(12000).decode('utf-8', errors='replace')
                tails.append("\n\n--- " + str(path) + " ---\n" + text)
        except OSError:
            continue
    return ''.join(tails)
