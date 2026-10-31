"""Explicit job adapter for the upstream saved-screening entry point."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import yaml

from .catalog import PIPELINES
from .runs import (absolute_path, bind_paths, check_run_files, current_controller,
                   dry_run_current, parse_config, run_status, tree_digest)
from .store import identifier, now
from . import __version__
from .release_contract import approval, preflight


def _contract(store, commit, parent_commit):
    row = approval(store, 'basis', commit)
    cap = row.get('followup', {}) if row else {}
    return row if cap and parent_commit in [commit, *cap['parent_commits']] else None


def source_options(store, run):
    if run['tool'] != 'basis' or run.get('run_type') == 'followup':
        return []
    options = []
    original = _contract(store, run['commit'], run['commit'])
    if original or run['commit'] in PIPELINES['basis'].get('followup_commits', []):
        options.append({'id': 'screening', 'label': 'Original screening workflow',
                        'commit': run['commit'], 'scopes': original['followup']['scopes'] if original else []})
    for installed in store.all('installs') if store else []:
        if (installed['tool'] != 'basis' or not installed.get('ready') or not installed.get('controller')
                or installed.get('setup_mode') == 'first_install' and not installed.get('setup_completed')):
            continue
        commit = installed['version']['commit']
        contract = _contract(store, commit, run['commit'])
        if contract and commit != run['commit']:
            options.append({'id': installed['id'], 'label': installed['version']['ref'],
                            'commit': commit, 'scopes': contract['followup']['scopes']})
    return options


def available(run, store=None):
    return bool(source_options(store, run))


def verify_transition(run):
    """Keep the selected source/scope binding immutable across validation/resume."""
    digest = run.get('followup_transition_sha256')
    if not digest:  # Existing follow-up drafts predate the recorded transition.
        return
    path = Path(run['folder']) / 'followup-transition.json'
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Saved follow-up source transition changed')
    row = json.loads(path.read_text())
    if row != run.get('followup_transition'):
        raise ValueError('Saved follow-up source transition metadata changed')
    for key in ('commit', 'installation', 'workflow_sha256', 'parent'):
        if row[key] != run[key]:
            raise ValueError('Saved follow-up source binding changed: ' + key)


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


def create_followup(store, parent_id, text, reviewed=False, source_id='screening', scope=None):
    parent = store.get("runs", parent_id)
    if not parent or not available(parent, store):
        raise ValueError("Choose a screening run using the BaSIS AWARE follow-up adapter")
    if not reviewed:
        raise ValueError("Review the saved screening report before requesting follow-up")
    if run_status(parent, store.jobs(), dry_run_current(store, parent)) != "completed":
        raise ValueError("Complete screening before requesting follow-up")
    check_run_files(parent)
    from .variants import verify
    verify(parent)  # Check inherited content before creating a separate follow-up request.
    source = Path(parent["folder"]) / "workflow"
    if tree_digest(source) != parent["workflow_sha256"]:
        raise ValueError("The screening run's workflow source changed")
    choice = next((r for r in source_options(store, parent) if r['id'] == source_id), None)
    if not choice:
        raise ValueError('Choose an installed, reviewed follow-up source compatible with this screening version')
    binding = {k: parent[k] for k in ('installation', 'commit')}
    binding['rule_cache'] = parent.get('rule_cache') or str(store.home / 'envs/basis')
    contract = _contract(store, choice['commit'], parent['commit'])
    if source_id != 'screening':
        installed = store.get('installs', source_id)
        source = Path(installed['source'])
        actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
        if actual != choice['commit'] or dirty:
            raise ValueError('Installed follow-up source changed. Use a clean installation.')
        binding.update(installation=source_id, commit=choice['commit'],
                       rule_cache=installed.get('rule_cache') or str(store.home / 'envs/basis'))
    preflight(store, 'basis', binding['commit'], source)
    workflow_hash = tree_digest(source)
    controller = current_controller(store, {**parent, **binding})
    cfg = parse_config(text)
    if set(cfg) - {"deep", "resource_receipts"}:
        raise ValueError("Follow-up may set only deep and resource_receipts; screening paths and resources are fixed")
    if not isinstance(cfg.get("deep"), dict) or cfg["deep"].get("enabled") is not True:
        raise ValueError("Explicitly enable follow-up in the reviewed configuration")
    aware = cfg['deep'].get('aware', {})
    if not isinstance(aware, dict):
        raise ValueError('deep.aware must be a mapping')
    explicit_scope = aware.get('scope')
    if scope is not None:
        if explicit_scope is not None and explicit_scope != scope:
            raise ValueError('The selected scope conflicts with the YAML scope')
        if aware.get('enabled') is False:
            raise ValueError('Enable AWARE to select a follow-up scope')
        cfg['deep']['aware'] = {**aware, 'enabled': True, 'scope': scope}
        explicit_scope = scope
    if explicit_scope is not None and explicit_scope not in choice['scopes']:
        raise ValueError('This follow-up source does not support the selected scope')
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
        shutil.copytree(source, folder / "workflow", ignore=shutil.ignore_patterns(".git", ".snakemake", "__pycache__", "*.pyc"))
        if tree_digest(folder / 'workflow') != workflow_hash:
            raise ValueError('Follow-up source changed while making its snapshot')
        preflight(store, 'basis', binding['commit'], folder / 'workflow')
        config = yaml.safe_dump(cfg, sort_keys=False)
        (folder / "config.yaml").write_text(config)
        (folder / "submitted.yaml").write_text(text)
        run = {**copy.deepcopy(parent), **binding, "id": rid, "run_type": "followup", "folder": str(folder),
               "bactologix_version": __version__,
               "parent": parent_id, "label": parent["sample"]["id"] + " · reviewed follow-up",
               "created": now(), "controller": controller, "config": config,
               "submitted": text, "config_sha256": hashlib.sha256(config.encode()).hexdigest(),
               "workflow_sha256": tree_digest(folder / "workflow"), "followup_sources": sources,
               "dry_run_ok": False, "reviewed_at": now(),
               "reuse": "Saved screening inputs; follow-up outputs share the screening folder. Only the latest follow-up attempt owns its displayed report."}
        # Variant manifests and restore entry points belong to the screening
        # request. Follow-up uses Followup.smk and its own evidence fingerprints.
        for key in ("completed", "previous_completed", "validated", "validated_controller", "last_action", "last_controller",
                    "variant", "variant_manifest_sha256", "variant_entry", "variant_restore_sha256"):
            run.pop(key, None)
        saved = json.loads((Path(parent['output']) / 'safety/metadata/config.json').read_text())
        effective_aware = {**saved.get('deep', {}).get('aware', {}), **cfg['deep'].get('aware', {})}
        transition = {'schema': 1, 'parent': parent_id, 'screening_commit': parent['commit'],
                      'screening_workflow_sha256': parent['workflow_sha256'],
                      **{k: run[k] for k in ('commit', 'installation', 'workflow_sha256')},
                      'scope': effective_aware.get('scope', 'all-supported'),
                      'aware_enabled': effective_aware.get('enabled', False),
                      'selection': source_id, 'release_contract': contract}
        encoded = json.dumps(transition, indent=2) + '\n'
        (folder / 'followup-transition.json').write_text(encoded)
        run['followup_transition'] = transition
        run['followup_transition_sha256'] = hashlib.sha256(encoded.encode()).hexdigest()
        (folder / "bactologix-run.json").write_text(json.dumps(run, indent=2) + "\n")
        store.put("runs", rid, run)
        return run
    except BaseException:
        shutil.rmtree(folder)
        raise
