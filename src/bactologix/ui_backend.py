"""Explicit command bridge for the HTML workspace; execution stays in the worker."""
from __future__ import annotations

import base64
import copy
from functools import lru_cache
import getpass
import json
from pathlib import Path
import shlex
import webbrowser
import uuid

import yaml

from . import __version__
from .catalog import DATA, PIPELINES, fetch_versions, supported, versions
from .engine import doctor, enqueue, enqueue_workflows, launch_worker
from .local import browse_folders, discover_environments, discover_installations, inspect_installation, local_path
from .runs import (absolute_path, clone_runs, command, create_run,
                   parse_config, register_sample, remove_sample, archive_runs, reports, template,
                   dry_run_current, run_status, check_run_files, basis_annotation_problem)
from .store import identifier, display_read, transient_lock
from .storage_guard import activity
from . import databases, updates, hardware, methods, telemetry, support, workstation, queue, results, storage, storage_cleanup, compatibility, comparisons, sample_imports
from . import setup_state, assessment, compute_memory
from . import isolate_actions
from . import desktop, trajectory_preview
from . import reference_catalog
from . import database_builder, followup, maintenance, database_runs, run_config, run_revisions, default_config, installations, readiness

_SNAPSHOTS = {}


def display_busy(exc):
    from .supervision import MonitoringInterrupted
    import sqlite3
    cause = exc.__cause__ if isinstance(exc, MonitoringInterrupted) else exc
    return isinstance(cause, sqlite3.Error) and transient_lock(cause)


def display_snapshot(store, *, cached=False):
    """Keep an existing UI mounted if a render encounters transient contention."""
    from . import update_checks
    with display_read():
        try:
            state = snapshot(store, cached=cached)
        except Exception as exc:
            if not display_busy(exc):
                raise
            prior = _SNAPSHOTS.get(store.home)
            if prior is None:
                return None
            from . import disk_stop
            state = copy.deepcopy(prior)
            state['workspace_busy'] = True
            state['queue']['storage_stop'] = disk_stop.snapshot(store)
            return state
        if not state['app_update']['active_job'] and not state['app_update']['restart_required']:
            try:
                update_checks.tick(store)
            except Exception as exc:
                if not display_busy(exc):
                    raise
        return state


def installation_status(item, jobs, store=None):
    tool, commit = item["tool"], item["version"]["commit"]
    supported_source = supported(tool, commit, store)
    def matches(job):
        p = job["payload"]
        return ((job["kind"] == "check" and p.get("installation") == item["id"])
                or (job["kind"] == "install" and p.get("tool") == tool and p.get("version", {}).get("commit") == commit)
                or (job["kind"] == "connect" and p.get("tool") == tool and p.get("path") == item.get("original_source") and p.get("commit") == commit))
    related = [j for j in jobs if matches(j)]
    latest = related[0] if related else None
    busy = bool(latest and latest["status"] in {"queued", "running"})
    if busy:
        state, message = "Setting up", "Open the job log to follow setup."
    elif not supported_source:
        state, message = "Version not supported", "Source downloaded. Update Bactologix or choose a version marked Runner supported."
    elif item.get('source') and not Path(item['source']).is_dir():
        state, message = "Setup incomplete", "Downloaded source is missing. Retry setup to restore the pinned source."
    elif item.get('setup_mode') == 'first_install' and not item.get('setup_completed'):
        state, message = "Setup incomplete", "First installation has not finished. Retry setup to prepare and check its tool environments."
    elif item.get("ready") and item.get('controller') and not Path(item['controller']).is_dir():
        state, message = "Setup incomplete", "The recorded runner folder is missing. Retry setup to recreate it."
    elif item.get("ready"):
        state, message = "Runner set up", "Runner verified. Runs reuse installed tool environments. Missing tools require an explicit installation."
        required = item.get("controller_requirements")
        if required:
            message = f"Runner verified: Python {required['python']} / Snakemake {required['snakemake']}. Runs reuse installed tool environments. Missing tools require an explicit installation."
    elif latest and latest["status"] in {"failed", "cancelled", "interrupted"}:
        state, message = "Setup incomplete", latest.get("error") or "Read the setup log, then retry setup."
    else:
        state, message = "Ready to set up", "Compatible source is downloaded. Finish setup to create or reuse its runner."
    return {**item, "supported": supported_source, "setup_state": state, "setup_message": message,
            "setup_busy": busy, "setup_job": latest["id"] if latest else None}


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
    rows = _small_field_rows(tool, text) if len(text) <= 16384 else _field_rows(tool, text)
    return [{"name": name, "path": list(path), "yaml": value} for name, path, value in rows]


def _field_rows(tool, text):
    defaults = json.loads((DATA / "config-catalog.json").read_text())["defaults"][tool]
    fields = flatten(defaults)
    fields.update(flatten(parse_config(text)))
    return tuple((name, tuple(row["path"]), yaml.safe_dump(
        row["value"], sort_keys=False, allow_unicode=True).removesuffix("...\n").strip())
        for name, row in sorted(fields.items()))


_small_field_rows = lru_cache(maxsize=12)(_field_rows)


def workspace_label():
    """Display the local app account; never change the workspace's location."""
    try:
        name = " ".join(getpass.getuser().split())
    except (OSError, KeyError):
        return "Local workspace"
    if not name or name.casefold() in {"root", "system", "administrator"}:
        return "Local workspace"
    name = name[:80]
    name = name[0].upper() + name[1:]
    return f"{name}'s workspace"


