"""Best-effort job observation. Never controls tools, scheduling or scientific settings."""
from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import time
import zipfile

from . import __version__
from .network_usage import NetworkSampler
from .store import now

try:
    import psutil
except ImportError:  # A broken observer must not prevent an already installed workflow.
    psutil = None

INTERVAL = 2.0
HISTORY_LIMIT = 2048
LOG_LIMIT = 1024 * 1024
RECORD_LIMIT = 4 * 1024 * 1024
NOTES = [
    'CPU and RAM are sampled from the launched process and its discoverable descendants; brief or detached processes and between-sample peaks may be missed.',
    '100% CPU represents one fully used logical CPU thread. CPU seconds are observed user+system counter increments, not complete scheduler accounting.',
    'RAM is the sum of process resident memory (RSS). Shared pages may be counted more than once; this is not unique physical memory.',
    'Network activity uses sampled TCP counters from the job process tree. Brief connections and transfers between samples can be missed. Built-in installer downloads record received response-body bytes. Their upload counters are unavailable.',
    'Task counts come from recognized workflow log messages and describe the current plan, not remaining time or biological completeness. Checkpoints can change the total.',
    'Long histories are combined into time buckets, retaining observed CPU seconds and the peak observed RSS in each bucket. Missing readings stay explicit.',
    'GPU utilization, energy consumption and remote cluster jobs are not measured. All records remain local until downloaded or shared.',
]


class Progress:
    def __init__(self, enabled=True, start_marker=None):
        self.enabled = enabled
        self.start_marker = start_marker
        self.done = self.total = None
        self.active = {}
        self.pending = None
        self.in_job_stats = False
        self.dynamic = self.gap = False

    def started(self, job_id, label, *, rule):
        if job_id in self.active or len(self.active) < 256:
            self.active[job_id] = (label, rule)
        else:
            self.gap = True

    def feed(self, line):
        if not self.enabled:
            return
        line = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line).strip()
        if self.start_marker is not None:
            if line.startswith(self.start_marker):
                self.start_marker = None
            return
        if line == 'Job stats:':
            self.in_job_stats = True
            return
        if self.in_job_stats:
            total = re.fullmatch(r'total\s+(\d{1,9})', line)
            if total and self.total is None and not self.gap and 0 < int(total[1]) <= 100000000:
                self.done, self.total = 0, int(total[1])
            if not total and re.fullmatch(r'(?:job\s+count|-+\s+-+|[\w.-]+\s+\d{1,9})', line):
                return
            self.in_job_stats = False
        if 'DAG of jobs will be updated after completion' in line:
            self.dynamic = True
        counts = re.fullmatch(r'(\d{1,9}) of (\d{1,9}) steps \([\d.]+%\) done', line)
        if counts:
            done, total = map(int, counts.groups())
            if 0 <= done <= total <= 100000000 and total:
                self.dynamic |= self.total is not None and total != self.total
                self.done, self.total = done, total
            self.pending = None
            return
        header = re.fullmatch(r'(?:local)?(?:rule|checkpoint) ([A-Za-z_][A-Za-z0-9_.-]{0,127}):', line)
        if header:
            self.pending = header[1]
            return
        message = re.fullmatch(r'Job (\d{1,12}):\s+(.+)', line)
        if message:
            label = ' '.join(message[2].split())
            self.started(message[1], label if len(label) <= 200 else label[:199] + '…', rule=False)
            self.pending = None
            return
        job = re.fullmatch(r'jobid: (\d{1,12})', line)
        if job and self.pending:
            self.started(job[1], self.pending, rule=True)
            self.pending = None
        finished = re.match(r'Finished (?:job (\d+)\.|jobid: (\d+)(?:\s|$))', line)
        if finished:
            self.active.pop(finished[1] or finished[2], None)
        if not line:
            self.pending = None

    def value(self):
        return {'completed': self.done, 'total': self.total,
                'percent': round(100 * self.done / self.total, 1) if self.total else None,
                'active_tasks': list(dict.fromkeys(label for label, _ in self.active.values()))[:12],
                'active_rules': list(dict.fromkeys(label for label, rule in self.active.values() if rule))[:12],
                'active_count': len(self.active), 'dynamic_plan': self.dynamic, 'log_gap': self.gap,
                'source': 'workflow log' if self.enabled else 'not applicable'}


