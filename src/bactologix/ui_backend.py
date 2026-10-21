"""Explicit command bridge for the HTML workspace; execution stays in the worker."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import shlex
import webbrowser

import yaml

from . import __version__
from .catalog import DATA, PIPELINES, fetch_versions, supported, versions
from .engine import doctor, enqueue, launch_worker
from .runs import (absolute_path, clone_runs, command, create_run, import_samples,
                   parse_config, register_sample, reports, template, validate_launch)
from .store import identifier


def flatten(value, prefix=()):
    result = {}
    for key, item in value.items():
        path = (*prefix, key)
        if isinstance(item, dict) and item:
            result.update(flatten(item, path))
        else:
            result[".".join(path)] = {"path": list(path), "value": item}
    return result


def configuration_fields(tool, text):
    defaults = json.loads((DATA / "config-catalog.json").read_text())["defaults"][tool]
    fields = flatten(defaults)
    fields.update(flatten(parse_config(text)))
    return [{"name": name, "path": row["path"], "yaml": yaml.safe_dump(
        row["value"], sort_keys=False, allow_unicode=True).removesuffix("...\n").strip()}
        for name, row in sorted(fields.items())]


def snapshot(store):
    jobs = store.jobs()
    runs = []
    for run in store.all("runs"):
        cfg = parse_config(run["config"])
        run_jobs = [j for j in jobs if j["kind"] == "workflow" and j["payload"].get("run_id") == run["id"]]
        active = next((j for j in run_jobs if j["status"] in {"queued", "running"}), None)
        status = active["status"] if active else ("completed" if run.get("completed") else "checked" if run["dry_run_ok"] else "draft")
        if not active and run_jobs and run_jobs[0]["status"] in {"failed", "cancelled", "interrupted"}:
            status = run_jobs[0]["status"]
        runs.append({k: run.get(k) for k in ("id", "tool", "sample", "label", "parent", "created", "output", "commit", "dry_run_ok", "cores", "memory_mb", "reuse")} | {
            "status": status, "busy": bool(active), "aware": "off" if cfg.get("kraken_decision_mode") == "off" else "aware",
            "strategy": cfg.get("polish_mode", "—"), "can_handoff": run["tool"] == "hybas" and Path(run["output"], "annotation", run["sample"]["id"] + ".fna").is_file()})
    configs = {t: store.get("configs", t, template(t)) for t in PIPELINES}
    catalogs = {}
    for tool in PIPELINES:
        catalogs[tool] = versions(store, tool)
        for version in catalogs[tool]["versions"]:
            version["supported"] = supported(tool, version["commit"])
    return {"version": __version__, "workspace": str(store.home), "doctor": doctor(store),
            "pipelines": PIPELINES, "installs": store.all("installs"),
            "active": {t: store.get("settings", "active-" + t) for t in PIPELINES},
            "catalogs": catalogs, "samples": store.all("samples"), "runs": runs,
            "jobs": jobs, "reports": reports(store), "configs": configs,
            "fields": {t: configuration_fields(t, configs[t]) for t in PIPELINES},
            "citations": {f.name: f.read_text() for f in DATA.glob("CITATION.*")}}


def _item(store, kind, key):
    if not isinstance(key, str):
        raise ValueError("Choose an existing " + kind)
    value = store.get(kind, key)
    if value is None:
        raise ValueError("The selected item no longer exists")
    return value


def _tool(payload):
    tool = payload.get("tool")
    if tool not in PIPELINES:
        raise ValueError("Choose HybAs or BaSIS")
    return tool


def _ids(payload):
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(x, str) for x in ids):
        raise ValueError("Select between 1 and 500 items")
    return list(dict.fromkeys(ids))


def _report(store, rid):
    report = next((r for r in reports(store) if r["id"] == rid), None)
    if not report:
        raise ValueError("Report no longer exists")
    return report


def dispatch(store, kind, p):
    """Only named UI commands are allowed. Browser-supplied objects are not trusted."""
    if not isinstance(p, dict):
        raise ValueError("Invalid action payload")
    if kind == "refresh":
        return {}
    if kind == "save_conda":
        path = absolute_path(p["path"])
        if not path.is_file():
            raise ValueError("That Conda executable does not exist")
        store.put("settings", "conda", str(path))
        return {"message": "Conda location saved."}
    if kind in {"bootstrap", "smoke"}:
        jid = enqueue(store, kind, {})
        return {"message": "Queued " + jid, "navigate": "jobs"}
    if kind == "refresh_versions":
        tool = _tool(p)
        store.put("catalog", tool, fetch_versions(tool))
        return {"message": "GitHub version list refreshed.", "keep_modal": True}
    if kind == "install":
        tool = _tool(p)
        version = next((v for v in versions(store, tool)["versions"] if v["id"] == p.get("version_id") and v["commit"] == p.get("commit")), None)
        if not version:
            raise ValueError("Version changed; refresh the list and select it again")
        jid = enqueue(store, "install", {"tool": tool, "version": version})
        return {"message": "Queued " + jid, "navigate": "jobs"}
    if kind in {"use_install", "check_install", "repair_install"}:
        item = _item(store, "installs", p.get("id"))
        if kind == "use_install":
            if not item["ready"]:
                raise ValueError("This runner is not ready")
            store.put("settings", "active-" + item["tool"], item["id"])
            return {"message": "Version selected for future drafts."}
        jid = enqueue(store, "check", {"installation": item["id"]}) if kind == "check_install" else enqueue(store, "install", {"tool": item["tool"], "version": item["version"], "repair": True})
        return {"message": "Queued " + jid, "navigate": "jobs"}
    if kind == "add_sample":
        register_sample(store, p["id"].strip(), p["path"].strip(), p["mode"])
        return {"message": "Sample registered.", "navigate": "samples"}
    if kind == "import_samples":
        items = import_samples(store, p["text"])
        return {"message": f"Imported {len(items)} samples.", "navigate": "samples"}
    if kind in {"save_config", "inspect_config", "edit_setting"}:
        tool = _tool(p)
        text = p["text"]
        cfg = parse_config(text)
        if kind == "edit_setting":
            fields = {x["name"]: x for x in configuration_fields(tool, text)}
            field = fields.get(p.get("field"))
            if not field:
                raise ValueError("Choose a listed setting")
            # Apply through the validated configuration parser, including size and type checks.
            value = parse_config("value:\n" + "\n".join("  " + line for line in p["value"].splitlines()))["value"]
            target = cfg
            for part in field["path"][:-1]:
                if not isinstance(target.get(part), dict):
                    target[part] = {}
                target = target[part]
            target[field["path"][-1]] = value
            text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
            parse_config(text)
        if kind == "save_config":
            store.put("configs", tool, text)
        return {"message": "Defaults saved for future drafts." if kind == "save_config" else "Configuration validated." if kind == "inspect_config" else "Setting applied to the draft; save when ready.",
                "config": {"tool": tool, "text": text, "fields": configuration_fields(tool, text)}, "keep_modal": True}
    if kind == "create_runs":
        tool = _tool(p)
        cores, memory = int(p["cores"]), int(p["memory_mb"])
        if not 1 <= cores <= 512 or not 512 <= memory <= 2000000:
            raise ValueError("CPU or memory budget is outside the supported range")
        samples = [_item(store, "samples", sid) for sid in _ids(p)]
        made = []
        for sample in samples:
            try:
                made.append(create_run(store, tool, sample, store.get("configs", tool, template(tool)), cores, memory))
            except Exception as exc:
                raise ValueError(f"Created {len(made)} draft(s). {sample['id']}: {exc}") from exc
        return {"message": f"Created {len(made)} independent drafts.", "navigate": "samples"}
    if kind == "inspect_run":
        run = copy.deepcopy(_item(store, "runs", p.get("id")))
        try:
            run["command"] = shlex.join(command(store, run, "run"))
        except Exception as exc:
            run["command"] = str(exc)
        return {"detail": run, "keep_modal": True}
    if kind == "workflow":
        ids = _ids(p)
        action = p.get("action")
        if action not in {"dry-run", "prepare", "run"}:
            raise ValueError("Unknown workflow action")
        for rid in ids:
            validate_launch(store, rid, action)
        queued = []
        for rid in ids:
            try:
                queued.append(enqueue(store, "workflow", {"run_id": rid, "action": action}))
            except Exception as exc:
                raise ValueError(f"Queued {len(queued)} job(s). {rid}: {exc}") from exc
        return {"message": f"Queued {len(queued)} jobs in order.", "navigate": "jobs"}
    if kind == "clone":
        if not isinstance(p.get("aware_free"), bool) or not isinstance(p.get("strategies"), list) or not p["strategies"]:
            raise ValueError("Choose a source branch and at least one polishing strategy")
        made = clone_runs(store, p["id"], p["aware_free"], list(dict.fromkeys(p["strategies"])))
        return {"message": f"Created {len(made)} independent variants.", "navigate": "samples"}
    if kind in {"revise_run", "handoff"}:
        run = _item(store, "runs", p.get("id"))
        if kind == "revise_run":
            create_run(store, run["tool"], run["sample"], p["text"], run["cores"], run["memory_mb"], run["id"], run["label"] + " · revised", run["installation"])
        else:
            if run["tool"] != "hybas":
                raise ValueError("Choose a HybAs run")
            create_run(store, "basis", dict(run["sample"], path=run["output"]), store.get("configs", "basis", template("basis")), run["cores"], run["memory_mb"], run["id"], run["label"] + " · BaSIS")
        return {"message": "Created a new draft.", "navigate": "samples"}
    if kind == "recover_jobs":
        launch_worker(store)
        return {"message": "Worker requested; active jobs retain their lock."}
    if kind == "cancel_job":
        job = store.job(p["id"])
        if not job or job["status"] not in {"queued", "running"}:
            raise ValueError("This job is no longer active")
        store.cancel(p["id"])
        return {"message": "Stop requested. The job log will be retained."}
    if kind in {"job_log", "download_log"}:
        job = store.job(p["id"])
        if not job:
            raise ValueError("Job does not exist")
        path = store.log_path(job["id"])
        if kind == "download_log":
            if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("Open the full log on disk: " + str(path))
            return {"download": {"name": path.name, "text": path.read_text(errors="replace"), "type": "text/plain"}, "keep_modal": True}
        text = "Waiting for process output…"
        if path.exists():
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 100000))
                text = handle.read().decode("utf-8", errors="replace") or text
        return {"log": {"id": job["id"], "text": text, "error": job.get("error"), "status": job["status"]}, "keep_modal": True}
    if kind == "register_report":
        file = absolute_path(p["path"])
        if not file.is_file() or file.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Choose an existing HTML file")
        rid = identifier("report")
        store.put("reports", rid, {"id": rid, "name": str(p["name"]), "path": str(file), "sample": "User-selected", "run": "External report"})
        return {"message": "Report registered.", "navigate": "reports"}
    if kind in {"open_report", "open_original"}:
        report = _report(store, p.get("id"))
        source = Path(report["path"])
        if kind == "open_original":
            opened = webbrowser.open(source.as_uri(), new=2)
            return {"message": "Requested your local browser." if opened else "No browser handler is available. Open the displayed local path with its companion files.", "keep_modal": True}
        if source.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("This report exceeds the 50 MB viewer limit. Open the original: " + str(source))
        return {"report": report | {"base64": base64.b64encode(source.read_bytes()).decode("ascii")}, "keep_modal": True}
    raise ValueError("Unknown workspace action")


def handle_request(store, request, session):
    """A rerun or duplicate delivery cannot submit the same operation twice."""
    if not isinstance(request, dict) or not isinstance(request.get("id"), str) or len(request["id"]) > 160:
        raise ValueError("Invalid action request")
    seen = session.setdefault("handled-actions", [])
    if request["id"] in seen:
        return session.get("ui-response")
    seen.append(request["id"])
    del seen[:-256]
    try:
        response = {"id": request["id"], "ok": True, **dispatch(store, request.get("kind"), request.get("payload", {}))}
    except Exception as exc:
        response = {"id": request["id"], "ok": False, "message": str(exc)}
    session["ui-response"] = response
    return response
