"""Only passive display can reuse coverage from unchanged output and attempts."""
import json
import os
from pathlib import Path

from bactologix import assessment
from bactologix.store import Store


def test_display_coverage_rechecks_attempts_run_identity_and_native_file(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    run = {'id': 'r', 'tool': 'micpscope', 'output': str(store.home / 'outputs'), 'sample': {'id': 'one'}}
    path = Path(run['output']) / 'micpscope/results.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema_version': '1.3', 'sample_id': 'one', 'proteins': []}))
    reads = []
    read = assessment._read

    def capture(*args):
        reads.append(args)
        return read(*args)

    monkeypatch.setattr(assessment, '_read', capture)
    def panel():
        return assessment.summary(store, run, run_jobs=store.jobs())

    assert panel()['label'] == 'Coverage requires review'
    assert panel()['label'] == 'Coverage requires review'
    assert len(reads) == 1
    assert assessment.summary(store, run)['label'] == 'Coverage requires review'
    assert len(reads) == 2
    attempted = store.submit('workflow', {'run_id': 'r', 'action': 'run'})
    assert 'queued' in panel()['reasons'][0]
    store.update_job(attempted, status='succeeded')
    assert panel()['label'] == 'Coverage requires review'
    run['sample']['id'] = 'two'
    assert 'does not match' in panel()['reasons'][0]
    run['sample']['id'] = 'one'
    assert panel()['label'] == 'Coverage requires review'
    before = path.stat()
    replacement = path.with_suffix('.replacement')
    replacement.write_text(path.read_text().replace('one', 'two'))
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement.replace(path)
    assert path.stat().st_size == before.st_size
    assert 'does not match' in panel()['reasons'][0]
    path.write_text(path.read_text().replace('two', 'one'))
    assert panel()['label'] == 'Coverage requires review'
    target = path.parent.with_name('elsewhere')
    path.parent.rename(target)
    path.parent.symlink_to(target, target_is_directory=True)
    assert 'Linked' in panel()['reasons'][0]


def test_display_detects_primary_evidence_appearing_after_fallback(tmp_path):
    store = Store(tmp_path / 'workspace')
    run = {'id': 'r', 'tool': 'hybas', 'output': str(store.home / 'outputs'), 'sample': {'id': 'one'}}
    fallback = Path(run['output'], 'metadata/run_summary.json')
    fallback.parent.mkdir(parents=True)
    fallback.write_text('{"isolate":"one"}')
    assert assessment.summary(store, run, run_jobs=[])['label'] == 'Coverage requires review'
    primary = Path(run['output'], 'reports/hybas/results.json')
    primary.parent.mkdir(parents=True)
    primary.write_text('{"isolate":"two"}')
    assert 'does not match' in assessment.summary(store, run, run_jobs=[])['reasons'][0]


def test_shared_output_owner_is_rechecked_before_display_cache(tmp_path):
    store = Store(tmp_path / 'workspace')
    run = {'id': 'r', 'tool': 'basis', 'run_type': 'followup', 'parent': 'parent',
           'output': str(store.home / 'outputs'), 'sample': {'id': 'one'}}
    store.put('followup_output_owner', 'parent', 'r')
    file = Path(run['output'], 'safety/deepsafe-report/evidence.json')
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({'schema_version': 'basis-report-1',
                                'run': {'completeness': 'completed_requested_scope'},
                                'deep_records': [{'sample_id': 'one', 'module': 'test'}]}))
    assert assessment.summary(store, run, run_jobs=[])['state'] == 'recorded'
    store.put('followup_output_owner', 'parent', 'other')
    assert 'no longer owns' in assessment.summary(store, run, run_jobs=[])['reasons'][0]
