"""Explicit job adapter for the upstream saved-screening entry point."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil

import yaml

from .catalog import PIPELINES
from .runs import (absolute_path, bind_paths, check_run_files, current_controller,
                   dry_run_current, parse_config, run_status, tree_digest)
from .store import identifier, now


def available(run):
    return (run["tool"] == "basis" and run.get("run_type") != "followup"
            and run["commit"] in PIPELINES["basis"].get("followup_commits", []))


def screening_sources(output):
    root = Path(output) / "safety"
    paths = [root / rel for rel in ("metadata/sample.json", "metadata/config.json",
             "metadata/validation.json", "metadata/setup.ready.json", "report.html",
             "evidence.json", "metadata/report-navigation.json")]
    bundle = json.loads((root / "evidence.json").read_text())
    sources = bundle.get("run", {}).get("evidence_sources")
    if not isinstance(sources, list):
        raise ValueError("Saved screening evidence manifest is missing")
    for row in sources:
        path = absolute_path(row["path"])
        if path.parent != (root / "evidence").resolve() or path.suffix != ".json" or path.name == "deepsafe.json":
            raise ValueError("Invalid saved screening evidence path")
        paths.append(path)
    result = []
    for path in dict.fromkeys(paths):
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or linked saved screening prerequisite: " + str(path))
        result.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def create_followup(store, parent_id, text, reviewed=False):
    parent = store.get("runs", parent_id)
    if not parent or not available(parent):
        raise ValueError("Choose a screening run using the BaSIS AWARE follow-up adapter")
    if not reviewed:
        raise ValueError("Review the saved screening report before requesting follow-up")
    if run_status(parent, store.jobs(), dry_run_current(store, parent)) != "completed":
        raise ValueError("Complete screening before requesting follow-up")
    check_run_files(parent)
    source = Path(parent["folder"]) / "workflow"
    if tree_digest(source) != parent["workflow_sha256"]:
        raise ValueError("The screening run's workflow source changed")
    cfg = parse_config(text)
    if set(cfg) - {"deep", "resource_receipts"}:
        raise ValueError("Follow-up may set only deep and resource_receipts; screening paths and resources are fixed")
    if not isinstance(cfg.get("deep"), dict) or cfg["deep"].get("enabled") is not True:
        raise ValueError("Explicitly enable follow-up in the reviewed configuration")
    receipts = cfg.get("resource_receipts", [])
    if not isinstance(receipts, list) or any(not isinstance(p, str) for p in receipts):
        raise ValueError("resource_receipts must be a list of absolute file paths")
    cfg = bind_paths(cfg, "basis", store, {})
    cfg.pop("resources", None)
    cfg["resource_receipts"] = [str(absolute_path(p)) for p in receipts]
    if any(not Path(p).is_file() for p in cfg["resource_receipts"]):
        raise ValueError("A selected resource receipt is missing")
    cfg.update(isolate=parent["output"], threads=parent["cores"], memory_mb=parent["memory_mb"])
    sources = screening_sources(parent["output"])
    sources.extend({"path": p, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in cfg["resource_receipts"])
    rid = identifier("run")
    folder = store.home / "runs" / rid
    folder.mkdir()
    try:
        shutil.copytree(source, folder / "workflow", ignore=shutil.ignore_patterns(".snakemake", "__pycache__", "*.pyc"))
        config = yaml.safe_dump(cfg, sort_keys=False)
        (folder / "config.yaml").write_text(config)
        (folder / "submitted.yaml").write_text(text)
        run = {**copy.deepcopy(parent), "id": rid, "run_type": "followup", "folder": str(folder),
               "parent": parent_id, "label": parent["sample"]["id"] + " · reviewed follow-up",
               "created": now(), "controller": current_controller(store, parent), "config": config,
               "submitted": text, "config_sha256": hashlib.sha256(config.encode()).hexdigest(),
               "workflow_sha256": tree_digest(folder / "workflow"), "followup_sources": sources,
               "dry_run_ok": False, "reviewed_at": now(),
               "reuse": "Saved screening inputs; follow-up outputs share the screening folder. Only the latest follow-up attempt owns its displayed report."}
        for key in ("completed", "previous_completed", "validated", "validated_controller", "last_action", "last_controller"):
            run.pop(key, None)
        (folder / "bactologix-run.json").write_text(json.dumps(run, indent=2) + "\n")
        store.put("runs", rid, run)
        return run
    except BaseException:
        shutil.rmtree(folder)
        raise
