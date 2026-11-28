"""Schedule ordered pipeline runs and explicit, isolate-scoped output handoffs."""
import fcntl
from contextlib import contextmanager
from pathlib import Path
from subprocess import SubprocessError

from . import hardware, micp_handoff, run_config
from .runs import basis_annotation_problem, check_inputs, check_run_files, create_run, dry_run_current, run_status, validate_launch
from .store import identifier


@contextmanager
def locked(store):
    with (store.home / 'sequences.lock').open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


TOOLS = {'hybas': 'HybAs', 'basis': 'BaSIS', 'micpscope': 'GeneScope'}
MEMBERSHIP_STATES = {'held', 'review', 'attention'}


def source_id(row):
    if 'source' in row:
        return row['source']
    return row['runs'][0]  # Existing HybAs-first plans stored the selection here.


def editable(plan, row, jobs):
    if plan['state'] == 'draft':
        return True
    if (plan['state'] not in MEMBERSHIP_STATES or row['state'] != 'waiting' or row['index']
            or row.get('started') or row.get('attempt') or row.get('retry')
            or (plan['pilot'] and row == plan['rows'][0])):
        return False
    if 'source' not in row:
        # A pre-upgrade worker could have enqueued the first job just before exiting.
        token = [plan['id'], source_id(row), 0]
        return not any(j['payload'].get('sequence_step') == token for j in jobs)
    return True


def summary(plan, jobs=()):
    rows = [{k: v for k, v in row.items() if k not in {'input_sample', 'reuse_generations'}}
            | {'source': source_id(row), 'reuse': row.get('reuse', {}), 'editable': editable(plan, row, jobs)}
            for row in plan['rows']]
    return {k: v for k, v in plan.items() if k not in {'settings', 'rows'}} | {
        'configs': {k: v['config'] for k, v in plan.get('settings', {}).items()}, 'rows': rows}


def _available(store, rid, sid, tool):
    run = store.get('runs', rid) if isinstance(rid, str) else None
    if (not run or run['tool'] != tool or run['sample']['id'] != sid
            or store.get('run_visibility', rid, {}).get('archived')):
        raise ValueError(f'{sid}: choose a current {TOOLS[tool]} run for this isolate.')
    return run


def _reuse_ok(run, source, sample):
    if source:
        origin = run['sample'].get('micp_handoff', {}).get('source_run') or run.get('parent')
        return origin == source['id']
    return bool(sample and sample.get('path') and run['sample'].get('path')
                and Path(sample['path']).resolve() == Path(run['sample']['path']).resolve())


def _check_result(run, source, sample, jobs, *, full):
    sid, tool = run['sample']['id'], run['tool']
    if run_status(run, jobs, False) != 'completed' or run.get('run_type') == 'followup' or not _reuse_ok(run, source, sample):
        raise ValueError(f'{sid}: choose a completed {TOOLS[tool]} run from the same input source.')
    root = Path(run['output'])
    outputs = ('safety/evidence.json', 'safety/report.html') if tool == 'basis' else ('micpscope/results.json', 'micpscope/report.html')
    if not any((root / rel).is_file() and (root / rel).stat().st_size for rel in outputs):
        raise ValueError(f'{sid}: the selected {TOOLS[tool]} run is missing its saved results.')
    if not source:
        if not run.get('inputs'):
            raise ValueError(f'{sid}: the selected result has no recorded input files.')
        for entry in run.get('input_layout', []):
            if (Path(sample['path']) / entry['path']).resolve() != Path(entry['target']):
                raise ValueError(f'{sid}: the registered annotation inputs changed since this run.')
    if run.get('inputs'):
        check_inputs(run, full=full)


