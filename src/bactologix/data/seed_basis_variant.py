"""Relocate verified BaSIS cached payloads into a child's independent store.

This is an import, not a new execution record. Original receipts are retained in
lineage metadata. Native run_cached still requires an exact execution identity
match before it can use the imported bytes. No completion receipt is installed:
the native worker must verify inputs, resources, executable and environment.
"""
import copy
import hashlib
import re
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime


def relocate(value, old, new):
    if isinstance(value, str):
        return value.replace(old + '/', new + '/') if value != old else new
    if isinstance(value, list):
        return [relocate(v, old, new) for v in value]
    if isinstance(value, dict):
        return {relocate(k, old, new): relocate(v, old, new) for k, v in value.items()}
    return value


def seed(request):
    parent, child = request['parent'], request['child']
    old, new = parent['folder'], child['folder']
    sys.path[:0] = [str(Path(new) / 'workflow/workflow/lib'), str(Path(new) / 'workflow/workflow/vendor')]
    from safety import result_store as native
    import yaml
    old_cfg, cfg = yaml.safe_load(parent['config']), yaml.safe_load(child['config'])
    source = Path(parent['output']) / 'safety/completion'
    result, imported_receipts = [], {}
    # A loader receipt is itself a verified input of the RGI analysis cache.
    receipts = sorted(source.glob('*.json'), key=lambda p: (p.name != 'rgi_database.json', p.name))
    for receipt in receipts:
        if receipt.is_symlink():
            raise ValueError('Linked native completion receipt')
        raw = json.loads(receipt.read_text())
        if raw.get('schema') != 'basis-native-result-v1':
            continue
        module = raw['identity'].get('module')
        settings = cfg.get('modules', {}).get(module, {})
        if settings.get('enabled') is not True or (raw.get('name') != 'rgi_database' and settings != old_cfg.get('modules', {}).get(module, {})):
            continue
        extras = raw.get('identity', {}).get('extra_inputs', {})
        if any(path not in imported_receipts for path in extras):
            continue  # Unknown cache dependencies are never rebound by guesswork.
        row = native.verify_receipt(receipt)
        if parent.get('completed') and datetime.fromisoformat(row['finished_at']).timestamp() > datetime.fromisoformat(parent['completed']).timestamp() + 1:
            raise ValueError('A native result was replaced after the parent completed; verify the parent before branching')
        payload = Path(row['payload_root'])
        destination = Path(row['destination'])
        if not destination.parent.resolve().is_relative_to(Path(parent['output']).resolve()) or not re.fullmatch(r'[a-z][a-z0-9_-]*', row['name']):
            raise ValueError('Native result destination is outside the parent run')
        identity = relocate(copy.deepcopy(row['identity']), old, new)
        # Verify that relocation changes names only, never source/input bytes.
        native._verify_execution_inputs(row['identity'])
        # Rebound source files and original sample inputs must match bytewise.
        native._verify_execution_inputs(dict(identity, extra_inputs={}))
        identity['extra_inputs'] = {relocate(path, old, new): imported_receipts[path] for path in extras}
        key = native.digest(identity)
        target_root = Path(child['output']) / 'safety/results-cache' / row['name']
        target_attempt = target_root / key / payload.parent.name
        target_payload = target_attempt / 'data'
        required = sum(item.get('bytes', 0) for item in row['artifacts'].values())
        if shutil.disk_usage(new).free < required + 64 * 1024 * 1024:
            raise ValueError('Insufficient free space for an isolated native result snapshot')
        target_attempt.mkdir(parents=True, exist_ok=False)
        shutil.copytree(payload, target_payload, symlinks=True)
        # Keep internal links inside the independent copy. External file links
        # are dereferenced so future writes cannot reach a parent's resource.
        for path in sorted(target_payload.rglob('*')):
            if path.is_symlink():
                relative = path.relative_to(target_payload)
                original = payload / relative
                resolved = original.resolve(strict=True)
                if resolved.is_relative_to(payload):
                    path.unlink()
                    path.symlink_to(os.path.relpath(target_payload / resolved.relative_to(payload), path.parent), target_is_directory=resolved.is_dir())
                elif resolved.is_file():
                    path.unlink()
                    shutil.copy2(resolved, path)
                else:
                    raise ValueError('External native directory links cannot be imported')
        # Original payload must still match its native receipt after copying.
        native.verify_receipt(receipt)
        copied = native.output_inventory(target_payload)
        for name, item in row['artifacts'].items():
            if 'sha256' in item and copied.get(name, {}).get('sha256') != item['sha256']:
                raise ValueError('Imported payload bytes differ: ' + name)
        imported = {**row, 'key': key, 'identity': identity,
                    'payload_root': str(target_payload), 'destination': relocate(row['destination'], old, new),
                    'artifacts': copied,
                    'bactologix_inheritance': {'parent_run': parent['id'], 'parent_receipt': str(receipt),
                                              'parent_receipt_sha256': native.sha256(receipt),
                                              'original_record': row}}
        imported_receipts[str(receipt)] = hashlib.sha256((json.dumps(imported, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()).hexdigest()
        for path in (target_attempt / 'completed.json', target_root / key / 'cache.json'):
            path.write_text(json.dumps(imported, indent=2) + '\n')
        result.append({'rule': row['name'], 'native_cache': str(target_root / key / 'cache.json'),
                       'parent_receipt': str(receipt), 'parent_key': row['key'], 'imported_key': key})
    Path(new, 'variant-imported.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    seed(json.loads(Path(sys.argv[1]).read_text()))
