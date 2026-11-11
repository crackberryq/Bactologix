"""Explicit scheduling allowances; never change a tool's own memory settings."""
from __future__ import annotations

import yaml


MAX_MB = 2_000_000
SWAP_RESERVE_MB = 1024


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_MB:
        raise ValueError(f'{label} must be a whole number from {minimum:,} to {MAX_MB:,} MB.')
    return value


def allowance(config):
    """Read the frozen policy. Missing means the historical RAM-only policy."""
    saved = config.get('bactologix_compute', {})
    if not isinstance(saved, dict):
        raise ValueError('The Bactologix compute policy must be a mapping.')
    return _integer(saved.get('swap_mb', 0), 'Additional swap allowance')


def policy(run, config=None):
    from .runs import parse_config
    ram = _integer(run['memory_mb'], 'RAM budget', 512)
    swap = allowance(parse_config(run['config']) if config is None else config)
    if ram + swap > MAX_MB:
        raise ValueError(f'The combined RAM and swap scheduling budget must not exceed {MAX_MB:,} MB.')
    return {'ram_mb': ram, 'swap_mb': swap, 'scheduler_mb': ram + swap}


def require_adapter(store, tool, commit, config, source):
    """HybAs v2 must explicitly support the frozen swap policy in its review."""
    if tool != 'hybas' or not allowance(config):
        return
    from .release_contract import approval, preflight
    review = approval(store, tool, commit)
    # HybAs v1 accepts the supplied scheduler budget without v2's physical-RAM
    # resolver. Its existing adapter and source checks remain unchanged.
    if not review or review.get('configuration_schema') != 'hybas-v2':
        return
    expected = {'interface': 1, 'swap_policy': 'physical_ram_plus_explicit_swap_v1',
                'helper': 'workflow/lib/settings.py'}
    if review.get('compute_memory') != expected:
        raise ValueError('This HybAs 10 source does not support the additional swap allowance. '
                         'Update HybAs in Install & update and create a new run draft using that source. '
                         'Existing drafts retain their original pipeline source.')
    # A marker alone never establishes support: verify the immutable reviewed
    # helper and workflow files in the installed/frozen source before using it.
    preflight(store, tool, commit, source)


def with_swap(text, value):
    """Set only the configured scheduler allowance, preserving all tool limits."""
    from .runs import parse_config
    cfg = parse_config(text)
    allowance(cfg)  # Do not overwrite a malformed saved policy silently.
    swap = _integer(value, 'Additional swap allowance')
    cfg.setdefault('bactologix_compute', {})['swap_mb'] = swap
    return yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)


def summary(run):
    values = policy(run)
    if not values['swap_mb']:
        return f"{values['ram_mb']:,} MB RAM budget; swap is not included in scheduling."
    return (f"{values['ram_mb']:,} MB RAM + {values['swap_mb']:,} MB additional swap allowance "
            f"= {values['scheduler_mb']:,} MB scheduling budget. "
            'Paging may substantially slow execution. Tool-specific memory settings remain unchanged; '
            'this allowance is not an operating-system limit or measured memory use.')


def capacity_issues(run, info):
    """Check an opted-in allowance against current Linux-visible capacity."""
    values = policy(run)
    if not values['swap_mb']:
        return []
    memory = info.get('memory', {})
    required = ('total_mb', 'available_mb', 'swap_available_mb', 'combined_total_mb', 'combined_available_mb')
    unknown = [name for name in required if isinstance(memory.get(name), bool)
               or not isinstance(memory.get(name), int) or memory[name] < 0]
    if unknown:
        return ['Usable RAM and free swap capacity could not be verified, including container limits. '
                'Refresh hardware detection before using a swap allowance.']
    reserve = max(1024, memory['total_mb'] // 20)
    problems = []
    if values['ram_mb'] > max(0, memory['total_mb'] - reserve):
        problems.append(f"The {values['ram_mb']:,} MB RAM budget does not leave the {reserve:,} MB "
                        'system reserve within detected RAM. Reduce the RAM budget in a revised draft.')
    free_swap = max(0, memory['swap_available_mb'] - SWAP_RESERVE_MB)
    if values['swap_mb'] > free_swap:
        problems.append(f"The {values['swap_mb']:,} MB swap allowance exceeds the {free_swap:,} MB "
                        f'usable free swap after a {SWAP_RESERVE_MB:,} MB reserve. '
                        'Restore capacity or revise the allowance.')
    combined = max(0, min(memory['combined_total_mb'], memory['combined_available_mb']) - reserve - SWAP_RESERVE_MB)
    if values['scheduler_mb'] > combined:
        problems.append(f"The {values['scheduler_mb']:,} MB scheduling budget exceeds the {combined:,} MB "
                        'currently usable RAM and swap after reserves and container limits. '
                        'Free resources or revise the run budget.')
    return problems
