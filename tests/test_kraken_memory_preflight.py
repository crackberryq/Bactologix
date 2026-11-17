import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bactologix import preflight


def fixture(tmp_path, size=98_605_320_723):
    native = tmp_path / 'native-db'
    native.mkdir()
    (native / 'opts.k2d').write_bytes(b'opts')
    (native / 'taxo.k2d').write_bytes(b'taxo')
    with (native / 'hash.k2d').open('wb') as handle:
        handle.truncate(size - 8)  # Sparse size fixture, never a real Kraken index.
    alias = tmp_path / 'alias'
    alias.symlink_to(native, target_is_directory=True)
    run = {'tool': 'hybas', 'folder': str(tmp_path), 'output': str(tmp_path / 'outputs'),
           'cores': 14, 'memory_mb': 46080, 'config': yaml.safe_dump({
               'db_root': str(tmp_path), 'kraken_db_name': 'alias', 'run_kraken': True,
               'bactologix_compute': {'swap_mb': 69632}})}
    info = {'cpu': {'available': 14}, 'checked_at': 'fixture', 'memory': {
        'total_mb': 49 * 1024, 'available_mb': 49 * 1024, 'swap_available_mb': 72 * 1024,
        'combined_total_mb': 121 * 1024, 'combined_available_mb': 121 * 1024}}
    return run, info, native


def test_large_native_database_warns_with_swap_without_blocking_or_rewriting(tmp_path, monkeypatch):
    run, info, native = fixture(tmp_path)
    before = copy.deepcopy(run)
    monkeypatch.setattr(preflight.hardware, 'inventory', lambda *a, **k: info)
    monkeypatch.setattr(preflight.hardware, 'detect', lambda *a, **k: info)
    measured = preflight.kraken_memory(run, info)
    assert measured['core_bytes'] == 98_605_320_723
    assert measured['estimated_full_load_mb'] == 113868
    assert measured['physical_ram_mb'] == 49 * 1024
    assert measured['selected_path'].endswith('/alias') and measured['resolved_path'] == str(native)
    assert all(Path(item['resolved_path']).parent == native for item in measured['files'])
    assert 'severe paging' in measured['text'] and 'not measured resident memory' in measured['text']
    panel = preflight.panel(None, run)
    assert not panel['blocked']
    assert next(row for row in panel['rows'] if row['id'] == 'kraken_memory')['level'] == 'warning'
    preflight.assert_launch(run, 'run')  # Explicitly opted-in, sufficient swap remains allowed.
    messages = []
    preflight.log_kraken_warnings(SimpleNamespace(store=None, write=messages.append), run)
    assert len(messages) == 1 and 'severe paging' in messages[0]
    assert run == before


def test_capacity_observation_never_reads_or_recursively_scans_database(tmp_path, monkeypatch):
    run, info, _ = fixture(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('Capacity check must only stat known index files')
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'glob', forbidden)
    monkeypatch.setattr(Path, 'rglob', forbidden)
    assert preflight.kraken_memory(run, info)['core_bytes'] == 98_605_320_723


@pytest.mark.parametrize('kind', ['disabled', 'off', 'basis'])
def test_unused_kraken_does_not_probe_database(tmp_path, monkeypatch, kind):
    run, info, _ = fixture(tmp_path, size=100)
    cfg = yaml.safe_load(run['config'])
    if kind == 'disabled':
        cfg['run_kraken'] = False
    elif kind == 'off':
        cfg['kraken_decision_mode'] = 'off'
    else:
        run['tool'] = 'basis'
    run['config'] = yaml.safe_dump(cfg)
    monkeypatch.setattr(Path, 'resolve', lambda *a, **k: pytest.fail('Unused DB probed'))
    assert preflight.kraken_memory(run, info) is None


def test_small_missing_and_unknown_ram_are_not_mislabelled_as_large_db(tmp_path):
    run, info, native = fixture(tmp_path, size=100)
    assert preflight.kraken_memory(run, info)['level'] == 'info'
    info['memory']['total_mb'] = None
    assert preflight.kraken_memory(run, info)['state'] == 'RAM comparison unavailable'
    (native / 'taxo.k2d').unlink()
    incomplete = preflight.kraken_memory(run, info)
    assert incomplete['state'] == 'Size unavailable' and 'core_bytes' not in incomplete
