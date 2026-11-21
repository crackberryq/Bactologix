"""Backend integration checks for reviewed destructive and replacement actions."""
from pathlib import Path

import pytest

from bactologix import database_redownload, factory_reset
from bactologix.ui_backend import dispatch
from test_database_redownload import manual_database, reviewed, complete_fixture
from test_factory_reset import store, run
from test_maintenance import installed, FakeContext


@pytest.mark.parametrize('state', ['running', 'missing_receipt', 'succeeded'])
def test_replacement_selection_requires_successful_job_and_receipt_without_saving_defaults(manual_database, state):
    workspace, _, original, _, config = manual_database
    payload = reviewed(workspace)
    context = FakeContext(workspace, payload, 'database_redownload')
    context.command = complete_fixture(payload)
    database_redownload.execute(context)
    row = workspace.get('database_downloads', payload['id'])
    assert row['status'] == 'completed'
    workspace.update_job(context.job['id'], status='running' if state == 'running' else 'succeeded')
    if state == 'missing_receipt':
        row.pop('replacement_receipt')
        workspace.put('database_downloads', row['id'], row)
    if state == 'succeeded':
        response = dispatch(workspace, 'use_downloaded_database', {'id': payload['id']})
        assert response['database_check']['path'] == str(Path(payload['destination']) / 'standard')
        assert 'Save locations' in response['message']
    else:
        with pytest.raises(ValueError, match='finish successfully|receipt is not available'):
            dispatch(workspace, 'use_downloaded_database', {'id': payload['id']})
    assert workspace.get('configs', 'hybas') == config
    assert all(path.read_bytes() == b'preserve original user database\n' for path in original.iterdir())


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
