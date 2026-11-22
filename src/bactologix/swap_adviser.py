"""Explain a Kraken swap allowance from current capacity without changing it.

This is an on-demand, read-only preview. Run it through the UI background-task
runner: filesystem and Windows capacity probes can wait on a busy device. The
proposal fills form values only; ordinary save and launch validation still apply.
"""
from __future__ import annotations

import copy
import hashlib
import re

import yaml

from . import compute_memory, hardware, preflight
from .catalog import PIPELINES
from .disk_capacity import CapacityProbe
from .disk_pause import PAUSE_BYTES, RESUME_BYTES, WARNING_BYTES
from .local import local_path
from .release_contract import approval
from .runs import parse_config
from .store import now

MIB = 1024 ** 2


def _known(value):
    return type(value) is int and value >= 0


def _hybas_review(store, saved_run):
    if saved_run is None:
        install = store.get('installs', store.get('settings', 'active-hybas'), {})
        commit = install.get('version', {}).get('commit')
    else:
        commit = saved_run.get('commit')
    return approval(store, 'hybas', commit) if commit else None


def _hybas_effective_ram(ram, cfg, memory):
    """Match the HybAs v2 controller's total_mem_mb ceiling before it adds swap."""
    limit = memory.get('limit_bytes')
    if 'limit_bytes' not in memory and _known(memory.get('total_mb')):
        limit = memory['total_mb'] * MIB  # Rounded down in earlier inventories.
    if not _known(limit) or not limit:
        raise ValueError('The physical RAM or container limit HybAs uses could not be measured. Refresh hardware detection and recalculate.')
    value = cfg.get('total_mem_mb', 'auto')
    if isinstance(value, str) and value.strip().lower() == 'auto':
        # Native total_mem_mb uses decimal MB; the Kraken index estimate uses MiB.
        return min(ram, int(limit * 0.8) // 1_000_000)
    if not re.fullmatch(r'[0-9]+', str(value).strip()) or int(value) < 1:
        raise ValueError('HybAs total_mem_mb must be auto or a positive integer.')
    if int(value) > limit // 1_000_000:
        raise ValueError('HybAs total_mem_mb exceeds the detected host or container memory limit. Reduce it or select auto and recalculate.')
    return min(ram, int(value))


def _storage(store, run):
    """Use the same workspace, output, temporary and WSL volumes as the guard."""
    observed = CapacityProbe(store, run).sample()
    volumes = observed.get('volumes', [])
    errors = list(observed.get('errors', []))
    warnings = []
    if not volumes:
        errors.append('Required storage capacity could not be measured.')
    for volume in volumes:
        free = volume.get('available_bytes')
        if not _known(free):
            errors.append('A required storage volume did not return valid free space.')
        elif free <= PAUSE_BYTES:
            errors.append(f"{volume.get('label', 'Required storage')} has {free / 1024 ** 3:.1f} GiB free. "
                          'Workspace runs stop at or below 5 GiB free.')
    return {**observed, 'errors': list(dict.fromkeys(errors)), 'warnings': warnings,
            'thresholds': {'pause_bytes': PAUSE_BYTES, 'resume_bytes': RESUME_BYTES,
                           'warning_bytes': WARNING_BYTES},
            'notice': ('A storage stop can be cleared after every monitored drive has more than 5 GiB free. '
                       'The swap allowance is a memory budget. '
                       'Free space is not a prediction of total output or swap-file growth.')}


def recommend(store, tool, memory_mb, *, run_id=None, config=None, kraken_path=None, draft_run=None):
    """Return reviewable form values, never save settings or configure OS swap.

    ``config`` may be the current unsaved editor text. Without it, a run uses its
    saved configuration and a pipeline uses its saved defaults. ``binding`` lets
    the UI discard a response after the researcher changes those inputs.
    """
    if tool not in PIPELINES:
        raise ValueError('Choose an installed pipeline for a memory recommendation.')
    ram = compute_memory._integer(memory_mb, 'RAM budget', 512)
    run = copy.deepcopy(store.get('runs', run_id) if run_id else draft_run)
    if run_id and (not run or run.get('tool') != tool):
        raise ValueError('Choose an existing run from this pipeline.')
    if config is None:
        from .run_config import defaults
        config = run['config'] if run else defaults(store, tool)
    if not isinstance(config, str):
        raise ValueError('Use the current YAML configuration text for the recommendation.')
    cfg = parse_config(config)
    # Check the type of the prior allowance, but validate the combined budget
    # against its replacement below. The user may be correcting an old value.
    compute_memory.allowance(cfg)
    result = {'available': False, 'can_apply': False, 'proposal': None,
              'state': 'unavailable', 'checked_at': now(), 'tool': tool,
              'binding': {'tool': tool, 'run_id': run_id, 'memory_mb': ram,
                          'config_sha256': hashlib.sha256(config.encode()).hexdigest()},
              'inputs': {'ram_budget_mb': ram}, 'warnings': [],
              'units': 'MiB (labelled MB in compute settings)',
              'scope': 'Kraken full-load resource estimate; not a whole-workflow peak-memory prediction.',
              'notice': ('The recommendation sets the Bactologix scheduling allowance. '
                         'Capacity is checked again when a run is queued and started.')}
    if tool != 'hybas':
        result['message'] = ('No reviewed database-based memory estimate is available for this pipeline. '
                             'Its tool-specific resource checks still apply; no swap amount has been guessed.')
        return result

    review = _hybas_review(store, run)
    if run is None:
        from .databases import _hybas_paths
        selected = local_path(kraken_path) if kraken_path is not None else _hybas_paths(store, cfg)['kraken']
        if kraken_path is not None:
            result['binding']['kraken_path'] = str(selected)
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
    result['inputs'].update(core_bytes=footprint['core_bytes'], estimated_kraken_mb=estimate,
                            configured_kraken_mb=explicit, required_budget_mb=required)
    info = hardware.compute_capacity() if draft_run is not None else hardware.inventory(store, refresh=True)
    result['hardware_checked_at'] = info.get('checked_at')
    result['binding']['hardware_id'] = info.get('id')
    memory = info.get('memory', {})
    result['inputs'].update({key: memory.get(key) for key in (
        'total_mb', 'available_mb', 'swap_mb', 'swap_available_mb',
        'combined_total_mb', 'combined_available_mb')})
    v2 = review is not None and review.get('configuration_schema') == 'hybas-v2'
    try:
        effective = _hybas_effective_ram(ram, cfg, memory) if v2 else ram
    except ValueError as exc:
        result.update(state='needs_attention', message=str(exc), problems=[str(exc)])
        return result
    swap = max(0, required - effective)
    result['available'] = True
    result['inputs'].update(effective_ram_mb=effective, effective_budget_mb=effective + swap,
                            calculated_swap_mb=swap)
    result['formula'] = ('Kraken index bytes × 1.2, converted to MiB, plus 1,024 MiB. A higher explicit '
                         'Kraken setting takes precedence. The additional swap is the amount still '
                         'needed after subtracting the RAM HybAs can use, measured in whole MiB.')
    if v2:
        result['formula'] += (' HybAs caps RAM at the chosen budget and total_mem_mb. Auto uses '
                              '80% of the host or container RAM limit, measured in decimal MB.')
    result['warnings'].append('This is a planning estimate, not measured resident memory. Other stages and concurrent jobs can require more memory.')
    if swap:
        result['warnings'].append('Disk swap is much slower than RAM. Loading a large database can involve severe paging and long runtimes.')
    result['storage'] = _storage(store, run)
    result['warnings'].extend(result['storage'].get('warnings', []))
    problems = list(result['storage']['errors'])
    if swap and v2 and not review.get('compute_memory'):
        problems.append('This HybAs version does not support additional swap. Select a version with swap support and create a new draft.')
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
    result['inputs']['usable_swap_mb'] = compute_memory.usable_swap_mb(free_swap)
    if swap and _known(free_swap):
        extra = max(0, swap + compute_memory.SWAP_RESERVE_MB - free_swap)
        result['inputs']['additional_usable_swap_needed_mb'] = extra
        if extra:
            result['inputs']['additional_swap_disk_headroom_mb'] = extra + WARNING_BYTES // MIB
            result['warnings'].append(
                f'At least {extra:,} MB more free swap capacity is needed before this allowance can be used. '
                f'If increasing an OS swap file, its chosen storage volume needs at least {extra + WARNING_BYTES // MIB:,} MB free '
                'for that increase plus the 5 GiB reserve, with further space for results. '
                'Container limits or other memory use may still prevent the allowance; recalculate after any change.')
    result['problems'] = list(dict.fromkeys(problems))
    if problems:
        result.update(state='needs_attention', message=(
            f'The calculated additional allowance is {swap:,} MB, but current capacity does not support applying it. '
            'Review the capacity details and recalculate after freeing space or changing operating-system swap.'))
        return result
    result.update(can_apply=True, state='ready',
                  proposal={'memory_mb': ram, 'swap_mb': swap, 'allow_swap': bool(swap)},
                  message=(f'With {ram:,} MB RAM requested, HybAs can use {effective:,} MB RAM. '
                           f'Adding {swap:,} MB swap gives Kraken an effective scheduling budget of {effective + swap:,} MB.'
                           if swap else f'The selected Kraken estimate fits within the {effective:,} MB of RAM HybAs can use.'))
    return result
