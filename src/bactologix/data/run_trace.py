"""Bounded observer of scheduler-declared rule I/O in this invocation only.

This standard-library helper runs in the controller interpreter. It observes
structured Snakemake log records without changing or suppressing them. Rule
completion is a scheduler observation, not proof of an internal tool invocation
or of the bytes actually read/written. No shell commands or environments enter
the trace, and no historical .snakemake metadata or input contents are read.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import logging
import os
import re
import threading


MAX_BYTES = 48 * 1024
MAX_JOBS = 128
MAX_FILES = 32
MAX_PATH = 1024
STATUSES = {'reported', 'completed', 'failed'}
WARNINGS = {
    'limit': 'The rule trace reached its metadata limit; omitted records are not inferred.',
    'path': 'Some scheduler file labels were unavailable, annotated or non-local; their identities are not inferred.',
    'record': 'Some scheduler events could not be captured; the rule trace is partial.',
    'conflict': 'A scheduler job identifier had conflicting rule or file identities; that job is omitted.',
    'group': 'Grouped execution error details were not captured as individual rule observations.',
}


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _string(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)


def _timestamp(value):
    if not _string(value, 80):
        raise ValueError('Invalid rule observation timestamp')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Rule observation timestamp has no timezone')
    return result


def clean_trace(value):
    """Validate a retained trace without probing the current filesystem."""
    if not isinstance(value, dict) or set(value) != {'schema', 'observer', 'coverage', 'truncated', 'jobs', 'warnings'}:
        raise ValueError('Invalid rule trace fields')
    if (type(value['schema']) is not int or value['schema'] != 1 or value['observer'] != 'snakemake-structured-log'
            or not isinstance(value['coverage'], str) or value['coverage'] not in {'captured', 'partial'}
            or type(value['truncated']) is not bool):
        raise ValueError('Invalid rule trace header')
    jobs, warnings = value['jobs'], value['warnings']
    if not isinstance(jobs, list) or len(jobs) > MAX_JOBS:
        raise ValueError('Invalid rule trace job count')
    if (not isinstance(warnings, list) or len(warnings) > len(WARNINGS)
            or any(w not in WARNINGS.values() for w in warnings) or len(set(warnings)) != len(warnings)):
        raise ValueError('Invalid rule trace warnings')
    if (warnings or value['truncated']) and value['coverage'] != 'partial':
        raise ValueError('Rule trace completeness conflicts with its omissions')
    identifiers = set()
    for row in jobs:
        if (not isinstance(row, dict) or set(row) - {'id', 'rule', 'inputs', 'outputs', 'status', 'reported_at', 'finished_at'}
                or not {'id', 'rule', 'inputs', 'outputs', 'status', 'reported_at'} <= set(row)):
            raise ValueError('Invalid rule observation fields')
        if (not _string(row['id'], 64) or row['id'] in identifiers or not _string(row['rule'], 120)
                or not isinstance(row['status'], str) or row['status'] not in STATUSES):
            raise ValueError('Invalid or duplicate rule observation identity')
        identifiers.add(row['id'])
        for key in ('inputs', 'outputs'):
            paths = row[key]
            if (not isinstance(paths, list) or len(paths) > MAX_FILES
                    or any(not _string(p, MAX_PATH) or not os.path.isabs(p) or os.path.normpath(p) != p for p in paths)
                    or len(set(paths)) != len(paths)):
                raise ValueError('Invalid scheduler-declared file identities')
        start = _timestamp(row['reported_at'])
        if row['status'] in {'completed', 'failed'}:
            if 'finished_at' not in row or _timestamp(row['finished_at']) < start:
                raise ValueError('Invalid terminal rule observation timestamp')
        elif 'finished_at' in row:
            raise ValueError('Unfinished rule observation has a finish timestamp')
    if len(_encoded(value)) > MAX_BYTES:
        raise ValueError('Rule trace exceeds its metadata limit')
    return copy.deepcopy(value)


class Observer(logging.Filter):
    """A filter is retained when Snakemake replaces its log handlers.

