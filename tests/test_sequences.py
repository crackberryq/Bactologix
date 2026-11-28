from copy import deepcopy

import pytest

from bactologix import sequences
from test_native import installation, sample, workspace


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


def completed(store, rid, tool, sample, root, **extra):
    run = {'id': rid, 'tool': tool, 'completed': 'yesterday', 'sample': sample, 'output': str(root / rid),
           'label': sample['id'], 'cores': 2, 'memory_mb': 1024, **extra}
    store.put('runs', rid, run)
    return run


def retained(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('fixture')
    return path


def test_downstream_only_uses_selected_outputs_in_order(workspace, tmp_path):
    sample = {'id': 'one', 'mode': 'hybrid', 'path': str(tmp_path)}
    completed(workspace, 'source', 'hybas', sample, tmp_path)
    completed(workspace, 'scope', 'micpscope', dict(sample, micp_handoff={'source_run': 'source'}), tmp_path)
    completed(workspace, 'screen', 'basis', sample, tmp_path, parent='source')
    retained(tmp_path, 'screen/safety/report.html')
    retained(tmp_path, 'scope/micpscope/results.json')
    choice = dict(sample='one', source='source', reuse={'basis': 'screen', 'micpscope': 'scope'})
    plan = sequences.save_inputs(workspace, [choice], ['micpscope', 'basis'], False)
    assert not plan['settings']
    row = plan['rows'][0]
    for _ in range(3):
        sequences.advance_row(workspace, plan, row, [])
    assert row['runs'] == ['scope', 'screen'] and row['state'] == 'done'
    assert not workspace.jobs()


def test_completed_source_must_supply_requested_step(workspace, tmp_path):
    sample = {'id': 'one', 'mode': 'hybrid', 'path': str(tmp_path)}
    completed(workspace, 'source', 'hybas', sample, tmp_path)
    choice = dict(sample='one', source='source')
    with pytest.raises(ValueError, match='Missing annotation input'):
        sequences.save_inputs(workspace, [choice], ['basis'], False)
    with pytest.raises(ValueError, match='annotation and final assembly'):
        sequences.save_inputs(workspace, [choice], ['micpscope'], False)
    completed(workspace, 'screen', 'basis', sample, tmp_path, parent='other')
    choice['reuse'] = {'basis': 'screen'}
    with pytest.raises(ValueError, match='same input source'):
        sequences.save_inputs(workspace, [choice], ['basis'], False)
    completed(workspace, 'screen', 'basis', sample, tmp_path, parent='source', run_type='followup')
    with pytest.raises(ValueError, match='same input source'):
        sequences.save_inputs(workspace, [choice], ['basis'], False)
    completed(workspace, 'screen', 'basis', sample, tmp_path, parent='source')
    with pytest.raises(ValueError, match='missing its saved results'):
        sequences.save_inputs(workspace, [choice], ['basis'], False)


def test_native_reuse_checks_content_again_when_sequence_starts(workspace, tmp_path):
    import os
    from bactologix.runs import snapshot_files
    proteins = retained(tmp_path, 'annotation/one.faa')
    sample = {'id': 'one', 'path': str(tmp_path), 'mode': 'hybrid'}
    workspace.put('samples', 'one', sample)
    completed(workspace, 'scope', 'micpscope', sample, tmp_path, inputs=snapshot_files([proteins]))
    retained(tmp_path, 'scope/micpscope/report.html')
    choice = dict(sample='one', reuse={'micpscope': 'scope'})
    plan = sequences.save_inputs(workspace, [choice], ['micpscope'], False)
    original = proteins.stat()
    proteins.write_text('revised')
    os.utime(proteins, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(ValueError, match='SHA-256'):
        sequences.advance_row(workspace, plan, plan['rows'][0], [])
    assert not workspace.jobs()


def test_draft_edit_and_started_membership_preserve_pins(workspace, tmp_path):
    for sid in ('one', 'two', 'three'):
        workspace.put('samples', sid, {'id': sid, 'path': str(tmp_path / sid), 'mode': 'hybrid'})
    install = {'id': 'i1', 'version': {'commit': '123'}, 'ready': True}
    workspace.put('installs', 'i1', install)
    workspace.put('settings', 'active-hybas', 'i1')
    choices = [{'sample': sid} for sid in ('one', 'two')]
    cfg = {'hybas': 'threads: 3\n'}
    plan = sequences.save_inputs(workspace, choices, ['hybas'], True, configs=cfg)
    assert not workspace.all('runs') and not workspace.jobs()
    workspace.put('settings', 'active-hybas', 'a-new-install')
    plan = sequences.save_inputs(workspace, choices, ['hybas'], True, sid=plan['id'])
    with pytest.raises(ValueError, match='Another window'):
        sequences.save_inputs(workspace, choices, ['hybas'], False, sid=plan['id'], revision=plan['revision'] - 1)
    assert sequences.summary(plan)['configs'] == cfg
    assert plan['settings']['hybas']['installation'] == 'i1'
    plan.update(state='review')
    plan['rows'][0].update(state='done', started=True, runs=['completed'], index=1)
    workspace.put('sequences', plan['id'], plan)
    with pytest.raises(ValueError, match='Keep the pilot'):
        sequences.edit_members(workspace, plan['id'], [{'sample': 'two'}])
    choices[1] = {'sample': 'three'}
    sequences.edit_members(workspace, plan['id'], choices)
    updated = workspace.get('sequences', plan['id'])
    assert updated['rows'][0] == plan['rows'][0]
    assert [r['sample'] for r in updated['rows']] == ['one', 'three']
    assert updated['settings'] == plan['settings']


def test_old_held_plan_can_remove_pending_isolate_but_not_one_with_a_queued_claim(workspace):
    rows = [dict(sample=i, runs=['run-' + i], generation=0, index=0, state='waiting') for i in ['one', 'two', 'three']]
    plan = dict(id='seq-old', steps=['hybas', 'basis'], rows=rows, pilot=False, state='held', settings={})
    workspace.put('sequences', 'seq-old', plan)
    workspace.submit('workflow', dict(sequence_step=['seq-old', 'run-one', 0], run_id='run-one'))
    with pytest.raises(ValueError, match='Keep the pilot'):
        sequences.edit_members(workspace, 'seq-old', [{'sample': 'two', 'source': 'run-two'}])
    flags = [r['editable'] for r in sequences.summary(plan, workspace.jobs())['rows']]
    assert flags == [False, True, True]


def test_new_hybas_and_recovered_child_use_same_native_run(workspace, monkeypatch, tmp_path):
    sample = {'id': 'sample', 'mode': 'illumina_only', 'path': str(tmp_path)}
    workspace.put('samples', 'sample', sample)
    for tool in ['hybas', 'basis']:
        workspace.put('installs', tool, dict(ready=True, version={'commit': tool}))
        workspace.put('settings', 'active-' + tool, tool)
    plan = sequences.save_inputs(workspace, [{'sample': 'sample'}], ['hybas', 'basis'], False)
    row = plan['rows'][0]
    calls = []
    def create(store, tool, inputs, config, cores, memory, parent=None, *args, **kwargs):
        rid = 'child-' + tool
        calls.append((tool, parent, inputs['path']))
        return completed(store, rid, tool, sample, tmp_path, sequence_step=kwargs['sequence_step'])
    monkeypatch.setattr(sequences, 'create_run', create)
    monkeypatch.setattr(sequences, 'check_run_files', lambda _: None)
    sequences.advance_row(workspace, plan, row, [])
    before = deepcopy(row)
    sequences.advance_row(workspace, plan, row, [])
    sequences.advance_row(workspace, plan, before, [])
    assert before['runs'] == row['runs'] == ['child-hybas', 'child-basis']
    assert calls == [('hybas', None, str(tmp_path)), ('basis', 'child-hybas', str(tmp_path / 'child-hybas'))]


def test_registered_reads_are_staged_and_checked_through_native_queue(workspace, installation, sample, monkeypatch):
    plan = sequences.save_inputs(workspace, [{'sample': sample['id']}], ['hybas'], False,
                                 configs={'hybas': 'threads: 1\n'})
    calls = []
    def validate(store, rid, action, **kwargs):
        run = store.get('runs', rid)
        sequences.check_run_files(run)
        calls.append((rid, action))
        return run
    monkeypatch.setattr(sequences, 'validate_launch', validate)
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda _: None)
    sequences.control(workspace, plan['id'], 'start')
    assert sequences.tick(workspace)
    saved = workspace.get('sequences', plan['id'])
    rid, = saved['rows'][0]['runs']
    assert calls == [(rid, 'dry-run')]
    jobs = workspace.jobs()
    assert len(jobs) == 1 and jobs[0]['payload']['sequence_step'] == [plan['id'], sample['id'], 0]
    run = workspace.get('runs', rid)
    assert run['installation'] == installation['id'] and len(run['inputs']) == 2
    assert not sequences.tick(workspace)
