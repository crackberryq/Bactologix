"""Release-specific runner provisioning using software-only fixtures."""
from pathlib import Path
import sys
import types

import pytest

from bactologix import engine
from bactologix.controllers import controller_check_code, controller_requirements
from bactologix.store import Store
from bactologix.ui_backend import installation_status


@pytest.mark.parametrize('dependency', ['snakemake=9.27.1', 'snakemake-minimal=9.27.1',
                                      'bioconda::snakemake-minimal==9.27.1=py_0'])
def test_recipe_selects_declared_versions(dependency):
    result = controller_requirements(f'dependencies: [python=3.12, "{dependency}", pyyaml]\n')
    assert result == {'python': '3.12', 'snakemake': '9.27.1'}


@pytest.mark.parametrize('dependencies', [
    [], ['python=3.11'], ['snakemake=9.6.2'], ['python', 'snakemake=9.6.2'],
    ['python>=3.11', 'snakemake=9.6.2'], ['python=3.11', 'snakemake>=9.6.2'],
    ['python=3.11', 'snakemake=9.6'], ['python=3.11', 'snakemake=9.6.2.*'],
    ['python=3.11', 'python=3.12', 'snakemake=9.6.2'],
    ['python=3.11', 'snakemake=9.6.2', 'snakemake-minimal=9.27.1'],
])
def test_incomplete_unpinned_or_conflicting_requirements_fail(dependencies):
    import yaml
    with pytest.raises(ValueError, match='Runner environment'):
        controller_requirements(yaml.safe_dump({'dependencies': dependencies}))


@pytest.mark.parametrize('content', ['[]', 'null', 'dependencies: nope', 'dependencies: [}'])
def test_bad_environment_definition(content):
    with pytest.raises(ValueError):
        controller_requirements(content)


@pytest.mark.parametrize('actual_python,actual_snake,expected_error', [
    ((3, 12, 7), '9.27.1', None),
    ((3, 11, 9), '9.27.1', 'Required Python'),
    ((3, 12, 7), '9.6.2', 'Required Snakemake'),
])
def test_probe_checks_actual_versions_even_with_optimization(monkeypatch, actual_python, actual_snake, expected_error):
    monkeypatch.setattr(sys, 'version_info', actual_python)
    monkeypatch.setitem(sys.modules, 'snakemake', types.SimpleNamespace(__version__=actual_snake))
    code = compile(controller_check_code('hybas', {'python': '3.12', 'snakemake': '9.27.1'}), '<probe>', 'exec', optimize=2)
    if expected_error:
        with pytest.raises(RuntimeError, match=expected_error):
            exec(code, {})
    else:
        exec(code, {})


def test_exact_python_patch_is_enforced(monkeypatch):
    requirements = controller_requirements('dependencies: [python=3.11.9, snakemake=9.6.2]')
    monkeypatch.setattr(sys, 'version_info', (3, 11, 10))
    with pytest.raises(RuntimeError, match='Required Python'):
        exec(controller_check_code('hybas', requirements), {})


class Setup:
    def __init__(self, store, fail=None):
        self.store = store
        self.job = {'id': 'job-fixture', 'payload': {}}
        self.calls = []
        self.output = []
        self.fail = fail

    def write(self, message):
        self.output.append(message)

    def command(self, argv, **kwargs):
        self.calls.append(argv)
        create = argv[1:3] == ['env', 'create']
        if self.fail == ('create' if create else 'check'):
            raise RuntimeError('fixture failure')
        if create:
            Path(argv[argv.index('--prefix') + 1]).mkdir(parents=True)


def record(tmp_path, snake='9.27.1', key='new'):
    source = tmp_path / key
    source.mkdir()
    (source / 'environment.yaml').write_text(f'dependencies: [python=3.12, snakemake-minimal={snake}, pyyaml]\n')
    return {'id': key, 'tool': 'hybas', 'source': str(source), 'ready': False, 'controller': None}


