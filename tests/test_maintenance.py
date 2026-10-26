"""Administrative software fixtures: no biological analyses or data downloads."""
import json
from pathlib import Path
import subprocess

import pytest

from bactologix import databases, maintenance as m
from bactologix.catalog import PIPELINES
from bactologix.store import Store
from bactologix.ui_backend import dispatch


@pytest.fixture
def installed(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace with spaces')
    source = tmp_path / 'source with spaces'
    envs = source / 'workflow/envs'
    envs.mkdir(parents=True)
    for name in ('assembly', 'qc'):
        (envs / (name + '.yml')).write_text('channels: [conda-forge]\ndependencies: [python=3.11]\n')
        (envs / (name + '.post-deploy.sh')).write_text('#!/bin/bash\nset -eu\npython --version\n')
    (source / 'Hybas.smk').write_text('rule all:\n    input: []\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', commit)
    monkeypatch.setattr(m, 'find_conda', lambda _: '/fixture/conda')
    item = {'id': 'fixture-install', 'tool': 'hybas', 'source': str(source), 'controller': '/fixture/runner',
            'ready': True, 'rule_cache': str(tmp_path / 'tool-cache'), 'version': {'commit': commit, 'ref': 'fixture'}}
    store.put('installs', item['id'], item)
    store.put('settings', 'active-hybas', item['id'])
    return store, item


class FakeContext:
    def __init__(self, store, payload, kind='environments'):
        self.store = store
        jid = store.submit(kind, payload)
        self.job = store.job(jid)
        self.calls = []
        self.output = []

    def write(self, text):
        self.output.append(str(text))

    def check_cancel(self):
        pass

    def command(self, argv, cwd=None, stdout=None):
        self.calls.append(argv)
        if stdout is not None:
            item = self.store.get('installs', self.job['payload']['installation'])
            stdout.write('environment\tcontainer\tlocation\n')
            for index, row in enumerate(m.recipes(item)):
                if row['id'] in self.job['payload']['definitions']:
                    prefix = m.cache_path(self.store, item) / (str(index + 1) * 32 + '_')
                    stdout.write(row['recipe'] + '\t\t' + str(prefix) + '\n')


def ready_environment(store, item, index):
    prefix = m.cache_path(store, item) / (str(index + 1) * 32 + '_')
    (prefix / 'conda-meta').mkdir(parents=True)
    (prefix / 'conda-meta/history').write_text('fixture\n')
    prefix.with_suffix('.env_setup_done').touch()
    return prefix


def test_check_distinguishes_missing_from_verified_without_install(installed):
    store, item = installed
    prefix = ready_environment(store, item, 0)
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['assembly', 'qc'], 'check'))
    m.execute_environments(ctx)
    rows = store.get('environment_checks', item['id'])
    assert rows['assembly']['status'] == 'Verified'
    assert rows['qc']['status'] == 'Missing'
    assert not any('--conda-create-envs-only' in argv for argv in ctx.calls)
    assert any(str(prefix) in argv and 'bash' in argv for argv in ctx.calls)
    assert len(ctx.calls) == 2
    assert not store.all('samples') and not store.all('runs')


def test_install_uses_only_environment_deployment_and_probes(installed):
    store, item = installed
    ready_environment(store, item, 0)
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['assembly'], 'install'))
    m.execute_environments(ctx)
    assert '--conda-create-envs-only' in ctx.calls[0]
    assert '--list-conda-envs' in ctx.calls[1]
    assert 'bash' in ctx.calls[2]
    assert store.get('environment_checks', item['id'])['assembly']['status'] == 'Verified'


def test_environment_failure_is_persisted_and_not_deleted(installed):
    store, item = installed
    prefix = ready_environment(store, item, 0)
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['assembly', 'qc'], 'check'))
    original = ctx.command
    def fail_probe(argv, **kwargs):
        if 'bash' in argv:
            raise RuntimeError('missing executable fixture')
        return original(argv, **kwargs)
    ctx.command = fail_probe
    with pytest.raises(RuntimeError, match='Environment checks failed'):
        m.execute_environments(ctx)
    rows = store.get('environment_checks', item['id'])
    assert rows['assembly']['status'] == 'Check failed'
    assert rows['qc']['status'] == 'Missing'
    assert prefix.exists()


