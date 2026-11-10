"""Collect HybAs publication metadata without inferring tool execution."""
import csv
import hashlib
import io
import json
from pathlib import Path
import re

from .evidence_identity import validate_native


UNKNOWN = 'Not recorded'
PACKAGE_SCOPE = 'Recorded environment package; execution not established'
RUNTIME_SOURCES = {
    '780ee7086c15c49f2825800ec463327f009f14a6',
    'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d',
    '87c1ab81c8652bd10db47954193e27ad802cfc86',
    '585abb34ed4401da64b37a9fd629ba8adc236396',
    '5db662262962bc822f2fe501234d15b882d5b042',
    '08c315479f5493ce0b1f44ba26da3c7f2e0d748b',
    '79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4',
    '0d334449d69fe2208b79affad2f5162ac801be05',
    '7cbd29d942a3d70bc1f6c7be176a75ce4bb7f2a8',
}
SCALARS = {
    'workflow_version', 'run_mode', 'requested_polish_mode', 'assembler',
    'input_strategy', 'long_read_platform', 'kraken_decision',
    'kraken_decision_mode', 'kraken_decision_reason', 'final_source_stage',
    'final_source_sha256', 'busco_lineage', 'busco_dataset_sha256',
    'medaka_model_requested', 'medaka_model_effective', 'medaka_model_sha256',
    'medaka_model_selection', 'medaka_input_metadata_status',
    'kraken_selected_target_taxid', 'kraken_selected_target_name',
    'kraken_selected_target_rank', 'kraken_selected_target_source',
    'kraken_target_selection_reason', 'pacbio_kraken_decision',
    'pacbio_kraken_decision_reason',
}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,79}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def _known(value):
    return value is not None and str(value).strip().lower() not in {
        '', 'na', 'n/a', 'unknown', 'unavailable', 'not recorded'}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON metadata key: ' + key)
        result[key] = value
    return result


def _table(raw, columns, key=None):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), delimiter='\t', strict=True)
    if reader.fieldnames != columns:
        raise ValueError('Unexpected metadata table columns')
    rows, seen = [], set()
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('Malformed metadata table row')
        if key:
            identity = row[key].strip().lower() if key == 'tool' else row[key]
            if not identity or identity in seen:
                raise ValueError('Missing or duplicate metadata table key')
            seen.add(identity)
        rows.append(row)
    return rows


def _decisions(summary):
    result = {}
    for key in sorted(SCALARS):
        if key not in summary:
            continue
        value = summary[key]
        if value is not None and not isinstance(value, str):
            raise ValueError('Invalid HybAs metadata field: ' + key)
        if key.endswith('_sha256') and _known(value) and not SHA256.fullmatch(value):
            raise ValueError('Invalid HybAs metadata checksum: ' + key)
        result[key] = value
    # The historical field stores the request, not the model resolved at runtime.
    if 'medaka_model_requested' not in result and 'medaka_model' in summary:
        value = summary['medaka_model']
        if value is not None and not isinstance(value, str):
            raise ValueError('Invalid requested Medaka model')
        result['medaka_model_requested'] = value
    if 'executed_stages' in summary:
        stages = summary['executed_stages']
        if (not isinstance(stages, list) or any(not isinstance(s, str) or not NAME.fullmatch(s) for s in stages)
                or len(stages) != len(set(stages))):
            raise ValueError('Invalid HybAs executed-stage list')
        result['executed_stages'] = list(stages)
    if 'skipped_stages' in summary:
        stages = summary['skipped_stages']
        if (not isinstance(stages, dict) or any(not NAME.fullmatch(k) or not isinstance(v, str) for k, v in stages.items())
                or set(stages).intersection(result.get('executed_stages', []))):
            raise ValueError('Invalid or contradictory HybAs skipped-stage records')
        result['skipped_stages'] = dict(stages)
    return result


def _versions(rows):
    result = {}
    for row in rows:
        name, version = row['tool'].strip().lower(), row['version']
        if not NAME.fullmatch(name) or name in result or not isinstance(version, str):
            raise ValueError('Invalid or duplicate HybAs software version')
        result[name] = version.strip() if _known(version) else UNKNOWN
    return result


