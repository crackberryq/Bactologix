"""Real Conda + Snakemake deployment of a tiny software-only environment."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from bactologix.catalog import PIPELINES
from bactologix.engine import Context
from bactologix import maintenance as m
from bactologix.store import Store

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_ENVIRONMENT_TESTS') != '1',
                                reason='Enable the real Conda deployment acceptance job')


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
            'ready': True, 'version': {'commit': commit, 'ref': 'software-only fixture'}}
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
    python = prefix / 'bin/python'
    held = prefix / 'bin/python-held-for-test'
    python.rename(held)
    try:
        assert run('check', fail=True)['status'] == 'Check failed'
        assert prefix.is_dir()
    finally:
        held.rename(python)
    assert not store.all('runs') and not store.all('samples')
