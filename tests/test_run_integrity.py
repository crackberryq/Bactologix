"""Regressions from the application audit; native commands are test doubles here."""
import copy
from pathlib import Path
import subprocess

import pytest

from test_native import workspace, installation, sample
from bactologix.catalog import PIPELINES
from bactologix.engine import prepare_controller, controller_key
from bactologix.runs import (check_inputs, clone_runs, command, create_run, dry_run_current,
                            execute_workflow, reports, validate_launch)
from bactologix.ui_backend import snapshot


def checked(workspace, installation, sample):
    run = create_run(workspace, 'hybas', sample, 'threads: 1\n')
    run['dry_run_ok'] = True
    workspace.put('runs', run['id'], run)
    return run


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('change', ['sheet', 'retarget', 'replace', 'remove', 'extra'])
def test_execution_rejects_changed_consumed_inputs(workspace, installation, sample, tmp_path, legacy, change):
    run = checked(workspace, installation, sample)
    if legacy:
        run.pop('input_layout')
        workspace.put('runs', run['id'], run)
    validate_launch(workspace, run['id'], 'run')
    staged = Path(run['output']) / 'raw/illumina/sample_R1.fastq'
    if change == 'sheet':
        Path(run['folder'], 'samples.tsv').write_text('isolate\nanother\n')
    elif change == 'retarget':
        other = tmp_path / 'other.fastq'
        other.write_text('different fixture')
        staged.unlink()
        staged.symlink_to(other)
    elif change == 'replace':
        content = staged.read_bytes()
        staged.unlink()
        staged.write_bytes(content)
    elif change == 'remove':
        staged.unlink()
    else:
        staged.with_name('extra.fastq').symlink_to(staged.resolve())
    with pytest.raises(ValueError, match='[Ss]taged|sample sheet'):
        validate_launch(workspace, run['id'], 'run')


@pytest.mark.parametrize('change', ['content', 'addition'])
def test_clone_does_not_recapture_changed_originals(workspace, installation, sample, change):
    parent = create_run(workspace, 'hybas', sample, 'threads: 1\n')
    source = Path(sample['path']) / 'raw/illumina/sample_R1.fastq'
    if change == 'content':
        source.write_text(source.read_text() + '\nchanged\n')
    else:
        source.with_name('new_lane.fastq').write_text(source.read_text())
    with pytest.raises(ValueError, match='Input changed|Original inputs differ'):
        clone_runs(workspace, parent['id'], False, ['none'])
    assert [r['id'] for r in workspace.all('runs')] == [parent['id']]
    assert list((workspace.home / 'runs').iterdir()) == [Path(parent['folder'])]


class Execution:
    def __init__(self, store, job, fail=False):
        self.store, self.job, self.fail = store, job, fail
        self.commands = []
    def write(self, message): pass
    def command(self, argv, **kwargs):
        self.commands.append(argv)
        if self.fail:
            raise RuntimeError('fixture failure')


def perform(store, run, action, fail=False):
    jid = store.submit('workflow', {'run_id': run['id'], 'action': action})
    store.update_job(jid, status='running')
    context = Execution(store, store.job(jid), fail)
    try:
        execute_workflow(context)
    except RuntimeError:
        store.update_job(jid, status='failed', error='fixture failure')
    else:
        store.update_job(jid, status='succeeded')
    return context


def test_existing_draft_uses_repaired_runner_after_new_dry_run(workspace, installation, sample, monkeypatch):
    source = Path(installation['source'])
    env = source / 'environment.yaml'
    env.write_text('name: fixture\ndependencies: []\n')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'environment'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', commit)
    installation['version']['commit'] = commit
    old = Path(installation['controller'])
    old.mkdir()
    installation['controller_digest'] = controller_key('hybas', env.read_bytes())
    workspace.put('installs', installation['id'], installation)
    run = checked(workspace, installation, sample)
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/fixture/conda')
    class Repair(Execution):
        def command(self, argv, **kwargs):
            if argv[1:3] == ['env', 'create']:
                Path(argv[argv.index('--prefix') + 1]).mkdir()
    prepare_controller(Repair(workspace, {'id': 'job-fixturerepair'}), copy.deepcopy(installation), repair=True)
    repaired = workspace.get('installs', installation['id'])
    assert repaired['ready'] and repaired['controller'] != str(old)
    assert not dry_run_current(workspace, run)
    assert not snapshot(workspace)['runs'][0]['dry_run_ok']
    with pytest.raises(ValueError, match='current runner'):
        validate_launch(workspace, run['id'], 'run')
    context = perform(workspace, run, 'dry-run')
    assert context.commands[0][context.commands[0].index('-p') + 1] == repaired['controller']
    accepted = validate_launch(workspace, run['id'], 'run')
    argv = command(workspace, accepted, 'run')
    assert argv[argv.index('-p') + 1] == repaired['controller']
    assert accepted['controller'] == str(old)  # creation provenance retained
    assert accepted['validated_controller'] == repaired['controller']


def test_failed_rerun_is_not_completed_by_dry_run(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, 'threads: 1\n')
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/fixture/conda')
    perform(workspace, run, 'dry-run')
    perform(workspace, run, 'run')
    assert snapshot(workspace)['runs'][0]['status'] == 'completed'
    output = Path(run['output'], 'reports/multiqc/multiqc_report.html')
    output.parent.mkdir(parents=True)
    output.write_text('<p>Earlier report</p>')
    perform(workspace, run, 'run', fail=True)
    perform(workspace, run, 'dry-run')
    assert snapshot(workspace)['runs'][0]['status'] == 'failed'
    assert reports(workspace)[0]['run_status'] == 'failed'
    saved = workspace.get('runs', run['id'])
    assert 'completed' not in saved and saved['previous_completed']
    perform(workspace, run, 'run')
    assert snapshot(workspace)['runs'][0]['status'] == 'completed'


def test_legacy_failed_rerun_status_survives_successful_check(workspace, installation, sample, monkeypatch):
    run = checked(workspace, installation, sample)
    run['completed'] = 'old completion'
    workspace.put('runs', run['id'], run)
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(jid, status='failed')
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/fixture/conda')
    perform(workspace, run, 'dry-run')
    assert snapshot(workspace)['runs'][0]['status'] == 'failed'