class KrakenProgress:
    """Count complete records from the current classifier's open output only."""
    def __init__(self):
        self.key = None
        self.offset = self.reads = 0

    def sample(self, processes):
        for proc in processes:
            try:
                if proc.name() != 'classify':
                    continue
                fd = Path('/proc') / str(proc.pid) / 'fd'
                output = (fd / '1').readlink()
                if output.name != 'nanopore.kraken2.txt':
                    continue
                with (fd / '1').open('rb') as stream:
                    stat = os.fstat(stream.fileno())
                    key = (proc.pid, proc.create_time(), stat.st_dev, stat.st_ino)
                    if key != self.key or stat.st_size < self.offset:
                        self.key, self.offset, self.reads = key, 0, 0
                    stream.seek(self.offset)
                    block = stream.read(1024 * 1024)
                    self.offset += len(block)
                    self.reads += block.count(b'\n')
                phase = 'Processing Nanopore reads' if self.reads else 'Starting Kraken2'
                if not self.reads and (fd / '2').readlink().name == 'kraken2_nanopore.log':
                    with (fd / '2').open('rb') as stream:
                        message = stream.read(4096)
                    if b'Loading database information...' in message:
                        phase = ('Waiting for first read results' if b'Loading database information... done.' in message
                                 else 'Loading Kraken database')
                return {'phase': phase, 'reads_observed': self.reads}
            except (AttributeError, OSError, psutil.Error):
                continue
        return None


class Sampler:
    def __init__(self, pid):
        self.root = psutil.Process(pid) if psutil else None
        self.counters = {}
        self.kraken = KrakenProgress()
        self.partial = not bool(psutil)
        self.network = None
        self.network_partial = False

    def sample(self):
        if self.root is None:
            return None
        try:
            if not self.root.is_running():
                return None
            processes = [self.root, *self.root.children(recursive=True)]
        except (psutil.Error, OSError):
            self.partial = True
            self.network = None
            self.network_partial = True
            return None
        cpu, rss = 0.0, 0
        observed_processes = []
        for process in processes[:4096]:
            try:
                with process.oneshot():
                    identity = (process.pid, process.create_time())
                    times = process.cpu_times()
                    counter = times.user + times.system  # Exclude reaped-child counters to avoid double counting.
                    memory = process.memory_info().rss
                previous = self.counters.get(identity, 0.0)
                if len(self.counters) >= 32768 and identity not in self.counters:
                    # Keep memory bounded without inventing CPU usage for untracked processes.
                    self.partial = True
                    previous = counter
                else:
                    self.counters[identity] = counter
                cpu += max(0.0, counter - previous)
                rss += memory
                observed_processes.append(process)
            except (psutil.Error, OSError):
                self.partial = True
        self.partial |= len(processes) > 4096
        self.network_partial |= len(observed_processes) != len(processes)
        if not observed_processes:
            self.network = None
            return None
        result = {'cpu_s': cpu, 'rss_mb': rss / 1024 ** 2, 'processes': len(observed_processes)}
        progress = self.kraken.sample(observed_processes)
        if progress:
            result['tool_progress'] = progress
        try:
            had_baseline_attempt = self.network is not None
            if self.network is None:
                self.network = NetworkSampler()
            net = self.network.sample(observed_processes)
            if net is not None:
                result['network'] = net
                self.network_partial |= net['partial']
            elif had_baseline_attempt:
                self.network_partial = True
        except Exception:
            # Counter collection must never discard CPU/RAM or affect job control.
            self.network = None
            self.network_partial = True
        return result


def _peak(*values):
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _observed_sum(a, b):
    if a is None:
        return b
    return a if b is None else a + b


