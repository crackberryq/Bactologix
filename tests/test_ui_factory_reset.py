"""Regular reset commands preserve fresh queue/execution boundaries."""
from pathlib import Path

import pytest

from bactologix import factory_reset
from bactologix.ui_backend import dispatch
from test_factory_reset import store, run


def test_queued_reset_blocks_new_work_and_database_reference_mutations(store):
    saved = run(store)
    plan = dispatch(store, 'factory_reset_preview', {'delete_databases': False})['reset_plan']
    response = dispatch(store, 'factory_reset_execute', {
        'plan_id': plan['id'], 'signature': plan['signature'],
        'confirmation': factory_reset.CONFIRMATION, 'acknowledged': True})
    job = store.job(response['job_id'])
    assert job['kind'] == 'factory_reset' and job['status'] == 'queued'
    assert Path(saved['folder']).exists()
    with pytest.raises(ValueError, match='cleanup is queued or running'):
        store.submit('smoke', {})
    for kind in ('database_locations', 'database_builds', 'reference_packages'):
        with pytest.raises(ValueError, match='cleanup is queued or running'):
            store.put(kind, 'new-reference', {'path': saved['output']})
