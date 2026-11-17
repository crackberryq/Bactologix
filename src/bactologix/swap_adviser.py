"""Explain a Kraken swap allowance from current capacity without changing it.

This is an on-demand, read-only preview. Run it through the UI background-task
runner: filesystem and Windows capacity probes can wait on a busy device. The
proposal fills form values only; ordinary save and launch validation still apply.
"""
from __future__ import annotations

import copy
import hashlib

import yaml

from . import compute_memory, hardware, preflight
from .catalog import PIPELINES
from .disk_capacity import CapacityProbe
from .disk_pause import PAUSE_BYTES, RESUME_BYTES, WARNING_BYTES
from .runs import parse_config
from .store import now

MIB = 1024 ** 2
GIB_MB = 1024


def _known(value):
    return type(value) is int and value >= 0


def _round_gib(value):
    return ((max(0, value) + GIB_MB - 1) // GIB_MB) * GIB_MB


def _storage(store, run):
    """Use the same workspace, output, temporary and WSL volumes as the guard."""
    observed = CapacityProbe(store, run).sample()
    volumes = observed.get('volumes', [])
    errors = list(observed.get('errors', []))
    if not volumes:
        errors.append('Required storage capacity could not be measured.')
    for volume in volumes:
        free = volume.get('available_bytes')
        if not _known(free):
            errors.append('A required storage volume did not return valid free space.')
        elif free < WARNING_BYTES:
            errors.append(f"{volume.get('label', 'Required storage')}: {free / 1024 ** 3:.1f} GiB free. "
                          'Free at least 20 GiB before applying a new swap recommendation; '
                          'additional space for results and temporary files is still needed.')
    return {**observed, 'errors': list(dict.fromkeys(errors)),
            'thresholds': {'pause_bytes': PAUSE_BYTES, 'resume_bytes': RESUME_BYTES,
                           'warning_bytes': WARNING_BYTES},
            'notice': ('The run guard pauses at 10 GiB or less and requires at least 15 GiB to resume. '
                       'This recommendation keeps the existing 20 GiB warning headroom. '
                       'Free space is not a prediction of total output or swap-file growth.')}


def recommend(store, tool, memory_mb, *, run_id=None, config=None):
    """Return reviewable form values, never save settings or configure OS swap.

    ``config`` may be the current unsaved editor text. Without it, a run uses its
    saved configuration and a pipeline uses its saved defaults. ``binding`` lets
    the UI discard a response after the researcher changes those inputs.
    """
    if tool not in PIPELINES:
        raise ValueError('Choose an installed pipeline for a memory recommendation.')
    ram = compute_memory._integer(memory_mb, 'RAM budget', 512)
    run = copy.deepcopy(store.get('runs', run_id)) if run_id else None
    if run_id and (not run or run.get('tool') != tool):
        raise ValueError('Choose an existing run from this pipeline.')
    if config is None:
        from .run_config import defaults
        config = run['config'] if run else defaults(store, tool)
    if not isinstance(config, str):
        raise ValueError('Use the current YAML configuration text for the recommendation.')
    cfg = parse_config(config)
    # A malformed existing allowance must not be hidden by this preview.
    compute_memory.policy({'memory_mb': ram}, cfg)
    result = {'available': False, 'can_apply': False, 'proposal': None,
              'state': 'unavailable', 'checked_at': now(), 'tool': tool,
              'binding': {'tool': tool, 'run_id': run_id, 'memory_mb': ram,
                          'config_sha256': hashlib.sha256(config.encode()).hexdigest()},
              'inputs': {'ram_budget_mb': ram}, 'warnings': [],
              'units': 'MiB (labelled MB in compute settings)',
              'scope': 'Kraken full-load resource estimate; not a whole-workflow peak-memory prediction.',
              'notice': ('Use recommendation fills the application allowance only. It does not create swap, '
                         'change Windows/WSL settings, restart the computer or start a run. '
                         'Capacity is checked again when a run is queued and started.')}
    if tool != 'hybas':
        result['message'] = ('No reviewed database-based memory estimate is available for this pipeline. '
                             'Its tool-specific resource checks still apply; no swap amount has been guessed.')
        return result

    if run is None:
        from .databases import _hybas_paths
        selected = _hybas_paths(store, cfg)['kraken']
        probe_cfg = dict(cfg, db_root=str(selected.parent), kraken_db_name=selected.name)
        run = {'tool': tool, 'folder': str(store.home), 'config': yaml.safe_dump(probe_cfg, sort_keys=False)}
    else:
        run['config'] = config
    run['memory_mb'] = ram

    # The helper stats only the three index files; no database hashing or scan.
    footprint = preflight.kraken_memory(run, {})
    result['database'] = footprint
    if footprint is None:
        result.update(state='not_needed', message='Kraken is disabled in these settings. No Kraken swap recommendation is needed.')
        return result
    estimate = footprint.get('estimated_full_load_mb')
    if not _known(estimate) or not estimate:
        result['message'] = ('Select a complete Kraken database before calculating swap. '
                             'The three index files could not be measured, so no amount has been guessed.')
        return result
    # Keep the file observation separate from the fresh hardware observation
    # below; the footprint helper was deliberately called without cached RAM.
    result['database'] = {key: footprint[key] for key in (
        'selected_path', 'resolved_path', 'files', 'core_bytes', 'estimated_full_load_mb')}
    explicit = cfg.get('kraken_mem_mb', 'auto')
    if explicit in (None, 'auto'):
        explicit = None
    elif type(explicit) is not int or not 1 <= explicit <= compute_memory.MAX_MB:
        raise ValueError('Kraken memory must be auto or a positive whole number of MB.')
    required = max(estimate, explicit or 0)
    swap = _round_gib(required - ram)
    result['available'] = True
    result['inputs'].update(core_bytes=footprint['core_bytes'], estimated_kraken_mb=estimate,
                            configured_kraken_mb=explicit, required_budget_mb=required,
                            calculated_swap_mb=swap)
    result['formula'] = ('Kraken index bytes × 1.2, converted to MiB, plus 1,024 MiB; use a higher '
                         'explicit Kraken memory setting when present. Subtract the chosen RAM budget '
                         'and round any additional allowance up to the next 1,024 MiB.')
    result['warnings'].append('This is a planning estimate, not measured resident memory. Other stages and concurrent jobs can require more memory.')
    if swap:
        result['warnings'].append('Disk swap is much slower than RAM. Loading a large database can involve severe paging and long runtimes.')

    info = hardware.inventory(store, refresh=True)
    result['hardware_checked_at'] = info.get('checked_at')
    result['binding']['hardware_id'] = info.get('id')
    memory = info.get('memory', {})
    result['inputs'].update({key: memory.get(key) for key in (
        'total_mb', 'available_mb', 'swap_mb', 'swap_available_mb',
        'combined_total_mb', 'combined_available_mb')})
    result['storage'] = _storage(store, run)
    problems = list(result['storage']['errors'])
    if not all(_known(memory.get(key)) for key in ('total_mb', 'available_mb')):
        problems.append('Usable RAM could not be verified. Detect hardware again before applying a recommendation.')
    else:
        reserve = max(1024, memory['total_mb'] // 20)
        usable_ram = max(0, min(memory['total_mb'], memory['available_mb']) - reserve)
        result['inputs'].update(ram_reserve_mb=reserve, usable_ram_mb=usable_ram)
        if ram > usable_ram:
            problems.append(f'The chosen {ram:,} MB RAM budget exceeds the {usable_ram:,} MB currently usable after the system reserve. Reduce RAM or free memory, then recalculate.')
    if ram + swap > compute_memory.MAX_MB:
        problems.append(f'The estimate exceeds the supported {compute_memory.MAX_MB:,} MB combined scheduling budget.')
    else:
        candidate = dict(run, config=compute_memory.with_swap(config, swap))
        problems.extend(compute_memory.capacity_issues(candidate, info))
    free_swap = memory.get('swap_available_mb')
    result['inputs']['swap_reserve_mb'] = compute_memory.SWAP_RESERVE_MB
    result['inputs']['usable_swap_mb'] = max(0, free_swap - compute_memory.SWAP_RESERVE_MB) if _known(free_swap) else None
    if swap and _known(free_swap):
        extra = _round_gib(max(0, swap + compute_memory.SWAP_RESERVE_MB - free_swap))
        result['inputs']['additional_usable_swap_needed_mb'] = extra
        if extra:
            result['inputs']['additional_swap_disk_headroom_mb'] = extra + WARNING_BYTES // MIB
            result['warnings'].append(
                f'At least {extra:,} MB more free swap capacity is needed before this allowance can be used. '
                f'If increasing an OS swap file, its chosen storage volume needs at least {extra + WARNING_BYTES // MIB:,} MB free '
                'for that increase plus the 20 GiB warning headroom, with further space for results. '
                'Container limits or other memory use may still prevent the allowance; recalculate after any change.')
    result['problems'] = list(dict.fromkeys(problems))
    if problems:
        result.update(state='needs_attention', message=(
            f'The calculated additional allowance is {swap:,} MB, but current capacity does not support applying it. '
            'Review the capacity details and recalculate after freeing space or changing operating-system swap.'))
        return result
    result.update(can_apply=True, state='ready',
                  proposal={'memory_mb': ram, 'swap_mb': swap, 'allow_swap': bool(swap)},
                  message=(f'Use {ram:,} MB RAM plus {swap:,} MB additional swap allowance '
                           f'({ram + swap:,} MB total scheduling budget) for the selected Kraken estimate.'
                           if swap else 'The selected Kraken estimate fits within this RAM budget. No additional swap allowance is needed for Kraken.'))
    return result
