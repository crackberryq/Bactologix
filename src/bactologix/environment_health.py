"""Runtime checks and bounded, explicitly requested tool environment repairs."""
from __future__ import annotations

import json
from pathlib import Path
import re

import yaml

from .catalog import DATA
from .engine import Cancelled, find_conda
from .store import now


def plotting_recipe(row):
    definition = yaml.safe_load(Path(row['recipe']).read_text()) or {}
    return any(isinstance(d, str) and re.match(r'(?:[\w-]+::)?nanoplot(?:[=<>!~\s]|$)', d, re.I)
               for d in definition.get('dependencies', []))


def probe(ctx, row, prefix, folder):
    if plotting_recipe(row) and incompatible_plotting(package_versions(prefix)):
        raise RuntimeError(PLOTTING_CONFLICT)
    command = [find_conda(ctx.store), 'run', '--no-capture-output', '-p', str(prefix)]
    native = ['bash', row['hook']] if row['hook'] else ['python', '-c', 'import sys; print(sys.version)']
    ctx.command([*command, *native], cwd=folder, extra_env={'HYBAS_PREPARE_BROWSER': '0'})
    if plotting_recipe(row):
        ctx.command([*command, 'python', str(DATA / 'plot_probe.py')], cwd=folder,
                    extra_env={'HYBAS_PREPARE_BROWSER': '0'})


def package_versions(prefix):
    result = {}
    for path in (prefix / 'conda-meta').glob('*.json'):
        try:
            metadata = json.loads(path.read_text())
            result[metadata['name']] = metadata['version']
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


PLOTTING_CONFLICT = (
    'NanoPlot 1.46.2 requires Kaleido 1 or newer, but MultiQC 1.32 requires Kaleido 0.2.1. '
    'These versions need separate tool environments. Install a reviewed pipeline version '
    'with separate QC and MultiQC environments, then create a revised draft. '
    'The existing environment and saved run have not been changed. '
    'Do not force an older Kaleido into the NanoPlot environment.'
)


def incompatible_plotting(versions):
    return versions.get('nanoplot') == '1.46.2' and versions.get('multiqc') == '1.32'


def repair_plotting(ctx, row, prefix, folder, preparation_env=None):
    """Record the known conflicting contract without forcing incompatible packages."""
    before = package_versions(prefix)
    if not plotting_recipe(row):
        return False
    if not incompatible_plotting(before):
        if not preparation_env or preparation_env.get('HYBAS_PREPARE_BROWSER') != '1' or not row.get('hook'):
            return False
        receipt = {'environment': str(prefix), 'recipe': row['recipe'], 'job': ctx.job['id'],
                   'started': now(), 'before': before, 'status': 'preparing_browser'}
        path = folder / (row['id'] + '-repair.json')
        path.write_text(json.dumps(receipt, indent=2) + '\n')
        ctx.write('Explicit repair may prepare a missing Chrome browser; existing browsers and package pins are retained.')
        try:
            ctx.command([find_conda(ctx.store), 'run', '--no-capture-output', '-p', str(prefix), 'bash', row['hook']],
                        cwd=folder, extra_env=preparation_env)
            probe(ctx, row, prefix, folder)
            receipt['status'] = 'verified'
        except BaseException:
            receipt['status'] = 'failed'
            raise
        finally:
            receipt.update(after=package_versions(prefix), finished=now())
            path.write_text(json.dumps(receipt, indent=2) + '\n')
        return True
    receipt = {'environment': str(prefix), 'recipe': row['recipe'], 'job': ctx.job['id'],
               'checked_at': now(), 'before': before, 'status': 'incompatible_recipe',
               'packages_changed': False, 'message': PLOTTING_CONFLICT}
    path = folder / (row['id'] + '-repair.json')
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    ctx.write('Conflicting QC dependency contract; no packages changed. Receipt: ' + str(path))
    raise RuntimeError(PLOTTING_CONFLICT)


def retain_incomplete(ctx, row, prefix):
    """Move an incomplete cache aside before native deployment can delete it."""
    if prefix.is_symlink():
        raise ValueError('Repair requires a real environment directory, not a symbolic link.')
    backup = prefix.parent / '.bactologix-repair-backups' / ctx.job['id'] / row['id']
    backup.parent.mkdir(parents=True, exist_ok=True)
    receipt = {'environment': str(prefix), 'backup': str(backup), 'job': ctx.job['id'], 'created': now()}
    (backup.parent / (row['id'] + '.json')).write_text(json.dumps(receipt, indent=2) + '\n')
    prefix.rename(backup)
    ctx.write('Retained incomplete environment at ' + str(backup))
    return backup


def restore_incomplete(ctx, prefix, backup):
    if prefix.exists():
        failed = backup.with_name(backup.name + '-failed-attempt')
        prefix.rename(failed)
        ctx.write('Retained unsuccessful repair at ' + str(failed))
    backup.rename(prefix)
    # An old incomplete cache must never acquire the attempted deployment's success flag.
    prefix.with_suffix('.env_setup_done').unlink(missing_ok=True)
    ctx.write('Restored the previous incomplete environment at ' + str(prefix))


def repair(ctx, row, prefix, folder, deploy, preparation_env=None):
    complete = (prefix / 'conda-meta/history').is_file() and prefix.with_suffix('.env_setup_done').is_file()
    if complete:
        try:
            probe(ctx, row, prefix, folder)
            return 'Existing environment passed its checks; no packages changed.'
        except Cancelled:
            raise
        except Exception:
            if repair_plotting(ctx, row, prefix, folder, preparation_env):
                return 'Rendering compatibility repaired; native checks and HTML/PNG rendering passed.'
            raise RuntimeError('No supported automatic fix for this failure. The existing environment was retained; inspect the check log.') from None
    backup = retain_incomplete(ctx, row, prefix) if prefix.exists() else None
    try:
        # A stale success flag without conda-meta/history is not deployment evidence.
        prefix.with_suffix('.env_setup_done').unlink(missing_ok=True)
        deploy()
        if not (prefix / 'conda-meta/history').is_file() or not prefix.with_suffix('.env_setup_done').is_file():
            raise RuntimeError('Environment deployment did not complete. See the job log.')
        try:
            probe(ctx, row, prefix, folder)
        except Cancelled:
            raise
        except Exception:
            if not repair_plotting(ctx, row, prefix, folder, preparation_env):
                raise
    except BaseException:
        if backup:
            restore_incomplete(ctx, prefix, backup)
        else:
            prefix.with_suffix('.env_setup_done').unlink(missing_ok=True)
        raise
    return 'Environment prepared and checks passed.' + (' The previous incomplete copy was retained.' if backup else '')