def compact(points):
    """Join adjacent intervals; preserve integrals, peaks and missing-sample counts."""
    result = []
    for index in range(0, len(points), 2):
        a = points[index]
        if index + 1 == len(points):
            result.append(a)
            continue
        b = points[index + 1]
        result.append({'start_s': a['start_s'], 'end_s': b['end_s'],
                       'cpu_s': a['cpu_s'] + b['cpu_s'],
                       'rss_peak_mb': _peak(a['rss_peak_mb'], b['rss_peak_mb']),
                       'processes_peak': _peak(a['processes_peak'], b['processes_peak']),
                       'rx_bytes': _observed_sum(a.get('rx_bytes'), b.get('rx_bytes')),
                       'tx_bytes': _observed_sum(a.get('tx_bytes'), b.get('tx_bytes')),
                       'observed_network_s': a.get('observed_network_s', 0) + b.get('observed_network_s', 0),
                       'observed_tx_s': a.get('observed_tx_s', 0) + b.get('observed_tx_s', 0),
                       'observed': a['observed'] + b['observed'], 'unavailable': a['unavailable'] + b['unavailable']})
    return result


def system_memory_sample():
    """Measure OS-wide memory in the environment that is running the job."""
    if psutil is None:
        return None
    try:
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
    except (OSError, psutil.Error, NotImplementedError):
        return None
    return {'sampled_at': now(), 'ram_used_bytes': ram.total - ram.available,
            'ram_total_bytes': ram.total, 'swap_used_bytes': swap.used,
            'swap_total_bytes': swap.total}


