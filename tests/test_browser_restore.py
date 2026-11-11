"""Archive recovery stays separate from permanent deletion and native unlocking."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
from bactologix import storage_cleanup
from bactologix.runs import command, create_run
from bactologix.ui_backend import dispatch
from test_archive_restore import files
from test_browser import render_component
from test_browser_swap import capture, respond
from test_native import workspace, installation, sample

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1', reason='Enable the browser acceptance job to run Chromium')

@pytest.fixture
def archived_locked(workspace, installation, sample, monkeypatch):
    run = create_run(workspace, 'hybas', sample, '{}', label='Archived locked software fixture')
    monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/inert-fixture/conda')
    argv = command(workspace, run, 'dry-run')
    native = Path(argv[argv.index('--directory') + 1])
    lock = native / '.snakemake/locks/0.output.lock'
    lock.parent.mkdir(parents=True)
    lock.write_text('retained fixture lock\n')
    output = Path(run['output']) / 'retained-tool-output.txt'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('retained intermediate output\n')
    job = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(job, status='failed', error='Directory cannot be locked')
    workspace.log_path(job).write_text('LockException:\nError: Directory cannot be locked.\n')
    dispatch(workspace, 'archive_runs', {'ids': [run['id']]})
    monkeypatch.setattr(storage_cleanup, '_other_workspaces', lambda _: ([], [], []))
    return run

@pytest.mark.parametrize('entry', ['storage-row', 'cleanup-preview', 'run-activity', 'jobs'])
def test_restore_locked_archived_run_preserves_all_files_and_exposes_lock_review(workspace, archived_locked, entry):
    from playwright.sync_api import sync_playwright, expect
    run = archived_locked
    original_files = files(Path(run['folder']))
    original_jobs = workspace.jobs()
    original_queue = workspace.queue_snapshot()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
        page.goto('https://workspace.test/')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, workspace)
        if entry in {'storage-row', 'cleanup-preview'}:
            page.get_by_role('link', name='Storage', exact=True).click()
            respond(page, workspace, 'inspect_storage')
            row = page.locator('[data-cleanup-key="run:' + run['id'] + '"]')
            expect(row.get_by_role('button', name='Restore run', exact=True)).to_be_enabled()
            row.locator('[data-cleanup-select]').check()
            if entry == 'cleanup-preview':
                page.get_by_role('button', name='Preview selected cleanup', exact=True).click()
                _, result = respond(page, workspace, 'preview_storage_cleanup')
                assert any('Native workflow locks remain' in reason for reason in result['cleanup_plan']['blocked'])
                dialog = page.get_by_role('dialog')
                expect(dialog.get_by_role('button', name='Permanently delete selected', exact=True)).to_be_disabled()
                expect(dialog.get_by_role('button', name='Restore run', exact=True)).to_be_enabled()
                capture(page, 'archived-lock-cleanup-restore-desktop')
                page.set_viewport_size({'width': 390, 'height': 844})
                assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
                capture(page, 'archived-lock-cleanup-restore-mobile')
                dialog.get_by_role('button', name='Restore run', exact=True).click()
            else:
                row.get_by_role('button', name='Restore run', exact=True).click()
        elif entry == 'run-activity':
            page.get_by_role('link', name='Runs', exact=True).click()
            page.locator('[data-action="run-list"][data-id="archived"]').click()
            page.locator('[data-run-row="' + run['id'] + '"] [data-action="run-detail"]').last.click()
            respond(page, workspace, 'inspect_run')
            page.locator('[data-action="saved-run-tab"][data-id="activity"]').click()
            expect(page.get_by_role('region', name='Archived run', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='Restore run to review lock', exact=True)).to_be_enabled()
            page.get_by_role('region', name='Archived run', exact=True).get_by_role('button', name='Restore run', exact=True).click()
        else:
            page.get_by_role('link', name='Activity', exact=True).click()
            expect(page.get_by_role('button', name='Check workflow lock', exact=True)).to_have_count(0)
            page.get_by_role('button', name='Restore run to review lock', exact=True).click()
        request, _ = respond(page, workspace, 'restore_runs')
        assert request['payload'] == {'ids': [run['id']]}
        respond(page, workspace, 'inspect_run')
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.get_by_role('button', name='Check workflow lock', exact=True)).to_be_enabled()
        assert not workspace.get('run_visibility', run['id'])['archived']
        assert workspace.get('runs', run['id']) == run
        assert files(Path(run['folder'])) == original_files
        assert workspace.jobs() == original_jobs
        assert workspace.queue_snapshot() == original_queue
        sent = page.evaluate('window.acceptanceEvents.map(event=>event.value.kind)')
        assert not {'execute_storage_cleanup', 'unlock_workflow', 'workflow'}.intersection(sent)
        page.set_viewport_size({'width': 1440, 'height': 1100})
        page.get_by_role('link', name='Storage', exact=True).click()
        if entry in {'run-activity', 'jobs'}:
            respond(page, workspace, 'inspect_storage')
        expect(page.get_by_role('button', name='Preview selected cleanup', exact=True)).to_be_disabled()
        expect(page.locator('[data-cleanup-key="run:' + run['id'] + '"] [data-action="restore-run"]')).to_have_count(0)
        assert not errors, errors
        browser.close()
