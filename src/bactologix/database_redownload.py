"""Reviewed preparation of a replacement candidate, without replacing data.

The current native provider interface does not replay an original database URL,
release and checksum. A user must explicitly choose a fresh supported snapshot;
neither the old selection nor a frozen run is changed by this operation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import databases, maintenance
from .catalog import PIPELINES
from .runs import parse_config
from .store import now


CHOICE = "supported-source"
SCHEMA = "bactologix-replacement-candidate-v1"
NOTICE = ("Prepare a new snapshot from the supported native provider. It may differ from the existing "
          "database. The provider's pipeline source is pinned, but the original database URL, release "
          "and checksum cannot be replayed by this interface. The existing database, saved selection "
          "and all saved runs remain unchanged. Select the completed candidate separately after review.")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _configured(store, tool, resource):
    spec = databases._spec(tool, resource)
    text = databases._text(store, tool)
    cfg = parse_config(text)
    row = next(r for r in databases.panel(store, tool)["rows"] if r["id"] == resource)
    fields = databases._fields(cfg, spec)
    recorded_version = next((value for key, value in fields.items() if key.endswith("database_version")), None)
    return text, cfg, {"path": row["path"], "fields": fields,
                       "recorded_version": recorded_version,
                       "identity_status": "Saved settings only; original content identity is not verified."}


def preview(store, tool, resource, *, lineage=None):
    """On-demand, read-only review; a cached panel status never authorizes work."""
    text, cfg, current = _configured(store, tool, resource)
    item = store.get("installs", store.get("settings", "active-" + tool))
    capability = maintenance.download_capability(item, store)
    from .release_contract import approval
    commit = (item or {}).get("version", {}).get("commit", "")
    review = approval(store, tool, commit) if commit else None
    requested_lineage = (str(cfg.get("busco_lineage", "bacteria_odb10")) if lineage is None else lineage) if resource == "busco" else ""
    provider = ("kraken2-build --standard" if resource == "kraken" and tool == "hybas" else
                "busco --download " + str(requested_lineage) if resource == "busco" and tool == "hybas" else
                "BaSIS native " + ("card" if resource == "rgi" else resource) + " provider")
    reason = ("Exact redownload is unavailable: no reviewed contract can replay this database's original "
              "source URL, release and checksum. A folder name is not a database version.")
    parent = store.home / "resources"
    if any(parent.resolve().is_relative_to(path) for path in _protected(store)):
        parent = store.home
    plan = {"tool": tool, "resource": resource, "name": databases._spec(tool, resource)["name"],
            "current": current, "config_revision": databases.revision(text),
            "exact": {"available": False, "reason": reason},
            "alternative": {"available": capability["available"], "reason": capability["reason"],
                "choice": CHOICE, "label": "Prepare a new snapshot", "provider": provider,
                "installation": (item or {}).get("id", ""), "commit": commit,
                "pipeline_source": "https://github.com/" + PIPELINES[tool]["repo"] + "/tree/" + commit if commit else "",
                "lineage": requested_lineage, "database_release": None},
            "notice": NOTICE, "parent": str(parent)}
    plan["signature"] = _digest({"plan": plan, "installation": item, "approval": review})
    return plan


def _path_root(value):
    """Protect both registered roots and directories containing database files."""
    if not isinstance(value, str) or not value or not Path(value).expanduser().is_absolute():
        return None
    path = Path(value).expanduser().resolve()
    if path.is_file() or path.suffix.lower() in {".dmnd", ".json", ".fa", ".fas", ".fasta", ".fna", ".faa", ".gz"}:
        path = path.parent
    return path


def _absolute_paths(value):
    """Include provider/override fields the database picker does not expose.

    Paths are dependencies even in future or unknown configuration fields. This
    operation never needs to create anything inside an existing dependency.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _absolute_paths(key)
            yield from _absolute_paths(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _absolute_paths(child)
    else:
        path = _path_root(value)
        if path is not None:
            yield path


def _protected(store, own_id=None):
    paths = set()
    for tool, specs in databases.CATALOG.items():
        cfg = parse_config(databases._text(store, tool))
        paths.update(_absolute_paths(cfg))
        if tool == "hybas":
            paths.update(databases._hybas_paths(store, cfg).values())
        if tool == "basis" and databases._get(cfg, "resources.directory", "resources") == "resources":
            paths.add(store.home / "resources/basis")
        for spec in specs:
            for field in spec.get("fields", []):
                if field.endswith(("database_version", "prot_t5_identity")):
                    continue
                path = _path_root(databases._get(cfg, field))
                if path:
                    paths.add(path)
        paths.update(_absolute_paths(store.get("database_locations", tool, {})))
    # Managed preparations are also preserved, including failed candidates.
    for row in store.all("database_downloads") + store.all("database_builds"):
        if row.get("id") != own_id:
            for field in ("destination", "result_path"):
                path = _path_root(row.get(field))
                if path:
                    paths.add(path)
    for row in store.all("reference_packages"):
        paths.update(_absolute_paths(row))
    # Frozen configurations may reference databases no longer selected by defaults.
    for run in store.all("runs"):
        if run.get("tool") not in databases.CATALOG or not isinstance(run.get("config"), str):
            continue
        cfg = parse_config(run["config"])
        paths.update(_absolute_paths(cfg))
        if run["tool"] == "hybas":
            paths.update(databases._hybas_paths(store, cfg).values())
        for spec in databases.CATALOG[run["tool"]]:
            for field in spec.get("fields", []):
                if not field.endswith(("database_version", "prot_t5_identity")):
                    path = _path_root(databases._get(cfg, field))
                    if path:
                        paths.add(path)
    return {path.resolve() for path in paths}


def _destination(store, parent, destination=None, own_id=None):
    # Use workspace resources or a sibling of a wholly protected resources root.
    # Never follow a symlink into a manual/external database, even on stable.
    base = Path(str(parent)).expanduser()
    root = store.home / "resources"
    if not base.is_absolute() or (base != store.home and not base.is_relative_to(root)):
        raise ValueError("Choose this workspace folder or a separate folder inside its resources folder.")
    for path in (base, *base.parents):
        if path.is_symlink():
            raise ValueError("Replacement folders must not use symbolic links. Existing databases are preserved.")
        if path == store.home:
            break
    base = base.resolve()
    if base != store.home and not base.is_relative_to(root.resolve()):
        raise ValueError("Replacement folder escapes this workspace's resources folder.")
    target = Path(destination) if destination else None
    if target is not None and (target.parent != base or target.is_symlink()):
        raise ValueError("Replacement destination changed or is a symbolic link.")
    for protected in _protected(store, own_id):
        if base.is_relative_to(protected) or (target is not None and protected.is_relative_to(target)):
            raise ValueError("Choose a separate replacement folder, outside existing or registered databases.")
    return base


def _no_active_download(store, tool, resource, *, job_id=None):
    for job in store.jobs():
        payload = job.get("payload", {})
        if (job["id"] != job_id and job["status"] in {"queued", "running"}
                and job["kind"] in {"database_download", "database_redownload"}
                and payload.get("tool") == tool and payload.get("resource") == resource):
            raise ValueError("A download for this database is already queued or running. Open its progress instead.")


def request(store, tool, resource, signature, parent, *, choice, cores=4, memory_mb=16000, lineage=None):
    plan = preview(store, tool, resource, lineage=lineage)
    if plan["signature"] != signature:
        raise ValueError("The database selection or provider changed. Review Download again.")
    if choice != CHOICE:
        raise ValueError(plan["exact"]["reason"] + " Explicitly choose Prepare a new snapshot to continue.")
    if not plan["alternative"]["available"]:
        raise ValueError(plan["alternative"]["reason"])
    _no_active_download(store, tool, resource)
    base = _destination(store, parent)
    payload = maintenance.download_request(store, tool, resource, str(base), cores, memory_mb,
                                           plan["alternative"]["lineage"] or "bacteria_odb10")
    _destination(store, base, payload["destination"], payload["id"])
    payload["redownload_review"] = {"schema": SCHEMA, "signature": signature, "choice": choice,
                                   "plan": plan, "reviewed_at": now(), "request_sha256": _digest(payload)}
    return payload


def _file_record(path, destination):
    if path.is_symlink() and not path.exists():
        raise ValueError("Native database provenance is a broken symbolic link.")
    if not path.exists():
        return None
    if not path.resolve().is_relative_to(destination.resolve()) or not path.is_file():
        raise ValueError("Native database provenance escapes the replacement destination.")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return {"path": str(path), "bytes": size, "sha256": digest.hexdigest(),
            "status": "Provider evidence retained; database inventory not independently rehashed."}


def execute(ctx):
    """Recheck the review, prepare separately, then record a truthful candidate."""
    store, payload = ctx.store, ctx.job["payload"]
    review = payload.get("redownload_review", {})
    if review.get("schema") != SCHEMA or review.get("choice") != CHOICE:
        raise ValueError("An explicit replacement snapshot review is required.")
    if review.get("request_sha256") != _digest({k: v for k, v in payload.items() if k != "redownload_review"}):
        raise ValueError("The replacement request changed after review. Review Download again.")
    lineage = review.get("plan", {}).get("alternative", {}).get("lineage") or None
    plan = preview(store, payload["tool"], payload["resource"], lineage=lineage)
    if plan["signature"] != review.get("signature") or plan != review.get("plan"):
        raise ValueError("The database selection or provider changed. Review Download again.")
    if not plan["alternative"]["available"]:
        raise ValueError(plan["alternative"]["reason"])
    if (payload.get("commit") != plan["alternative"]["commit"]
            or payload.get("installation") != plan["alternative"]["installation"]
            or payload.get("lineage", "") != plan["alternative"]["lineage"]):
        raise ValueError("The replacement provider request differs from the reviewed source.")
    _no_active_download(store, payload["tool"], payload["resource"], job_id=ctx.job["id"])
    destination = Path(payload["destination"])
    _destination(store, payload["parent"], destination, payload["id"])
    ctx.write(NOTICE)
    maintenance.execute_download(ctx)
    row = store.get("database_downloads", payload["id"])
    try:
        ctx.check_cancel()
        _destination(store, payload["parent"], destination, payload["id"])
        result_path = Path(row["result_path"])
        if not result_path.is_dir() or not result_path.resolve().is_relative_to(destination.resolve()):
            raise ValueError("Prepared database escapes the isolated replacement destination.")
        candidates = [row["result"]["path"], row["result"]["folder"]]
        candidates += [value for field, value in row["result"].get("updates", {}).items()
                       if not field.endswith(("database_version", "prot_t5_identity"))]
        for value in candidates:
            if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).resolve().is_relative_to(destination.resolve()):
                raise ValueError("Prepared database or companion paths escape the isolated replacement destination.")
        # The native BaSIS receipt may include a database identity and artifact
        # inventory. Retain it as evidence, never invent a release for Kraken.
        native = _file_record(result_path / "provenance.json", destination)
        receipt = {"schema": SCHEMA, "status": "candidate-prepared", "created": now(),
                   "request_sha256": _digest(payload), "review": review,
                   "pipeline_commit": row["commit"], "workflow_sha256": row["workflow_sha256"],
                   "config_sha256": row["config_sha256"], "result_path": row["result_path"],
                   "verification": "Native preparation completed and recognizable database layout checked.",
                   "native_evidence": native, "exact_replay": False,
                   "database_release": None, "original_database_deleted": False,
                   "selection_updated_by_this_job": False, "saved_runs_modified": False}
        folder = Path(row["folder"])
        path = folder / "replacement-candidate.json"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        row["replacement_receipt"] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "exact_replay": False, "selection_updated_by_this_job": False}
        store.put("database_downloads", row["id"], row)
        ctx.write("Replacement candidate prepared. The old database and saved selection are unchanged.")
    except BaseException as exc:
        from .engine import Cancelled
        row.update(status="cancelled" if isinstance(exc, Cancelled) else "failed", error=str(exc))
        store.put("database_downloads", row["id"], row)
        raise
