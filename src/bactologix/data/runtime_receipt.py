"""Standalone observer used by the actual Snakemake controller interpreter."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from datetime import datetime, timezone


LIMIT = 64 * 1024
ENV = ('BACTOLOGIX_RUNTIME_CONTEXT', 'BACTOLOGIX_RUNTIME_NONCE', 'BACTOLOGIX_RUNTIME_CONTEXT_SHA256')


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def directory(path):
    """Open an existing absolute directory without following linked ancestors."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid runtime receipt directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate runtime receipt field')
        value[key] = item
    return value


def read(path):
    path = Path(path)
    parent = directory(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT or before.st_nlink != 1:
            raise ValueError('Runtime receipt must be a bounded regular file')
        raw = b''
        while len(raw) <= LIMIT:
            part = os.read(fd, min(8192, LIMIT + 1 - len(raw)))
            if not part:
                break
            raw += part
        after = os.fstat(fd)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if len(raw) > LIMIT or signature(before) != signature(after) or signature(after) != signature(current):
            raise ValueError('Runtime receipt changed while being read')
        doc = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(doc, dict):
            raise ValueError('Runtime receipt must be an object')
        return doc, hashlib.sha256(raw).hexdigest(), [before.st_dev, before.st_ino]
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def write_reserved(folder, name, expected, doc):
    """Fill a parent-reserved file once; never create or overwrite a destination."""
    if name not in {'started.json', 'finished.json'}:
        raise ValueError('Unknown runtime receipt destination')
    raw = encoded(doc)
    if len(raw) > LIMIT:
        raise ValueError('Runtime receipt exceeds its size limit')
    parent = directory(folder)
    fd = None
    try:
        fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or [info.st_dev, info.st_ino] != expected or info.st_size != 0):
            raise ValueError('Runtime receipt destination changed or was already recorded')
        written = 0
        while written < len(raw):
            written += os.write(fd, raw[written:])
        os.fsync(fd)
        return hashlib.sha256(raw).hexdigest()
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def begin(snakemake_version):
    values = [os.environ.pop(key, None) for key in ENV]
    if not any(values):
        return None
    path, nonce, digest = values
    if not all(isinstance(v, str) and v for v in values):
        raise ValueError('Incomplete runtime receipt context')
    path = Path(path)
    if path.name != 'context.json':
        raise ValueError('Unexpected runtime receipt context path')
    context, actual, _ = read(path)
    if (actual != digest or context.get('schema') != 1 or context.get('nonce') != nonce
            or context.get('controller') != str(Path(sys.prefix).resolve())):
        raise ValueError('Runtime receipt context does not match this controller invocation')
    if not isinstance(snakemake_version, str) or not snakemake_version or len(snakemake_version) > 200:
        raise ValueError('Actual Snakemake version is unavailable')
    doc = {'schema': 1, 'job_id': context['job_id'], 'run_id': context['run_id'],
           'nonce': nonce, 'context_sha256': digest, 'identity_sha256': context['identity_sha256'],
           'controller': str(Path(sys.prefix).resolve()), 'executable': sys.executable,
           'started_at': timestamp(),
           'versions': {'snakemake': snakemake_version, 'controller_python': sys.version.split()[0]}}
    start_sha = write_reserved(path.parent, 'started.json', context['files']['started.json'], doc)
    return {'context': context, 'folder': str(path.parent), 'started': doc, 'started_sha256': start_sha}


def finish(token, error=None):
    if token is None:
        return
    if isinstance(error, SystemExit):
        code = error.code if isinstance(error.code, int) else 0 if error.code is None else 1
    else:
        code = 0 if error is None else 130 if isinstance(error, KeyboardInterrupt) else 1
    status = 'succeeded' if code == 0 else 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
    doc = {key: token['started'][key] for key in
           ('schema', 'job_id', 'run_id', 'nonce', 'context_sha256', 'identity_sha256')}
    doc.update(started_sha256=token['started_sha256'], finished_at=timestamp(), status=status, exit_code=code)
    write_reserved(token['folder'], 'finished.json', token['context']['files']['finished.json'], doc)
