"""Host checks do not write probe files or tune frozen run settings."""
import copy
from pathlib import Path
from collections import namedtuple

import pytest

from test_native import workspace, installation, sample
from bactologix import preflight, readiness
from bactologix.runs import create_run, validate_launch


def host(cores=4, total=8192, available=6144):
    return {'cpu': {'available': cores}, 'memory': {'total_mb': total, 'available_mb': available},
            'checked_at': '2026-10-27T00:00:00+00:00'}


def test_destinations_inspect_missing_parents_without_writing(tmp_path):
    target = tmp_path / 'new/results'
    result = preflight.destination(target, 'Results')
    assert result['level'] == 'info' and result['checked_parent'] == str(tmp_path)
    assert result['free_mb'] is not None
    assert not target.parent.exists()
    file = tmp_path / 'file'
    file.write_text('keep')
    assert preflight.destination(file / 'results', 'Results')['level'] == 'error'
    assert file.read_text() == 'keep'
    link = tmp_path / 'broken'
    link.symlink_to(tmp_path / 'absent')
    assert preflight.destination(link / 'results', 'Results')['level'] == 'error'


@pytest.mark.parametrize('failure', ['permission', 'readonly', 'full'])
def test_unusable_destination_blocks_queue_and_worker_launch(workspace, installation, sample, monkeypatch, failure):
    run = create_run(workspace, 'hybas', sample, 'threads: 1\nrun_busco: false\n')
    original = copy.deepcopy(run)
    if failure == 'permission':
        monkeypatch.setattr(preflight.os, 'access', lambda *_: False)
    elif failure == 'readonly':
        monkeypatch.setattr(preflight.os, 'statvfs', lambda _: type('ReadOnly', (), {'f_flag': 1})())
    else:
        usage = namedtuple('Usage', 'total used free')
        monkeypatch.setattr(preflight.shutil, 'disk_usage', lambda _: usage(10000, 10000, 0))
    for check_busy in (True, False):
        with pytest.raises(ValueError, match='Pre-run checks:'):
            validate_launch(workspace, run['id'], 'dry-run', check_busy=check_busy)
    assert workspace.get('runs', run['id']) == original


def test_output_failure_allows_diagnostic_dry_run_but_blocks_execution(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, 'threads: 1\n')
    run['dry_run_ok'] = True
    workspace.put('runs', run['id'], run)
    access = preflight.os.access
    monkeypatch.setattr(preflight.os, 'access', lambda p, mode: False if str(p) == run['output'] else access(p, mode))
    monkeypatch.setattr(preflight.hardware, 'inventory', lambda _: host())
    panel = readiness.panel(workspace, run)
    assert not panel['blocked'] and panel['execution_blocked'] and panel['validated']
    assert panel['next'] == 'review'
    validate_launch(workspace, run['id'], 'dry-run')
    with pytest.raises(ValueError, match='Results folder'):
        validate_launch(workspace, run['id'], 'run')


def test_low_disk_and_oversubscription_warn_without_rewriting(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample,
                     'assembly_mem_mb: 24000\nspades_memory_gb: 24\npolish_mode: none\n'
                     'polishing_mem_mb: 32000\nrun_kraken: false\nkraken_mem_mb: 64000\n', 12, 16000)
    original = copy.deepcopy(run)
    usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(preflight.shutil, 'disk_usage', lambda _: usage(10 * preflight.MIB, 0, 2 * preflight.MIB))
    monkeypatch.setattr(preflight.hardware, 'inventory', lambda _: host())
    panel = preflight.panel(workspace, run)
    assert not panel['blocked']
    rows = {r['id']: r for r in panel['rows']}
    assert rows['storage']['level'] == 'warning' and rows['resources']['level'] == 'warning'
    assert '12 CPU threads' in rows['resources']['text']
    assert 'assembly_mem_mb' in rows['resources']['text'] and 'spades_memory_gb' in rows['resources']['text']
    assert 'polishing_mem_mb' not in rows['resources']['text'] and 'kraken_mem_mb' not in rows['resources']['text']
    validate_launch(workspace, run['id'], 'dry-run')
    assert run == original and workspace.get('runs', run['id']) == original


def test_unknown_ram_and_basis_explicit_limits():
    run = {'tool': 'basis', 'cores': 2, 'memory_mb': 2048,
           'config': 'modules:\n  example: {enabled: true, memory_mb: 4096}\n  off: {enabled: false, memory_mb: 5000}\ndeep: {enabled: false, memory_mb: 6000}\n'}
    warnings = ' '.join(preflight.budget(run, host(total=None, available=None)))
    assert 'could not be detected' in warnings and 'modules.example.memory_mb' in warnings
    assert 'modules.off' not in warnings and 'deep.memory_mb' not in warnings
    run['config'] = 'modules: []\n'
    assert preflight.budget(run, host()) == []


def test_cache_write_is_checked_only_for_explicit_install(workspace, installation, sample, monkeypatch, tmp_path):
    run = create_run(workspace, 'hybas', sample, 'threads: 1\n')
    run['rule_cache'] = str(tmp_path / 'cache')
    Path(run['rule_cache']).mkdir()
    access = preflight.os.access
    monkeypatch.setattr(preflight.os, 'access', lambda p, mode: False if str(p) == run['rule_cache'] else access(p, mode))
    preflight.assert_launch(run, 'run')
    with pytest.raises(ValueError, match='Tool environment cache'):
        preflight.assert_launch(run, 'prepare')


def test_basis_explicit_output_is_checked_but_database_is_not(tmp_path):
    (tmp_path / 'workflow').mkdir()
    (tmp_path / 'outputs').mkdir()
    run = {'tool': 'basis', 'folder': str(tmp_path), 'output': str(tmp_path / 'outputs'),
           'config': f'storage: {{native_results_directory: {tmp_path}/external}}\nmodules: {{example: {{database: /read-only/input}}}}\n'}
    paths = preflight.destinations(run)
    assert any(p['path'].endswith('/external') for p in paths)
    assert not any('read-only/input' in p['path'] for p in paths)
    assert not (tmp_path / 'external').exists()