def collect(run, read_file):
    """Read allowlisted metadata with the caller's bounded, link-safe reader."""
    result = {'files': [], 'warnings': [], 'versions': {}, 'version_scopes': {},
              'records': [], 'decisions': {}, 'database_hashes': [],
              'identity': {'checks': [], 'missing': []}}
    root = Path(run['output'])
    failed = set()

    def read(relative, columns=None, key=None, directory=None, recorded_path=None):
        try:
            raw = read_file(root if directory is None else directory, relative)
            if raw is None:
                return None
            parsed = (_table(raw, columns, key) if columns else
                      json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_object))
            if columns is None and not isinstance(parsed, dict):
                raise ValueError('Expected a JSON object')
            result['files'].append({'path': recorded_path or relative,
                                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
            return parsed
        except (OSError, ValueError, UnicodeError, RecursionError, csv.Error) as exc:
            failed.add(relative)
            result['warnings'].append('Could not read ' + relative + ': ' + str(exc))
            return None

    summary = read('metadata/run_summary.json')
    provenance = read('metadata/workflow_provenance.tsv', ['field', 'value'], 'field')
    version_rows = read('metadata/tool_versions.tsv', ['tool', 'version'], 'tool')
    database_rows = read('database_hashes.tsv', ['database_type', 'path', 'sha256'],
                         directory=root.parent / 'provenance/databases',
                         recorded_path='../provenance/databases/database_hashes.tsv')
    if summary is None:
        if 'metadata/run_summary.json' not in failed:
            result['warnings'].append('HybAs summary is missing; sample identity and metadata association cannot be verified.')
        return result
    try:
        if not isinstance(summary.get('isolate'), str) or not _known(summary['isolate']):
            raise ValueError('HybAs summary has no sample identity')
        if summary['isolate'] != run['sample']['id']:
            raise ValueError('HybAs summary sample identity does not match this run')
        if 'metadata/workflow_provenance.tsv' in failed:
            raise ValueError('HybAs workflow identity metadata is unreadable')
        native = dict(summary, tables={'workflow_provenance': provenance or []})
        identity = validate_native(run, native)
        if 'sample identity' not in identity['checks']:
            raise ValueError('HybAs summary sample identity is unverified')
        fields = {row['field']: row['value'] for row in provenance or []}
        if not _known(fields.get('config_sha256')):
            identity['missing'].append('native configuration hash')
        source_verified = False
        if _known(fields.get('source_sha256')):
            if not SHA256.fullmatch(fields['source_sha256']):
                raise ValueError('Invalid HybAs source checksum')
            frozen = read_file(Path(run['folder']) / 'workflow', 'Hybas.smk')
            if frozen is None:
                identity['missing'].append('frozen workflow source file')
            elif hashlib.sha256(frozen).hexdigest() != fields['source_sha256']:
                raise ValueError('HybAs source checksum does not match the frozen workflow')
            else:
                identity['checks'].append('workflow source hash')
                result['files'].append({'path': '../../workflow/Hybas.smk',
                                        'sha256': fields['source_sha256'], 'bytes': len(frozen)})
                from .runs import tree_digest
                source_verified = (run.get('commit') in RUNTIME_SOURCES
                                   and bool(run.get('workflow_sha256'))
                                   and tree_digest(Path(run['folder']) / 'workflow') == run['workflow_sha256'])
        else:
            identity['missing'].append('native workflow source hash')
        decisions = _decisions(summary)
        embedded = []
        for key, value in summary.items():
            if key.startswith('tool_') and key.endswith('_version'):
                embedded.append({'tool': key[5:-8], 'version': value})
        embedded_versions = _versions(embedded)
        versions = _versions(version_rows or [])
        for name in versions.keys() & embedded_versions.keys():
            if versions[name] != embedded_versions[name]:
                raise ValueError('HybAs summary and version table disagree for ' + name)
        if version_rows is None and 'metadata/tool_versions.tsv' not in failed:
            versions = embedded_versions
        if 'metadata/tool_versions.tsv' in failed:
            versions = {}  # A damaged sidecar is not bypassed using a duplicate field.
        databases = []
        for row in database_rows or []:
            if (not row['database_type'] or not row['path']
                    or _known(row['sha256']) and not SHA256.fullmatch(row['sha256'])):
                raise ValueError('Invalid HybAs database identity metadata')
            databases.append(row)
        result.update(decisions=decisions, versions=versions, database_hashes=databases,
                      identity={key: sorted(set(value)) for key, value in identity.items()})
        result['version_scopes'] = {name: PACKAGE_SCOPE for name in versions}
        if 'snakemake' in versions:
            result['version_scopes']['snakemake'] = ('Recorded workflow runtime version' if source_verified
                                                    else 'Recorded version metadata; execution not established')
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        result['warnings'].append('HybAs Methods metadata was not accepted: ' + str(exc))
    return result
