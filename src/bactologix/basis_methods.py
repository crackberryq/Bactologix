"""BaSIS publication metadata, with native identities and scope retained."""
from .evidence_identity import validate_native
from .metadata_stream import read
from .run_config import MODULE_NAMES

UNKNOWN = 'Not recorded'
_RECORD_FIELDS = ('sample_id', 'module', 'tool', 'execution_status', 'reason', 'tool_version',
                  'database_version', 'database_manifest', 'module_decision', 'complete',
                  'scope_incomplete', 'upstream_incomplete', 'completion_summary', 'input_hashes',
                  'config_hash', 'record_id', 'reused', 'cache_hit', 'started_at', 'finished_at')
_NATIVE_FIELDS = ('version', 'completeness', 'incomplete_modules', 'report_view', 'finished_at',
                  'started_at', 'engine', 'schema', 'run_id', 'config_hash', 'source_hash',
                  'enabled_modules', 'options', 'screening_version', 'reporting_scope', 'evidence_sources', 'report_inputs')
_IDENTITY_FIELDS = ('source_commit', 'workflow_commit', 'config_sha256', 'workflow_sha256', 'bactologix')
_FIELDS = {
    ('schema_version',), ('bactologix',),
    *(('provenance', field) for field in _IDENTITY_FIELDS),
    *(('run', field) for field in _NATIVE_FIELDS),
    ('run', 'input_validations', '*', 'input_hashes'), ('run', 'module_decisions'),
    ('samples', '*', 'sample_id'), ('samples', '*', 'input_validation', 'input_hashes'),
    *((kind, '*', field) for kind in ('records', 'deep_records') for field in _RECORD_FIELDS),
}
_STATUSES = {'completed', 'failed', 'blocked', 'not_started', 'not_assessed', 'not_requested', 'disabled'}


def _string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Invalid BaSIS ' + label)
    return value


def _version(value):
    if value is None or value == '':
        return UNKNOWN
    value = _string(value, 'software version')
    return UNKNOWN if value.strip().lower() in {'unknown', 'na', 'n/a', 'unavailable', 'not recorded'} else value


def _validate(run, data):
    if data.get('schema_version') != 'basis-report-1':
        raise ValueError('Unsupported BaSIS Methods evidence schema')
    meta = data.get('run')
    if not isinstance(meta, dict):
        raise ValueError('BaSIS report metadata must be an object')
    view = meta.get('report_view')
    allowed = {'deep'} if run.get('run_type') == 'followup' else {'screening', 'combined'}
    if view is not None and view not in allowed:
        raise ValueError('BaSIS report view does not match the selected run')
    for name in ('incomplete_modules', 'enabled_modules'):
        value = meta.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError('Invalid BaSIS ' + name)
    if meta.get('completeness') not in (None, 'incomplete', 'completed_requested_scope'):
        raise ValueError('Invalid BaSIS report completeness')
    identity_data = dict(data)
    identity_data['samples'] = list(data.get('samples', []))
    for key in ('records', 'deep_records'):
        rows = data.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Invalid BaSIS ' + key)
        for row in rows:
            if 'input_hashes' in row:
                identity_data['samples'].append({'sample_id': row.get('sample_id'),
                    'input_validation': {'input_hashes': row['input_hashes']}})
    identity = validate_native(run, identity_data)
    if 'sample identity' not in identity['checks'] or 'sample identity' in identity['missing']:
        raise ValueError('BaSIS Methods evidence has no verified sample identity')
    # Report-only refresh identities are distinct from native execution identities.
    # Never use the native run.run_id namespace as a Bactologix run ID.
    records, seen = [], set()
    for key in ('records', 'deep_records'):
        rows = data.get(key, [])
        for row in rows:
            module = row.get('module')
            if module not in {*MODULE_NAMES, 'deepsafe'} or module in seen:
                raise ValueError('Unknown or duplicate BaSIS Methods module')
            seen.add(module)
            if key == 'deep_records' and module != 'deepsafe' or key == 'records' and module == 'deepsafe':
                raise ValueError('BaSIS module belongs to the wrong report record list')
            if row.get('execution_status') not in _STATUSES:
                raise ValueError('Invalid BaSIS module execution status')
            for flag in ('complete', 'scope_incomplete', 'reused', 'cache_hit'):
                if flag in row and type(row[flag]) is not bool:
                    raise ValueError('Invalid BaSIS ' + flag)
            if 'upstream_incomplete' in row and (not isinstance(row['upstream_incomplete'], list)
                    or any(not isinstance(item, str) for item in row['upstream_incomplete'])):
                raise ValueError('Invalid BaSIS upstream incomplete list')
            decision = row.get('module_decision', {})
            if not isinstance(decision, dict):
                raise ValueError('Invalid BaSIS module decision')
            for flag in ('requested', 'scope_incomplete'):
                if flag in decision and type(decision[flag]) is not bool:
                    raise ValueError('Invalid BaSIS module decision ' + flag)
            if 'completion_summary' in row and not isinstance(row['completion_summary'], dict):
                raise ValueError('Invalid BaSIS completion summary')
            _version(row.get('tool_version'))
            if run.get('run_type') != 'followup' or key == 'deep_records':
                records.append(row)
    options = meta.get('options', {})
    if not isinstance(options, dict) or 'deep' in options and type(options['deep']) is not bool:
        raise ValueError('Invalid BaSIS report options')
    if (view == 'screening' or options.get('deep') is False) and data.get('deep_records'):
        raise ValueError('BaSIS report contains follow-up evidence outside its declared scope')
    return meta, records, identity


def collect(run):
    """Read only the fixed report path; never probe tools or chase native paths."""
    result = {'files': [], 'warnings': [], 'versions': {}, 'version_scopes': {},
              'records': [], 'decisions': {}}
    relative = ('safety/deepsafe-report/evidence.json' if run.get('run_type') == 'followup'
                else 'safety/evidence.json')
    try:
        loaded = read(run['output'], relative, _FIELDS)
        if loaded is None:
            result['warnings'].append('BaSIS Methods evidence is missing: ' + relative)
            return result
        data, fingerprint = loaded
        meta, records, identity = _validate(run, data)
        versions = {'basis': _version(meta.get('version'))}
        scopes = {'basis': 'Native report version; analysis tool versions are separate'}
        for row in records:
            version = _version(row.get('tool_version'))
            if version != UNKNOWN:
                versions[row['module']] = version
                scopes[row['module']] = ('Native module metadata; ' + row['execution_status'] +
                    ('; reused result, not a new execution in this attempt'
                     if row.get('reused') is True or row.get('cache_hit') is True else ''))
        result.update(files=[fingerprint], metadata=data, identity=identity, records=records,
                      native_run={key: meta[key] for key in _NATIVE_FIELDS if key in meta},
                      versions=versions, version_scopes=scopes)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        result['warnings'].append('Could not collect BaSIS Methods metadata: ' + str(exc))
    return result