def _row(store, choice, steps, jobs, folder):
    sid = choice['sample']
    src = choice.get('source') or None
    reuse = choice.get('reuse') or {}
    if not isinstance(reuse, dict) or set(reuse) - (set(steps) - {'hybas'}):
        raise ValueError(f'{sid}: choose existing results only for selected downstream pipelines.')
    source = _available(store, src, sid, 'hybas') if src else None
    sample = source['sample'] if source else store.get('samples', sid)
    if not sample:
        raise ValueError(f'{sid}: register the isolate or choose an existing HybAs run.')
    if source:
        allowed = {'completed', 'checked', 'draft'} if 'hybas' in steps else {'completed'}
        if run_status(source, jobs, False) not in allowed:
            raise ValueError(f'{sid}: choose a completed HybAs source or include a ready HybAs draft as step one.')
    row = {'sample': sid, 'source': src, 'generation': source.get('draft_generation', 0) if source else 0,
           'reuse': dict(reuse), 'reuse_generations': {}, 'runs': [], 'index': 0, 'state': 'waiting'}
    if not source:
        row['input_sample'] = sample
    for tool, rid in reuse.items():
        run = _available(store, rid, sid, tool)
        if 'hybas' in steps and not source:
            raise ValueError(f'{sid}: new HybAs outputs need a new downstream run.')
        _check_result(run, source, sample, jobs, full=False)
        row['reuse_generations'][tool] = run.get('draft_generation', 0)
    needed = set(steps) - reuse.keys() - {'hybas'}
    if source and source.get('completed'):
        if 'basis' in needed:
            issue = basis_annotation_problem(source['output'], sid)
            if issue:
                raise ValueError(f'{sid}: {issue}')
        if 'micpscope' in needed:
            micp_handoff.source_files(source)
    if not source and 'hybas' not in steps:
        for tool in needed:
            if sample.get('input_manifest'):
                raise ValueError(f'{sid}: select completed HybAs outputs for this read manifest.')
            if tool == 'basis':
                issue = basis_annotation_problem(sample['path'], sid)
                if issue:
                    raise ValueError(f'{sid}: {issue}')
            else:
                proteins = Path(sample['path']) / 'annotation' / (sid + '.faa')
                if not proteins.is_file() or not proteins.stat().st_size:
                    raise ValueError(f'{sid}: GeneScope requires an annotation protein FASTA. Choose a HybAs source.')
    if folder:
        from .result_delivery import destination
        row['folder'] = str(destination(store, folder))
    return row


def _specs(store, steps, rows, configs, previous):
    settings = {}
    for tool in steps:
        if all(source_id(r) if tool == 'hybas' else r.get('reuse', {}).get(tool) for r in rows):
            if tool in previous:
                settings[tool] = previous[tool]
            continue
        saved = previous.get(tool, {})
        installation = saved.get('installation') or store.get('settings', 'active-' + tool)
        installed = store.get('installs', installation) if installation else None
        if not installed or not installed.get('ready') or saved.get('commit', installed['version']['commit']) != installed['version']['commit']:
            raise ValueError(f'Set up {TOOLS[tool]} before scheduling new runs for this step.')
        text = configs.get(tool) if tool in configs else saved.get('config')
        if text is None:
            text = run_config.defaults(store, tool)
        run_config.validate(tool, text)
        budget = saved.get('budget') or hardware.run_defaults(store, tool)
        settings[tool] = {'installation': installation, 'commit': installed['version']['commit'],
                          'config': text, 'budget': budget}
    return settings


def _choices(isolates):
    if not isinstance(isolates, list) or not 1 <= len(isolates) <= 500:
        raise ValueError('Choose between one and 500 isolates.')
    if any(not isinstance(r, dict) or not isinstance(r.get('sample'), str) or not r['sample'] for r in isolates):
        raise ValueError('Choose an isolate for each row.')
    if len({r['sample'] for r in isolates}) != len(isolates):
        raise ValueError('Choose each isolate once.')


def _check_revision(plan, expected):
    if expected is not None and plan.get('revision', 0) != expected:
        raise ValueError('Another window changed this sequence. Reopen it before saving your edits.')


def save_inputs(store, isolates, steps, pilot, *, configs=None, folders=None, sid=None, revision=None):
    _choices(isolates)
    if (not isinstance(steps, list) or not steps or any(not isinstance(t, str) or t not in TOOLS for t in steps)
            or len(set(steps)) != len(steps) or ('hybas' in steps and steps[0] != 'hybas')):
        raise ValueError('Choose distinct pipelines in execution order. Place HybAs first when it supplies the assembly.')
    with locked(store):
        old = store.get('sequences', sid) if sid else None
        if sid and (not old or old['state'] != 'draft'):
            raise ValueError('Only a saved draft can change its pipeline order or settings.')
        if old:
            _check_revision(old, revision)
        jobs = store.jobs()
        rows = [_row(store, r, steps, jobs, (folders or {}).get(r['sample'])) for r in isolates]
        plan = {'id': sid or identifier('sequence'), 'steps': steps, 'revision': (old or {}).get('revision', 0) + 1,
                'settings': _specs(store, steps, rows, configs or {}, (old or {}).get('settings', {})),
                'rows': rows, 'state': 'draft', 'pilot': bool(pilot and len(rows) > 1)}
        store.put('sequences', plan['id'], plan)
    return plan


