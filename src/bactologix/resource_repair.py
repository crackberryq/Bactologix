"""Explicit, non-destructive repair of stale external VFDB references.

This creates a new draft for native preparation. It never downloads a resource,
selects an unverified scan result, writes a receipt, or edits the parent run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from . import databases, maintenance, run_revisions
from .runs import parse_config
from .storage_guard import mutation


FIELDS = tuple("modules.vfdb." + key for key in
               ("database", "reference_fasta", "metadata", "database_version"))


def _status(field, value):
    if field.endswith("database_version"):
        return "recorded identity" if value else "not set"
    if not value:
        return "not set"
    if not isinstance(value, str) or not Path(value).is_absolute():
        return "invalid path"
    try:
        path = Path(value)
        if path.is_symlink() and not path.exists():
            return "broken link"
        if not path.is_file():
            return "missing file"
        return "present; identity not verified" if path.stat().st_size else "empty file"
    except OSError:
        return "unreadable file"


def preview(store, rid):
    parent = run_revisions.source(store, rid)
    if parent["tool"] != "basis":
        raise ValueError("VFDB reference repair is available for BaSIS runs only.")
    cfg = parse_config(parent["config"])
    fields = [{"field": field, "value": databases._get(cfg, field),
               "status": _status(field, databases._get(cfg, field))} for field in FIELDS]
    installed = store.get("installs", parent["installation"])
    capability = maintenance.download_capability(installed, store)
    stale = any(row["status"] in {"invalid path", "broken link", "missing file", "empty file", "unreadable file"}
                for row in fields)
    explicit = bool(databases._get(cfg, "modules.vfdb.database"))
    available = capability["available"] and explicit and stale
    reason = (capability["reason"] if not capability["available"] else
              "This draft has no explicit VFDB database reference to repair." if not explicit else
              "No missing or invalid VFDB path was found. Existing file identities have not been verified." if not stale else
              "Create a revised draft that lets the native workflow prepare VFDB and record its provenance.")
    policy = databases._get(cfg, "resources.policy", "auto")
    notice = ("The original run and its files remain unchanged. The revised draft keeps the saved module switches, "
              "thresholds, compute allocation and resource directory. Validate the draft before execution; "
              "a dry-run does not download VFDB. Execution may download a new VFDB snapshot, whose identity "
              "will be recorded by the native provider. No receipt is created by this repair.")
    if policy != "auto":
        notice += (" Changing the resource policy to Auto also permits native preparation for other enabled "
                   "resources without explicit paths.")
    plan = {"id": parent["id"], "resource": "vfdb", "action": "native-preparation",
            "run_signature": run_revisions.signature(parent),
            "available": available, "reason": reason, "fields": fields, "policy": policy,
            "resource_directory": databases._get(cfg, "resources.directory"),
            "changes": [{"field": row["field"], "before": row["value"], "after": None} for row in fields]
                       + [{"field": "resources.policy", "before": policy, "after": "auto"}],
            "notice": notice}
    binding = {"plan": plan, "installation": installed}
    plan["signature"] = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    return plan


@mutation
def apply(store, rid, signature):
    plan = preview(store, rid)
    if plan["signature"] != signature:
        raise ValueError("This run, installation or VFDB reference changed. Review the repair again.")
    if not plan["available"]:
        raise ValueError(plan["reason"])
    parent = run_revisions.source(store, rid)
    if run_revisions.signature(parent) != plan["run_signature"]:
        raise ValueError("This run changed. Review the repair again.")
    cfg = parse_config(parent["config"])
    for field in FIELDS:
        databases._set(cfg, field, None)
    databases._set(cfg, "resources.policy", "auto")
    # Use the existing guarded revision path: immutable inputs/source are checked
    # again, the new draft has independent outputs, and no job is submitted.
    return run_revisions.apply(store, {"id": rid, "signature": plan["run_signature"],
        "config": {"tool": "basis", "text": yaml.safe_dump(cfg, sort_keys=False), "changes": {}},
        "cores": parent["cores"], "memory_mb": parent["memory_mb"]})