class Recorder:
    def __init__(self, store, job):
        self.store, self.job = store, job
        self.started = self.last = time.monotonic()
        self.last_save = self.started
        self.proc = None
        self.sampler = None
        self.pending_download_bytes = None
        self.position, self.pending = 0, b''
        self.discard_line = False
        self.history = []
        self.monitor_sources = None
        self.cycle = 0
        self.beats = []
        action = job['payload'].get('action')
        # Continuations first validate the revised workflow using a dry run in the same job log.
        marker = f"Run {job['payload'].get('run_id')} · source " if action == 'continue' else None
        self.progress = Progress(job['kind'] == 'workflow' and action in {'run', 'continue'}, marker)
        run = store.get('runs', job['payload'].get('run_id'), {})
        self.record = {'schema': 1, 'id': job['id'], 'run_id': run.get('id'),
                       'kind': job['kind'], 'action': job['payload'].get('action'),
                       'started_at': now(), 'updated_at': now(), 'state': 'recording',
                       'app_version': __version__, 'python': platform.python_version(),
                       'system': platform.system(), 'architecture': platform.machine(),
                       'pipeline': run.get('tool'), 'commit': run.get('commit'),
                       'config_sha256': run.get('config_sha256'), 'workflow_sha256': run.get('workflow_sha256'),
                       'budget': {k: run.get(k) for k in ('cores', 'memory_mb')},
                       'elapsed_s': 0.0, 'command_count': 0, 'command_name': None,
                       'observed_cpu_s': 0.0, 'peak_rss_mb': None, 'samples': 0,
                       'observed_rx_bytes': None, 'observed_tx_bytes': None,
                       'network_last_active_at': None, 'network_partial': False,
                       'unavailable_samples': 0, 'partial_process_tree': False,
                       'latest': None, 'history_compacted': False, 'observer_errors': False, 'notes': NOTES}

    def begin_command(self, proc, name):
        self.proc = proc
        self.record.update(command_count=self.record['command_count'] + 1, command_name=Path(str(name)).name)
        self.last = time.monotonic()
        try:
            self.sampler = Sampler(proc.pid)
        except (OSError, psutil.Error if psutil else OSError):
            self.sampler = None
            self.record['partial_process_tree'] = True
        self.tick(force=True)

    def begin_download(self, name):
        self.pending_download_bytes = 0
        self.last = time.monotonic()
        self.record['command_name'] = name
        self.tick(force=True)

    def received_download_bytes(self, count):
        if self.pending_download_bytes is not None:
            self.pending_download_bytes += count
            if count:
                self.record['network_last_active_at'] = now()
            self.tick()

    def end_download(self):
        try:
            self.tick(force=True)
        finally:
            self.pending_download_bytes = None
            self.record.update(command_name=None, latest=None)
        self.store.put('job_telemetry', self.job['id'], self.record)

    def read_log(self):
        path = self.store.log_path(self.job['id'])
        with path.open('rb') as handle:
            size = os.fstat(handle.fileno()).st_size
            if size < self.position or size - self.position > LOG_LIMIT:
                self.position = max(0, size - LOG_LIMIT)
                self.pending = b''
                self.discard_line = False
                self.progress.active.clear()
                self.progress.pending = None
                self.progress.in_job_stats = False
                self.progress.gap = True
                if self.position:
                    handle.seek(self.position)
                    handle.readline(LOG_LIMIT)  # Do not interpret a clipped line.
                    self.position = handle.tell()
            handle.seek(self.position)
            block = handle.read(LOG_LIMIT)
            self.position = handle.tell()
        lines = (self.pending + block).replace(b'\r', b'\n').split(b'\n')
        tail = lines.pop()
        if self.discard_line and lines:
            lines.pop(0)
            self.discard_line = False
        self.discard_line |= len(tail) > 8192
        self.pending = b'' if self.discard_line else tail
        for line in lines:
            if len(line) <= 8192:
                self.progress.feed(line.decode('utf-8', errors='replace'))

    def tick(self, force=False):
        current = time.monotonic()
        if not force and current - self.last < INTERVAL:
            return
        elapsed = max(0.0, current - self.started)
        interval = max(0.0, current - self.last)
        sample = self.sampler.sample() if self.sampler else None
        if self.proc is not None or self.pending_download_bytes is not None:
            self.record['system_memory'] = system_memory_sample()
            network = sample.get('network') if sample else None
            rx = network['rx_bytes'] if network is not None else None
            tx = network['tx_bytes'] if network is not None else None
            if self.pending_download_bytes is not None:
                rx = self.pending_download_bytes
                self.pending_download_bytes = 0
            point = {'start_s': max(0.0, self.last - self.started), 'end_s': elapsed,
                     'cpu_s': sample['cpu_s'] if sample else 0.0,
                     'rss_peak_mb': sample['rss_mb'] if sample else None,
                     'processes_peak': sample['processes'] if sample else None,
                     'rx_bytes': rx, 'tx_bytes': tx, 'observed_network_s': interval if rx is not None else 0,
                     'observed_tx_s': interval if tx is not None else 0,
                     'observed': int(sample is not None), 'unavailable': int(self.proc is not None and sample is None)}
            self.history.append(point)
            if len(self.history) > HISTORY_LIMIT:
                self.history = compact(self.history)
                self.record['history_compacted'] = True
            self.record['samples'] += point['observed']
            self.record['unavailable_samples'] += point['unavailable']
            self.record['observed_cpu_s'] += point['cpu_s']
            self.record['peak_rss_mb'] = _peak(self.record['peak_rss_mb'], point['rss_peak_mb'])
            self.record['observed_rx_bytes'] = _observed_sum(self.record['observed_rx_bytes'], rx)
            self.record['observed_tx_bytes'] = _observed_sum(self.record['observed_tx_bytes'], tx)
            self.record['network_partial'] |= getattr(self.sampler, 'network_partial', False)
            if self.pending_download_bytes is None and (rx or tx):
                self.record['network_last_active_at'] = now()
            self.record['latest'] = {'cpu_percent': 100 * sample['cpu_s'] / interval if sample and interval >= .1 else None,
                                     'rx_bps': rx / interval if rx is not None and interval > 0 else None,
                                     'tx_bps': tx / interval if tx is not None and interval > 0 else None,
                                     'rss_mb': sample['rss_mb'] if sample else None,
                                     'processes': sample['processes'] if sample else None}
        self.record['partial_process_tree'] |= bool(self.sampler and self.sampler.partial)
        self.last = current
        self.read_log()
        self.record.update(elapsed_s=elapsed, updated_at=now(), progress=self.progress.value())
        self.cycle += 1
        disk, heartbeat = self.monitor_sources() if self.monitor_sources else ({}, None)
        if isinstance(heartbeat, (int, float)) and (not self.beats or heartbeat > self.beats[-1]):
            self.beats = (self.beats + [heartbeat])[-120:]
        progress = (sample or {}).get('tool_progress') or {}
        tasks = self.record['progress'].get('active_tasks') or []
        phase = progress.get('phase') or ', '.join(tasks) or self.record.get('command_name')
        previous = self.record.get('monitor_cycle') or {}
        stamp = self.record['updated_at']
        changed = phase != previous.get('phase')
        advanced = changed or progress.get('reads_observed') != previous.get('reads_observed')
        paused = disk.get('paused_total_s', 0)
        if disk.get('pause_start_mono') is not None:
            paused += max(0, current - disk['pause_start_mono'])
        self.record['monitor_cycle'] = {
            'id': self.cycle, 'sampled_at': stamp, 'resource_sampled_at': stamp if sample else None,
            'storage': {k: disk.get(k) for k in ('checked_at', 'volumes', 'probe_ms', 'write_ms', 'write_delay_ms')},
            'heartbeats': list(self.beats), 'phase': phase, **progress,
            'phase_since': stamp if changed else previous.get('phase_since'),
            'last_progress_at': stamp if advanced else previous.get('last_progress_at'),
            'paused_s': min(elapsed, paused), 'active_s': max(0, elapsed - paused)}
        self.store.put('job_telemetry', self.job['id'], self.record)
        if force or current - self.last_save >= 30:
            self.persist_history()
            self.last_save = current

    def persist_history(self):
        # A separate bounded snapshot avoids sending the series with every UI refresh.
        self.store.put('job_usage_history', self.job['id'], {'schema': 1, 'id': self.job['id'],
                       'updated_at': self.record['updated_at'], 'points': self.history})

    def end_command(self):
        try:
            self.tick(force=True)
        finally:
            self.proc = self.sampler = None
            self.record.update(command_name=None, latest=None)
        self.store.put('job_telemetry', self.job['id'], self.record)

    def finish(self, status):
        self.tick(force=True)
        if self.pending:
            self.progress.feed(self.pending.decode('utf-8', errors='replace'))
        self.record.update(state=status, finished_at=now(), latest=None, command_name=None, progress=self.progress.value())
        self.store.put('job_telemetry', self.job['id'], self.record)
        self.persist_history()


