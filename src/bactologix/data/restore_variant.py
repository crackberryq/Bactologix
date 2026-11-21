"""Restore a verified isolated checkpoint. This program runs no analysis tools."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone
import uuid


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def identity(path):
    if path.is_symlink():
        raise ValueError('Linked cache files are not supported')
    if path.is_file():
        return {'kind': 'file', 'size': path.stat().st_size, 'sha256': sha(path)}
    if not path.is_dir():
        raise ValueError('Missing checkpoint: ' + str(path))
    files = {}
    for item in sorted(path.rglob('*')):
        if item.is_symlink():
            raise ValueError('Linked cache contents are not supported')
        if item.is_file():
            files[item.relative_to(path).as_posix()] = {'size': item.stat().st_size, 'sha256': sha(item)}
        elif not item.is_dir():
            raise ValueError('Invalid cache contents')
    return {'kind': 'directory', 'files': files}


def restore(manifest, index):
    manifest = Path(manifest).absolute()
    root = manifest.parent
    record = json.loads(manifest.read_text())
    if record.get('schema') != 1:
        raise ValueError('Unsupported checkpoint schema')
    entry = record['checkpoints'][int(index)]
    for row in entry['outputs']:
        source, target = Path(row['cache']), Path(row['path'])
        if any(not p.is_absolute() or '..' in p.parts for p in (source, target)):
            raise ValueError('Checkpoint paths must be absolute without parent traversal')
        if not source.is_relative_to(root / 'variant-cache') or not target.is_relative_to(root / 'outputs'):
            raise ValueError('Checkpoint path is outside this run')
        for path in (source, target):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('Linked checkpoint paths are not allowed')
        if identity(source) != row['identity']:
            raise ValueError('Checkpoint bytes changed: ' + str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.bactologix-restore-', dir=target.parent))
        try:
            staged = staging / 'output'
            if row['identity']['kind'] == 'directory':
                shutil.copytree(source, staged)
            else:
                shutil.copy2(source, staged)
            if identity(staged) != row['identity']:
                raise ValueError('Checkpoint copy failed verification')
            if target.exists():
                # Preserve prior content even for repeated restores in one invocation.
                history = root / 'output-history'
                if history.is_symlink():
                    raise ValueError('Linked output history is not allowed')
                history.mkdir(exist_ok=True)
                archive = history / ('restore-' + uuid.uuid4().hex)
                archive.mkdir()
                previous = archive / 'outputs' / target.relative_to(root / 'outputs')
                previous.parent.mkdir(parents=True)
                metadata = {'schema': 1, 'reason': 'preserved-before-checkpoint-restore',
                            'created_at': datetime.now(timezone.utc).isoformat(),
                            'source': str(target), 'identity': identity(target),
                            'attribution': 'Observed before restoration; producing attempt is not inferred.'}
                (archive / 'started.json').write_text(json.dumps(metadata, indent=2) + '\n')
                target.rename(previous)
                (archive / 'manifest.json').write_text(json.dumps(
                    {**metadata, 'status': 'completed', 'completed': True}, indent=2) + '\n')
            os.replace(staged, target)
        finally:
            # Failed staged copies remain available for inspection.
            if staging.exists() and not any(staging.iterdir()):
                staging.rmdir()
    print('Inherited checkpoint:', entry['rule'], 'from', record['parent'])


if __name__ == '__main__':
    restore(sys.argv[1], int(sys.argv[2]))