def save(store, ids, steps, pilot, *, configs=None, folders=None):
    """Accept the original HybAs-run selector payload from already-open clients."""
    if not isinstance(ids, list):
        raise ValueError('Choose HybAs runs.')
    isolates, destinations = [], {}
    for rid in ids:
        run = store.get('runs', rid)
        if not run or run['tool'] != 'hybas':
            raise ValueError('Choose a current HybAs draft or completed run.')
        sid = run['sample']['id']
        isolates.append({'sample': sid, 'source': rid})
        if folders:
            destinations[sid] = folders.get(rid)
    return save_inputs(store, isolates, ['hybas', *(steps or [])], pilot, configs=configs, folders=destinations)


def edit_members(store, sid, isolates, folders=None, *, revision=None):
    _choices(isolates)
    with locked(store):
        plan = store.get('sequences', sid)
        if not plan or plan['state'] not in MEMBERSHIP_STATES:
            raise ValueError('Hold an active sequence before changing its remaining isolates.')
        _check_revision(plan, revision)
        jobs = store.jobs()
        fixed = {r['sample']: r for r in plan['rows'] if not editable(plan, r, jobs)}
        selected = {r['sample'] for r in isolates}
        if (fixed.keys() - selected or (plan['pilot'] and isolates[0]['sample'] != plan['rows'][0]['sample'])):
            raise ValueError('Keep the pilot and every started isolate in the sequence.')
        rows = []
        for choice in isolates:
            sample = choice['sample']
            if sample in fixed:
                row = fixed[sample]
                if source_id(row) != (choice.get('source') or None) or row.get('reuse', {}) != (choice.get('reuse') or {}):
                    raise ValueError('The inputs of a started isolate are fixed.')
            else:
                row = _row(store, choice, plan['steps'], jobs, (folders or {}).get(sample))
            rows.append(row)
        plan['settings'] = _specs(store, plan['steps'], rows, {}, plan['settings'])
        plan['rows'] = rows
        plan['revision'] = plan.get('revision', 0) + 1
        store.put('sequences', sid, plan)


def control(store, sid, action):
    from .engine import launch_worker
    with locked(store):
        plan = store.get('sequences', sid)
        allowed = {'start': 'draft', 'release': 'review', 'retry': 'attention', 'hold': 'active', 'resume': 'held', 'wake': 'active'}
        if not plan or action not in allowed or plan['state'] != allowed[action]:
            raise ValueError('Refresh the sequence to see its current action.')
        if action == 'retry':
            for row in plan['rows']:
                if row['state'] == 'attention':
                    row.update(state='waiting', message='')
                    row.pop('attempt', None)
                    row['retry'] = row.get('retry', 0) + 1
        if action == 'release':
            plan['pilot'] = False
        plan['state'] = 'held' if action == 'hold' else 'active'
        store.put('sequences', sid, plan)
    if action != 'hold':
        launch_worker(store)


def _create_step(store, plan, row, source, tool, token):
    spec = plan['settings'][tool]
    installed = store.get('installs', spec['installation'])
    if not installed or installed['version']['commit'] != spec['commit']:
        raise ValueError(f'The saved {TOOLS[tool]} installation changed. Review a new sequence.')
    if source and tool == 'micpscope':
        return micp_handoff.create(store, source['id'], settings=spec, sequence_step=token)
    if source:
        check_run_files(source)
        sample = dict(id=source['sample']['id'], path=source['output'], mode=source['sample']['mode'])
        return create_run(store, tool, sample, spec['config'], source['cores'], source['memory_mb'], source['id'],
                          source['label'] + ' · ' + TOOLS[tool], spec['installation'], sequence_step=token)
    budget = spec['budget']
    cfg = run_config.parse_config(spec['config'])
    auto_swap = tool == 'hybas' and cfg.get('bactologix_compute', {}).get('auto_swap', False)
    return create_run(store, tool, row['input_sample'], spec['config'], budget['cores'], budget['memory_mb'],
                      installation=spec['installation'], sequence_step=token, auto_swap=auto_swap)


