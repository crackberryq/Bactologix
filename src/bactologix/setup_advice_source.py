"""Read setup defaults from the active pipeline's immutable Git source.

Verification here establishes source identity, not analytical performance.
Pipeline Python, shell scripts and workflow files are never executed.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import yaml

from .catalog import PIPELINES, supported
from .runs import parse_config


MAX_CONFIG_BYTES = 2 * 1024 * 1024
CONFIG_PATHS = {'hybas': 'config.example.yaml', 'basis': 'config.yaml',
                'micpscope': 'config.yaml'}
HYBAS_PROFILES = {
    '780ee7086c15c49f2825800ec463327f009f14a6': 'hybas9',
    'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d': 'hybas9',
    '87c1ab81c8652bd10db47954193e27ad802cfc86': 'hybas9',
    '585abb34ed4401da64b37a9fd629ba8adc236396': 'hybas9',
    '08c315479f5493ce0b1f44ba26da3c7f2e0d748b': 'hybas10',
    '79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4': 'hybas10',
}


def _git(source, *args):
    # A user-configured filesystem monitor would otherwise run during status.
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        env.pop(key, None)
    result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(source), *args],
                            capture_output=True, timeout=10, env=env, check=False)
    if result.returncode:
        raise ValueError('The installed Git source could not be verified.')
    return result.stdout


def _clean(source, commit):
    if _git(source, 'rev-parse', 'HEAD').decode('ascii').strip() != commit:
        raise ValueError('The installed source no longer matches its recorded commit.')
    if _git(source, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('The installed source has local changes. Its setup defaults are unavailable.')


def load(store, tool):
    """Return source-labelled defaults, or an unverified result with no defaults.

    A known profile permits the separately reviewed input-route interpretation.
    Other supported commits may expose their own template values, but do not
    inherit that interpretation merely because they share a pipeline name.
    """
    result = {'commit': None, 'label': 'No verified pipeline source',
              'verified': False, 'config_path': CONFIG_PATHS.get(tool),
              'config_sha256': None, 'defaults': {}, 'profile': None,
              'installation': None}
    try:
        if tool not in PIPELINES:
            raise ValueError('Select a supported pipeline to review its setup.')
        install_id = store.get('settings', 'active-' + tool)
        installed = store.get('installs', install_id) if install_id else None
        if not isinstance(installed, dict) or installed.get('tool') != tool or not installed.get('ready'):
            raise ValueError('Set up and select a ready pipeline runner to review source defaults.')
        if installed.get('setup_mode') == 'first_install' and not installed.get('setup_completed'):
            raise ValueError('Finish first-time pipeline installation before reviewing source defaults.')
        version = installed.get('version')
        if not isinstance(version, dict) or not re.fullmatch('[0-9a-f]{40}', str(version.get('commit', ''))):
            raise ValueError('The selected installation has no valid recorded source commit.')
        commit = version['commit']
        result.update(commit=commit, installation=install_id,
                      label=str(version.get('ref') or version.get('name') or commit[:12]))
        if not supported(tool, commit, store):
            raise ValueError('The selected pipeline commit has no supported execution adapter.')
        raw_source = installed.get('source')
        if not isinstance(raw_source, str) or not raw_source:
            raise ValueError('The selected pipeline source folder is unavailable.')
        source = Path(raw_source).resolve(strict=True)
        root = Path(_git(source, 'rev-parse', '--show-toplevel').decode('utf-8').strip()).resolve()
        if root != source:
            raise ValueError('The selected source must be the pipeline Git repository root.')
        _clean(source, commit)
        config_path = CONFIG_PATHS[tool]
        tree = _git(source, 'ls-tree', commit, '--', config_path).decode('utf-8').strip()
        fields = tree.split(None, 3)
        if len(fields) != 4 or fields[0] not in {'100644', '100755'} or fields[1] != 'blob' or fields[3] != config_path:
            raise ValueError('This pipeline commit has no ordinary configuration template.')
        blob = commit + ':' + config_path
        size = int(_git(source, 'cat-file', '-s', blob).strip())
        if size > MAX_CONFIG_BYTES:
            raise ValueError('The source configuration template exceeds 2 MB.')
        raw = _git(source, 'cat-file', 'blob', blob)
        if len(raw) != size or len(raw) > MAX_CONFIG_BYTES:
            raise ValueError('The source configuration template could not be read consistently.')
        defaults = parse_config(raw.decode('utf-8'))
        _clean(source, commit)
        result.update(verified=True, defaults=defaults,
                      config_sha256=hashlib.sha256(raw).hexdigest(),
                      profile=HYBAS_PROFILES.get(commit) if tool == 'hybas' else None)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, yaml.YAMLError, RecursionError) as exc:
        result['error'] = str(exc) if isinstance(exc, ValueError) else 'The installed source could not be read. Check the pipeline installation.'
    return result