def test_incomplete_environment_is_not_executed(installed):
    store, item = installed
    prefix = ready_environment(store, item, 0)
    prefix.with_suffix('.env_setup_done').unlink()
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['assembly'], 'check'))
    m.execute_environments(ctx)
    assert len(ctx.calls) == 1
    assert store.get('environment_checks', item['id'])['assembly']['status'] == 'Incomplete'


def test_changed_source_cannot_run_queued_maintenance(installed):
    store, item = installed
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['assembly'], 'install'))
    Path(item['source'], 'workflow/envs/assembly.yml').write_text('changed\n')
    with pytest.raises(ValueError, match='source changed'):
        m.execute_environments(ctx)
    assert not ctx.calls


def test_environment_list_rejects_unexpected_prefix(installed, tmp_path):
    store, item = installed
    row = m.recipes(item)[0]
    text = row['recipe'] + '\t\t/tmp/unrelated-env\n'
    with pytest.raises(ValueError, match='unexpected environment'):
        m.parse_environment_list(text, tmp_path, [row], m.cache_path(store, item))


def test_install_rejects_unsafe_native_cache_before_any_deployment(installed):
    store, item = installed
    item.pop('rule_cache')  # This fixture's workspace contains spaces.
    store.put('installs', item['id'], item)
    assert m.environment_request(store, item['id'], ['assembly'], 'check')
    with pytest.raises(ValueError, match='cache path without spaces'):
        m.environment_request(store, item['id'], ['assembly'], 'install')
    assert not (store.home / 'envs/hybas').exists()


def test_cache_picker_preserves_existing_run_cache(installed, tmp_path):
    store, item = installed
    old = item['rule_cache']
    store.put('runs', 'existing', {'rule_cache': old})
    cache = tmp_path / 'selected-cache'
    cache.mkdir()
    m.set_cache(store, item['id'], str(cache))
    assert store.get('installs', item['id'])['rule_cache'] == str(cache)
    assert store.get('runs', 'existing')['rule_cache'] == old
    assert store.get('environment_checks', item['id']) == {}


def test_cache_change_is_blocked_while_jobs_are_pending(installed, tmp_path):
    store, item = installed
    store.submit('smoke', {})
    with pytest.raises(ValueError, match='queued or running'):
        m.set_cache(store, item['id'], str(tmp_path))


def test_download_destination_is_new_and_configuration_is_sample_free(installed, tmp_path):
    store, item = installed
    payload = m.download_request(store, 'hybas', 'busco', str(tmp_path), lineage='bacteria_odb10')
    assert Path(payload['destination']).parent == tmp_path
    assert not Path(payload['destination']).exists()
    cfg, target, result = m.download_config(payload, tmp_path)
    assert cfg['admin_mode'] is True and cfg['sample_sheet'] == ''
    assert target == 'download_busco_db'
    assert result.endswith('/busco/bacteria_odb10')
    assert not store.get('configs', 'hybas')
    with pytest.raises(ValueError, match='lineage'):
        m.download_request(store, 'hybas', 'busco', str(tmp_path), lineage='../escape')
    with pytest.raises(ValueError, match='Threads'):
        m.download_request(store, 'hybas', 'busco', str(tmp_path), cores=0)
    with pytest.raises(ValueError, match='supports a local folder'):
        m.download_request(store, 'hybas', 'prokka', str(tmp_path))


@pytest.mark.parametrize('key', [s['id'] for s in databases.CATALOG['basis'] if not s.get('managed')])
def test_basis_download_requests_one_resource_without_samples(tmp_path, key):
    payload = {'tool': 'basis', 'resource': key, 'destination': str(tmp_path / 'databases'), 'cores': 2, 'memory_mb': 4000}
    cfg, target, result = m.download_config(payload, tmp_path)
    assert cfg['isolates'] == [] and cfg['samples'] == '' and cfg['isolate'] == ''
    provider = 'card' if key == 'rgi' else key
    assert target == str(Path(result) / 'provenance.json')
    assert Path(result).name == provider
    enabled = [k for k, v in cfg['modules'].items() if v['enabled']]
    assert enabled == ([] if key in {'prosite', 'deep_catalogue'} else ['pathogenfinder2' if key == 'prott5' else key])


