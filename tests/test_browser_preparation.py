"""Only reviewed source hashes and explicit maintenance authorize downloads."""
import hashlib
from pathlib import Path

import pytest

from bactologix import maintenance as m
from test_maintenance import installed, ready_environment, FakeContext
from test_environment_health import metadata


def reviewed_browser_source(store, item):
    root = Path(item['source'])
    (root / 'workflow/envs/qc.yml').write_text(
        'dependencies: [python=3.11, nanoplot=1.46.2, plotly=6.1.2, python-kaleido=1.0.0]\n')
    (root / 'workflow/envs/qc.post-deploy.sh').write_text('# HYBAS_BROWSER_PREPARATION_CONTRACT=1\n')
    (root / 'workflow/lib').mkdir(exist_ok=True)
    (root / 'workflow/lib/rendering_runtime.py').write_text('# software fixture\n')
    review = store.get('release_approvals', 'hybas:' + item['version']['commit'])
    review['browser_preparation'] = {'interface': 1, 'environment': 'qc', 'policy': 'install_or_repair_when_missing'}
    for name in ('workflow/envs/qc.yml', 'workflow/envs/qc.post-deploy.sh', 'workflow/lib/rendering_runtime.py'):
        review['files_sha256'][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    store.put('release_approvals', 'hybas:' + item['version']['commit'], review)
    return root, review


@pytest.mark.parametrize('action,expected', [('check', '0'), ('run', '0'), ('install', '1'), ('repair', '1'), ('prepare', '1')])
def test_browser_permission_requires_explicit_action_and_reviewed_source(installed, action, expected):
    store, item = installed
    reviewed_browser_source(store, item)
    assert m.browser_preparation_env(store, 'hybas', item['version']['commit'], item['source'], action) == {
        'HYBAS_PREPARE_BROWSER': expected}


@pytest.mark.parametrize('change', ['approval', 'hash', 'helper', 'marker'])
def test_self_declaration_or_changed_source_never_authorizes_browser_download(installed, change):
    store, item = installed
    root, review = reviewed_browser_source(store, item)
    if change == 'approval':
        review.pop('browser_preparation')
    elif change == 'hash':
        review['files_sha256'].pop('workflow/envs/qc.post-deploy.sh')
    elif change == 'helper':
        (root / 'workflow/lib/rendering_runtime.py').write_text('changed\n')
    else:
        path = root / 'workflow/envs/qc.post-deploy.sh'
        path.write_text('# no browser preparation contract\n')
        review['files_sha256']['workflow/envs/qc.post-deploy.sh'] = hashlib.sha256(path.read_bytes()).hexdigest()
    store.put('release_approvals', 'hybas:' + item['version']['commit'], review)
    assert m.browser_preparation_env(store, 'hybas', item['version']['commit'], root, 'install')['HYBAS_PREPARE_BROWSER'] == '0'


def test_complete_split_environment_can_prepare_missing_browser_only_on_repair(installed, monkeypatch):
    store, item = installed
    reviewed_browser_source(store, item)
    # The fixture source is intentionally not committed; content checks above
    # are real, while bypassing only the fixture installation's git-clean check.
    monkeypatch.setattr(m, 'installation', lambda *_: item)
    prefix = ready_environment(store, item, 1)
    metadata(prefix, nanoplot='1.46.2', plotly='6.1.2', **{'python-kaleido': '1.0.0'})
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['qc'], 'repair'))
    original = ctx.command
    prepared = []
    def command(argv, **kwargs):
        result = original(argv, **kwargs)
        if 'bash' in argv:
            if kwargs.get('extra_env', {}).get('HYBAS_PREPARE_BROWSER') == '1':
                prepared.append(True)
            elif not prepared:
                raise RuntimeError('Missing browser')
        return result
    ctx.command = command
    m.execute_environments(ctx)
    assert prepared == [True]
    assert store.get('environment_checks', item['id'])['qc']['status'] == 'Verified'
    assert ctx.command_envs[-1] == {'HYBAS_PREPARE_BROWSER': '0'}
    assert not any('install' in argv or '--conda-create-envs-only' in argv for argv in ctx.calls)


def test_read_only_probe_overrides_inherited_browser_preparation(installed, monkeypatch):
    store, item = installed
    ready_environment(store, item, 1)
    monkeypatch.setenv('HYBAS_PREPARE_BROWSER', '1')
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['qc'], 'check'))
    m.execute_environments(ctx)
    assert ctx.command_envs[-1] == {'HYBAS_PREPARE_BROWSER': '0'}
