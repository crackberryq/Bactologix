"""Verified AWARE bundles and derived sample registration; no layout guessing."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .identification import sha
from .hybas_manifest_schema import validate_manifest
from .store import now, identifier

SCHEMA = 'hybas-aware-bundle-v1'


def supports(source):
    p = Path(source)/'app/aware-outputs.json'
    try:
        return json.loads(p.read_text()).get('interface') == 1
    except (OSError, ValueError, AttributeError):
        return False


def enabled(cfg):
    return cfg.get('aware_run_until', 'assembly') == 'aware' or cfg.get('aware_preserve_groups') is True or cfg.get('aware_statistics') is True


def safe_file(root, relative):
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError('AWARE bundle file paths must stay within the bundle.')
    current = Path(root).resolve()
    for part in p.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('AWARE bundles cannot contain symbolic links.')
    if not current.is_file():
        raise ValueError('Missing AWARE bundle file: ' + relative)
    return current


def review(path):
    from .runs import absolute_path, valid_id, snapshot_files
    path = absolute_path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32*1024*1024:
        raise ValueError('Choose a valid AWARE manifest.json file.')
    checksum = sha(path)
    doc = json.loads(path.read_text())
    if doc.get('schema') != SCHEMA:
        raise ValueError('Unsupported AWARE bundle schema.')
    files = {}
    for item in doc['files']:
        relative = item['path']
        if relative in files:
            raise ValueError('Duplicate file in AWARE bundle.')
        p = safe_file(path.parent, relative)
        if p.stat().st_size != item['size'] or sha(p) != item['sha256']:
            raise ValueError('AWARE bundle content changed: ' + relative)
        files[relative] = item
    rows = []; ids = set()
    for group in doc['groups']:
        if group['id'] in ids:
            raise ValueError('Duplicate group in AWARE bundle.')
        ids.add(group['id'])
        row = {k:group.get(k) for k in ('id','name','category','taxid','units','reason')}
        row['eligible'] = False
        if group.get('manifest') and group['category'] in {'primary','provisional_species_assignment'}:
            manifest = deepcopy(group['manifest'])
            if len(manifest.get('samples', [])) != 1:
                raise ValueError('Each AWARE group must declare exactly one sample.')
            for lib in manifest['samples'][0]['libraries']:
                for rg in lib['read_groups']:
                    for key in ('r1','r2','reads'):
                        if key in rg:
                            if rg[key] not in files:
                                raise ValueError('Unverified input in AWARE group.')
                            rg[key] = str(safe_file(path.parent, rg[key]))
            try:
                result = validate_manifest(manifest, base_dir=path.parent)
                sample = result['manifest']['samples'][0]
                for lib in sample['libraries']:
                    if lib['platform'] == 'illumina':
                        ordinary = lib['library_type'] == 'paired_end' and lib['orientation'] == 'fr'
                        method = lib.get('preprocessing', 'fastp' if ordinary else None)
                        if method is None or method == 'fastp' and not ordinary:
                            raise ValueError('Non-FR or mate-pair inputs require explicit preprocessing metadata.')
                sid = valid_id(sample['sample_id'])
                inputs = [Path(rg[k]) for lib in sample['libraries'] for rg in lib['read_groups'] for k in ('r1','r2','reads') if k in rg]
                row.update(eligible=True, mode=result['selections'][0]['selected_mode'], sample=dict(
                    id=sid, path=str(path.parent), mode=result['selections'][0]['selected_mode'],
                    input_manifest=sample, selection=result['selections'][0], inputs=snapshot_files(inputs),
                    manifest_source={'path':str(path), 'sha256':checksum},
                    aware_provenance=dict(bundle_path=str(path), bundle_sha256=checksum, parent_sample=doc['isolate'],
                        group=group['id'], category=group['category'], context=doc['context'],
                        filtering_history=doc.get('filtering_history'),
                        files=[dict(path=str(p), sha256=sha(p), size=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns) for p in inputs])))
            except ValueError as exc:
                row['reason'] = str(exc)
        rows.append(row)
    if sha(path) != checksum:
        raise ValueError('AWARE manifest changed while reading. Preview again.')
    token = hashlib.sha256(json.dumps(dict(checksum=checksum, rows=rows),sort_keys=True).encode()).hexdigest()
    return dict(path=str(path), token=token, rows=rows, parent=doc['isolate'], limitation=doc.get('limitation',''))


def commit(store, path, token, selected):
    result = review(path)
    if result['token'] != token:
        raise ValueError('AWARE outputs changed. Preview again before importing.')
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise ValueError('Select one or more distinct eligible output groups.')
    by_id = {r['id']:r for r in result['rows']}
    if any(k not in by_id or not by_id[k]['eligible'] for k in selected):
        raise ValueError('Only supported primary or provisional species groups can become assembly samples.')
    samples = [by_id[k]['sample'] for k in selected]
    if len({s['id'].casefold() for s in samples}) != len(samples):
        raise ValueError('Derived sample identifiers are not unique.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = {json.loads(r[0])['id'].casefold() for r in db.execute("SELECT body FROM items WHERE kind='samples'")}
        if any(s['id'].casefold() in existing for s in samples):
            raise ValueError('A selected sample is already registered. No samples were imported.')
        for sample in samples:
            sample['created'] = now()
            db.execute("INSERT INTO items VALUES ('samples',?,?)", (sample['id'], json.dumps(sample)))
    return samples


def verify_sample(sample, full=True):
    provenance = sample.get('aware_provenance')
    if not provenance:
        return
    for item in provenance['files']:
        p = Path(item['path'])
        if p.is_symlink() or not p.is_file() or p.stat().st_size != item['size'] or (full and sha(p) != item['sha256']) or (not full and p.stat().st_mtime_ns != item.get('mtime_ns', p.stat().st_mtime_ns)):
            raise ValueError('Derived AWARE input changed; import the changed bundle as a new sample.')


def bundle_path(run):
    return Path(run['output'])/'reports/aware/manifest.json'


def capture(run, job):
    data = review(bundle_path(run))
    if data['parent'] != run['sample']['id']:
        raise ValueError('AWARE outputs belong to another sample.')
    previous = run.get('aware', {})
    run['aware'] = {**previous, 'state':'complete', 'completed':now(), 'job':job,
                    'bundle_sha256':sha(bundle_path(run))}


def check_continuation(run):
    state = run.get('aware', {})
    if not state.get('continuation_path'):
        return
    path = Path(state['continuation_path'])
    if path.is_symlink() or path.parent != Path(run['folder'])/'aware-continuations' or sha(path) != state['continuation_sha256']:
        raise ValueError('Saved AWARE continuation changed.')
    doc = json.loads(path.read_text())
    if doc.get('bundle_path') != str(bundle_path(run)) or doc.get('bundle_sha256') != state['bundle_sha256'] or sha(bundle_path(run)) != state['bundle_sha256']:
        raise ValueError('AWARE bundle changed after continuation was saved.')


def arguments(run):
    state = run.get('aware', {})
    if not state.get('continuation_path'):
        return []
    # The caller merges these with the identification --config arguments.
    return ['aware_assembly_continuation='+state['continuation_path'],
            'aware_assembly_continuation_sha256='+state['continuation_sha256']]


def continue_assembly(store, rid, token):
    from .runs import parse_config, check_run_files, tree_digest
    run = store.get('runs', rid)
    if not run or run['tool'] != 'hybas' or parse_config(run['config']).get('aware_run_until') != 'aware':
        raise ValueError('This run is not an AWARE endpoint.')
    check_run_files(run)
    if tree_digest(Path(run['folder'])/'workflow') != run['workflow_sha256']:
        raise ValueError('Saved workflow changed. Create a new run.')
    result = review(bundle_path(run))
    if not any(r['id'] == 'primary' and r['eligible'] for r in result['rows']):
        raise ValueError('No supported primary read set is available for assembly.')
    if result['token'] != token or sha(bundle_path(run)) != run.get('aware',{}).get('bundle_sha256'):
        raise ValueError('AWARE outputs changed. Review the outputs again.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = json.loads(db.execute("SELECT body FROM items WHERE kind='runs' AND id=?", (rid,)).fetchone()[0])
        if current != run or store.get('run_visibility',rid,{}).get('archived'):
            raise ValueError('Run state changed; reopen the run.')
        if any(j['kind']=='workflow' and j['payload'].get('run_id')==rid and j['status'] in {'queued','running'} for j in store.jobs()):
            raise ValueError('This run already has an active job.')
        folder = Path(run['folder'])/'aware-continuations';folder.mkdir(exist_ok=True)
        path = folder/(identifier('continue')+'.json')
        doc = dict(schema='hybas-aware-continuation-v1', bundle_path=str(bundle_path(run)), bundle_sha256=sha(bundle_path(run)))
        with path.open('x') as stream: json.dump(doc,stream,indent=2)
        run['aware'].update(state='continuation_ready', continuation_path=str(path), continuation_sha256=sha(path))
        run['dry_run_ok']=False
        db.execute("UPDATE items SET body=? WHERE kind='runs' AND id=?", (json.dumps(run),rid))
    return run
