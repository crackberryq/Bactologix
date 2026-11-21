"""Read a bounded metadata projection while validating and hashing all JSON."""
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import stat

import ijson

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_TOKEN_BYTES = 256 * 1024
MAX_DEPTH = 64
MAX_KEY_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


class _CheckedReader:
    def __init__(self, handle):
        self.handle, self.sha256 = handle, hashlib.sha256()
        self.count = self.token = self.depth = 0
        self.string = self.escape = False

    def read(self, size=-1):
        raw = self.handle.read(CHUNK_BYTES if size < 0 else min(size, CHUNK_BYTES))
        self.count += len(raw)
        if self.count > MAX_FILE_BYTES:
            raise ValueError('Methods evidence exceeds the file size limit')
        for byte in raw:
            if self.string:
                self.token += 1
                if self.escape:
                    self.escape = False
                elif byte == 92:
                    self.escape = True
                elif byte == 34:
                    self.string, self.token = False, 0
            elif byte == 34:
                self.string, self.token = True, 0
            elif byte in b' \t\r\n,:{}[]':
                self.token = 0
                if byte in b'{[':
                    self.depth += 1
                    if self.depth > MAX_DEPTH:
                        raise ValueError('Methods evidence exceeds the nesting limit')
                elif byte in b'}]':
                    self.depth -= 1
            else:
                self.token += 1
            if self.token > MAX_TOKEN_BYTES:
                raise ValueError('Methods evidence exceeds the scalar token limit')
        self.sha256.update(raw)
        return raw

    def readinto(self, buffer):
        raw = self.read(len(buffer))
        buffer[:len(raw)] = raw
        return len(raw)


def _match(actual, pattern):
    return all(wanted == '*' or wanted == present for present, wanted in zip(actual, pattern))


def _project(reader, fields):
    """Terminal patterns retain subtrees; '*' matches one map key/array item."""
    stack, projected = [], None
    root_seen, retained, key_bytes = False, 0, 0
    for event, value in ijson.basic_parse(reader, buf_size=CHUNK_BYTES):
        if event == 'map_key':
            frame = stack[-1]
            if value in frame['keys']:
                raise ValueError('Duplicate key in Methods evidence')
            cost = len(value.encode('utf-8')) + 64
            key_bytes += cost
            frame['key_bytes'] += cost
            if key_bytes > MAX_KEY_BYTES:
                raise ValueError('Methods evidence exceeds the object key limit')
            frame['keys'].add(value)
            frame['key'] = value
            continue
        if event in ('end_map', 'end_array'):
            key_bytes -= stack.pop()['key_bytes']
            continue
        parent = stack[-1] if stack else None
        if parent is None:
            if root_seen or event != 'start_map':
                raise ValueError('Methods evidence must be one JSON object')
            root_seen, path, key = True, (), None
        else:
            key = parent['key'] if parent['kind'] == 'map' else parent['index']
            path = parent['path'] + (key,) if parent['path'] is not None else None
            parent['index'] += 1
            parent['key'] = None
        full = bool(parent and parent['full']) or path is not None and any(
            len(pattern) <= len(path) and _match(path[:len(pattern)], pattern) for pattern in fields)
        keep = full or path is not None and any(
            len(path) <= len(pattern) and _match(path, pattern[:len(path)]) for pattern in fields)
        container = event in ('start_map', 'start_array')
        selected = ({} if event == 'start_map' else []) if container else value
        if keep:
            if isinstance(selected, Decimal):
                selected = float(selected)
                if not math.isfinite(selected):
                    raise ValueError('Non-finite Methods metadata number')
            retained += 128 if container else len(json.dumps(selected, ensure_ascii=False).encode('utf-8')) + 64
            if isinstance(key, str):
                retained += len(key.encode('utf-8')) + 64
            if retained > MAX_METADATA_BYTES:
                raise ValueError('Methods evidence exceeds the retained metadata limit')
            if parent is None:
                projected = selected
            elif parent['kind'] == 'map':
                parent['value'][key] = selected
            else:
                parent['value'].append(selected)
        if container:
            stack.append({'kind': 'map' if event == 'start_map' else 'array',
                          'path': path if keep else None, 'value': selected if keep else None,
                          'full': full, 'key': None, 'index': 0, 'keys': set(), 'key_bytes': 0})
            if len(stack) > MAX_DEPTH:
                raise ValueError('Methods evidence exceeds the nesting limit')
    if not root_seen:
        raise ValueError('Methods evidence is empty')
    return projected


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read(root, relative, fields):
    """Return projected metadata and full-file identity, or None if absent.

    Caller-provided fields are static path patterns; they do not name files.
    All paths must be regular files beneath the given nonsymlink directory.
    """
    root, rel = Path(root).absolute(), Path(relative)
    if '..' in root.parts or rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('Invalid Methods evidence path')
    fields = tuple(tuple(field) for field in fields)
    if not fields or any(not field for field in fields):
        raise ValueError('Methods metadata projection must name fields')
    directory = file_fd = None
    before = None
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        directory = os.open(root.anchor, flags | os.O_DIRECTORY)
        for part in (*root.parts[1:], *rel.parts[:-1]):
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        file_fd = os.open(rel.name, flags | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Methods evidence is not a regular file')
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError('Methods evidence exceeds the file size limit')
        with os.fdopen(file_fd, 'rb') as handle:
            file_fd = None
            reader = _CheckedReader(handle)
            try:
                metadata = _project(reader, fields)
            except (ijson.JSONError, UnicodeError, OverflowError) as exc:
                raise ValueError('Invalid Methods evidence JSON') from exc
            after = os.fstat(handle.fileno())
            current = os.stat(rel.name, dir_fd=directory, follow_symlinks=False)
            path = root / rel
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('Methods evidence changed to a linked path while being read')
            named = path.stat(follow_symlinks=False)
            if (any(_signature(before) != _signature(value) for value in (after, current, named))
                    or reader.count != before.st_size):
                raise ValueError('Methods evidence changed while being read')
        return metadata, {'path': rel.as_posix(), 'sha256': reader.sha256.hexdigest(), 'bytes': reader.count}
    except FileNotFoundError:
        if before is not None:
            raise ValueError('Methods evidence changed while being read')
        return None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory is not None:
            os.close(directory)