def snapshot(store, *, cached=False):
    # Background observations must not trigger another full workspace/source
    # inspection merely to poll their progress. This is display data only;
    # dispatch and workers still perform fresh authorization checks.
    if cached and store.home in _SNAPSHOTS:
        return copy.deepcopy(_SNAPSHOTS[store.home])
    from . import disk_pause, notifications, supervision, workflow_unlock
    from .queue import reconcile_interrupted
    reconcile_interrupted(store)
    jobs = store.jobs()
    records = store.job_display_records()
    workflow_jobs = {}
    for j in jobs:
        jid = j['id']
        if j['kind'] == 'workflow':
            workflow_jobs.setdefault(j['payload'].get('run_id'), []).append(j)
        if jid in records['job_history_hidden']:
            j['history_hidden'] = True
        j["monitoring"] = supervision.snapshot(store, j['id'])
        j["disk_safety"] = disk_pause.display_state(j, records['disk_safety'].get(jid),
                                                 records['disk_safety_requests'].get(jid, {}))
        j["telemetry"] = telemetry.view(records['job_telemetry'].get(jid), j)
    readiness.history_guidance(store, jobs)
    queue_state = store.queue_snapshot()
    app_update = updates.status(store, jobs=jobs, queue=queue_state)
    # pip briefly replaces package data. Keep the already-rendered workspace
    # available until restart instead of reading those files during replacement.
    if (app_update["active_job"] or app_update["restart_required"]) and store.home in _SNAPSHOTS:
        cached = copy.deepcopy(_SNAPSHOTS[store.home])
        return {**cached, "jobs": jobs, "app_update": app_update, "queue": queue_state,
                "notifications": notifications.panel(store, jobs, cached.get('runs', []))}
    runs = []
    archived = {r["id"] for r in store.all("run_visibility") if r.get("archived")}
    saved_runs = store.all("runs")
    for run in saved_runs:
        cfg = parse_config(run["config"])
        kraken_mode = run.get('identification', {}).get('selection', {}).get('mode', cfg.get('kraken_decision_mode'))
        run_jobs = workflow_jobs.get(run['id'], ())
        active = next((j for j in run_jobs if j["status"] in {"queued", "running"}), None)
        checked = dry_run_current(store, run)
        status = run_status(run, run_jobs, checked)
        followup_sources = followup.source_options(store, run) if status == "completed" else []
        runs.append({k: run.get(k) for k in ("id", "tool", "sample", "label", "parent", "created", "output", "commit", "dry_run_ok", "cores", "memory_mb", "reuse", "run_type", "variant", "identification", "experimental")} | {
            "previous_draft_jobs": run.get("previous_draft_jobs", []),
            "draft_generation": run.get("draft_generation", 0),
            "swap_mb": compute_memory.allowance(cfg),
            "aware_state": run.get("aware"), "status": status, "assessment": assessment.summary(store, run, run_jobs=run_jobs), "busy": bool(active), "archived": run["id"] in archived, "dry_run_ok": checked, "aware": "off" if kraken_mode == "off" else "aware",
            "kraken_mode": "off" if cfg.get("run_kraken") is False else kraken_mode,
            "can_micp_handoff": run["tool"] == "hybas" and status == "completed",
            "can_followup": bool(followup_sources), "followup_sources": followup_sources,
            "followup_transition": run.get('followup_transition'),
            "review_enabled": cfg.get("kraken_review_before_filtering", False), "strategy": cfg.get("polish_mode", "—"),
            "can_handoff": run["tool"] == "hybas" and status == "completed" and
                basis_annotation_problem(run['output'], run['sample']['id']) is None})
    configs = {t: run_config.defaults(store, t) for t in PIPELINES}
    catalogs = {}
    for tool in PIPELINES:
        catalogs[tool] = versions(store, tool)
        for version in catalogs[tool]["versions"]:
            version["supported"] = supported(tool, version["commit"], store)
    coverage = {run["id"]: run["assessment"] for run in runs}
    report_rows = []
    for report in reports(store):
        # Refreshed editions retain their own evidence-time coverage, not the
        # current output directory's possibly newer assessment.
        if not report.get("refresh") and report.get("run") in coverage:
            report = {**report, "assessment": coverage[report["run"]]}
        report_rows.append(report)
    hardware_panel = hardware.panel(store)
    result = {"channel": store.channel, "application_title": "Bactologix Dev" if store.channel == "dev" else "Bactologix", "version": __version__, "workspace": str(store.home), "workspace_label": workspace_label(), "doctor": doctor(store),
            "desktop_close_available": desktop.close_directory() is not None,
            "notifications": notifications.panel(store, jobs, runs),
            "setup_overview": {t: setup_state.overview(store, t) for t in PIPELINES},
            "pipelines": PIPELINES, "installs": [installation_status(i, jobs, store) for i in store.all("installs")],
            "app_update": app_update, "hardware": hardware_panel, "workstation_checks": workstation.history(store),
            "resource_defaults": hardware_panel["defaults"],
            "active": {t: store.get("settings", "active-" + t) for t in PIPELINES},
            "catalogs": catalogs, "samples": store.all("samples"), "runs": runs,
            "workflow_locks": workflow_unlock.review_list(store, saved_runs, jobs, archived),
            "isolate_exports": isolate_actions.export_history(store),
            "jobs": jobs, "queue": queue_state, "reports": report_rows, "configs": configs,
            "fields": {t: configuration_fields(t, configs[t]) for t in PIPELINES},
            "databases": {t: databases.panel(store, t) for t in PIPELINES},
            "reference_databases": reference_catalog.choices(store),
            "environments": {i["id"]: maintenance.environment_panel(store, i, jobs) for i in store.all("installs")},
            "database_builder": {"available": database_builder.available(store), "starter": database_builder.starter(store), "builds": store.all("database_builds")},
            "database_downloads": [{**d, "job_status": next((j["status"] for j in jobs if j["id"] == d.get("job")), d.get("status"))}
                                   for d in store.all("database_downloads")],
            "download_options": {t: {**maintenance.download_panel_capability(store.get("installs", store.get("settings", "active-" + t)), store),
                                      "parent": str(store.home / "resources")}
                                 for t in PIPELINES},
            "citations": {f.name: f.read_text() for f in DATA.glob("CITATION.*")}}
    if len(_SNAPSHOTS) >= 8:
        _SNAPSHOTS.pop(next(iter(_SNAPSHOTS)))
    _SNAPSHOTS[store.home] = copy.deepcopy(result)
    return result


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
        raise ValueError("Choose HybAs AWARE or BaSIS AWARE")
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


def _run_detail(store, run, *, just_created=False):
    run = copy.deepcopy(run)
    run.setdefault("draft_generation", 0)
    run['swap_mb'] = compute_memory.policy(run)['swap_mb']
    if run['swap_mb'] and not just_created:
        # This is the explicit Refresh checks path. Background snapshots
        # retain the ordinary inventory cache and never trigger probes.
        hardware.inventory(store, refresh=True)
    run["database_paths"] = database_runs.paths(store, run)
    cfg = parse_config(run['config'])
    vfdb = cfg.get('modules', {}).get('vfdb', {}) if run['tool'] == 'basis' else {}
    run['has_external_vfdb'] = isinstance(vfdb, dict) and any(
        vfdb.get(key) for key in ('database', 'reference_fasta', 'metadata', 'database_version'))
    run["readiness"] = readiness.panel(store, run, just_created=just_created)
    run["settings"] = run_config.editor(run["tool"], run["config"])
    try:
        run["command"] = shlex.join(command(store, run, "run"))
    except Exception as exc:
        run["command"] = str(exc)
    return {"detail": run, "keep_modal": True}