def view(record, job):
    if not isinstance(record, dict) or record.get('schema') != 1 or record.get('id') != job['id']:
        return None
    result = dict(record, job_status=job['status'])
    try:
        updated = datetime.fromisoformat(record['updated_at'])
        age = (datetime.now(timezone.utc) - updated).total_seconds()
    except (ValueError, TypeError, KeyError):
        age = float('inf')
    result['live'] = job['status'] == 'running' and record.get('state') == 'recording' and 0 <= age <= 15
    result['stale'] = job['status'] == 'running' and not result['live']
    result['incomplete'] = record.get('state') == 'recording' and job['status'] not in {'queued', 'running'}
    if not result['live']:
        result['latest'] = None
    return result


def inspect(store, jid):
    if not isinstance(jid, str):
        raise ValueError('Choose an existing job.')
    job = store.job(jid)
    if not job:
        raise ValueError('Choose an existing job.')
    summary = view(store.get('job_telemetry', jid), job)
    if not summary:
        raise ValueError('No resource record was collected for this job. Records start with new jobs in v0.1.0a27.')
    history = store.get('job_usage_history', jid, {})
    valid = isinstance(history, dict) and history.get('schema') == 1 and history.get('id') == jid
    points = history.get('points', []) if valid else []
    if not isinstance(points, list) or len(points) > HISTORY_LIMIT:
        raise ValueError('This resource history is invalid or exceeds the display limit.')
    cleaned = []
    previous = 0
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('This resource history contains an invalid interval.')
        keys = ('start_s', 'end_s', 'cpu_s', 'rss_peak_mb', 'processes_peak', 'observed', 'unavailable',
                'rx_bytes', 'tx_bytes')
        row = {key: point.get(key) for key in keys}
        row['observed_network_s'] = point.get('observed_network_s', 0)
        row['observed_tx_s'] = point.get('observed_tx_s', 0)
        for key, value in row.items():
            if value is None and key in {'rss_peak_mb', 'processes_peak', 'rx_bytes', 'tx_bytes'}:
                continue
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError('This resource history contains an invalid measurement.')
            if key in {'rx_bytes', 'tx_bytes'} and type(value) is not int:
                raise ValueError('This resource history contains an invalid network byte count.')
        if row['end_s'] < row['start_s'] or row['start_s'] < previous:
            raise ValueError('This resource history contains overlapping or reversed intervals.')
        for size, measured in (('rx_bytes', 'observed_network_s'), ('tx_bytes', 'observed_tx_s')):
            if ((row[size] is None and row[measured])
                    or row[measured] > row['end_s'] - row['start_s'] + 1e-6):
                raise ValueError('This resource history contains an invalid network interval.')
        previous = row['end_s']
        cleaned.append(row)
    return {'id': jid, 'summary': summary, 'points': cleaned,
            'history_updated_at': history.get('updated_at') if valid else None}


