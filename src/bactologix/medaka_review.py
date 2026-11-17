"""Gate Medaka review settings on a checked native execution contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIELDS = {'medaka_model_review_acknowledged', 'medaka_model_review_reason', 'medaka_model_reviewed_model'}
CONTRACT = {'interface': 1, 'capability': 'app/medaka-model-review.json',
            'helper': 'workflow/lib/medaka_stage.py', 'preflight_rule': 'medaka_model_preflight'}
REQUIRED_FILES = (CONTRACT['capability'], CONTRACT['helper'], 'Hybas.smk',
                  'workflow/lib/settings.py', 'workflow/lib/library_inputs.py',
                  'workflow/lib/workflow_support.py', 'workflow/envs/medaka.yml')


def require_adapter(store, tool, commit, cfg, source):
    if tool != 'hybas' or not FIELDS.intersection(cfg):
        return
    from .release_contract import approval
    row = approval(store, tool, commit)
    try:
        if not row or row.get('medaka_model_review') != CONTRACT:
            raise ValueError('no reviewed model-selection contract')
        root = Path(source).resolve(strict=True)
        hashes = row.get('files_sha256', {})
        for name in REQUIRED_FILES:
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or name not in hashes or hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
                raise ValueError('model-selection source identity changed')
        value = json.loads((root / CONTRACT['capability']).read_text())
        if (not isinstance(value, dict) or value.get('contract') != 'hybas-medaka-model-review' or type(value.get('version')) is not int
                or value['version'] != 1 or value.get('helper') != CONTRACT['helper']
                or value.get('preflight_rule') != CONTRACT['preflight_rule']
                or set(value.get('configuration_fields', [])) != FIELDS | {'medaka_model'}
                or value.get('automatic_fallback') is not False
                or value.get('inspection') != 'all_raw_ont_reads_before_analysis_and_all_filtered_reads_at_medaka_execution'
                or value.get('scientific_suitability') != 'not_assessed'):
            raise ValueError('model-selection capability is invalid')
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('Medaka review acknowledgement is unavailable for this pipeline source (' + str(exc) +
                         '). Use a reviewed Dev source with the model-selection contract. These fields cannot '
                         'enable preflight or authorise an unmatched model in an older frozen workflow.') from exc


def editor_text(store, tool, text, run=None):
    """Add controls to an unsaved editor only for its verified source."""
    if tool != 'hybas':
        return text
    import yaml
    from .runs import parse_config
    if run is not None:
        commit, source = run['commit'], Path(run['folder']) / 'workflow'
    else:
        installed = store.get('installs', store.get('settings', 'active-hybas'))
        if not installed or not installed.get('ready'):
            return text
        commit, source = installed['version']['commit'], installed['source']
    try:
        require_adapter(store, tool, commit, {'medaka_model_review_acknowledged': False}, source)
    except ValueError:
        return text
    cfg = parse_config(text)
    changed = False
    for key, value in {'medaka_model_review_acknowledged': False, 'medaka_model_review_reason': '',
                       'medaka_model_reviewed_model': ''}.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    return yaml.safe_dump(cfg, sort_keys=False) if changed else text
