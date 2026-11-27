from copy import deepcopy

import pytest

from bactologix import sequences
from test_native import workspace


def test_pilot_survives_reload_and_requires_release(workspace, monkeypatch):
    rows = [{'sample': sid, 'runs': [sid], 'state': 'waiting', 'index': 0} for sid in ['one', 'two']]
    plan = {'id': 'sequence-test', 'state': 'draft', 'steps': ['hybas', 'basis'], 'pilot': True, 'rows': rows}
    workspace.put('sequences', plan['id'], plan)
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda _: None)
    seen = []
    def complete(store, plan, row, jobs):
        seen.append(row['sample'])
        row['state'] = 'done'
    monkeypatch.setattr(sequences, 'advance_row', complete)
    sequences.control(workspace, plan['id'], 'start')
    assert sequences.tick(workspace) and sequences.tick(workspace)
    assert workspace.get('sequences', plan['id'])['state'] == 'review'
    assert not sequences.tick(workspace)
    assert seen == ['one']
    sequences.control(workspace, plan['id'], 'release')
    with pytest.raises(ValueError):
        sequences.control(workspace, plan['id'], 'release')
    assert sequences.tick(workspace) and sequences.tick(workspace)
    assert seen == ['one', 'two']
    assert workspace.get('sequences', plan['id'])['state'] == 'done'


def test_reuse_and_input_handoff(workspace, monkeypatch):
    source = {'id': 'source', 'completed': 'yesterday', 'sample': {'id': 'sample', 'mode': 'hybrid'},
              'output': '/fixture', 'label': 'sample', 'cores': 2, 'memory_mb': 1000}
    workspace.put('runs', 'source', source)
    workspace.put('installs', 'install', {'version': {'commit': '123'}})
    spec = {'commit': '123', 'installation': 'install', 'config': '{}'}
    plan = {'id': 'seq', 'settings': {'basis': spec}, 'steps': ['hybas', 'basis']}
    row = {'index': 0, 'runs': ['source'], 'generation': 0}
    sequences.advance_row(workspace, plan, row, [])
    assert row['index'] == 1 and not workspace.jobs()
    monkeypatch.setattr(sequences, 'check_run_files', lambda _: None)
    def create(store, tool, sample, *args, **kwargs):
        assert sample['path'] == '/fixture' and tool == 'basis'
        child = dict(source, id='child', sequence_step=kwargs['sequence_step'])
        store.put('runs', 'child', child)
        return child
    monkeypatch.setattr(sequences, 'create_run', create)
    before = deepcopy(row)
    sequences.advance_row(workspace, plan, row, [])
    assert row['runs'] == ['source', 'child']
    monkeypatch.setattr(sequences, 'create_run', lambda *a, **kw: pytest.fail('duplicate child'))
    sequences.advance_row(workspace, plan, before, [])
    assert before['runs'] == row['runs']
