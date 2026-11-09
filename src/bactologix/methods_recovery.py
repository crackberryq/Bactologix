"""Read-time supplements never replace historical completion evidence."""
import copy
import re

from .evidence_identity import validate_native
from .report_evidence import binding
from .store import now


RESULTS = 'micpscope/results.json'
LEGACY_SIZE_WARNING = 'Could not read micpscope/results.json: Methods evidence exceeds 8 MB: micpscope/results.json'


def supplement(store, run, receipt, latest_job, collector):
    """Recover only a legacy GeneScope size failure, with explicit attribution.

    The caller has already checked completion and the snapshot's run binding.
    All returned evidence was collected now, even when its content fingerprint
    matches an observed historical attempt. Nothing is written to the store.
    """
    original = receipt.get('evidence') if isinstance(receipt, dict) else None
    if (run.get('tool') != 'micpscope' or not isinstance(original, dict)
            or original.get('files') or original.get('versions')
            or not isinstance(original.get('warnings'), list)
            or LEGACY_SIZE_WARNING not in original['warnings']):
        return None

    result = {'schema': 1, 'status': 'unavailable', 'read_at': None, 'evidence': {},
              'attribution': {'state': 'unverified', 'label': 'Evidence attribution unverified',
                              'job_id': None, 'attempt_status': 'unknown', 'commit': None},
              'warnings': ['The original completion snapshot is unchanged.']}

    def failed(status, message):
        result['status'] = status
        result['evidence'] = {}
        result['warnings'].append(message)
        return result

    if latest_job is not None and latest_job.get('status') != 'succeeded':
        return failed('blocked', 'Metadata recovery cannot establish completed execution for an unsuccessful latest attempt.')
    if latest_job is not None and receipt.get('job_id') != latest_job.get('id'):
        return failed('blocked', 'The completion snapshot does not match the latest execution attempt.')
    try:
        result['read_at'] = now()
        evidence = collector(run)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        return failed('unavailable', 'Could not recover GeneScope metadata: ' + str(exc))
    if not isinstance(evidence, dict):
        return failed('unavailable', 'No usable GeneScope metadata was collected.')
    result['warnings'].extend(w for w in evidence.get('warnings', []) if isinstance(w, str))
    metadata = evidence.get('metadata')
    files = evidence.get('files')
    fingerprint = next((row for row in files if isinstance(row, dict) and row.get('path') == RESULTS), None) if isinstance(files, list) else None
    if (not isinstance(metadata, dict) or not isinstance(fingerprint, dict)
            or not isinstance(fingerprint.get('sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', fingerprint['sha256'])):
        return failed('unavailable', 'GeneScope metadata recovery has no usable metadata and complete-file fingerprint.')
    try:
        native = validate_native(run, metadata)
        if 'sample identity' not in native['checks'] or 'sample identity' in native['missing']:
            raise ValueError('Recovered GeneScope metadata has no verified sample identity.')
        provenance = metadata.get('provenance') or {}
        # Optional producer claims can disqualify a supplement, but agreement
        # never establishes attribution without the content-bound receipt.
        for declared in (metadata, provenance, metadata.get('bactologix'), provenance.get('bactologix')):
            if not isinstance(declared, dict):
                continue
            for key in ('run_id', 'bactologix_run_id'):
                if key in declared and declared[key] != run['id']:
                    raise ValueError('Recovered GeneScope metadata names a different or unsupported run identity.')
            for key in ('job_id', 'bactologix_job_id'):
                if key in declared and (latest_job is None or declared[key] != latest_job['id']):
                    raise ValueError('Recovered GeneScope metadata names a different or unrecorded execution attempt.')
        attribution = binding(store, run, RESULTS, fingerprint['sha256'], [latest_job] if latest_job else [])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        return failed('blocked', 'GeneScope metadata recovery was blocked: ' + str(exc))

    result['attribution'] = attribution
    result['status'] = 'recovered' if attribution['state'] == 'recorded_attempt' else 'unverified'
    # Only descriptive metadata is supplemented. Do not infer stage execution.
    result['evidence'] = copy.deepcopy({key: evidence[key] for key in ('files', 'warnings', 'versions', 'metadata') if key in evidence})
    result['evidence']['native_identity'] = native
    if result['status'] == 'recovered':
        result['warnings'].append('The recovered metadata checksum matches content created or changed during the recorded successful attempt; it was collected at export, not at completion.')
    else:
        result['warnings'].append('Recovered values describe current output metadata only. No matching content-bound receipt establishes its producing attempt, source or configuration.')
    if native['missing']:
        result['warnings'].append('Unavailable identity checks for recovered metadata: ' + ', '.join(native['missing']) + '.')
    return result
