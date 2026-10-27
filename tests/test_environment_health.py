import json
from pathlib import Path

import pytest

from bactologix import environment_health as h, maintenance as m
from bactologix.engine import Cancelled
from test_maintenance import installed, ready_environment, FakeContext


def metadata(prefix, **versions):
    (prefix / 'conda-meta').mkdir(parents=True, exist_ok=True)
    for name, version in versions.items():
        (prefix / 'conda-meta' / (name + '.json')).write_text(json.dumps({'name': name, 'version': version}))


def test_check_runs_actual_renderer_without_changing_packages(installed, monkeypatch):
    store, item = installed
    prefix = ready_environment(store, item, 1)
    row = m.recipes(item)[1]
    monkeypatch.setattr(h, 'plotting_recipe', lambda _: True)
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['qc'], 'check'))
    m.execute_environments(ctx)
    assert any(str(h.DATA / 'plot_probe.py') in argv for argv in ctx.calls)
    assert not any('install' in argv or '--conda-create-envs-only' in argv for argv in ctx.calls)
    assert 'rendering passed' in store.get('environment_checks', item['id'])['qc']['message']


def test_known_plotting_conflict_is_repaired_and_probed(installed, monkeypatch):
    store, item = installed
    prefix = ready_environment(store, item, 1)
    metadata(prefix, nanoplot='1.46.2', multiqc='1.32', plotly='7.1.0', **{'python-kaleido': '0.2.1'})
    monkeypatch.setattr(h, 'plotting_recipe', lambda _: True)
    monkeypatch.setattr(h, 'find_conda', lambda _: '/fixture/conda')
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['qc'], 'repair'))
    original = ctx.command
    def command(argv, **kwargs):
        result = original(argv, **kwargs)
        if str(h.DATA / 'plot_probe.py') in argv and h.package_versions(prefix)['plotly'].startswith('7'):
            raise RuntimeError('create_2d_density missing')
        if 'install' in argv:
            assert '--freeze-installed' in argv and 'plotly=6.1.2' in argv and 'python-kaleido=0.2.1' in argv
            metadata(prefix, plotly='6.1.2')
        return result
    ctx.command = command
    m.execute_environments(ctx)
    assert store.get('environment_checks', item['id'])['qc']['status'] == 'Verified'
    receipt = json.loads((store.home / 'maintenance' / ctx.job['id'] / 'qc-repair.json').read_text())
    assert receipt['before']['plotly'] == '7.1.0' and receipt['after']['plotly'] == '6.1.2'
    assert receipt['status'] == 'verified'
    assert not any('--conda-create-envs-only' in argv for argv in ctx.calls)


def test_healthy_repair_makes_no_changes_and_unknown_failure_is_retained(installed):
    store, item = installed
    prefix = ready_environment(store, item, 0)
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['assembly'], 'repair'))
    m.execute_environments(ctx)
    assert len(ctx.calls) == 2
    original = ctx.command
    def broken(argv, **kwargs):
        if 'bash' in argv:
            raise RuntimeError('Unknown broken tool')
        return original(argv, **kwargs)
    ctx2 = FakeContext(store, ctx.job['payload'])
    ctx2.command = broken
    with pytest.raises(RuntimeError, match='Environment checks failed'):
        m.execute_environments(ctx2)
    assert prefix.exists() and prefix.with_suffix('.env_setup_done').is_file()
    assert store.get('environment_checks', item['id'])['assembly']['status'] == 'Repair failed'


@pytest.mark.parametrize('outcome', ['success', 'failed', 'cancelled'])
def test_incomplete_environment_is_retained_and_restored_on_failure(installed, monkeypatch, outcome):
    store, item = installed
    prefix = ready_environment(store, item, 0)
    prefix.with_suffix('.env_setup_done').unlink()
    (prefix / 'keep.txt').write_text('original incomplete installation')
    row = m.recipes(item)[0]
    ctx = FakeContext(store, m.environment_request(store, item['id'], ['assembly'], 'repair'))
    folder = store.home / 'maintenance' / ctx.job['id']
    folder.mkdir(parents=True)
    monkeypatch.setattr(h, 'probe', lambda *args: None)
    def deploy():
        assert not prefix.exists()
        (prefix / 'conda-meta').mkdir(parents=True)
        (prefix / 'conda-meta/history').write_text('new installation')
        prefix.with_suffix('.env_setup_done').touch()
        if outcome == 'failed':
            raise RuntimeError('Failed new deployment')
        if outcome == 'cancelled':
            raise Cancelled('Cancelled new deployment')
    if outcome == 'success':
        h.repair(ctx, row, prefix, folder, deploy)
        backups = list(prefix.parent.glob('.bactologix-repair-backups/*/assembly/keep.txt'))
        assert len(backups) == 1 and backups[0].read_text() == 'original incomplete installation'
        assert not (prefix / 'keep.txt').exists()
    else:
        with pytest.raises((RuntimeError, Cancelled)):
            h.repair(ctx, row, prefix, folder, deploy)
        assert (prefix / 'keep.txt').read_text() == 'original incomplete installation'
        assert not prefix.with_suffix('.env_setup_done').exists()


def test_repair_rejects_unknown_action_and_symbolic_link(installed, tmp_path):
    store, item = installed
    with pytest.raises(ValueError):
        m.environment_request(store, item['id'], ['qc'], 'delete')
    row = m.recipes(item)[0]
    cache = m.cache_path(store, item)
    cache.mkdir()
    other = cache / ('b' * 32 + '_')
    other.mkdir()
    link = cache / ('a' * 32 + '_')
    link.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic link'):
        m.parse_environment_list(row['recipe'] + '\t\t' + str(link), tmp_path, [row], cache)
