"""Display recorded assessment coverage separately from process completion.

This adapter does not interpret findings, QC thresholds or biological validity.
Only BaSIS currently records an explicit requested-scope completeness contract.
"""
from __future__ import annotations

import json

from .methods import _read


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


def summary(store, run):
    """Return a bounded, read-only coverage view; never infer it from job success."""
    tool = run.get("tool")
    if tool == "basis" and run.get("run_type") == "followup" and store.get("followup_output_owner", run.get("parent")) != run.get("id"):
        return _result("unknown", "Coverage unverified", ["This follow-up no longer owns the shared evidence."])
    paths = {"basis": "safety/deepsafe-report/evidence.json" if run.get("run_type") == "followup" else "safety/evidence.json",
             "micpscope": "micpscope/results.json", "hybas": "reports/hybas/results.json"}
    if tool not in paths:
        return _result("unknown", "Coverage not recorded", ["No reviewed coverage adapter is available for this pipeline."])
    try:
        raw = _read(run["output"], paths[tool])
        if raw is None and tool == "hybas":
            raw = _read(run["output"], "metadata/run_summary.json")
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
        jobs = [j for j in store.jobs() if j.get("kind") == "workflow" and j.get("payload", {}).get("run_id") == run["id"]
                and j["payload"].get("action") in {"run", "continue"}]
        for declared in (data.get('bactologix'), (data.get('provenance') or {}).get('bactologix')):
            if isinstance(declared, dict) and declared.get('job_id') and not any(j['id'] == declared['job_id'] for j in jobs):
                raise ValueError('Saved evidence attempt does not belong to the selected run.')
        if jobs and jobs[0]["status"] != "succeeded":
            result["state"] = "unknown"
            result["label"] = "Coverage unverified"
            result["reasons"].insert(0, "The latest analysis attempt is " + jobs[0]["status"] + "; saved coverage may belong to an earlier attempt.")
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        return _result("unknown", "Coverage unverified", [str(exc)])
