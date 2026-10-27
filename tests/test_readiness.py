"""Run checks describe evidence without installing tools or changing frozen runs."""
import copy
from pathlib import Path

import pytest

from test_native import workspace, installation, sample
from bactologix import readiness as r
from bactologix.runs import create_run, execute_workflow, validate_launch


def make_run(store, sample):
    return create_run(store, 'hybas', sample, 'threads: 1\nrun_busco: false\n')


def inventory(folder, cache):
    recipe = folder / 'software fixture.yaml'
    recipe.write_text('dependencies: [python=3.11]\n')
    prefix = cache / ('a' * 32)
    text = 'environment\tcontainer\tlocation\n' + str(recipe) + '\t\t' + str(prefix) + '\n'
    return text, prefix


def test_checks_do_not_claim_full_readiness_or_mutate_defaults(workspace, installation, sample):
    run = make_run(workspace, sample)
    original = copy.deepcopy(run)
    workspace.put('configs', 'hybas', 'db_root: /new/defaults\n')
    panel = r.panel(workspace, run)
    assert panel['next'] == 'dry-run' and not panel['blocked']
    states = {row['id']: row['state'] for row in panel['rows']}
    assert states == {'inputs': 'Unchanged', 'pipeline': 'Setup recorded',
                      'configuration': 'Validation needed', 'databases': 'Frozen in this draft',
                      'environments': 'Check this run’s cache'}
    run['dry_run_ok'] = True
    assert r.panel(workspace, run)['next'] == 'check-environments'
    assert workspace.get('runs', run['id']) == original
    assert workspace.get('configs', 'hybas') == 'db_root: /new/defaults\n'


@pytest.mark.parametrize('change', ['input', 'config', 'source', 'runner'])
def test_drift_blocks_old_validation(workspace, installation, sample, change):
    run = make_run(workspace, sample)
    run['dry_run_ok'] = True
    if change == 'input':
        Path(sample['path'], 'raw/illumina/sample_R1.fastq').write_text('changed')
    elif change == 'config':
        Path(run['folder'], 'config.yaml').write_text('changed: true\n')
    elif change == 'source':
        Path(run['folder'], 'workflow/Hybas.smk').write_text('changed source')
    else:
        workspace.put('installs', installation['id'], {**installation, 'ready': False})
    panel = r.panel(workspace, run)
    assert panel['blocked'] and not panel['validated'] and panel['next'] == 'review'


def test_inventory_uses_native_paths_and_handles_empty_plan(tmp_path):
    folder, cache = tmp_path / 'workflow', tmp_path / 'cache'
    folder.mkdir()
    text, prefix = inventory(folder, cache)
    rows = r.parse_inventory('Native preamble\n' + text, folder, cache)
    assert rows[0]['path'] == str(prefix)
    assert r.environment_state(rows[0]) == 'Missing'
    assert not cache.exists()
    assert r.parse_inventory('environment\tcontainer\tlocation\n', folder, cache) == []
    assert r.parse_inventory(text.replace(str(folder) + '/', ''), folder, cache) == rows


@pytest.mark.parametrize('change', ['header', 'container', 'source', 'cache', 'hash', 'malformed'])
def test_untrusted_inventory_fails_closed(tmp_path, change):
    folder, cache = tmp_path / 'workflow', tmp_path / 'cache'
    folder.mkdir()
    text, prefix = inventory(folder, cache)
    if change == 'header': text = text.split('\n', 1)[1]
    elif change == 'container': text = text.replace('\t\t', '\tcontainer.sif\t')
    elif change == 'source': text = text.replace(str(folder / 'software fixture.yaml'), '/outside.yaml')
    elif change == 'cache': text = text.replace(str(prefix), str(tmp_path / prefix.name))
    elif change == 'malformed': text = text.replace('\t\t', '\t')
    else: text = text.replace(prefix.name, 'not-a-native-prefix')
    with pytest.raises(ValueError): r.parse_inventory(text, folder, cache)


