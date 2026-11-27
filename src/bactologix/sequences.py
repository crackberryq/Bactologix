"""Sequence existing HybAs drafts and run-scoped downstream analyses."""
import fcntl
from contextlib import contextmanager

from . import micp_handoff, run_config
from .runs import check_run_files, create_run, dry_run_current, run_status, validate_launch
from .store import identifier
from subprocess import SubprocessError


@contextmanager
def locked(store):
    with (store.home / 'sequences.lock').open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def save(store, ids, steps, pilot, *, configs=None, folders=None):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or len(set(ids)) != len(ids):
        raise ValueError('Choose distinct HybAs runs.')
    if steps not in (['basis'], ['micpscope'], ['basis', 'micpscope'], ['micpscope', 'basis']):
        raise ValueError('Choose a downstream pipeline order.')
    rows = []
    for rid in ids:
        run = store.get('runs', rid)
        if not run or run['tool'] != 'hybas' or store.get('run_visibility', rid, {}).get('archived'):
            raise ValueError('Choose a current HybAs draft or completed run.')
        rows.append({'sample': run['sample']['id'], 'runs': [rid], 'generation': run.get('draft_generation', 0),
                     'state': 'waiting', 'index': 0})
    if len({row['sample'] for row in rows}) != len(rows):
        raise ValueError('Choose one HybAs run per isolate.')
    if folders:
        from .result_delivery import destination
        for row in rows:
            row['folder'] = str(destination(store, folders.get(row['runs'][0])))
    settings = {}
    for step in steps:
        installation = store.get('settings', 'active-' + step)
        installed = store.get('installs', installation) if installation else None
        if not installed or not installed.get('ready'):
            raise ValueError('Set up the downstream pipeline before saving this sequence.')
        text = (configs or {}).get(step, run_config.defaults(store, step))
        run_config.validate(step, text)
        settings[step] = {'installation': installation, 'commit': installed['version']['commit'],
                          'config': text}
    plan = {'id': identifier('sequence'), 'steps': ['hybas', *steps], 'settings': settings,
            'rows': rows, 'state': 'draft', 'pilot': bool(pilot and len(rows) > 1)}
    store.put('sequences', plan['id'], plan)
    return plan


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


def advance_row(store, plan, row, jobs):
    index = row['index']
    if index == len(plan['steps']):
        if row.get('folder'):
            from .result_delivery import deliver_runs
            row['delivery'] = deliver_runs(store, [store.get('runs', r) for r in row['runs']], row['folder'], plan['id'])
        row['state'] = 'done'
        return
    source = store.get('runs', row['runs'][0])
    if not source or source.get('draft_generation', 0) != row['generation']:
        raise ValueError('The source draft changed. Save a new sequence with the revised settings.')
    token = [plan['id'], source['id'], index]
    if len(row['runs']) == index:
        existing = next((r for r in store.all('runs') if r.get('sequence_step') == token), None)
        if existing:
            row['runs'].append(existing['id'])
    if len(row['runs']) == index:
        tool = plan['steps'][index]
        spec = plan['settings'][tool]
        installed = store.get('installs', spec['installation'])
        if not installed or installed['version']['commit'] != spec['commit']:
            raise ValueError('The saved downstream installation changed. Review a new sequence.')
        check_run_files(source)
        if tool == 'micpscope':
            child = micp_handoff.create(store, source['id'], settings=spec, sequence_step=token)
        else:
            child = create_run(store, tool, dict(id=source['sample']['id'], path=source['output'], mode=source['sample']['mode']),
                               spec['config'], source['cores'], source['memory_mb'], source['id'],
                               source['label'] + ' · BaSIS', spec['installation'], sequence_step=token)
        row['runs'].append(child['id'])
        store.put('sequences', plan['id'], plan)
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
