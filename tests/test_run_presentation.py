"""Display the actual saved Kraken option without rewriting the frozen configuration."""
import pytest

from test_native import workspace, installation, sample
from bactologix.runs import create_run
from bactologix.ui_backend import snapshot


@pytest.mark.parametrize('config,expected', [
    ('kraken_decision_mode: manual\nrun_kraken: true\n', 'manual'),
    ('kraken_decision_mode: auto\nrun_kraken: true\n', 'auto'),
    ('kraken_decision_mode: aware\nrun_kraken: true\n', 'aware'),
    ('kraken_decision_mode: aware\nrun_kraken: false\n', 'off'),
    ('threads: 1\n', None),
])
def test_run_list_retains_distinct_modes(workspace, installation, sample, config, expected):
    run = create_run(workspace, 'hybas', sample, config)
    view, = snapshot(workspace)['runs']
    assert view['kraken_mode'] == expected
    assert workspace.get('runs', run['id']) == run
