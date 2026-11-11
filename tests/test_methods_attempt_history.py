"""Later attempts must not replace earlier publication evidence."""
import copy

import pytest

from bactologix import methods
from bactologix.store import Store


def test_attempt_receipts_retain_prior_evidence_and_reject_replacement(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    evidence = {'versions': {'assembler': '1'}, 'records': [], 'warnings': []}
    monkeypatch.setattr(methods, 'collect', lambda run: copy.deepcopy(evidence))
    run = {'id': 'run-sample', 'completed': 'first', 'config_sha256': 'config',
           'workflow_sha256': 'workflow', 'commit': 'source'}
    first = methods.capture(store, run, 'job-first')
    assert methods.capture(store, run, 'job-first') == first
    evidence['versions']['assembler'] = '2'
    run['completed'] = 'second'
    second = methods.capture(store, run, 'job-second')
    assert store.get('methods_evidence', run['id']) == second
    assert store.get('methods_attempt_evidence', 'job-first') == first
    assert store.get('methods_attempt_evidence', 'job-second') == second
    with pytest.raises(ValueError, match='cannot be replaced'):
        methods.capture(store, run, 'job-first')
    assert store.get('methods_evidence', run['id']) == second
    assert store.get('methods_attempt_evidence', 'job-first') == first


def test_next_capture_retains_available_legacy_receipt(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    legacy = {'run_id': 'run-sample', 'job_id': 'job-old', 'evidence': {'versions': {'tool': 'old'}}}
    store.put('methods_evidence', 'run-sample', legacy)
    monkeypatch.setattr(methods, 'collect', lambda run: {'versions': {'tool': 'new'}})
    run = {'id': 'run-sample', 'completed': 'next', 'config_sha256': 'config',
           'workflow_sha256': 'workflow', 'commit': 'source'}
    methods.capture(store, run, 'job-next')
    assert store.get('methods_attempt_evidence', 'job-old') == legacy
