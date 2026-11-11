"""Application-channel ownership and development storage boundaries.

This is accidental-cross-use protection, not an operating-system sandbox.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from platformdirs import user_data_dir

MARKER = '.bactologix-channel.json'


def current():
    value = os.environ.get('BACTOLOGIX_CHANNEL', 'stable')
    if value not in {'stable', 'dev'}:
        raise ValueError('Unknown Bactologix installation channel.')
    return value


def title():
    return 'Bactologix Dev' if current() == 'dev' else 'Bactologix'


def installation_root():
    return Path.home() / '.local/share/bactologix-dev-install'


def default_workspace():
    if current() == 'dev':
        return Path.home() / '.local/share/bactologix-dev'
    return Path(user_data_dir('bactologix'))


def application_guard():
    marker = Path(sys.prefix) / MARKER
    if marker.is_file():
        if json.loads(marker.read_text()).get('channel') != current():
            raise ValueError('This application environment belongs to the other channel. Use its separate launcher.')
    elif current() == 'dev':
        raise ValueError('Bactologix Dev requires its dedicated installer and application environment.')


def claim(home):
    home = Path(home).resolve()
    channel = current()
    stable = (Path.home() / '.local/share/bactologix').resolve() if channel == 'dev' else Path(user_data_dir('bactologix')).resolve()
    if channel == 'dev' and (home == stable or home.is_relative_to(stable) or stable.is_relative_to(home)):
        raise ValueError('Development and stable workspaces must be separate, non-nested folders.')
    for parent in home.parents:
        if (parent / MARKER).exists() or (parent / 'workspace.sqlite3').exists():
            raise ValueError('A workspace cannot be nested inside another Bactologix workspace.')
    marker = home / MARKER
    if marker.exists():
        if json.loads(marker.read_text()).get('channel') != channel:
            raise ValueError('This workspace belongs to the other Bactologix channel.')
    else:
        if channel == 'dev' and (home / 'workspace.sqlite3').exists():
            raise ValueError('Dev cannot adopt an existing unmarked workspace. Choose a new Dev folder.')
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with marker.open('x') as stream:
                json.dump({'schema':1, 'channel':channel}, stream)
        except FileExistsError:
            claim(home)
    return channel


def owned(store, value, label='Resource'):
    path = Path(value).expanduser().resolve()
    if store.channel == 'dev' and not path.is_relative_to(store.home):
        raise ValueError(label + ' must be inside the Dev workspace; copy it there instead of sharing stable files.')
    return path


def _storage_discovery_defaults():
    """Keep the original data-home defaults when Dev redirects its own caches."""
    encoded = os.environ.get('BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS')
    if encoded is not None:
        try:
            defaults = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise ValueError('Preserved workspace discovery defaults are invalid.') from exc
        if (not isinstance(defaults, list) or not defaults or len(defaults) > 16
                or any(not isinstance(value, str) or not Path(value).is_absolute()
                       or '\x00' in value for value in defaults)):
            raise ValueError('Preserved workspace discovery defaults must be absolute workspace paths.')
    else:
        defaults = [str(Path(user_data_dir('bactologix')).expanduser().resolve())]
    # Dev has a fixed default independent of XDG. Keep the conventional stable
    # default too, even when a custom data home was inherited at launch.
    defaults.extend(str(Path.home() / '.local/share' / name)
                    for name in ('bactologix', 'bactologix-dev'))
    defaults = sorted(set(defaults))
    if len(defaults) > 16:
        raise ValueError('Preserved workspace discovery defaults contain too many paths.')
    return json.dumps(defaults, separators=(',', ':'))


def environment(home):
    """Route common writable caches and temporary data to this workspace."""
    if current() != 'dev':
        return {}
    home = Path(home).resolve()
    discovery_defaults = _storage_discovery_defaults()
    entries = {'XDG_CACHE_HOME':'cache', 'XDG_CONFIG_HOME':'config', 'XDG_DATA_HOME':'data',
               'CONDA_PKGS_DIRS':'cache/conda/pkgs', 'CONDA_ENVS_PATH':'envs',
               'PIP_CACHE_DIR':'cache/pip', 'MAMBA_ROOT_PREFIX':'tools/mamba',
               'HF_HOME':'cache/huggingface', 'TORCH_HOME':'cache/torch',
               'MPLCONFIGDIR':'cache/matplotlib', 'NUMBA_CACHE_DIR':'cache/numba',
               'TMPDIR':'tmp', 'TMP':'tmp', 'TEMP':'tmp'}
    result = {}
    for key, relative in entries.items():
        path = home / relative
        path.mkdir(parents=True, exist_ok=True)
        owned(type('Owner', (), {'home':home,'channel':'dev'})(), path, key)
        result[key] = str(path)
    condarc = home / 'config/condarc'
    owned(type('Owner', (), {'home':home,'channel':'dev'})(), condarc, 'Conda configuration')
    if not condarc.exists():
        try:
            with condarc.open('x') as stream:
                stream.write('channels:\n  - conda-forge\n  - bioconda\nchannel_priority: strict\nauto_activate_base: false\n')
        except FileExistsError:
            pass
    result.update(CONDARC=str(condarc), PYTHONNOUSERSITE='1', BACTOLOGIX_DEV_HOME=str(home),
                  BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS=discovery_defaults,
                  CONDA_EXE=str(home / 'tools/miniforge/bin/conda'),
                  CONDA_PYTHON_EXE=str(home / 'tools/miniforge/bin/python'),
                  CONDA_PREFIX='', CONDA_DEFAULT_ENV='', VIRTUAL_ENV='', PYTHONPATH='', PYTHONHOME='', PYTHONUSERBASE='',
                  LD_LIBRARY_PATH='', LD_PRELOAD='')
    # Never fall back to executables from an activated stable Conda environment.
    result['PATH'] = os.pathsep.join([str(Path(sys.prefix)/'bin'), str(home/'tools/miniforge/bin'),
        '/usr/local/sbin','/usr/local/bin','/usr/sbin','/usr/bin','/sbin','/bin',
        '/usr/lib/wsl/lib','/mnt/c/Windows/System32','/mnt/c/Windows/System32/WindowsPowerShell/v1.0'])
    return result


def validate_resources(store, cfg):
    if store.channel != 'dev':
        return
    keys = {'db_root','reference_database','directory','native_results_directory','database','database_manifest',
            'result','profiles_dir','reference_fasta','reference_metadata','domain_database','motif_rules',
            'placement_registry','prot_t5_path','weights_model','prosite_snapshot',
            'embeddings','embeddings_manifest','db_proteins','metadata','tree_review_registry','placement_cache_dir'}
    def walk(value, name=''):
        if isinstance(value, dict):
            for key, child in value.items():walk(child, key)
        elif name in keys and isinstance(value, str) and value:
            # Native relative defaults are resolved inside the run/workspace.
            if Path(value).is_absolute() or value.startswith('~'):
                owned(store, value, name)
            elif '..' in Path(value).parts:
                raise ValueError('Dev resource paths cannot escape their workspace.')
    walk(cfg)
