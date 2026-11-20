"""Display recorded assessment coverage separately from process completion.

This adapter does not interpret findings, QC thresholds or biological validity.
Only BaSIS currently records an explicit requested-scope completeness contract.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import json
from pathlib import Path
import threading

from .methods import LIMIT, _read
from .results import file_info


# Store only small UI summaries. Clear/rebuild a row on evidence, run or
# attempt changes. Authoritative callers use summary() without a snapshot.
_DISPLAY = OrderedDict()
_DISPLAY_LOCK = threading.Lock()


def _result(state, label, reasons, origin="No usable saved coverage evidence"):
    return {"state": state, "label": label, "reasons": reasons, "origin": origin,
            "interpretation": "Assessment coverage does not establish scientific validity or organism safety."}


def _basis(run, data):
    if data.get("schema_version") != "basis-report-1":
        raise ValueError("Unsupported BaSIS coverage schema.")
    meta = data.get("run")
    if not isinstance(meta, dict):
        raise ValueError("BaSIS coverage metadata is malformed.")
    keys = ("deep_records",) if run.get("run_type") == "followup" else ("records", "deep_records")
    records = []
    for key in keys:
        rows = data.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("BaSIS coverage records are malformed.")
        records.extend(rows)
    incomplete = meta.get("incomplete_modules", [])
    if not isinstance(incomplete, list) or any(not isinstance(value, str) for value in incomplete):
        raise ValueError("BaSIS incomplete-module list is malformed.")
    reasons = ["Native report lists incomplete scope: " + ", ".join(incomplete)] if incomplete else []
    enabled = meta.get("enabled_modules", [])
    if not isinstance(enabled, list) or any(not isinstance(value, str) for value in enabled):
        raise ValueError("BaSIS enabled-module list is malformed.")
    seen = set()
    for row in records:
        module = row.get("module")
        if not isinstance(module, str) or not module or module in seen:
            raise ValueError("BaSIS coverage has absent or duplicate module identities.")
        seen.add(module)
        decision = row.get("module_decision", {})
        if not isinstance(decision, dict):
            raise ValueError("BaSIS module coverage decision is malformed.")
        requested = decision.get("requested") is True or row.get("module") in enabled
        if (requested and (row.get("execution_status") != "completed" or decision.get("scope_incomplete"))
                or row.get("complete") is False or row.get("upstream_incomplete")):
            reasons.append(str(row.get("module", "Module")) + ": " + str(row.get("reason") or row.get("execution_status") or "scope incomplete"))
    if run.get("run_type") != "followup":
        reasons.extend(module + ": requested module evidence is missing" for module in enabled if module not in seen)
    if meta.get("completeness") == "incomplete" or reasons:
        return _result("incomplete", "Assessment incomplete", reasons or ["The native report records incomplete requested scope."])
    if meta.get("completeness") == "completed_requested_scope" and records:
        return _result("recorded", "Reported scope complete", ["Native requested scope is recorded as complete; the producing attempt is unverified. Review module exclusions and input limitations."])
    return _result("unknown", "Coverage not recorded", ["No explicit, usable requested-scope completeness record is available."])


def _evidence_stamps(output, paths):
    # file_info checks each ancestor, refuses links/special files and provides
    # the same metadata identity used by the Results viewer.
    files = [file_info(Path(output) / name, LIMIT) for name in paths]
    if any(row['state'] == 'unavailable' for row in files):
        return None
    return tuple(row.get('fingerprint') for row in files)


def _analysis_attempts(jobs, run):
    return [j for j in jobs if j["kind"] == "workflow" and j["payload"].get("run_id") == run["id"]
            and j["payload"].get("action") in {"run", "continue"}]


def _display_summary(store, run, paths, run_jobs):
    jobs = _analysis_attempts(run_jobs, run)
    stamps = _evidence_stamps(run["output"], paths)
    if stamps is None:
        # The native evidence reader supplies the exact permissions/path error.
        return _review(store, run, paths, jobs)
    if not any(stamps):
        return _result("unknown", "Coverage not recorded", ["Saved assessment evidence is unavailable."])
    # Bind to the saved record, not just the run ID. This covers edits and
    # input/commit/attempt attribution, even for older records with no revision.
    attempts = [(job["id"], job["status"]) for job in jobs]
    identity = hashlib.sha256(json.dumps([run, attempts], sort_keys=True).encode()).digest()
    key = (store.home, run["id"])
    signature = (identity, stamps)
    with _DISPLAY_LOCK:
        saved = _DISPLAY.get(key)
        if saved and saved[0] == signature:
            _DISPLAY.move_to_end(key)
            return copy.deepcopy(saved[1])
    result = _review(store, run, paths, jobs)
    if (sum(map(len, result["reasons"])) <= 16384
            and _evidence_stamps(run["output"], paths) == stamps):
        with _DISPLAY_LOCK:
            _DISPLAY[key] = (signature, copy.deepcopy(result))
            _DISPLAY.move_to_end(key)
            if len(_DISPLAY) > 256:
                _DISPLAY.popitem(last=False)
    return result


def summary(store, run, *, run_jobs=None):
    """Return a bounded, read-only coverage view; never infer it from job success."""
    tool = run.get("tool")
    if tool == "basis" and run.get("run_type") == "followup" and store.get("followup_output_owner", run.get("parent")) != run.get("id"):
        return _result("unknown", "Coverage unverified", ["This follow-up no longer owns the shared evidence."])
    paths = {"basis": "safety/deepsafe-report/evidence.json" if run.get("run_type") == "followup" else "safety/evidence.json",
             "micpscope": "micpscope/results.json", "hybas": "reports/hybas/results.json"}
    if tool not in paths:
        return _result("unknown", "Coverage not recorded", ["No reviewed coverage adapter is available for this pipeline."])
    evidence = (paths[tool], "metadata/run_summary.json") if tool == "hybas" else (paths[tool],)
    try:
        if run_jobs is not None:
            return _display_summary(store, run, evidence, run_jobs)
        return _review(store, run, evidence, None)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        return _result("unknown", "Coverage unverified", [str(exc)])


def _review(store, run, paths, jobs):
    tool = run["tool"]
    for path in paths:
        raw = _read(run["output"], path)
        if raw is not None:
            break
    if raw is None:
        return _result("unknown", "Coverage not recorded", ["Saved assessment evidence is unavailable."])
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Saved assessment evidence must be an object.")
    from .evidence_identity import validate_native
    identity = validate_native(run, data)
    if "sample identity" not in identity.get("checks", []) or "sample identity" in identity.get("missing", []):
        raise ValueError("Native coverage sample identity is not recorded for all evidence.")
    if tool == "basis":
        result = _basis(run, data)
    elif tool == "hybas":
        result = _result("unknown", "Coverage requires review", ["Open the assembly and QC evidence for the assessed stages. Workflow QC PASS is not a scientific-quality verdict; no aggregate coverage contract is recorded."])
    else:
        if data.get("schema_version") != "1.3" or not isinstance(data.get("proteins"), list):
            raise ValueError("Unsupported GeneScope coverage schema.")
        result = _result("unknown", "Coverage requires review", ["Review protein descriptor and reference-search statuses. Operon and promoter prediction are not assessed by this report."])
    result["origin"] = "Saved native evidence; producing attempt unverified"
    if identity.get("missing"):
        result["reasons"].append("Evidence attribution is incomplete: " + "; ".join(identity["missing"]))
    if jobs is None:
        jobs = _analysis_attempts(store.jobs(), run)
    for declared in (data.get('bactologix'), (data.get('provenance') or {}).get('bactologix')):
        if isinstance(declared, dict) and declared.get('job_id') and not any(j['id'] == declared['job_id'] for j in jobs):
            raise ValueError('Saved evidence attempt does not belong to the selected run.')
    if jobs and jobs[0]["status"] != "succeeded":
        result["state"] = "unknown"
        result["label"] = "Coverage unverified"
        result["reasons"].insert(0, "The latest analysis attempt is " + jobs[0]["status"] + "; saved coverage may belong to an earlier attempt.")
    return result
