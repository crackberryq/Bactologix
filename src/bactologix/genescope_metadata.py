"""Bounded, complete validation of GeneScope results for Methods metadata.

Protein rows and reference hits can be large; no biological result payload is
retained. A digest describes the complete document, including discarded fields.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

import ijson


RELATIVE = 'micpscope/results.json'
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 256 * 1024
MAX_DEPTH = 64
MAX_KEY_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024

_PROVENANCE = ('input_sha256', 'catalog_sha256', 'evidence_sha256', 'assembly_sha256',
               'python_version', 'biopython_version', 'created_utc', 'run_id', 'job_id',
               'bactologix_run_id', 'bactologix_job_id', 'source_commit', 'workflow_commit',
               'config_sha256', 'workflow_sha256')
_IDENTITY = ('run_id', 'job_id', 'bactologix_run_id', 'bactologix_job_id')
_BACTOLOGIX = ('run_id', 'job_id', 'sample_id', 'commit', 'config_sha256', 'workflow_sha256')
_FIELDS = {
    *((key,) for key in ('schema_version', 'software_version', 'sample_id', *_IDENTITY)),
    *(('provenance', key) for key in _PROVENANCE),
    *(('bactologix', key) for key in _BACTOLOGIX),
    *(('provenance', 'bactologix', key) for key in _BACTOLOGIX),
    *(('reference_search', key) for key in ('schema_version', 'input_sha256', 'tool_version')),
    *(('reference_search', 'database', key) for key in
      ('id', 'name', 'version', 'checksums_sha256', 'manifest_sha256')),
}
_OBJECTS = {(), ('provenance',), ('bactologix',), ('provenance', 'bactologix'),
            ('reference_search',), ('reference_search', 'database')}
_NULLABLE = {('provenance', 'catalog_sha256'), ('provenance', 'evidence_sha256')}


class _CheckedReader:
    """Hash bytes and bound tokens before passing them into the JSON parser."""

    def __init__(self, handle):
        self.handle = handle
        self.sha256 = hashlib.sha256()
        self.count = 0
        self.string = False
        self.escape = False
        self.token = 0
        self.depth = 0

    def read(self, size=-1):
        raw = self.handle.read(CHUNK_BYTES if size < 0 else min(size, CHUNK_BYTES))
        self.count += len(raw)
        if self.count > MAX_FILE_BYTES:
            raise ValueError('GeneScope Methods evidence exceeds the file size limit')
        for byte in raw:
            if self.string:
                self.token += 1
                if self.escape:
                    self.escape = False
                elif byte == 92:
                    self.escape = True
                elif byte == 34:
                    self.string = False
                    self.token = 0
            elif byte == 34:
                self.string, self.token = True, 0
            elif byte in b' \t\r\n,:{}[]':
                self.token = 0
                if byte in b'{[':
                    self.depth += 1
                    if self.depth > MAX_DEPTH:
                        raise ValueError('GeneScope Methods evidence exceeds the nesting limit')
                elif byte in b'}]':
                    self.depth -= 1
            else:
                self.token += 1
            if self.token > MAX_TOKEN_BYTES:
                raise ValueError('GeneScope Methods evidence exceeds the scalar token limit')
        self.sha256.update(raw)
        return raw

    def readinto(self, buffer):
        raw = self.read(len(buffer))
        buffer[:len(raw)] = raw
        return len(raw)


def _project(reader):
    projected, stack = {}, []
    metadata_bytes = key_bytes = 0
    root_seen = False
    for event, value in ijson.basic_parse(reader, buf_size=CHUNK_BYTES):
        if event == 'map_key':
            frame = stack[-1]
            if value in frame['keys']:
                raise ValueError('Duplicate key in GeneScope Methods evidence')
            cost = len(value.encode('utf-8')) + 64
            key_bytes += cost
            frame['key_bytes'] += cost
            if key_bytes > MAX_KEY_BYTES:
                raise ValueError('GeneScope Methods evidence exceeds the object key limit')
            frame['keys'].add(value)
            frame['key'] = value
            continue
        if event in ('end_map', 'end_array'):
            key_bytes -= stack.pop()['key_bytes']
            continue
        if not stack:
            if root_seen or event != 'start_map':
                raise ValueError('GeneScope Methods evidence must be one JSON object')
            root_seen, path = True, ()
        else:
            parent = stack[-1]
            path = (parent['path'] + (parent['key'],)
                    if parent['kind'] == 'map' and parent['path'] is not None else None)
            parent['key'] = None
        if event in ('start_map', 'start_array'):
            if path in _FIELDS or (path in _OBJECTS and event != 'start_map'):
                raise ValueError('Invalid metadata type in GeneScope Methods evidence')
            if path not in _OBJECTS:
                path = None
            elif path:
                target = projected
                for key in path:
                    target = target.setdefault(key, {})
            stack.append({'kind': 'map' if event == 'start_map' else 'array',
                          'path': path, 'key': None, 'keys': set(), 'key_bytes': 0})
            if len(stack) > MAX_DEPTH:
                raise ValueError('GeneScope Methods evidence exceeds the nesting limit')
            continue
        if path in _OBJECTS:
            raise ValueError('Invalid metadata object in GeneScope Methods evidence')
        if path not in _FIELDS:
            continue
        if not isinstance(value, str) or not value.strip():
            if not (value is None and path in _NULLABLE):
                raise ValueError('Invalid metadata scalar in GeneScope Methods evidence')
        if path[-1].endswith('_sha256') and value is not None:
            if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Invalid metadata SHA256 in GeneScope Methods evidence')
        metadata_bytes += len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        metadata_bytes += sum(len(key.encode('utf-8')) + 4 for key in path)
        if metadata_bytes > MAX_METADATA_BYTES:
            raise ValueError('GeneScope Methods evidence exceeds the retained metadata limit')
        target = projected
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = value
    if projected.get('schema_version') != '1.3':
        raise ValueError('Unsupported or missing GeneScope results schema version')
    if not all(key in projected for key in ('sample_id', 'software_version', 'provenance')):
        raise ValueError('Required GeneScope Methods metadata is missing')
    reference_schema = projected.get('reference_search', {}).get('schema_version')
    if reference_schema is not None and reference_schema != '1.0':
        raise ValueError('Unsupported GeneScope reference search schema version')
    return projected


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read(root):
    """Return ``(metadata, file_identity)``; return None only for a missing file.

    Only the audited results schema 1.3 is accepted. Version and identity fields
    retain their source spelling and are not inferred from installed packages.
    Linked ancestors and nonregular files are rejected without following them.
    """
    root = Path(root).absolute()
    if '..' in root.parts:
        raise ValueError('Invalid GeneScope Methods evidence path')
    directory = None
    file_fd = None
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        directory = os.open(root.anchor, flags | os.O_DIRECTORY)
        for part in (*root.parts[1:], 'micpscope'):
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        file_fd = os.open('results.json', flags | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('GeneScope Methods evidence is not a regular file')
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError('GeneScope Methods evidence exceeds the file size limit')
        with os.fdopen(file_fd, 'rb') as handle:
            file_fd = None
            reader = _CheckedReader(handle)
            try:
                metadata = _project(reader)
            except (ijson.JSONError, UnicodeError, OverflowError) as exc:
                raise ValueError('Invalid GeneScope Methods JSON: ' + str(exc)) from exc
            after = os.fstat(handle.fileno())
            current = os.stat('results.json', dir_fd=directory, follow_symlinks=False)
            path = root / RELATIVE
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError('GeneScope Methods evidence changed to a linked path while being read')
            named = path.stat(follow_symlinks=False)
            if (_signature(before) != _signature(after)
                    or _signature(before) != _signature(current)
                    or _signature(before) != _signature(named)
                    or reader.count != before.st_size):
                raise ValueError('GeneScope Methods evidence changed while being read')
        return metadata, {'path': RELATIVE, 'sha256': reader.sha256.hexdigest(), 'bytes': reader.count}
    except FileNotFoundError:
        if file_fd is not None or 'before' in locals():
            raise ValueError('GeneScope Methods evidence changed while being read')
        return None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory is not None:
            os.close(directory)