def test_new_version_installs_its_own_runner_and_retains_old(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    old = record(tmp_path, '9.6.2', 'old')
    old_prefix = store.home / 'controllers/old'
    old_prefix.mkdir()
    (old_prefix / 'sentinel').write_text('existing runner')
    old.update(ready=True, controller=str(old_prefix))
    store.put('installs', 'old', old)
    store.put('settings', 'active-hybas', 'old')
    item = record(tmp_path)
    monkeypatch.setattr(engine, 'find_conda', lambda _: '/fixture/conda')
    ctx = Setup(store)
    engine.prepare_controller(ctx, item)
    assert ctx.calls[0][1:3] == ['env', 'create']
    assert ctx.calls[0][-1] == str(Path(item['source']) / 'environment.yaml')
    assert '9.27.1' in ctx.calls[1][-1] and '(3, 12)' in ctx.calls[1][-1]
    assert item['ready'] and item['controller'] != str(old_prefix)
    assert item['controller_requirements'] == {'python': '3.12', 'snakemake': '9.27.1'}
    assert store.get('settings', 'active-hybas') == 'new'
    assert store.get('installs', 'old') == old
    assert (old_prefix / 'sentinel').read_text() == 'existing runner'
    # Same recipe reuses its runner, after checking it again.
    retry = Setup(store)
    engine.prepare_controller(retry, item)
    assert len(retry.calls) == 2 and all(argv[1] == 'run' for argv in retry.calls)


def test_missing_conda_is_bootstrapped_before_runner_creation(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    item = record(tmp_path)
    found = {'conda': None}
    monkeypatch.setattr(engine, 'find_conda', lambda _: found['conda'])
    ctx = Setup(store)
    def bootstrap(context):
        assert not context.calls
        found['conda'] = '/fixture/local-conda'
    monkeypatch.setattr(engine, 'bootstrap', bootstrap)
    engine.prepare_controller(ctx, item)
    assert ctx.calls[0][0] == '/fixture/local-conda'
    assert item['ready']


@pytest.mark.parametrize('failure', ['create', 'check'])
def test_setup_failure_preserves_active_version(tmp_path, monkeypatch, failure):
    store = Store(tmp_path / 'workspace')
    store.put('settings', 'active-hybas', 'old')
    item = record(tmp_path)
    item['ready'] = True  # Even a failed retry cannot leave a stale Ready flag.
    monkeypatch.setattr(engine, 'find_conda', lambda _: '/fixture/conda')
    with pytest.raises(RuntimeError):
        engine.prepare_controller(Setup(store, failure), item)
    assert store.get('settings', 'active-hybas') == 'old'
    assert store.get('installs', 'new')['ready'] is False


def test_invalid_recipe_cannot_bootstrap_or_install(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    item = record(tmp_path)
    Path(item['source'], 'environment.yaml').write_text('dependencies: [python, snakemake]\n')
    monkeypatch.setattr(engine, 'bootstrap', lambda _: pytest.fail('Invalid recipe cannot bootstrap'))
    monkeypatch.setattr(engine, 'find_conda', lambda _: None)
    ctx = Setup(store)
    with pytest.raises(ValueError):
        engine.prepare_controller(ctx, item)
    assert not ctx.calls and not store.get('installs', 'new')['ready']


def test_cancellation_remains_cancellation(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    item = record(tmp_path)
    monkeypatch.setattr(engine, 'find_conda', lambda _: '/fixture/conda')
    def cancel(*args):
        raise engine.Cancelled('Stopped')
    monkeypatch.setattr(engine, 'check_controller', cancel)
    with pytest.raises(engine.Cancelled):
        engine.prepare_controller(Setup(store), item)
    assert not store.get('settings', 'active-hybas')


def test_setup_card_shows_verified_requirements():
    from bactologix.catalog import PIPELINES
    item = {'id': 'fixture', 'tool': 'hybas', 'ready': True,
            'version': {'commit': PIPELINES['hybas']['commit']},
            'controller_requirements': {'python': '3.12', 'snakemake': '9.27.1'}}
    message = installation_status(item, [])['setup_message']
    assert 'Python 3.12 / Snakemake 9.27.1' in message
