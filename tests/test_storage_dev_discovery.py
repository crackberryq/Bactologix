"""Dev cache isolation must retain discovery of the original stable workspace."""
import json
from pathlib import Path

import pytest
from platformdirs import user_data_dir

from bactologix import channels, storage_cleanup
from bactologix.store import Store


def test_dev_cache_environment_preserves_stable_default_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'user'))
    monkeypatch.delenv('BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'original-user-data'))
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'stable')
    stable = Store(Path(user_data_dir('bactologix')))
    inputs = tmp_path / 'shared-inputs'
    inputs.mkdir()
    reads = inputs / 'reads.fastq'
    reads.write_text('synthetic storage fixture\n')
    sample = {'id': 'shared', 'path': str(inputs), 'inputs': [{'path': str(reads)}]}
    stable.put('samples', sample['id'], sample)

    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
    dev = Store(tmp_path / 'other-volume' / 'dev')
    dev.put('samples', sample['id'], sample)
    assert stable.home.parent != dev.home.parent
    assert str(stable.home) in storage_cleanup._other_workspaces(dev)[1]

    first = channels.environment(dev.home)
    for key, value in first.items():
        monkeypatch.setenv(key, value)
    assert Path(user_data_dir('bactologix')) == dev.home / 'data' / 'bactologix'
    defaults = json.loads(first['BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS'])
    assert str(stable.home) in defaults
    assert str(dev.home / 'data' / 'bactologix') not in defaults

    # The worker and reopened UI inherit the redirected XDG environment. They
    # must keep the original defaults rather than replacing them with Dev/data.
    reopened = Store(dev.home)
    assert channels.environment(reopened.home)['BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS'] == first['BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS']
    panel = storage_cleanup.panel(reopened)
    assert str(stable.home) in panel['checked_workspaces']
    candidate = next(row for row in panel['candidates'] if row['kind'] == 'sample_files')
    assert any(str(stable.home) in reason for reason in candidate['blocked'])
    preview = storage_cleanup.preview(reopened, [{'kind': 'sample_files', 'id': sample['id']}])
    assert any(str(stable.home) in reason for reason in preview['blocked'])
    assert reads.read_text() == 'synthetic storage fixture\n'
    assert stable.get('samples', sample['id']) == sample
    assert not stable.jobs() and not reopened.jobs()


@pytest.mark.parametrize('encoded', ['', 'not-json', '{"path":"/workspace"}', '["relative"]'])
def test_invalid_preserved_discovery_fails_before_creating_caches(tmp_path, monkeypatch, encoded):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
    monkeypatch.setenv('BACTOLOGIX_STORAGE_DISCOVERY_DEFAULTS', encoded)
    dev = Store(tmp_path / 'dev')
    with pytest.raises(ValueError, match='Preserved workspace discovery defaults'):
        channels.environment(dev.home)
    assert not (dev.home / 'cache').exists()
