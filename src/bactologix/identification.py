"""Reviewed HybAs continuation within one saved run and immutable configuration."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from .runs import check_run_files, parse_config, tree_digest
from .store import now, identifier

OPTION = 'kraken_review_before_filtering'
SCHEMA = 'hybas-identification-review-v1'


def enabled(run):
    return run.get('tool') == 'hybas' and parse_config(run['config']).get(OPTION, False) is True


def supports(source):
    p = Path(source) / 'app/identification-review.json'
    try:
        return json.loads(p.read_text()).get('interface') == 1
    except (OSError, ValueError, AttributeError):
        return False


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def report_path(run):
    return Path(run['output']) / 'reports/identification/review.json'


def read_evidence(run):
    p = report_path(run)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Identification report is missing or invalid. Finish identification first.')
    if sha(p) != run.get('identification', {}).get('evidence_sha256'):
        raise ValueError('The identification report changed. Create a new identification run and review its report.')
    doc = json.loads(p.read_text())
    if doc.get('schema') != SCHEMA or doc.get('isolate') != run['sample']['id']:
        raise ValueError('This identification report belongs to another sample or version.')
    # UI checks only metadata; the native continuation additionally hashes bytes.
    root = Path(run['output']).resolve()
    for row in doc['files']:
        f = Path(row['path'])
        if f.is_symlink() or not f.resolve().is_relative_to(root) or not f.is_file():
            raise ValueError('Invalid identification evidence path.')
        stat = f.stat()
        if stat.st_size != row['size'] or stat.st_mtime_ns != row['mtime_ns']:
            raise ValueError('Saved Kraken evidence changed; create a new identification run before review.')
    return doc


def capture(run, job):
    doc = json.loads(report_path(run).read_text())
    if doc.get('schema') != SCHEMA or doc.get('isolate') != run['sample']['id']:
        raise ValueError('The identification phase did not produce the expected report.')
    run['identification'] = {'state': 'awaiting_review', 'evidence_sha256': sha(report_path(run)),
                             'completed': now(), 'job': job}
    read_evidence(run)


def check_decision(run):
    state = run.get('identification', {})
    if not state.get('decision_path'):
        return
    p = Path(state['decision_path'])
    if p.is_symlink() or not p.resolve().is_relative_to(Path(run['folder']).resolve() / 'identification-decisions'):
        raise ValueError('Invalid saved identification decision path.')
    if sha(p) != state['decision_sha256']:
        raise ValueError('The saved identification decision changed.')
    doc = json.loads(p.read_text())
    if doc.get('evidence_sha256') != state['evidence_sha256'] or doc.get('evidence_path') != str(report_path(run)):
        raise ValueError('The saved decision refers to different identification evidence.')
    read_evidence(run)


def arguments(run):
    state = run.get('identification', {})
    if state.get('decision_path'):
        return ['--config', 'kraken_review_decision=' + state['decision_path'],
                'kraken_review_decision_sha256=' + state['decision_sha256']]
    return []


def token(run, draft):
    return hashlib.sha256(json.dumps([run['config_sha256'], run['workflow_sha256'], run.get('identification'), draft], sort_keys=True).encode()).hexdigest()


def inspect(store, rid, mode=None, taxid=None, query=""):
    run = store.get('runs', rid)
    if not run or not enabled(run) or not run.get('identification'):
        raise ValueError('Finish an identification-review run first.')
    check_run_files(run)
    if tree_digest(Path(run['folder']) / 'workflow') != run['workflow_sha256']:
        raise ValueError('Saved workflow source changed.')
    doc = read_evidence(run)
    draft = store.get('identification_drafts', rid, {})
    chosen_mode = mode if mode is not None else draft.get('mode', 'aware')
    chosen_id = str(taxid if taxid is not None else draft.get('taxid', ''))
    if chosen_mode not in {'aware', 'manual'}:
        raise ValueError('Choose automatic AWARE or a manual target.')
    if chosen_mode == 'aware':
        chosen_id = doc['recommendation']['taxid']
    candidate = next((x for x in doc['candidates'] if x['taxid'] == chosen_id), None)
    actions = doc['recommendation']['actions'] if chosen_mode == 'aware' else (candidate or {}).get('manual_actions', {})
    query = str(query).strip().lower()[:200]
    candidates = [r for r in doc['candidates'] if not query or query in r['name'].lower() or query in r['taxid']]
    return {'id': rid, 'signature': token(run, draft), 'mode': chosen_mode, 'taxid': chosen_id,
            'candidate': candidate, 'actions': actions, 'valid': bool(candidate),
            'candidates': candidates[:100], 'candidate_count': len(candidates), 'recommendation': doc['recommendation'],
            'platforms': doc['platforms'], 'include_children': doc['include_children'],
            'state': run['identification']['state'], 'decision': run['identification'].get('selection'),
            'created': doc['created']}


def save(store, rid, mode, taxid, signature, confirm=False):
    """Compare-and-swap against both the reviewed evidence and the saved choice."""
    review = inspect(store, rid, mode, taxid)
    if not review['valid']:
        raise ValueError('Choose a TaxID present in this report. Unclassified/root entries cannot be targets.')
    if signature != review['signature']:
        raise ValueError('The review changed in another window. Refresh identification and review again.')
    selection = {k: review[k] for k in ('mode', 'taxid')}
    selection.update(name=review['candidate']['name'], rank=review['candidate']['rank'], actions=review['actions'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT body FROM items WHERE kind='runs' AND id=?", (rid,)).fetchone()
        run = json.loads(row[0]) if row else {}
        row = db.execute("SELECT body FROM items WHERE kind='identification_drafts' AND id=?", (rid,)).fetchone()
        draft = json.loads(row[0]) if row else {}
        if token(run, draft) != signature:
            raise ValueError('The review changed in another window. Refresh and try again.')
        if run.get('identification', {}).get('state') != 'awaiting_review':
            raise ValueError('The target is already finalized. Existing decisions and outputs are preserved.')
        visible = db.execute("SELECT body FROM items WHERE kind='run_visibility' AND id=?", (rid,)).fetchone()
        if visible and json.loads(visible[0]).get('archived'):
            raise ValueError('Restore this run before reviewing its target.')
        for job in db.execute("SELECT kind,payload FROM jobs WHERE status IN ('queued','running')"):
            if job['kind'] == 'app_update' or (job['kind'] == 'workflow' and json.loads(job['payload']).get('run_id') == rid):
                raise ValueError('Wait for active work before changing the identification decision.')
        from .variants import lock_active
        if lock_active(db, rid):
            raise ValueError('Wait for the run snapshot to finish.')
        if confirm:
            directory = Path(run['folder']) / 'identification-decisions'
            directory.mkdir(exist_ok=True)
            path = directory / (identifier('decision') + '.json')
            decision = dict(schema='hybas-identification-decision-v1', **selection,
                            evidence_path=str(report_path(run)), evidence_sha256=run['identification']['evidence_sha256'],
                            recorded_at=now(), original_config_sha256=run['config_sha256'],
                            workflow_sha256=run['workflow_sha256'])
            with path.open('x') as stream:
                json.dump(decision, stream, indent=2); stream.write('\n')
            run['identification'].update(state='continuation_ready', decision_path=str(path),
                                         decision_sha256=sha(path), selection=selection)
            run['dry_run_ok'] = False
            db.execute("UPDATE items SET body=? WHERE kind='runs' AND id=?", (json.dumps(run), rid))
        else:
            selection['revision'] = identifier('choice')
            db.execute("INSERT INTO items VALUES ('identification_drafts',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body", (rid, json.dumps(selection)))
    return run