def _step_run(store, plan, row, jobs):
    index = row['index']
    tool = plan['steps'][index]
    src = source_id(row)
    if not src and index and plan['steps'][0] == 'hybas':
        src = row['runs'][0]
    source = store.get('runs', src) if src else None
    if src and (not source or (src == source_id(row) and source.get('draft_generation', 0) != row['generation'])):
        raise ValueError('The selected HybAs draft changed or was removed. Review a new sequence.')
    if src and store.get('run_visibility', src, {}).get('archived'):
        raise ValueError('The selected HybAs source has been archived.')
    if src and tool != 'hybas' and run_status(source, jobs, False) != 'completed':
        raise ValueError('The selected HybAs source must be complete before this step can use its outputs.')
    # Preserve durable queue and child-run keys for sequences created before isolate selection.
    token = [plan['id'], row['sample'] if 'source' in row else src, index]
    reused = row.get('reuse', {}).get(tool)
    if reused:
        run = _available(store, reused, row['sample'], tool)
        _check_result(run, source, row.get('input_sample'), jobs, full=True)
        if run.get('draft_generation', 0) != row['reuse_generations'][tool]:
            raise ValueError(f'The selected {TOOLS[tool]} result changed. Review its source and completion status.')
    if index == len(row['runs']):
        rid = src if tool == 'hybas' else reused
        if not rid:
            child = next((r for r in store.all('runs') if r.get('sequence_step') == token), None)
            if not child:
                child = _create_step(store, plan, row, source, tool, token)
            rid = child['id']
        row['runs'].append(rid)
        store.put('sequences', plan['id'], plan)
    return token


def advance_row(store, plan, row, jobs):
    index = row['index']
    if index == len(plan['steps']):
        if row.get('folder'):
            from .result_delivery import deliver_runs
            row['delivery'] = deliver_runs(store, [store.get('runs', r) for r in row['runs']], row['folder'], plan['id'])
        row['state'] = 'done'
        return
    if not row.get('started'):
        row['started'] = True
        store.put('sequences', plan['id'], plan)
    token = _step_run(store, plan, row, jobs)
    rid = row['runs'][index]
    run = store.get('runs', rid)
    if not run:
        raise ValueError('The selected run was removed.')
    status = run_status(run, jobs, False)
    if status in {'queued', 'running'}:
        return
    if status == 'completed':
        row.pop('attempt', None)
        row['index'] += 1
        row['state'] = 'waiting'
        return
    if status in {'awaiting_review', 'aware_complete'}:
        raise ValueError('Open the HybAs run and complete its researcher review and continuation.')
    matching = next((j for j in jobs if j['payload'].get('sequence_step') == token and
                     j['payload'].get('sequence_retry') == row.get('retry', 0)), None)
    if matching:
        row['attempt'] = matching['id']
    attempt = store.job(row['attempt']) if row.get('attempt') else None
    if attempt and attempt['status'] != 'succeeded':
        raise ValueError('Review the failed or interrupted attempt before retrying.')
    action = 'run' if dry_run_current(store, run) else 'dry-run'
    checked = validate_launch(store, rid, action, full_inputs=False)
    row['attempt'] = store.submit('workflow', {'run_id': rid, 'action': action, 'sequence_id': plan['id'], 'sequence_step': token, 'sequence_retry': row.get('retry', 0)},
                                  expected_draft_generation=checked.get('draft_generation', 0))
    row['state'] = 'active'


def tick(store):
    """Called by the single worker between jobs, including after restarting."""
    active = [p for p in store.all('sequences') if p['state'] == 'active']
    if not active:
        return False
    queue = store.queue_snapshot()
    if queue['paused'] or queue['waiting'] or queue['running']:
        return False
    progressed = False
    with locked(store):
        for old in active:
            plan = store.get('sequences', old['id'])
            if plan['state'] != 'active':
                continue
            rows = plan['rows'][:1] if plan['pilot'] else plan['rows']
            for row in rows:
                if row['state'] in {'done', 'attention'}:
                    continue
                try:
                    advance_row(store, plan, row, store.jobs())
                except (ValueError, OSError, RuntimeError, SubprocessError) as exc:
                    row.update(state='attention', message=str(exc))
                store.put('sequences', plan['id'], plan)
                progressed = True
                break
            else:
                plan['state'] = ('attention' if any(r['state'] == 'attention' for r in rows)
                                 else 'review' if plan['pilot'] else 'done')
                store.put('sequences', plan['id'], plan)
                progressed = True
    return progressed
