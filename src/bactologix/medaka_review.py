"""Gate Medaka review settings on a checked native execution contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIELDS = {'medaka_model_review_acknowledged', 'medaka_model_review_reason', 'medaka_model_reviewed_model'}
POLICY_FIELD = 'medaka_model_selection_policy'
CONTRACT = {'interface': 1, 'capability': 'app/medaka-model-review.json',
            'helper': 'workflow/lib/medaka_stage.py', 'preflight_rule': 'medaka_model_preflight'}
FALLBACK_CONTRACT = dict(CONTRACT, interface=2)
FALLBACK = {'policy': 'documented_default', 'medaka_version': '2.2.0',
            'model': 'r1041_e82_400bps_sup_v5.2.0',
            'upstream_source_commit': '019a2ab597218a1c239fb7a5315f271a4577ed6b',
            'options_sha256': '16af07e1530bd30950cd22954e084b4d8f92316d0bc41ecd5669e768eb707f8e'}
REQUIRED_FILES = (CONTRACT['capability'], CONTRACT['helper'], 'Hybas.smk',
                  'workflow/lib/settings.py', 'workflow/lib/library_inputs.py',
                  'workflow/lib/workflow_support.py', 'workflow/envs/medaka.yml')


def require_adapter(store, tool, commit, cfg, source):
    if tool != 'hybas' or not (FIELDS | {POLICY_FIELD}).intersection(cfg):
        return
    from .release_contract import approval
    row = approval(store, tool, commit)
    try:
        contract = row.get('medaka_model_review') if row else None
        if (contract not in (CONTRACT, FALLBACK_CONTRACT) or type(contract.get('interface')) is not int):
            raise ValueError('no reviewed model-selection contract')
        version = contract['interface']
        if POLICY_FIELD in cfg and (version != 2 or cfg[POLICY_FIELD] not in ('strict', 'documented_default')):
            raise ValueError('this source does not support the requested model-selection policy')
        root = Path(source).resolve(strict=True)
        hashes = row.get('files_sha256', {})
        for name in REQUIRED_FILES:
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or name not in hashes or hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
                raise ValueError('model-selection source identity changed')
        value = json.loads((root / CONTRACT['capability']).read_text())
        if (not isinstance(value, dict) or value.get('contract') != 'hybas-medaka-model-review' or type(value.get('version')) is not int
                or value['version'] != version or value.get('helper') != CONTRACT['helper']
                or value.get('preflight_rule') != CONTRACT['preflight_rule']
                or set(value.get('configuration_fields', [])) != FIELDS | {'medaka_model'} | ({POLICY_FIELD} if version == 2 else set())
                or value.get('inspection') != 'all_raw_ont_reads_before_analysis_and_all_filtered_reads_at_medaka_execution'
                or value.get('scientific_suitability') != 'not_assessed'):
            raise ValueError('model-selection capability is invalid')
        if version == 1 and value.get('automatic_fallback') is not False:
            raise ValueError('legacy source cannot authorize automatic fallback')
        if version == 2 and (value.get('automatic_fallback') != FALLBACK
                or value.get('selection_policies') != ['strict', 'documented_default']
                or value.get('missing_policy') != 'strict' or value.get('new_draft_policy') != 'documented_default'
                or value.get('selection_precedence') != ['explicit', 'exact_installed_mapping', 'documented_default']
                or value.get('fallback_eligible') != ['basecaller_metadata_absent', 'basecaller_model_unsupported']
                or value.get('fallback_excluded') != ['mixed_metadata', 'incomplete_metadata', 'installed_mapping_unavailable',
                                                       'resolution_failure', 'runtime_failure', 'invalid_reads']):
            raise ValueError('documented fallback capability is invalid')
        return version
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
        version = require_adapter(store, tool, commit, {'medaka_model_review_acknowledged': False}, source)
    except ValueError:
        return text
    cfg = parse_config(text)
    changed = False
    additions = {'medaka_model_review_acknowledged': False, 'medaka_model_review_reason': '',
                 'medaka_model_reviewed_model': ''}
    if version == 2:
        additions[POLICY_FIELD] = 'strict' if run is not None else 'documented_default'
    for key, value in additions.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    return yaml.safe_dump(cfg, sort_keys=False) if changed else text