def test_receipts_expire_and_live_completion_is_rechecked(workspace, installation, sample, tmp_path):
    run = make_run(workspace, sample)
    run['dry_run_ok'] = True
    prefix = tmp_path / 'cache' / ('f' * 32)
    receipt = {'id': run['id'], 'identity': r.identity(workspace, run), 'status': 'checked',
               'checked_at': '2026-10-27T00:00:00+00:00', 'rows': [{'name': 'fixture', 'path': str(prefix)}]}
    workspace.put('run_environment_checks', run['id'], receipt)
    assert r.panel(workspace, run)['next'] == 'prepare'
    (prefix / 'conda-meta').mkdir(parents=True)
    history = prefix / 'conda-meta/history'
    history.write_text('retained software fixture')
    assert r.panel(workspace, run)['environments'][0]['state'] == 'Incomplete'
    prefix.with_suffix('.env_setup_done').touch()
    assert r.panel(workspace, run)['next'] == 'run'
    prefix.with_suffix('.env_setup_done').unlink()
    assert r.panel(workspace, run)['missing_environments'] == 1
    assert history.read_text() == 'retained software fixture'
    for field in ('rule_cache', 'config_sha256', 'workflow_sha256'):
        changed = {**run, field: 'different'}
        assert not r.panel(workspace, changed)['environment_check_current']
    installation['controller'] = '/replacement/runner'
    workspace.put('installs', installation['id'], installation)
    assert not r.panel(workspace, run)['environment_check_current']


def test_explicit_inventory_job_is_gated_and_preserves_run(workspace, installation, sample, monkeypatch):
    run = make_run(workspace, sample)
    with pytest.raises(ValueError, match='dry-run'):
        validate_launch(workspace, run['id'], 'check-environments')
    run['dry_run_ok'] = True
    workspace.put('runs', run['id'], run)
    validate_launch(workspace, run['id'], 'check-environments')
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/fixture/conda')
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'check-environments'})
    workspace.update_job(jid, status='running')
    class Context:
        store, job = workspace, workspace.job(jid)
        def command(self, argv, *, cwd, stdout):
            assert '--list-conda-envs' in argv
            assert '--conda-create-envs-only' not in argv
            assert not any('run_existing.py' in arg for arg in argv)
            stdout.write('environment\tcontainer\tlocation\n')
        def write(self, text): pass
        def check_cancel(self): pass
    execute_workflow(Context())
    assert workspace.get('runs', run['id']) == run
    assert r.panel(workspace, run)['environment_check_current']
    assert r.panel(workspace, run)['rows'][-1]['state'] == 'None listed'
    class Failed(Context):
        def command(self, *args, **kwargs): raise RuntimeError('fixture failure')
    with pytest.raises(RuntimeError): execute_workflow(Failed())
    assert not r.panel(workspace, run)['environment_check_current']
    assert workspace.get('runs', run['id']) == run


@pytest.mark.parametrize('message,category', [
    ('BUSCO lineage database bacteria_odb10 missing at /fixture', 'databases'),
    ('Bactologix existing environments only: cache missing', 'environments'),
    ('Input changed since draft creation', 'inputs'),
    ('unrecognized failure <script>literal</script>', 'log'),
])
def test_failed_jobs_offer_scoped_guidance(workspace, message, category):
    jid = workspace.submit('workflow', {'run_id': 'run-fixture', 'action': 'run'})
    assert r.job_guidance(workspace, workspace.job(jid)) is None
    workspace.log_path(jid).write_text('ignored old database missing\n' + ('x' * 25000) + '\n' + message)
    workspace.update_job(jid, status='failed')
    guidance = r.job_guidance(workspace, workspace.job(jid))
    assert guidance['category'] == category and guidance['run_id'] == 'run-fixture'
    assert '<script>' not in guidance['text']
