"""Read-only, bounded support snapshots. No uploads or external commands."""
from __future__ import annotations
import json
import os
import platform
import re
import stat
from . import __version__
from .store import now

MAX_LOG_BYTES = 32768
MAX_JOBS = 5


def redact(text, identifiers=()):
    """Best effort only: preview remains mandatory for user-controlled free text."""
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)', '[REDACTED KEY]', text, flags=re.S)
    text = re.sub(r'(?im)^.*(?:authorization|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|cookie).*$','[REDACTED CREDENTIAL LINE]', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})\b', '[REDACTED TOKEN]', text)
    text = re.sub(r'https?://[^\s<>"\']+', '[URL]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', text)
    text = re.sub(r'(?<!\w)(?:[A-Za-z]:[\\/]|/|\\\\)[^\s<>"\']+', '[PATH]', text)
    for value in sorted({str(x) for x in identifiers if x}, key=len, reverse=True):
        text = text.replace(value, '[IDENTIFIER]')
    return text


def _log_tail(store, jid):
    # Open both directory and file without following symlinks. Never accept a path.
    directory = os.open(store.home / 'logs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(store.log_path(jid).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, 'rb') as handle:
        size = os.fstat(handle.fileno()).st_size
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError('Log is not a regular file')
        handle.seek(max(0, size - MAX_LOG_BYTES))
        return handle.read(MAX_LOG_BYTES).decode('utf-8', errors='replace'), size > MAX_LOG_BYTES


def build_snapshot(store, job_ids=None, include_logs=False):
    if job_ids is None:
        job_ids = []
    if not isinstance(job_ids, list) or len(job_ids) > MAX_JOBS or any(not isinstance(x, str) for x in job_ids):
        raise ValueError('Choose up to five jobs')
    if not isinstance(include_logs, bool):
        raise ValueError('Invalid log selection')
    identifiers = [x.get('id') for x in store.all('samples')]
    identifiers += [x['sample'].get('id') for x in store.all('runs') if isinstance(x.get('sample'), dict)]
    result = {'schema': 'bactologix-support/1', 'created_at': now(),
              'application': {'version': __version__, 'python': platform.python_version(), 'os': platform.system(), 'architecture': platform.machine()},
              'scope': 'Selected job metadata; optional last 32 KiB of each log. No input files, reports, full configuration, job payloads or environment variables.',
              'privacy': 'Redaction is best effort. Review and edit before sharing. This is a snapshot, not live access.',
              'installations': [], 'jobs': []}
    for item in store.all('installs')[:100]:
        version = item.get('version', {})
        result['installations'].append({'pipeline': redact(str(item.get('tool', 'unknown')), identifiers),
            'commit': version.get('commit') if re.fullmatch(r'[a-fA-F0-9]{40}', str(version.get('commit', ''))) else 'unknown',
            'runner_ready_recorded': item.get('ready') is True})
    for jid in dict.fromkeys(job_ids):
        job = store.job(jid)
        if not job:
            raise ValueError('Selected job no longer exists')
        entry = {k: redact(str(job.get(k) or ''), identifiers) for k in ('id', 'kind', 'status', 'created', 'started', 'finished')}
        run = store.get('runs', job['payload'].get('run_id'), {}) if job['payload'].get('run_id') else {}
        entry['pipeline'] = run.get('tool') if run.get('tool') in {'hybas', 'basis'} else 'unknown'
        commit = run.get('commit', '')
        entry['run_commit'] = commit if re.fullmatch(r'[a-fA-F0-9]{40}', str(commit)) else 'unknown'
        if include_logs:
            entry['error'] = redact(str(job.get('error') or '')[:MAX_LOG_BYTES], identifiers)
            try:
                tail, truncated = _log_tail(store, jid)
                entry.update(log_excerpt=redact(tail, identifiers), log_truncated=truncated)
            except (OSError, ValueError):
                entry['log_unavailable'] = True
        result['jobs'].append(entry)
    return json.dumps(result, indent=2, ensure_ascii=False)