def test_download_failure_resume_and_explicit_selection(installed, tmp_path):
    store, item = installed
    store.put('configs', 'hybas', 'custom: preserved\n')
    payload = m.download_request(store, 'hybas', 'kraken', str(tmp_path))
    ctx = FakeContext(store, payload, 'database_download')
    def fail(argv, **kwargs):
        raise RuntimeError('fixture interrupted download')
    ctx.command = fail
    with pytest.raises(RuntimeError, match='interrupted'):
        m.execute_download(ctx)
    saved = store.get('database_downloads', payload['id'])
    assert saved['status'] == 'failed'
    store.update_job(ctx.job['id'], status='failed')
    retry = FakeContext(store, payload, 'database_download')
    def complete(argv, **kwargs):
        root = Path(payload['destination']) / 'standard'
        root.mkdir()
        for name in ('hash.k2d', 'opts.k2d', 'taxo.k2d'):
            (root / name).write_text('software fixture\n')
    retry.command = complete
    m.execute_download(retry)
    saved = store.get('database_downloads', payload['id'])
    assert saved['status'] == 'completed'
    assert saved['workflow_sha256'] == m.tree_digest(Path(saved['folder']) / 'workflow')
    assert store.get('configs', 'hybas') == 'custom: preserved\n'
    chosen = dispatch(store, 'use_downloaded_database', {'id': payload['id']})
    assert chosen['database_check']['path'] == str(Path(payload['destination']) / 'standard')
    assert store.get('configs', 'hybas') == 'custom: preserved\n'
    (Path(payload['destination']) / 'standard/hash.k2d').unlink()
    with pytest.raises(ValueError, match='hash.k2d'):
        dispatch(store, 'use_downloaded_database', {'id': payload['id']})


def test_duplicate_resume_is_not_queued_twice(installed, tmp_path):
    store, _ = installed
    payload = m.download_request(store, 'hybas', 'kraken', str(tmp_path))
    store.submit('database_download', payload)
    with pytest.raises(ValueError, match='already queued'):
        store.submit('database_download', payload)


@pytest.mark.parametrize('host', ['127.0.0.1', '172.21.21.143'])
def test_launcher_preserves_requested_listen_address(tmp_path, monkeypatch, host):
    import sys
    from bactologix.cli import main
    calls = []
    monkeypatch.setattr(sys, 'argv', ['bactologix', '--home', str(tmp_path), '--host', host, '--port', '8502'])
    monkeypatch.setattr(subprocess, 'call', lambda argv: calls.append(argv) or 0)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert calls[0][calls[0].index('--server.address') + 1] == host
    assert calls[0][calls[0].index('--browser.serverAddress') + 1] == host
    assert calls[0][calls[0].index('--server.port') + 1] == '8502'


def test_scanner_enters_hidden_cache_and_reports_incomplete_match(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    root = tmp_path / 'native checkout'
    card = root / '.snakemake/conda/hash/lib/python3.11/site-packages/rgi/database'
    card.mkdir(parents=True)
    (card / 'card.json').write_text('{"software_fixture":true}')
    incomplete = root / 'incomplete amrfinder'
    incomplete.mkdir()
    (incomplete / 'AMRProt.fa').write_text('software fixture\n')
    (root / 'cycle').symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(databases, '_roots', lambda *args: [root])
    scan = databases.scan(store, 'basis')
    assert scan['candidates']['rgi'][0]['path'] == str(card / 'card.json')
    rejected = [r for r in scan['invalid'] if r['id'] == 'amrfinder']
    assert len(rejected) == 1 and 'Missing or empty' in rejected[0]['message']
    assert scan['visited'] < 30


def test_scanner_follows_database_alias_with_bounded_cycles(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    root = tmp_path / 'root'
    target = tmp_path / 'separate volume'
    (target / 'nested/card').mkdir(parents=True)
    (target / 'nested/card/card.json').write_text('{}')
    root.mkdir()
    (root / 'database-link').symlink_to(target, target_is_directory=True)
    (target / 'loop').symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(databases, '_roots', lambda *args: [root])
    scan = databases.scan(store, 'basis')
    assert scan['candidates']['rgi'][0]['path'] == str(target / 'nested/card/card.json')
    assert scan['visited'] < 10
