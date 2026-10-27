"""Real Conda + Snakemake deployment of a tiny software-only environment."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from bactologix.catalog import DATA, PIPELINES
from bactologix.engine import Context
from bactologix.engine import prepare_controller
from bactologix import maintenance as m
from bactologix.store import Store

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_ENVIRONMENT_TESTS') != '1',
                                reason='Enable the real Conda deployment acceptance job')


def test_release_declared_runner_is_installed_and_reused(tmp_path):
    """The app's own runner is 9.6.2; this source explicitly requires 9.27.1."""
    conda = shutil.which('conda')
    assert conda, 'Real Conda is required'
    store = Store(tmp_path / 'workspace')
    store.put('settings', 'conda', conda)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'environment.yaml').write_text(
        'channels: [conda-forge, bioconda, nodefaults]\n'
        'dependencies: [python=3.11, snakemake-minimal=9.27.1, pyyaml]\n')
    item = {'id': 'release-runner', 'tool': 'hybas', 'source': str(source), 'ready': False}
    for attempt in range(2):
        jid = store.submit('install', {})
        ctx = Context(store, store.job(jid))
        try:
            prepare_controller(ctx, item)
        except Exception:
            print(store.log_path(jid).read_text())
            raise
        finally:
            ctx.log.close()
        assert item['ready']
        assert item['controller_requirements'] == {'python': '3.11', 'snakemake': '9.27.1'}
        log = store.log_path(jid).read_text()
        if attempt == 0:
            prefix = Path(item['controller'])
            history = (prefix / 'conda-meta/history').read_bytes()
            assert 'env create' in log
        else:
            assert Path(item['controller']) == prefix
            assert 'env create' not in log
            assert (prefix / 'conda-meta/history').read_bytes() == history
    guard = DATA / 'run_existing.py'
    result = subprocess.run([conda, 'run', '--no-capture-output', '-p', str(prefix), 'python',
                             str(guard), 'snakemake', '--version'], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '9.27.1' in result.stdout
    assert_missing_guard([conda, 'run', '--no-capture-output', '-p', str(prefix), 'python'], tmp_path / 'new-runner-guard')
    assert not store.all('samples') and not store.all('runs')


def test_missing_install_verified_and_broken_tool(tmp_path, monkeypatch):
    conda = shutil.which('conda')
    assert conda, 'Real Conda is required'
    store = Store(tmp_path / 'workspace with spaces')
    store.put('settings', 'conda', conda)
    source = tmp_path / 'software source'
    envs = source / 'workflow/envs'
    envs.mkdir(parents=True)
    (envs / 'qc.yml').write_text('channels: [conda-forge]\ndependencies: [python=3.11]\n')
    (envs / 'qc.post-deploy.sh').write_text(
        '#!/bin/bash\nset -eu\ntest "$(command -v python)" = "$CONDA_PREFIX/bin/python"\n'
        'python -c "import sys; assert sys.version_info[:2] == (3,11); print(sys.version)"\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', commit)
    item = {'id': 'real-deployment-fixture', 'tool': 'hybas', 'source': str(source), 'controller': sys.prefix,
            'ready': True, 'rule_cache': str(tmp_path / 'native-cache'),
            'version': {'commit': commit, 'ref': 'software-only fixture'}}
    store.put('installs', item['id'], item)
    def run(action, fail=False):
        payload = m.environment_request(store, item['id'], ['qc'], action)
        jid = store.submit('environments', payload)
        ctx = Context(store, store.job(jid))
        try:
            if fail:
                with pytest.raises(RuntimeError, match='Environment checks failed'):
                    m.execute_environments(ctx)
            else:
                m.execute_environments(ctx)
        except Exception:
            print(store.log_path(jid).read_text())
            raise
        finally:
            ctx.log.close()
            store.update_job(jid, status='succeeded')
        return store.get('environment_checks', item['id'])['qc']
    assert run('check')['status'] == 'Missing'
    installed = run('install')
    assert installed['status'] == 'Verified'
    prefix = Path(installed['path'])
    history = (prefix / 'conda-meta/history').read_bytes()
    assert run('check')['status'] == 'Verified'
    assert (prefix / 'conda-meta/history').read_bytes() == history
    # A later source version selects the same cache and reuses the exact prefix.
    newer_source = tmp_path / 'newer software source'
    shutil.copytree(source, newer_source)
    (newer_source / 'README.md').write_text('A new version with unchanged software recipes.\n')
    subprocess.run(['git', '-C', str(newer_source), 'add', 'README.md'], check=True)
    subprocess.run(['git', '-C', str(newer_source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                    'commit', '-qm', 'new software version'], check=True)
    newer_commit = subprocess.check_output(['git', '-C', str(newer_source), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', newer_commit)
    original_cache = item['rule_cache']
    item = {**item, 'id': 'newer-deployment-fixture', 'source': str(newer_source),
            'rule_cache': str(tmp_path / 'initially-empty-cache'),
            'version': {'commit': newer_commit, 'ref': 'newer software-only fixture'}}
    store.put('installs', item['id'], item)
    m.set_cache(store, item['id'], original_cache)
    reused = run('install')
    assert reused['status'] == 'Verified' and reused['path'] == str(prefix)
    assert (prefix / 'conda-meta/history').read_bytes() == history
    # Execute a software-only checkpoint and rule using the cache inherited by the new source.
    folder = tmp_path / 'guarded-run'
    folder.mkdir()
    snakefile = folder / 'Snakefile'
    snakefile.write_text(
        'from pathlib import Path\n'
        'rule all:\n    input: "done.txt"\n'
        'checkpoint discover:\n    output: "checkpoint.txt"\n    run:\n'
        '        from snakemake.deployment.conda import Env\n'
        '        assert getattr(Env.create, "_bactologix_existing_only", False)\n'
        '        Path(output[0]).write_text("ready")\n'
        'rule finish:\n    input: lambda wc: checkpoints.discover.get().output[0]\n'
        '    output: "done.txt"\n    conda: ' + repr(str(newer_source / 'workflow/envs/qc.yml')) + '\n'
        '    shell: "python --version > {output}"\n')
    argv = [sys.executable, str(DATA / 'run_existing.py'), 'snakemake', '--snakefile', str(snakefile),
            '--directory', str(folder), '--cores', '1', '--use-conda', '--conda-prefix', original_cache]
    result = subprocess.run(argv, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Python 3.11' in (folder / 'done.txt').read_text()
    assert (prefix / 'conda-meta/history').read_bytes() == history
    assert_missing_guard([sys.executable], tmp_path / 'missing-guard')
    # Native Snakemake normally deletes an incomplete prefix. The guarded run must retain it.
    from snakemake.deployment.conda import get_env_setup_done_flag_file
    marker = get_env_setup_done_flag_file(prefix)
    marker.unlink()
    (folder / 'done.txt').unlink()
    try:
        result = subprocess.run(argv, text=True, capture_output=True)
        assert result.returncode != 0 and 'existing environments only' in result.stderr
        assert (prefix / 'conda-meta/history').read_bytes() == history
        assert not (folder / 'done.txt').exists()
    finally:
        marker.touch()
    python = prefix / 'bin/python'
    held = prefix / 'bin/python-held-for-test'
    python.rename(held)
    try:
        assert run('check', fail=True)['status'] == 'Check failed'
        assert prefix.is_dir()
    finally:
        held.rename(python)
    assert not store.all('runs') and not store.all('samples')


def assert_missing_guard(python_command, folder):
    folder.mkdir()
    recipe = folder / 'missing.yaml'
    recipe.write_text('channels: [conda-forge]\ndependencies: [python=3.11]\n')
    snakefile = folder / 'Snakefile'
    snakefile.write_text('rule missing:\n    output: "unexpected.txt"\n    conda: ' + repr(str(recipe))
                         + '\n    shell: "touch {output}"\n')
    # Native inventory is read-only, including incomplete prefixes, on both runner versions.
    from bactologix.readiness import parse_inventory, environment_state
    cache = folder / 'empty-cache'
    listing = [*python_command, '-m', 'snakemake', '--snakefile', str(snakefile),
               '--directory', str(folder), '--cores', '1', '--use-conda',
               '--conda-prefix', str(cache), '--list-conda-envs']
    def check_inventory():
        result = subprocess.run(listing, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        rows = parse_inventory(result.stdout, folder, cache)
        assert len(rows) == 1
        return rows[0]
    row = check_inventory()
    assert environment_state(row) == 'Missing'
    prefix = Path(row['path'])
    assert not prefix.exists() and not (folder / 'unexpected.txt').exists()
    (prefix / 'conda-meta').mkdir(parents=True)
    history = prefix / 'conda-meta/history'
    history.write_text('incomplete software fixture retained')
    assert environment_state(check_inventory()) == 'Incomplete'
    assert history.read_text() == 'incomplete software fixture retained'
    shutil.rmtree(prefix)  # remove only the test-created empty fixture
    result = subprocess.run([*python_command, str(DATA / 'run_existing.py'), 'snakemake',
                             '--snakefile', str(snakefile), '--directory', str(folder), '--cores', '1',
                             '--use-conda', '--conda-prefix', str(cache)], text=True, capture_output=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'existing environments only' in result.stderr, result.stdout + result.stderr
    assert not (folder / 'unexpected.txt').exists()
    assert not list(cache.glob('*/conda-meta/history'))
