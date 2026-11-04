"""Selectable local GeneScope packages, without rescanning disks or hashing indexes."""
import hashlib
from pathlib import Path

from .application_package import application
from .databases import _json
from .runs import parse_config, template


def register(store, path):
    path = str(Path(path).expanduser().resolve())
    key = hashlib.sha256(path.encode()).hexdigest()
    store.put('reference_packages', key, {'path': path})


def choices(store):
    paths = set()
    def add(path):
        if isinstance(path, str) and path.strip():
            paths.add(str(Path(path).expanduser().resolve()))
    for row in store.all('reference_packages'):
        add(row.get('path'))
    for row in store.all('database_builds'):
        if row.get('status') == 'completed':
            add(row.get('result_path'))
    add(store.get('database_locations', 'micpscope', {}).get('reference', {}).get('path'))
    texts = [store.get('configs', 'micpscope', template('micpscope'))]
    texts += [r.get('config', '') for r in store.all('runs') if r.get('tool') == 'micpscope']
    for text in texts:
        try:
            add(parse_config(text).get('reference_database'))
        except (ValueError, TypeError):
            continue
    rows = []
    for path in sorted(paths):
        row = {'path': path, 'available': False, 'name': Path(path).name, 'version': '', 'description': ''}
        try:
            root = Path(path)
            receipt = _json(root / 'completed.json')
            checksums = _json(root / 'checksums.json')
            if receipt.get('status') != 'completed' or receipt.get('checksums_sha256') != hashlib.sha256((root / 'checksums.json').read_bytes()).hexdigest():
                raise ValueError('Incomplete package or completion receipt mismatch')
            required = {'proteins.faa', 'proteins.dmnd', 'reference-manifest.json', 'diamond-version.txt'}
            if not isinstance(checksums, dict) or not required <= checksums.keys():
                raise ValueError('Missing required package checksums')
            if any(not (root / name).is_file() or (root / name).stat().st_size == 0 for name in required):
                raise ValueError('Required database files are missing or empty')
            if hashlib.sha256((root / 'reference-manifest.json').read_bytes()).hexdigest() != checksums['reference-manifest.json']:
                raise ValueError('Application metadata checksum mismatch')
            manifest = _json(root / 'reference-manifest.json')
            if manifest.get('schema_version') != '1.0':
                raise ValueError('Unsupported reference package schema')
            app = application(manifest['specification'])
            row.update(app, available=True)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            row['error'] = str(exc)
        rows.append(row)
    return sorted(rows, key=lambda r: (not r['available'], r['name'].casefold(), r['version'], r['path']))
