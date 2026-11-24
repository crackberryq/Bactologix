"""Run the active BaSIS researcher's catalog tool against local, reviewed files."""
import hashlib
import json
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryFile

from .channels import owned
from .local import git_read, local_path
from .release_contract import approval, preflight

SCRIPT = 'scripts/reference_catalog.py'
TEMPLATE = 'config/reference-catalog.example.json'
RESULTS = 'basis_catalog_results'


def _installation(store, key=None):
    key = key or store.get('settings', 'active-basis')
    item = store.get('installs', key)
    if store.channel != 'dev' or not item or item.get('tool') != 'basis':
        raise ValueError('Activate a BaSIS Dev installation with the reference catalog builder.')
    commit = item['version']['commit']
    contract = approval(store, 'basis', commit) or {}
    files = contract.get('files_sha256', {})
    if SCRIPT not in files or TEMPLATE not in files:
        raise ValueError('Update and activate BaSIS Dev to use the reference catalog builder.')
    return item


def panel(store):
    if store.channel != 'dev':
        return None
    item = store.get('installs', store.get('settings', 'active-basis'))
    available = bool(item and item.get('source') and (Path(item['source']) / SCRIPT).is_file())
    return {'available': available, 'results': store.all(RESULTS)}


def _source(store, item):
    path = Path(item['source'])
    commit = item['version']['commit']
    if git_read(path, 'rev-parse', 'HEAD') != commit or git_read(path, 'status', '--porcelain'):
        raise ValueError('The installed BaSIS source has changed. Activate a clean installation.')
    preflight(store, 'basis', commit, path)
    return path


def template(store):
    source = _source(store, _installation(store))
    return {'name': 'reference-catalog.json', 'type': 'application/json',
            'text': (source / TEMPLATE).read_text(encoding='utf-8')}


def citation_draft(store, doi):
    if not isinstance(doi, str) or not doi.strip() or len(doi) > 2048:
        raise ValueError('Enter a DOI or doi.org link.')
    source = _source(store, _installation(store))
    run = subprocess.run([sys.executable, '-B', str(source / SCRIPT), 'draft-doi', '--', doi],
                         cwd=source, capture_output=True, text=True, timeout=20)
    if run.returncode:
        raise ValueError(run.stderr.strip() or 'Crossref citation lookup failed.')
    return {'name': 'pending-citation.json', 'type': 'application/json',
            'text': json.dumps(json.loads(run.stdout), ensure_ascii=False, indent=2) + '\n'}


def select_registry(store, build_id, deep):
    """Bind just the reviewed files consumed by this follow-up draft."""
    row = store.get(RESULTS, build_id)
    if not row or row.get('action') != 'build' or not row.get('placement_registry'):
        raise ValueError('Choose a built catalog with an approved placement registry.')
    root = store.home / 'resources' / 'basis-catalogs' / build_id
    manifest = root / 'catalog.json'
    registry = Path(row['placement_registry'])
    if (Path(row['output']) != root or registry.parent != root / 'files'
            or manifest.resolve() != manifest or registry.resolve() != registry):
        raise ValueError('The reviewed registry location changed. Verify the package again.')
    data = manifest.read_bytes()
    if hashlib.sha256(data).hexdigest() != row['catalog_sha256']:
        raise ValueError('The catalog manifest changed since it was built.')
    members = [r for r in json.loads(data)['records'] if r['kind'] == 'placement_registry' and r['status'] == 'approved']
    if len(members) != 1 or root / members[0]['path'] != registry:
        raise ValueError('The approved registry differs from its recorded location.')
    digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    if digest != members[0]['sha256']:
        raise ValueError('The registry checksum differs from its reviewed manifest.')
    if deep.get('placement_registry') and deep['placement_registry'] != str(registry):
        raise ValueError('The selected catalog conflicts with the registry in the follow-up configuration.')
    if deep.get('placement_allow_download') is True:
        raise ValueError('For a local catalog, clear placement_allow_download in the follow-up configuration.')
    deep.update(placement_registry=str(registry), placement_allow_download=False)
    return [{'path': str(manifest), 'sha256': row['catalog_sha256']}, {'path': str(registry), 'sha256': digest}]


def request(store, action, spec=None, build_id=None):
    if action not in ('check', 'build', 'verify'):
        raise ValueError('Choose a catalog action.')
    item = _installation(store)
    payload = {'tool': 'basis', 'action': action, 'installation': item['id'], 'commit': item['version']['commit']}
    if action == 'verify':
        row = store.get(RESULTS, build_id)
        if not row or row.get('action') != 'build':
            raise ValueError('Choose a completed catalog build.')
        return {**payload, 'build_id': build_id, 'expected_catalog_sha256': row['catalog_sha256']}
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError('Enter the path to your reference catalog JSON file.')
    path = local_path(spec)
    if not path.is_file():
        raise ValueError('Reference catalog file does not exist: ' + str(path))
    with path.open('rb') as handle:
        checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
    return {**payload, 'spec': str(path), 'spec_sha256': checksum}


def execute(ctx):
    payload = ctx.job['payload']
    item = _installation(ctx.store, payload['installation'])
    if item['version']['commit'] != payload['commit']:
        raise ValueError('The BaSIS version changed after this request was queued.')
    source = _source(ctx.store, item)
    action = payload['action']
    directory = owned(ctx.store, ctx.store.home / 'resources' / 'basis-catalogs' /
                      (payload.get('build_id') or ctx.job['id']), 'BaSIS catalog')
    args = [sys.executable, '-B', str(source / SCRIPT), action]
    if action == 'verify':
        args.append(str(directory))
    else:
        args.append(payload['spec'])
        if action == 'build':
            directory.parent.mkdir(parents=True, exist_ok=True)
            args.append(str(directory))
        args.extend(['--expect-spec-sha256', payload['spec_sha256']])
    with TemporaryFile(mode='w+b') as output:
        ctx.command(args, cwd=source, stdout=output)
        output.seek(0)
        receipt = json.load(output)
    if receipt.get('schema') != 'basis-reference-catalog-status-v1':
        raise ValueError('BaSIS returned an unrecognized catalog receipt.')
    if action == 'verify' and receipt.get('catalog_sha256') != payload['expected_catalog_sha256']:
        raise ValueError('The catalog identity differs from its recorded build.')
    if action in ('build', 'verify') and Path(receipt['output']) != directory:
        raise ValueError('The reported catalog location differs from the requested directory.')
    row = {**receipt, **payload, 'job': ctx.job['id']}
    ctx.store.put(RESULTS, ctx.job['id'], row)
    ctx.write(f"{row['name']} {row['version']} · {row['approved']} approved, "
              f"{row['pending']} pending, {row['excluded']} excluded.")
    if row['output']:
        ctx.write('Catalog: ' + row['output'])
