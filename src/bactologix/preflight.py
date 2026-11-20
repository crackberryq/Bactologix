"""Bounded, read-only host checks with explicitly labelled database estimates."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat

from . import compute_memory, hardware
from .local import host_path
from .runs import parse_config
from .store import now

MIB = 1024 ** 2


def kraken_memory(run, info):
    """Stat only the three native Kraken index files; never read or scan a DB."""
    if run.get('tool') != 'hybas':
        return None
    cfg = parse_config(run['config'])
    if (str(cfg.get('run_kraken', True)).lower() in {'false', '0', 'no', 'off'}
            or str(cfg.get('kraken_decision_mode', 'manual')).lower() == 'off'):
        return None
    result = {'level': 'pending', 'state': 'Size unavailable'}
    try:
        # Mirror native HybAs BASE_DIR/db_root/kraken_db_name joining. Saved
        # Bactologix drafts normally bind db_root to an absolute resource alias.
        base = Path(str(cfg.get('base_dir', '.'))).expanduser()
        if not base.is_absolute():
            base = Path(run['folder']) / 'workflow' / base
        selected = base / host_path(cfg.get('db_root', 'db')) / host_path(cfg.get('kraken_db_name', 'standard'))
        resolved = selected.resolve(strict=True)
        files = []
        for name in ('hash.k2d', 'opts.k2d', 'taxo.k2d'):
            target = (resolved / name).resolve(strict=True)
            measured = target.stat()
            if not stat.S_ISREG(measured.st_mode) or measured.st_size <= 0:
                raise ValueError(name + ' is not a nonempty regular index file')
            files.append({'name': name, 'resolved_path': str(target), 'bytes': measured.st_size})
        total = sum(item['bytes'] for item in files)
        estimate = int(total / MIB * 1.2) + 1024
        memory = hardware.memory_capacity() if info is None else info.get('memory', {})
        physical = memory.get('total_mb')
        known_ram = isinstance(physical, int) and not isinstance(physical, bool) and physical > 0
        text = (f'Selected Kraken database: {total / (1024 * MIB):.2f} GiB across its three index files. '
                f'Estimated full-load requirement with headroom: {estimate / 1024:.2f} GiB '
                '(file bytes × 1.2 plus 1 GiB). ')
        level, state = 'info', 'Footprint checked'
        if known_ram:
            text += f'Detected physical RAM: {physical / 1024:.2f} GiB. '
            if total > physical * MIB:
                level, state = 'warning', 'Database exceeds physical RAM'
                text += ('An opted-in swap allowance may permit execution, but severe paging and long runtimes '
                         'are expected with ordinary database loading. ')
            elif estimate > physical:
                level, state = 'warning', 'Review memory headroom'
                text += ('The estimate exceeds physical RAM; paging may substantially slow this run. '
                         'Swap allowance does not provide RAM-speed access. ')
        else:
            level, state = 'pending', 'RAM comparison unavailable'
            text += 'Physical RAM could not be verified. '
        text += ('File footprint and headroom are resource estimates, not measured resident memory. '
                 f'Database alias: {selected}. Resolved folder: {resolved}.')
        result.update(level=level, state=state, text=text, selected_path=str(selected), resolved_path=str(resolved),
                      files=files, core_bytes=total, estimated_full_load_mb=estimate,
                      physical_ram_mb=physical if known_ram else None)
    except (OSError, ValueError, TypeError, RuntimeError, KeyError) as exc:
        result['text'] = 'Kraken index size could not be verified: ' + str(exc) + '. Native database checks still apply.'
    return result


def kraken_warnings(run, info):
    result = kraken_memory(run, info)
    return [result['text']] if result and result['level'] == 'warning' else []


def log_kraken_warnings(write, run, info=None):
    """Refresh cheap capacity diagnostics at worker launch without changing policy."""
    if run.get('tool') != 'hybas':
        return
    try:
        for warning in kraken_warnings(run, info):
            write('Pre-run warning: ' + warning)
    except (OSError, ValueError, TypeError, RuntimeError, KeyError) as exc:
        write('Kraken memory advisory unavailable: ' + str(exc))


def destination(path, label):
    """Inspect the closest existing directory without creating probe files."""
    result = {'label': label, 'path': str(path), 'level': 'info', 'free_mb': None}
    try:
        target = Path(path).expanduser()
        if not target.is_absolute():
            raise ValueError('Use an absolute destination path.')
        folder = target
        while not folder.exists():
            if folder.is_symlink():
                raise ValueError('A destination symlink has no accessible target.')
            if folder == folder.parent:
                raise ValueError('No accessible parent directory.')
            folder = folder.parent
        if not folder.is_dir():
            raise ValueError('The destination or its parent is a file, not a directory.')
        if not os.access(folder, os.W_OK | os.X_OK):
            raise ValueError('The app has no write/search permission here.')
        if hasattr(os, 'statvfs') and os.statvfs(folder).f_flag & getattr(os, 'ST_RDONLY', 1):
            raise ValueError('The destination filesystem is read-only.')
        result.update(checked_parent=str(folder), free_mb=shutil.disk_usage(folder).free // MIB)
        result['text'] = 'Directory permissions allow writing; actual writes are checked by the workflow.'
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result.update(level='error', text=str(exc))
    return result


def destinations(run, action='run'):
    cfg = parse_config(run['config'])
    paths = [(run['folder'], 'Run folder'), (str(Path(run['folder']) / 'workflow'), 'Workflow working folder')]
    if action == 'run':
        paths.append((run['output'], 'Results folder'))
        # Only explicit additional write destinations; never probe database inputs as writable.
        if run['tool'] == 'basis':
            for section, key, label in [('storage', 'native_results_directory', 'Native results folder'),
                                        ('deep', 'placement_cache_dir', 'Placement cache')]:
                value = cfg.get(section, {})
                if isinstance(value, dict) and value.get(key):
                    paths.append((value[key], label))
    if action == 'prepare' and run.get('rule_cache'):
        paths.append((run['rule_cache'], 'Tool environment cache'))
    seen, result = set(), []
    for path, label in paths:
        if str(path) not in seen:
            result.append(destination(path, label))
            seen.add(str(path))
    return result


def budget(run, info):
    """Report oversubscription; never rewrite a frozen configuration or tune thresholds."""
    cores, memory = run['cores'], run['memory_mb']
    scheduling = compute_memory.policy(run)['scheduler_mb']
    issues = []
    available_cpu = info['cpu']['available']
    total, available = info['memory']['total_mb'], info['memory']['available_mb']
    if cores > available_cpu:
        issues.append(f'The run requests {cores} CPU threads; {available_cpu} are available to this app.')
    if total is None:
        issues.append('RAM capacity could not be detected.')
    elif memory > total:
        issues.append(f'The {memory:,} MB run budget exceeds the detected {total:,} MB RAM capacity.')
    elif available is not None and memory > available:
        issues.append(f'The {memory:,} MB run budget exceeds the {available:,} MB RAM currently available.')
    cfg = parse_config(run['config'])
    # Report explicit tool limits only. Native defaults/dynamic rules are validated by Snakemake.
    if run['tool'] == 'hybas':
        limits = [('assembly_mem_mb', cfg.get('assembly_mem_mb'), 1)]
        limits.append(('spades_memory_gb', cfg.get('spades_memory_gb'), 1024))
        if str(cfg.get('polish_mode', '')).lower() not in {'', 'none'}:
            limits.append(('polishing_mem_mb', cfg.get('polishing_mem_mb'), 1))
        pending = (cfg.get('aware_run_until') == 'aware' and not run.get('aware', {}).get('continuation_path')) or (cfg.get('kraken_review_before_filtering') and not run.get('identification', {}).get('decision_path'))
        if pending:
            limits = []
        if cfg.get('run_kraken') is not False and cfg.get('kraken_decision_mode') != 'off':
            limits.append(('kraken_mem_mb', cfg.get('kraken_mem_mb'), 1))
    else:
        limits = []
        modules = cfg.get('modules', {})
        for name, values in (modules.items() if isinstance(modules, dict) else []):
            if isinstance(values, dict) and values.get('enabled') is not False:
                limits.append((f'modules.{name}.memory_mb', values.get('memory_mb'), 1))
        deep = cfg.get('deep', {})
        if isinstance(deep, dict) and deep.get('enabled') is True:
            limits.append(('deep.memory_mb', deep.get('memory_mb'), 1))
    for name, value, scale in limits:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value * scale > scheduling:
            issues.append(f'{name} ({value}) exceeds the {scheduling:,} MB scheduling budget. Open Edit settings to review this limit.')
    issues.extend(compute_memory.capacity_issues(run, info))
    return issues


def panel(store, run):
    checked_at = now()
    locations = destinations(run)
    invalid = [x for x in locations if x['level'] == 'error']
    measured = [x for x in locations if x['free_mb'] is not None]
    full = [x for x in locations if x['free_mb'] == 0]
    low = [x for x in locations if x['free_mb'] is not None and x['free_mb'] < 5120]
    info = hardware.inventory(store)
    warnings = budget(run, info)
    memory_blockers = compute_memory.capacity_issues(run, info)
    def row(key, title, state, text, level, **extra):
        return dict(id=key, title=title, state=state, text=text, level=level, checked_at=checked_at, **extra)
    rows = [row('destinations', 'Output permissions', 'Needs attention' if invalid else 'Permissions checked',
                ' '.join(x['label'] + ': ' + x['text'] for x in invalid) if invalid else
                'The results and working folders, or their nearest existing parents, pass read-only permission checks. No probe files were created.',
                'error' if invalid else 'info', action='paths'),
            row('storage', 'Disk space', 'Less than 1 MB free' if full else 'Low free space' if low else 'Partly checked' if invalid and measured else 'Free space checked' if measured else 'Unavailable',
                (' '.join(f"{x['label']}: {x['free_mb'] / 1024:.1f} GB free." for x in measured) if measured else 'Disk space could not be checked for these destinations.') +
                ' Output, temporary-file and database growth cannot be predicted here. Below 5 GB is a warning, not an analysis size estimate.',
                'error' if full else 'warning' if low else 'info' if measured else 'pending', action='paths'),
            row('resources', 'CPU and memory budget', 'Review budget' if warnings else 'Within detected capacity',
                f"{run['cores']} CPU threads. " + compute_memory.summary(run) + (' ' + ' '.join(warnings) if warnings else
                ' Tool requirements and concurrent memory use still apply.'),
                'error' if memory_blockers else 'warning' if warnings else 'info', action='budget', inventory_checked_at=info['checked_at'])]
    rows[-1]['checked_at'] = info['checked_at']
    kraken = kraken_memory(run, info)
    if kraken:
        rows.append(row('kraken_memory', 'Kraken database and memory', kraken.pop('state'),
                        kraken.pop('text'), kraken.pop('level'), action='budget', **kraken))
    return {'rows': rows, 'destinations': locations, 'blocked': bool(invalid or full or memory_blockers)}


def assert_launch(run, action, store=None, *, check_memory_capacity=True):
    # Rechecked when queued AND when the worker starts; warnings do not prevent dry-runs.
    problems = []
    for item in destinations(run, action):
        if item['level'] == 'error':
            problems.append(item['label'] + ': ' + item['text'] + ' ' + item['path'])
        elif item['free_mb'] == 0:
            problems.append(item['label'] + ': less than 1 MB free disk space. ' + item['path'])
    if problems:
        raise ValueError('Pre-run checks: ' + ' '.join(problems) + ' Restore access/free space, then refresh run checks.')
    info = None
    if check_memory_capacity and action in {'dry-run', 'run'} and compute_memory.policy(run)['swap_mb']:
        # Refresh at both queue submission and worker launch; a prior capacity
        # observation does not reserve memory while a run is waiting.
        info = {'memory': hardware.memory_capacity()}
        problems = compute_memory.capacity_issues(run, info)
        if problems:
            raise ValueError('Pre-run memory checks: ' + ' '.join(problems))
    return info