def dispatch(store, kind, p):
    """Only named UI commands are allowed. Browser-supplied objects are not trusted."""
    if not isinstance(p, dict):
        raise ValueError("Invalid action payload")
    if kind == 'close_desktop':
        if p:
            raise ValueError('The desktop close request takes no parameters.')
        desktop.request_close()
        return {'desktop_closing': True, 'keep_modal': True}
    if kind == 'clear_job_history':
        cleared = store.clear_job_history()
        noun = 'attempt' if cleared == 1 else 'attempts'
        return {'cleared': cleared, 'message': f'Cleared {cleared} finished {noun} from job history.'}
    if kind in {'mark_notifications_read', 'clear_notifications', 'save_notification_settings', 'claim_desktop_notifications', 'check_notification_updates'}:
        from . import notifications, update_checks
        if kind == 'mark_notifications_read':
            return notifications.mark_read(store, p)
        if kind == 'clear_notifications':
            return notifications.clear(store, p)
        if kind == 'save_notification_settings':
            return notifications.configure(store, p)
        if kind == 'claim_desktop_notifications':
            # Reconcile outcomes before claiming so resolved review/update
            # alerts cannot be delivered from an old browser snapshot.
            notifications.panel(store)
            return notifications.claim_desktop(store, p)
        started = update_checks.tick(store, force=True)
        return {'message': 'Checking update availability in the background.' if started else 'A check is already running, was requested recently, or is disabled for this session.', 'keep_modal': True}
    isolate_readonly = (isolate_actions.ACTIONS - {'preview_isolate_export', 'start_isolate_export'}) | {'inspect_output_files', 'open_output_folder'}
    # Workflow submission checks the current application in enqueue_workflows;
    # Store.submit checks for a competing update inside its write transaction.
    if kind not in {"workflow", "refresh", "job_log", "download_log", "job_metrics", "download_metrics", "support_preview", "download_support", "open_report", "open_original", "inspect_run", "inspect_methods", "download_methods", "inspect_results", "download_result", "open_result_report", "inspect_storage", "inspect_storage_cleanup", "inspect_storage_cleanup_receipt", "download_storage", "inspect_compatibility", "download_compatibility", "compare_saved_runs", "download_comparison", "preview_samples", "refresh_app_releases", "cancel_job"} | isolate_readonly:
        updates.assert_current(store)
        if updates.status(store)["active_job"]:
            raise ValueError("Bactologix is updating. Wait for the result and restart the application.")
    if kind in isolate_actions.ACTIONS:
        return isolate_actions.dispatch(store, kind, p, enqueue)
    if kind == 'restart_as_browser':
        from .desktop import request_browser
        if request_browser(store):
            return {'message': 'Opening a browser window owned by this desktop session. Closing its last window stops this session.'}
        from .launcher import open_browser
        item = updates.restart_control.current_launcher(store)
        if not item:
            raise ValueError('This session has no active launcher. Open its current address in your browser.')
        if not open_browser(item['url']):
            raise ValueError('Could not open the regular browser. Open the current workspace address manually.')
        return {'message': 'Workspace opened in your browser. You can close the app window; the server and jobs keep running.'}
    if kind == 'check_workstation':
        payload = workstation.request(store, p.get('installation'))
        jid = enqueue(store, 'workstation', payload)
        return {'message': 'Workstation check queued: ' + jid, 'navigate': 'jobs'}
    if kind == 'inspect_workstation':
        return {'workstation_check': workstation.inspect(store, p.get('id')), 'keep_modal': True}
    if kind == 'observe_workstation':
        return {'workstation_check': workstation.record_observation(store, p.get('id'), p.get('key'), p.get('confirmed')), 'keep_modal': True}
    if kind == 'workstation_report':
        return {'report': workstation.artifact(store, p.get('id'), 'report'), 'keep_modal': True}
    if kind == 'download_workstation':
        return {'download': workstation.artifact(store, p.get('id'), 'download'), 'keep_modal': True}
    if kind == 'preview_report_refresh':
        from . import report_refresh
        return {'refresh_plan': report_refresh.inspect(store, p.get('id'), target=p.get('target', 'standard')), 'keep_modal': True}
    if kind == 'regenerate_report':
        from . import report_refresh
        plan = report_refresh.request(store, p.get('id'), p.get('evidence_sha256'), p.get('renderer_sha256'),
                                      p.get('target', 'standard'), p.get('inputs_sha256'))
        jid = enqueue(store, 'report_refresh', plan)
        return {'message': 'Report regeneration queued: ' + jid, 'navigate': 'jobs'}
    if kind == "refresh":
        return {}
    if kind == 'detect_hardware':
        hardware.inventory(store, refresh=True)
        return {'message': 'Hardware detection refreshed.', 'keep_modal': True}
    if kind == 'save_compute_defaults':
        return hardware.apply_custom(store, _tool(p), p.get('cores'), p.get('memory_mb'), p.get('gpu_mode'),
                                     p.get('revisions'), p.get('hardware_id'),
                                     **{key: p[key] for key in ('swap_mb', 'auto_swap') if key in p})
    if kind == 'apply_resource_preset':
        return hardware.apply_preset(store, p.get('preset'), p.get('tools'), p.get('revisions'), p.get('hardware_id'))
    if kind == "refresh_app_releases":
        store.put("app_catalog", "releases", updates.fetch_releases())
        return {"message": "Bactologix releases refreshed.", "keep_modal": True}
    if kind == 'update_all':
        raise ValueError('Global updates have been removed. Use Update Bactologix for the application, or Install & update for each pipeline.')
    if kind == "update_app":
        catalog = store.get("app_catalog", "releases", {})
        release = next((r for r in catalog.get("releases", []) if r["id"] == p.get("release_id") and r["version"] == p.get("version")), None)
        if not release or not release["available"]:
            raise ValueError("Refresh the releases and select an available Bactologix wheel.")
        if not updates.channel_accepts(release["version"]):
            raise ValueError("Choose an application release from this installation channel.")
        if release["version"] == __version__:
            raise ValueError("You are already running this Bactologix version.")
        launcher = updates.restart_control.current_launcher(store)
        jid = enqueue(store, "app_update", {"release": release, 'launcher_id': launcher['id'] if launcher else None})
        return {"message": "Application update queued: " + jid, "navigate": "jobs"}
    if kind == "browse_folders":
        path = p.get("path", "")
        if p.get("nearest") is True and path:
            path = local_path(path)
            while not path.is_dir() and path != path.parent:
                path = path.parent
        return {"browser": browse_folders(store, path, p.get("offset", 0),
                                          str(p.get("query", "")), p.get("show_hidden") is True), "keep_modal": True}
    if kind == 'refresh_reference_databases':
        return {'message': 'Available GeneScope databases refreshed.', 'keep_modal': True}
    if kind == "scan_databases":
        tool = _tool(p)
        found = databases.scan(store, tool, p.get('root', ''))
        if tool == 'micpscope':
            for row in found['candidates'].get('reference', []):
                reference_catalog.register(store, row['path'])
        return {"database_scan": found, "keep_modal": True}
    if kind == 'factory_reset_preview':
        from . import factory_reset, async_tasks
        return {'reset_plan': factory_reset.preview(store, p.get('delete_databases', False),
                                                    checkpoint=async_tasks.checkpoint), 'keep_modal': True}
    if kind == 'factory_reset_execute':
        from . import factory_reset
        jid = factory_reset.request(store, p.get('plan_id'), p.get('signature'), p.get('confirmation'),
                                    p.get('acknowledged'), p.get('database_confirmation', ''))
        return {'job_id': jid, 'message': 'Workspace reset queued: ' + jid, 'navigate': 'storage'}
    if kind == 'database_redownload_preview':
        from . import database_redownload
        return {'redownload_plan': database_redownload.preview(store, _tool(p), p.get('id'), lineage=p.get('lineage')),
                'keep_modal': True}
    if kind == 'database_redownload_request':
        from . import database_redownload
        payload = database_redownload.request(store, _tool(p), p.get('id'), p.get('signature'), p.get('parent'),
            choice=p.get('choice'), cores=p.get('cores', 4), memory_mb=p.get('memory_mb', 16000), lineage=p.get('lineage'))
        jid = enqueue(store, 'database_redownload', payload)
        return {'job_id': jid, 'message': 'New database snapshot queued: ' + jid, 'navigate': 'jobs'}
    if kind == 'recommend_swap':
        from . import swap_adviser
        tool, config = _tool(p), p.get('config')
        kraken_path = p.get('kraken_path')
        if 'kraken_path' in p:
            if tool != 'hybas' or config is not None or p.get('run_id'):
                raise ValueError('A database picker selection applies only to HybAs compute defaults.')
            if kraken_path is None:  # The picker explicitly selected “Use pipeline default”.
                kraken_path = str(databases._hybas_paths(store, {})['kraken'])
            elif not isinstance(kraken_path, str) or not kraken_path.strip():
                raise ValueError('Choose a Kraken database folder or select Use pipeline default.')
        if config is not None:
            if not isinstance(config, dict) or config.get('tool') != tool or not isinstance(config.get('text'), str):
                raise ValueError('The recommendation configuration belongs to another pipeline.')
            config = run_config.compile_config(tool, config['text'], config.get('changes'))
        return {'swap_recommendation': swap_adviser.recommend(store, tool, p.get('memory_mb'),
                run_id=p.get('run_id'), config=config, kraken_path=kraken_path), 'keep_modal': True}
    if kind == "manage_environments":
        payload = maintenance.environment_request(store, p.get("id"), p.get("selected"), p.get("action"))
        jid = enqueue(store, "environments", payload)
        return {"message": "Environment " + payload["action"] + " queued: " + jid, "navigate": "jobs"}
    if kind == "set_environment_cache":
        maintenance.set_cache(store, p.get("id"), p.get("path"))
        return {"message": "Environment folder saved for this installation and future drafts. Matching environments are reused automatically.",
                "open_environments": p.get("id"), "keep_modal": True}
    if kind == "reuse_environment_choices":
        item = _item(store, 'installs', p.get('id'))
        return {'cache_choices': {'id': item['id'], 'choices': installations.cache_choices(store, item)}, 'keep_modal': True}
    if kind == 'preview_uninstall':
        return {'uninstall_plan': installations.uninstall_plan(store, p.get('id')), 'keep_modal': True}
    if kind == 'uninstall_version':
        return {'message': installations.uninstall(store, p.get('id'), p.get('signature')), 'navigate': 'install'}
    if kind == "validate_reference_spec":
        spec, assets = database_builder.validate(store, p.get("spec"))
        return {"message": f"Input structure checked: {len(spec['proteins'])} protein records, {len(assets)} local files. Full sequence checks run in Database Builder."}
    if kind == "build_reference_database":
        payload = database_builder.request(store, p.get("spec"), p.get("cores", 2), p.get("memory_mb", 2048))
        jid = enqueue(store, "database_build", payload)
        row = store.get("database_builds", payload["id"])
        row["job"] = jid
        store.put("database_builds", row["id"], row)
        return {"message": "Database Builder queued. Progress and logs are in Jobs.", "job": jid}
    if kind == "download_database":
        payload = maintenance.download_request(store, _tool(p), p.get("id"), p.get("parent"),
                                               p.get("cores", 4), p.get("memory_mb", 16000), p.get("lineage", "bacteria_odb10"))
        jid = enqueue(store, "database_download", payload)
        return {"message": "Database preparation queued: " + jid, "navigate": "jobs"}
    if kind == "resume_database_download":
        row = _item(store, "database_downloads", p.get("id"))
        job = store.job(row["job"])
        if not job or job["status"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("Only a stopped or failed download can be resumed.")
        kind = 'database_redownload' if row['request'].get('redownload_review') else 'database_download'
        jid = enqueue(store, kind, row["request"])
        return {"message": "Database preparation resumed: " + jid, "navigate": "jobs"}
    if kind == "use_downloaded_database":
        row = _item(store, "database_downloads", p.get("id"))
        if row.get("status") != "completed":
            raise ValueError("Wait for database preparation to finish.")
        job = store.job(row.get('job'))
        if not job or job['status'] != 'succeeded':
            raise ValueError('Wait for the database preparation job to finish successfully.')
        if row.get('request', {}).get('redownload_review'):
            if not row.get('replacement_receipt'):
                raise ValueError('The replacement database receipt is not available. Review its preparation job.')
        checked = databases.inspect(store, row["tool"], row["resource"], row["result_path"])
        return {"database_check": {"tool": row["tool"], **checked}, "keep_modal": True,
                "message": "Downloaded location selected. Save locations to use it for future drafts."}
    if kind == "load_databases":
        return {"database_saved": _tool(p), "message": "Saved database locations reloaded.", "keep_modal": True}
    if kind == "check_database":
        tool = _tool(p)
        return {"database_check": {"tool": tool, **databases.inspect(store, tool, p.get("id"), p.get("path"))}, "keep_modal": True}
    if kind == "save_databases":
        tool = _tool(p)
        text = databases.save(store, tool, p.get("changes"), p.get("revision"))
        return {"database_saved": tool, "message": "Database locations saved for future run drafts.",
                "config": {"tool": tool, "text": text, "fields": configuration_fields(tool, text)}, "keep_modal": True}
    if kind == "discover_environments":
        return {"local_envs": discover_environments(store), "keep_modal": True}
    if kind == "discover_installations":
        return {"discovery": discover_installations(store, _tool(p), p.get("root", "")), "keep_modal": True}
    if kind == "inspect_installation":
        return {"local_install": inspect_installation(p["path"], _tool(p), store), "keep_modal": True}
    if kind == "connect_installation":
        tool = _tool(p)
        info = inspect_installation(p["path"], tool, store)
        if info["commit"] != p.get("commit"):
            raise ValueError("The checkout changed. Inspect the folder again.")
        if not info["supported"]:
            raise ValueError("This source version needs an adapter compatibility review before managed runs.")
        if p.get("runner_mode") not in {"existing", "managed"}:
            raise ValueError("Choose an existing environment or a Bactologix runner.")
        if p["runner_mode"] == "existing" and not str(p.get("controller", "")).strip():
            raise ValueError("Choose the existing Conda environment folder, or select a Bactologix runner.")
        payload = {"tool": tool, "path": info["path"], "commit": info["commit"],
                   "controller": str(local_path(p["controller"])) if p["runner_mode"] == "existing" else "",
                   "rule_cache": str(local_path(p["rule_cache"])) if p.get("rule_cache") else ""}
        jid = enqueue(store, "connect", payload)
        return {"message": "Checking and connecting your installation: " + jid, "navigate": "jobs"}
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
        from .release_contract import refresh
        try:
            refresh(store, tool)
        except (OSError, ValueError, KeyError) as exc:
            return {"message": "Refresh failed; showing the dated cached catalogue. " + str(exc), "keep_modal": True}
        return {"message": "GitHub version list refreshed.", "keep_modal": True, "refreshed_catalog": tool}
    if kind == "install":
        tool = _tool(p)
        version = next((v for v in versions(store, tool)["versions"] if v["id"] == p.get("version_id") and v["commit"] == p.get("commit")), None)
        if not version:
            raise ValueError("Version changed; refresh the list and select it again")
        jid = enqueue(store, "install", {"tool": tool, "version": version})
        return {"message": "Queued " + jid, "navigate": "jobs"}
    if kind in {"use_install", "check_install", "repair_install", "setup_install"}:
        item = _item(store, "installs", p.get("id"))
        if not supported(item["tool"], item["version"]["commit"], store):
            raise ValueError("This version is not supported. Update Bactologix or choose a Runner supported snapshot.")
        if kind == "use_install":
            if not item["ready"]:
                raise ValueError("This runner is not ready")
            store.activate_installation(item['id'])
            return {"message": "Version selected for future drafts."}
        if item.get("origin") == "local":
            jid = enqueue(store, "check", {"installation": item["id"], "repair": kind == "repair_install", "setup": kind == "setup_install"})
        else:
            jid = enqueue(store, "check", {"installation": item["id"]}) if kind == "check_install" else enqueue(store, "install", {"tool": item["tool"], "version": item["version"], "repair": kind == "repair_install"})
        return {"message": "Queued " + jid, "navigate": "jobs"}
    if kind in {"preview_manifest", "import_manifest"}:
        from . import manifest_inputs
        if kind == "preview_manifest":
            return {"manifest_import": manifest_inputs.review(str(local_path(p["path"]))), "keep_modal": True}
        rows = manifest_inputs.commit(store, str(local_path(p["path"])), p.get("token"))
        return {"message": f"Imported {len(rows)} manifest samples.", "navigate": "samples"}
    if kind == "add_sample":
        register_sample(store, p["id"].strip(), str(local_path(p["path"])), p["mode"])
        return {"message": "Sample registered.", "navigate": "samples"}
    if kind == "remove_sample":
        result = remove_sample(store, p.get("id"), p.get("sample"))
        message = f"Sample removed. {len(result['archived'])} saved run(s) moved to Archived. Files and reports are preserved."
        if result["active"]:
            message += f" {len(result['active'])} queued/running run(s) stay visible."
        return {"message": message, "removed_sample": p["id"], "archived_runs": result["archived"], "keep_modal": True}
    if kind in {"archive_runs", "restore_runs"}:
        archived = kind == "archive_runs"
        ids = archive_runs(store, _ids(p), archived)
        return {"message": f"{len(ids)} run(s) " + ("moved to Archived. Files and reports are preserved." if archived else "restored to the current run list."),
                "archived_runs" if archived else "restored_runs": ids, "keep_modal": True}
    if kind == 'preview_samples':
        return {'sample_import': sample_imports.review(store, p.get('text')), 'keep_modal': True}
    if kind == "import_samples":
        token = p.get('token')
        if not isinstance(token, str) or len(token) != 64:
            raise ValueError('Preview the sample table before importing.')
        items = sample_imports.commit(store, p.get('text'), token)
        return {"message": f"Imported {len(items)} samples.", "navigate": "samples"}
    if kind in {"default_config_editor", "save_default_config"}:
        return default_config.edit(store, _tool(p), p, save=kind == "save_default_config")
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
            if run_config.normalize_kraken_folder(tool, cfg):
                text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
            with activity(store), store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                hardware.sync_config_defaults(db, tool, text)
                db.execute("INSERT INTO items VALUES ('configs',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                           (tool, json.dumps(text)))
        return {"message": "Defaults saved for future drafts." if kind == "save_config" else "Configuration validated." if kind == "inspect_config" else "Setting applied to the draft; save when ready.",
                "config": {"tool": tool, "text": text, "fields": configuration_fields(tool, text)}, "keep_modal": True}
    if kind == "edit_run_settings":
        response = run_revisions.editor(store, p.get("id"))
        if p.get('variant'):
            from . import variants
            variants.eligible(store, _item(store, 'runs', p.get('id')))
            response['edit_run']['variant'] = True
        elif p.get('recalculate_swap') is True:
            editor = response['edit_run']
            if editor['tool'] != 'hybas' or not editor['can_edit_draft']:
                raise ValueError('This saved run can no longer be edited in place. Refresh its run checks.')
            editor['recalculate_swap'] = True
        return response
    if kind == "preview_vfdb_repair":
        from . import resource_repair
        return {"resource_repair": resource_repair.preview(store, p.get("id")), "keep_modal": True}
    if kind == "apply_vfdb_repair":
        from . import resource_repair
        child = resource_repair.apply(store, p.get("id"), p.get("signature"))
        response = dispatch(store, "inspect_run", {"id": child["id"]})
        response.update(message="Created a revised draft for native VFDB preparation. Validate it before running. The original run is unchanged.",
                        navigate="runs", open_run=child["id"], keep_modal=False)
        return response
    if kind in {"inspect_setup_advice", "apply_setup_advice"}:
        from . import setup_advice
        if kind == "apply_setup_advice":
            return setup_advice.apply(store, p)
        return {"setup_advice": setup_advice.review(store, p), "keep_modal": True}
    if kind == "run_config_editor":
        tool = _tool(p)
        text = p.get("text", run_config.defaults(store, tool))
        if 'text' not in p:
            from .medaka_review import editor_text
            text = editor_text(store, tool, text)
        if not isinstance(text, str):
            raise ValueError("Provide configuration as YAML text.")
        text = run_config.compile_config(tool, text, p.get("changes"))
        if p.get("validate"):
            run_config.validate(tool, text)
        view = p.get("view", "settings")
        if view not in {"purpose", "samples", "settings", "yaml", "review"}:
            raise ValueError("Choose a run configuration view.")
        return {"run_config": run_config.editor(tool, text), "run_config_view": view, "keep_modal": True,
                "message": "Run settings checked. The native dry-run is still required." if p.get("validate") else ""}
    if kind == "create_runs":
        tool = _tool(p)
        defaults = hardware.run_defaults(store, tool)
        cores, memory = int(p.get("cores", defaults['cores'])), int(p.get("memory_mb", defaults['memory_mb']))
        if not 1 <= cores <= 512 or not 512 <= memory <= 2000000:
            raise ValueError("CPU or memory budget is outside the supported range")
        samples = [_item(store, "samples", sid) for sid in _ids(p)]
        text = run_config.defaults(store, tool)
        if "config" in p:
            config = p["config"]
            if not isinstance(config, dict) or config.get("tool") != tool or not isinstance(config.get("text"), str):
                raise ValueError("The run configuration belongs to another pipeline. Review its settings again.")
            text = run_config.compile_config(tool, config["text"], config.get("changes"))
            run_config.validate(tool, text)
        auto_swap = hardware.automatic_swap_setting(tool, p.get('auto_swap', defaults['auto_swap'] and 'swap_mb' not in p))
        if auto_swap or 'swap_mb' in p:
            text = compute_memory.with_swap(text, 0 if auto_swap else p['swap_mb'])
        compute_memory.policy({'memory_mb': memory}, parse_config(text))
        made = []
        for sample in samples:
            try:
                made.append(create_run(store, tool, sample, text, cores, memory, **({'auto_swap': True} if auto_swap else {})))
            except Exception as exc:
                raise ValueError(f"Created {len(made)} draft(s). {sample['id']}: {exc}") from exc
        response = {"message": f"Created {len(made)} independent drafts with this run configuration.", "navigate": "runs"}
        if len(made) == 1:
            response.update(_run_detail(store, made[0], just_created=True))
            response.update(keep_modal=False, open_run=made[0]["id"])
        return response
    if kind == "inspect_run":
        return _run_detail(store, _item(store, "runs", p.get("id")))
    if kind in {'compare_saved_runs', 'download_comparison'}:
        doc = comparisons.panel(store, p.get('ids'))
        return {'comparison': doc, **({'download': comparisons.export(doc)} if kind == 'download_comparison' else {})}
    if kind == 'inspect_compatibility':
        return {'compatibility': compatibility.panel(store), 'keep_modal': True}
    if kind == 'download_compatibility':
        doc = compatibility.panel(store)
        return {'compatibility': doc, 'download': compatibility.export(doc), 'keep_modal': True}
    if kind == 'inspect_storage':
        return {'storage': storage.panel(store), 'keep_modal': True}
    if kind == 'inspect_storage_cleanup':
        return {'storage_cleanup': storage_cleanup.panel(store), 'keep_modal': True}
    if kind == 'inspect_storage_cleanup_receipt':
        return {'cleanup_receipt': storage_cleanup.receipt(store, p.get('id')), 'keep_modal': True}
    if kind == 'preview_storage_cleanup':
        from . import async_tasks
        if p.get('scope') == 'failed':
            plan = storage_cleanup.preview_failed(store, checkpoint=async_tasks.checkpoint)
        else:
            plan = storage_cleanup.preview(store, p.get('targets'), checkpoint=async_tasks.checkpoint)
        return {'cleanup_plan': plan, 'keep_modal': True}
    if kind == 'execute_storage_cleanup':
        jid = storage_cleanup.request(store, p.get('plan_id'), p.get('signature'),
                                      p.get('confirmation'), p.get('acknowledged'))
        return {'cleanup_refresh': True, 'job_id': jid,
                'message': 'Cleanup queued. Follow it in Jobs & progress and find the receipt in Storage.'}
    if kind == 'scan_storage':
        storage.scan(store, p.get('revision'), p.get('include_external', False))
        return {'storage': storage.panel(store), 'message': 'Storage measurement saved. Review its scope and limits.', 'keep_modal': True}
    if kind == 'download_storage':
        return {'download': storage.download(store), 'keep_modal': True}
    if kind == 'inspect_results':
        return {'results': results.inspect(store, p.get('id')), 'keep_modal': True}
    if kind in {'inspect_output_files', 'open_output_folder'}:
        from . import output_inventory
        if kind == 'inspect_output_files':
            return {'output_files': output_inventory.inspect(store, p.get('id'), p.get('relative', ''),
                        p.get('snapshot', ''), p.get('page', 0)), 'keep_modal': True}
        from .launcher import open_folder
        path = output_inventory.folder(store, p.get('id'), p.get('relative', ''), p.get('snapshot', ''))
        opened = open_folder(path)
        return {'message': 'Requested the file manager on the computer running Bactologix: ' + opened, 'keep_modal': True}
    if kind == 'open_report_folder':
        from .launcher import open_folder
        if p.get('run_id'):
            doc = results.inspect(store, p['run_id'])
            report = next((r for r in doc['reports'] if r['id'] == p.get('id') and r['state'] == 'available'), None)
            if report is None:
                raise ValueError('Report no longer available for this run')
        else:
            report = _report(store, p.get('id'))
        folder = open_folder(Path(report['path']).parent)
        return {'message': 'Requested the file manager on the computer running Bactologix: ' + folder, 'keep_modal': True}
    if kind == 'open_result_report':
        doc = results.inspect(store, p.get('id'))
        row = next((r for r in doc['reports'] if r['id'] == p.get('report_id')), None)
        if row is None:
            raise ValueError('Choose a report from this run.')
        artifact = results.download(store, doc['id'], 'report', row['id'])
        run = store.get('runs', doc['id']) or {}
        content = trajectory_preview.preview(artifact['base64'], row['path'], run.get('output'))
        return {'report': {**row, 'run_id': doc['id'], 'base64': content}, 'keep_modal': True}
    if kind == 'download_result':
        return {'download': results.download(store, p.get('id'), p.get('format'), p.get('report_id')), 'keep_modal': True}
    if kind == 'inspect_methods':
        return {'methods': methods.build(store, _item(store, 'runs', p.get('id'))), 'keep_modal': True}
    if kind == 'download_methods':
        return {'download': methods.download(store, _item(store, 'runs', p.get('id')), p.get('format')),
                'keep_modal': True}
    if kind == "preview_run_databases":
        return {"database_run_plan": database_runs.preview(store, p.get("ids")), "keep_modal": True}
    if kind == "apply_run_databases":
        made = database_runs.apply(store, p.get("ids"), p.get("selections"), p.get("signature"))
        response = {"message": f"Created {len(made)} revised draft(s) with saved databases. Validate the revised draft before execution.",
                    "navigate": "runs", "created_runs": [r["id"] for r in made]}
        if len(made) == 1:
            response.update(dispatch(store, "inspect_run", {"id": made[0]["id"]}))
            response.update(keep_modal=False, open_run=made[0]["id"])
        return response
    if kind == 'inspect_workflow_lock':
        from .workflow_unlock import preview
        return {'unlock_plan': preview(store, p.get('id')), 'keep_modal': True}
    if kind == 'unlock_workflow':
        from .workflow_unlock import request
        jid = request(store, p.get('id'), p.get('signature'))
        return {'message': 'Unlock queued. The analysis queue stays paused; no analysis will start.', 'navigate': 'jobs', 'unlock_job': jid}
    if kind in {'preview_aware', 'import_aware', 'continue_aware'}:
        from . import aware_outputs
        rid = p.get('id')
        run = store.get('runs', rid) if rid else None
        path = aware_outputs.bundle_path(run) if run else local_path(p['path'])
        if kind == 'preview_aware':
            return {'aware_outputs': {**aware_outputs.review(path), 'run_id': rid,
                     'can_continue': bool(run and run.get('aware',{}).get('state') in {'complete','continuation_ready'} and parse_config(run['config']).get('aware_run_until') == 'aware')}, 'keep_modal': True}
        if kind == 'continue_aware':
            aware_outputs.continue_assembly(store, rid, p.get('token'))
            enqueue(store, 'workflow', {'run_id':rid, 'action':'continue'})
            return {'message':'Assembly continuation queued.', 'navigate':'jobs'}
        samples = aware_outputs.commit(store, path, p.get('token'), p.get('selected'))
        return {'message':f'Imported {len(samples)} derived samples. Select them to create separate run drafts.', 'navigate':'samples'}

    if kind in {"inspect_identification", "save_identification", "continue_identification"}:
        from . import identification
        rid = p.get('id')
        if kind == 'inspect_identification':
            return {'identification': identification.inspect(store, rid, p.get('mode'), p.get('taxid'), p.get('query', '')), 'keep_modal': True}
        identification.save(store, rid, p.get('mode'), p.get('taxid'), p.get('signature'), confirm=kind == 'continue_identification')
        if kind == 'continue_identification':
            jid = enqueue(store, 'workflow', {'run_id': rid, 'action': 'continue'})
            return {'message': 'Target recorded. Continuation queued: ' + jid, 'navigate': 'jobs'}
        return {'identification': identification.inspect(store, rid), 'keep_modal': True, 'message': 'Choice saved. The run is still awaiting review.'}
    if kind == "workflow":
        ids = _ids(p)
        action = p.get("action")
        if action not in {"dry-run", "prepare", "run", "check-environments", "continue"}:
            raise ValueError("Unknown workflow action")
        queued = enqueue_workflows(store, [{"run_id": rid, "action": action} for rid in ids])
        return {"message": f"Queued {len(queued)} jobs in order.", "navigate": "jobs"}
    if kind == "clone":
        raise ValueError("Independent polishing clones have been replaced. Open a completed run and choose Create variant to reuse results.")
    if kind == "create_followup":
        followup.create_followup(store, p.get("id"), p["text"], p.get("reviewed") is True,
                                 p.get('source_id', 'screening'), p.get('scope'))
        return {"message": "Created a reviewed follow-up draft. Check its native dry-run before execution.", "navigate": "runs"}
    if kind in {"update_run_draft", "revise_run"}:
        in_place = kind == "update_run_draft"
        saved = run_revisions.apply(store, {**p, "in_place": in_place})
        if in_place:
            message = "Saved changes to this draft. Choose Validate run to check the updated settings."
        elif saved.get("variant"):
            message = "Created a result variant. Review inherited checkpoints and validate before running."
        else:
            message = "Created a revised draft. Validate the new draft before running."
        response = dispatch(store, "inspect_run", {"id": saved["id"]})
        response.update(message=message, navigate="runs", open_run=saved["id"], keep_modal=False)
        return response
    if kind == "micp_handoff":
        from .micp_handoff import create
        child = create(store, p.get("id"))
        return {"message": "Created GeneScope draft with HybAs inputs and a pinned reference database.", "navigate": "runs"}
    if kind == "handoff":
        run = _item(store, "runs", p.get("id"))
        if run["tool"] != "hybas":
            raise ValueError("Choose a HybAs AWARE run")
        if run_status(run, store.jobs(), dry_run_current(store, run)) != "completed":
            raise ValueError("Complete the HybAs AWARE run before creating a BaSIS AWARE draft")
        check_run_files(run)
        create_run(store, "basis", {"id": run["sample"]["id"], "path": run["output"], "mode": run["sample"]["mode"]}, store.get("configs", "basis", template("basis")), run["cores"], run["memory_mb"], run["id"], run["label"] + " · BaSIS")
        return {"message": "Created a new draft.", "navigate": "runs"}
    if kind == 'release_storage_stop':
        from . import disk_stop
        disk_stop.release(store, p.get('id'))
        return {'message': 'Storage check passed. The queue remains paused. Choose which jobs to run.'}
    if kind == 'queue_control':
        state = queue.change(store, p.get('action'), p.get('revision'), p.get('id'))
        if p.get('action') in {'resume', 'release'} and not state['paused']:
            try:
                launch_worker(store)
            except Exception:
                return {'message': 'Queue change saved, but the worker could not start. Use Start / recover queue to retry.'}
        messages = {'pause': 'Queue paused. Any current job continues; no next job will start.',
                    'resume': 'Queue resumed. Held jobs remain held; interrupted attempts are not retried.',
                    'hold': 'Waiting job held. Other eligible jobs can continue.',
                    'release': 'Job released from hold. The global queue pause still applies.',
                    'up': 'Waiting order updated.', 'down': 'Waiting order updated.'}
        return {'message': messages[p['action']]}
    if kind == "recover_jobs":
        launch_worker(store)
        return {"message": "Worker requested; active jobs retain their lock. Pauses and holds remain in effect. Recovered interruptions pause the queue for review."}
    if kind == "resume_disk_paused_job":
        from . import disk_pause
        if set(p) != {"job_id", "generation"}:
            raise ValueError("Select the current storage pause before resuming.")
        from .supervision import snapshot as monitoring_snapshot
        selected_job = store.job(p['job_id']) if isinstance(p['job_id'], str) else None
        monitor = (monitoring_snapshot(store, selected_job['id']) or {}) if selected_job else {}
        if monitor.get('state') in {'degraded', 'interrupted', 'unavailable'}:
            raise ValueError("Storage resume is unavailable while execution monitoring needs attention. Review this attempt's log and status.")
        disk_pause.request_resume(store, p["job_id"], p["generation"])
        return {"message": "Resume requested. Storage will be checked again before the run continues."}
    if kind == "cancel_job":
        from .desktop import request_live_job_stop
        try:
            sent = request_live_job_stop(store, p["id"])
        except OSError:
            sent = False
        if sent:
            store.cancel(p["id"], sent_to_worker=True)
            return {"message": "Stop requested for the running job. The job log will be retained."}
        from .queue import reconcile_interrupted
        recovered = reconcile_interrupted(store)
        if p["id"] in recovered:
            return {"message": "No worker remains. The attempt is marked interrupted and the queue is paused for review. Existing outputs and logs are retained."}
        job = store.job(p["id"])
        if not job or job["status"] not in {"queued", "running"}:
            raise ValueError("This job is no longer active")
        from .supervision import snapshot as monitoring_snapshot
        monitor = monitoring_snapshot(store, job['id']) or {}
        if job['status'] == 'running' and monitor.get('state') == 'interrupted' and monitor.get('cleanup_confirmed') is not True:
            raise ValueError("Monitoring was interrupted and native process exit is still unconfirmed. "
                             "Cleanup was attempted; another stop request cannot restore process ownership. "
                             "Review this attempt's log. Relaunch and cleanup remain blocked.")
        store.cancel(p["id"])
        return {"message": "Stop requested. The job log will be retained."}
    if kind == "job_metrics":
        return {"usage": telemetry.inspect(store, p.get("id")), "keep_modal": True}
    if kind == "support_preview":
        return {"support": support.preview(store, p.get("job_id"), p.get("include_log", False)), "keep_modal": True}
    if kind == "download_support":
        return {"download": support.download(p.get("text"), p.get("reviewed", False), p.get("format", "txt")), "keep_modal": True}
    if kind == "download_metrics":
        return {"download": telemetry.download(store, p.get("id"), p.get("format", "zip")), "keep_modal": True}
    if kind in {"job_log", "download_log"}:
        job = store.job(p["id"])
        if not job:
            raise ValueError("Job does not exist")
        path = store.log_path(job["id"])
        if kind == "download_log":
            if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("Open the full log on disk: " + str(path))
            return {"download": {"name": path.name, "text": results.read_file(path, results.LOG_LIMIT).decode("utf-8", errors="replace"), "type": "text/plain"}, "keep_modal": True}
        text = "Waiting for process output…"
        if path.exists():
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 100000))
                text = handle.read().decode("utf-8", errors="replace") or text
        if job["kind"] == "database_download":
            text += maintenance.download_log_tail(store, job)
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
        content = base64.b64encode(source.read_bytes()).decode("ascii")
        run = store.get("runs", report.get("run")) or {}
        edition = store.home / 'report-refreshes' / report['producing_job'] / 'edition' if report.get('refresh') and report.get('producing_job') else None
        measured = trajectory_preview.measured_tables(store, report, edition)
        rendered = trajectory_preview.preview(content, source, run.get("output"), edition, measured)
        return {"report": report | {"base64": rendered}, "keep_modal": True}
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
        from . import async_tasks
        kind, payload = request.get('kind'), request.get('payload', {})
        if not isinstance(payload, dict):
            raise ValueError('Invalid action payload')
        owner = session.setdefault('ui-task-owner', uuid.uuid4().hex)
        if kind in async_tasks.ACTIONS:
            value = {'async_task': async_tasks.submit(store, owner, request['id'], kind, payload), 'keep_modal': True}
        elif kind in {'poll_ui_task', 'cancel_ui_task'}:
            action = async_tasks.poll if kind == 'poll_ui_task' else async_tasks.cancel
            value = {'async_task': action(store, owner, payload.get('token')), 'keep_modal': True}
        elif kind in {'inspect_run', 'inspect_results', 'job_log', 'job_metrics',
                      'inspect_workstation', 'isolate_export_receipt'}:
            # These same read requests drive the automatic active-job poll.
            with display_read():
                value = dispatch(store, kind, payload)
        else:
            value = dispatch(store, kind, payload)
        response = {"id": request["id"], "ok": True, **value}
    except Exception as exc:
        response = {"id": request["id"], "ok": False, "message": str(exc)}
    session["ui-response"] = response
    return response



