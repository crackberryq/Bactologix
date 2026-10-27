"""Existing-only execution cannot delegate to Conda's creating/removing path."""
from pathlib import Path
import runpy
import sys
import types

import pytest

from bactologix.catalog import DATA
from bactologix.runs import command
from test_native import workspace, installation, sample
from test_run_integrity import checked


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    class WorkflowError(Exception): pass
    class Env:
        is_containerized = False
        address = str(tmp_path / 'cached-environment')
        def check_is_file_based(self): pass
        def create(self, dryrun=False): pytest.fail('Original environment creation must never be called')
    conda = types.SimpleNamespace(Env=Env, get_env_setup_done_flag_file=lambda p: p.with_suffix('.env_setup_done'))
    monkeypatch.setitem(sys.modules, 'snakemake.deployment', types.SimpleNamespace(conda=conda))
    monkeypatch.setitem(sys.modules, 'snakemake.exceptions', types.SimpleNamespace(WorkflowError=WorkflowError))
    module = runpy.run_path(str(DATA / 'env_guard/sitecustomize.py'))
    return Env, WorkflowError, module


@pytest.mark.parametrize('state', ['missing', 'incomplete', 'no-history', 'ready'])
def test_missing_and_incomplete_environments_are_retained_and_never_installed(guarded, state):
    Env, Error, module = guarded
    prefix = Path(Env.address)
    if state != 'missing':
        (prefix / 'conda-meta').mkdir(parents=True)
        (prefix / 'keep.txt').write_text('do not remove')
        if state != 'no-history': (prefix / 'conda-meta/history').write_text('installed fixture')
        if state != 'incomplete': prefix.with_suffix('.env_setup_done').touch()
    module['enforce_existing_environments']()
    module['enforce_existing_environments']()  # Child startup may encounter an already guarded import.
    if state == 'ready':
        assert Env().create() == str(prefix)
    else:
        with pytest.raises(Error, match='existing environments only'):
            Env().create()
    assert prefix.exists() == (state != 'missing')
    if prefix.exists(): assert (prefix / 'keep.txt').read_text() == 'do not remove'


def test_changed_native_api_fails_closed(guarded):
    Env, _, module = guarded
    Env.create = lambda self, new_required_argument: None
    with pytest.raises(RuntimeError, match='adapter review'):
        module['enforce_existing_environments']()


@pytest.mark.parametrize('argv,expected', [
    (['python', '-m', 'snakemake', '--cores', '1'], True),
    (['/runner/bin/snakemake', '--cores', '1'], True),
    (['/base/bin/conda', 'info', '--json'], False),
    (['python', 'analysis-script.py'], False),
])
def test_guard_applies_to_snakemake_children_not_conda_or_analysis_tools(monkeypatch, argv, expected):
    monkeypatch.setattr(sys, 'orig_argv', argv)
    monkeypatch.setattr(sys, 'argv', [argv[0]])
    module = runpy.run_path(str(DATA / 'env_guard/sitecustomize.py'))
    # runpy temporarily changes argv[0]; call the function after it restores it.
    assert module['snakemake_process']() == expected


def test_run_command_is_guarded_but_explicit_preparation_can_install(workspace, installation, sample, monkeypatch):
    monkeypatch.setattr('bactologix.engine.find_conda', lambda store: '/fixture/conda')
    run = checked(workspace, installation, sample)
    execute = command(workspace, run, 'run')
    assert execute[execute.index('python') + 1] == str(DATA / 'run_existing.py')
    assert execute[execute.index('--conda-prefix') + 1] == run['rule_cache']
    for action in ('prepare', 'dry-run'):
        assert str(DATA / 'run_existing.py') not in command(workspace, run, action)
    assert '--conda-create-envs-only' in command(workspace, run, 'prepare')