def download(store, jid, format):
    if not isinstance(format, str) or format not in {'json', 'csv', 'zip'}:
        raise ValueError('Choose JSON, CSV or ZIP.')
    data = inspect(store, jid)
    if format in {'json', 'zip'}:
        from .storage_events import timeline
        data['storage_events'] = timeline(store, jid)
    text = json.dumps(data, indent=2, allow_nan=False) + '\n'
    if len(text.encode()) > RECORD_LIMIT:
        raise ValueError('This resource record exceeds the download limit.')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['interval_start_s', 'interval_end_s', 'observed_cpu_s', 'cpu_percent_mean',
                     'peak_process_rss_sum_mb', 'peak_process_count', 'observed_samples', 'unavailable_samples',
                     'received_bytes', 'sent_bytes', 'observed_network_s', 'observed_tx_s',
                     'received_bytes_per_s', 'sent_bytes_per_s'])
    for point in data['points']:
        duration = point['end_s'] - point['start_s']
        percent = 100 * point['cpu_s'] / duration if duration > .1 and point['observed'] else ''
        network_s = point['observed_network_s']
        tx_s = point['observed_tx_s']
        writer.writerow([point['start_s'], point['end_s'], point['cpu_s'], percent,
                         point['rss_peak_mb'], point['processes_peak'], point['observed'], point['unavailable'],
                         point['rx_bytes'], point['tx_bytes'], network_s, tx_s,
                         point['rx_bytes'] / network_s if network_s else '',
                         point['tx_bytes'] / tx_s if tx_s else ''])
    csv_text = stream.getvalue()
    name = jid + '-resource-use.' + format
    if format != 'zip':
        return {'name': name, 'text': text if format == 'json' else csv_text,
                'type': 'application/json' if format == 'json' else 'text/csv'}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('resource-use.json', text)
        archive.writestr('resource-use.csv', csv_text)
        archive.writestr('README.txt', 'Bactologix resource-use record\nJob: ' + jid + '\nStatus: ' + data['summary']['job_status'] +
                         '\n\n' + '\n\n'.join(NOTES) + '\n\nOne record describes one job attempt. A resumed run creates another job record.\n')
    return {'name': name, 'base64': base64.b64encode(output.getvalue()).decode(), 'type': 'application/zip'}