Always return True: observation failures must never affect scheduler logging or
the analysis. Only memory is touched inside the callback.
"""
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.ambiguous = set()
        self.notes = set()
        self.truncated = False
        self.ambiguity_exhausted = False
        self.lock = threading.RLock()
        self.current_bytes = 0

    def warn(self, key):
        self.notes.add(key)
        if key == 'limit':
            self.truncated = True

    def paths(self, values):
        if not isinstance(values, (list, tuple)):
            self.warn('path')
            return []
        if len(values) > MAX_FILES:
            self.warn('limit')
        result = []
        for value in values[:MAX_FILES]:
            # Formatted flags such as "(update)" are not raw file identities.
            # Do not strip them: a real filename could have the same suffix.
            if (not _string(value, MAX_PATH) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*://', value)
                    or value.startswith('<') or re.search(r' \([^)]*\)$', value)):
                self.warn('path')
                continue
            normalized = os.path.normpath(os.path.abspath(value))
            if not _string(normalized, MAX_PATH):
                self.warn('path')
                continue
            if normalized not in result:
                result.append(normalized)
        return result

    def put(self, key, row):
        old = self.rows.get(key)
        size = len(_encoded(row))
        total = self.current_bytes - (len(_encoded(old)) if old else 0) + size
        # Reserve room for header, warnings and subsequent terminal timestamps.
        if total > MAX_BYTES - 4096 or not old and len(self.rows) >= MAX_JOBS:
            self.warn('limit')
            return
        self.rows[key] = row
        self.current_bytes = total

    def observe(self, record):
        event = getattr(record, 'event', None)
        name = getattr(event, 'name', None)
        if name is None and isinstance(event, str):
            name = event.rsplit('.', 1)[-1].upper()
        if name == 'GROUP_ERROR':
            self.warn('group')
            return
        if name not in {'JOB_INFO', 'JOB_ERROR', 'JOB_FINISHED'}:
            return
        identity = getattr(record, 'job_id' if name == 'JOB_FINISHED' else 'jobid', None)
        if not isinstance(identity, (int, str)) or isinstance(identity, bool):
            self.warn('record')
            return
        key = str(identity)
        if not _string(key, 64):
            self.warn('record')
            return
        if key in self.ambiguous:
            return
        stamp = datetime.now(timezone.utc).isoformat()
        old = self.rows.get(key)
        if old is None and self.ambiguity_exhausted:
            return
        if name == 'JOB_FINISHED':
            if not old:
                self.warn('record')
                return
            if old['status'] == 'failed':
                self.warn('record')  # Compact logical-job status omits retry history.
            self.put(key, dict(old, status='completed', finished_at=stamp))
            return
        rule = getattr(record, 'rule_name', None)
        if not _string(rule, 120):
            self.warn('record')
            return
        inputs, outputs = self.paths(getattr(record, 'input', None)), self.paths(getattr(record, 'output', None))
        if old and (old['rule'], old['inputs'], old['outputs']) != (rule, inputs, outputs):
            self.warn('conflict')
            self.current_bytes -= len(_encoded(self.rows.pop(key)))
            if len(self.ambiguous) < MAX_JOBS:
                self.ambiguous.add(key)
            else:
                # Stop new identities rather than later accepting a rejected
                # identifier whose ambiguity tombstone could not be retained.
                self.ambiguity_exhausted = True
                self.warn('limit')
            return
        if name == 'JOB_INFO' and old and old['status'] in {'completed', 'failed'}:
            self.warn('record')
            old = None  # New retry observation; do not retain its old finish.
        row = old or {'id': key, 'rule': rule, 'inputs': inputs, 'outputs': outputs,
                      'status': 'reported', 'reported_at': stamp}
        if name == 'JOB_ERROR':
            row = dict(row, status='failed', finished_at=stamp)
        self.put(key, row)

    def filter(self, record):
        try:
            with self.lock:
                self.observe(record)
        except Exception:
            with self.lock:
                self.warn('record')
        return True

    def snapshot(self):
        with self.lock:
            value = {'schema': 1, 'observer': 'snakemake-structured-log',
                     'coverage': 'partial' if self.notes else 'captured', 'truncated': self.truncated,
                     'jobs': list(self.rows.values()),
                     'warnings': [WARNINGS[key] for key in sorted(self.notes)]}
            return clean_trace(value)
