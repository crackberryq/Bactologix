"""Late background observations cannot overwrite a new view or edited path."""
import os

import pytest

from bactologix.runs import create_run
from bactologix.ui_backend import dispatch
from test_browser import render_component, open_databases
from test_native import workspace, installation, sample

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def respond(page, request, payload):
    page.evaluate('(response) => window.acceptanceRender(response)', {'id': request['id'], 'ok': True, **payload})


def pending(page, request):
    task = {'token': 'fixture-task', 'request_id': request['id'], 'kind': request['kind'], 'status': 'running'}
    respond(page, request, {'async_task': task, 'keep_modal': True})
    return task


def test_edit_completion_does_not_navigate_after_user_leaves(workspace, installation, sample):
    from playwright.sync_api import sync_playwright, expect
    run = create_run(workspace, 'hybas', sample, '{}')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, workspace)
        page.get_by_role('link', name='Runs', exact=True).click()
        page.locator('[data-action="run-detail"][data-id="' + run['id'] + '"]').first.click()
        request = page.evaluate('window.acceptanceEvents.at(-1).value')
        respond(page, request, dispatch(workspace, request['kind'], request['payload']))
        page.get_by_role('button', name='Edit settings', exact=True).click()
        request = page.evaluate('window.acceptanceEvents.at(-1).value')
        assert request['kind'] == 'edit_run_settings'
        task = pending(page, request)
        expect(page.get_by_role('region', name='Background checks')).to_contain_text('Opening saved run settings')
        expect(page.get_by_role('button', name='Edit settings', exact=True)).to_be_enabled()
        page.get_by_role('link', name='Home', exact=True).click()
        task.update(status='completed', result=dispatch(workspace, 'edit_run_settings', {'id': run['id']}))
        respond(page, {'id': 'late-poll'}, {'async_task': task, 'keep_modal': True})
        expect(page.get_by_role('heading', name='Edit run settings', exact=True)).to_have_count(0)
        assert '#overview' in page.url
        assert not errors
        browser.close()


def test_database_check_keeps_newly_typed_path(workspace):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, workspace)
        open_databases(page)
        path = page.get_by_label('Kraken2 location', exact=True)
        path.fill('/original-database')
        page.locator('.database-card').filter(has=page.get_by_role('heading', name='Kraken2', exact=True)).get_by_role('button', name='Check location', exact=True).click()
        request = page.evaluate('window.acceptanceEvents.at(-1).value')
        assert request['kind'] == 'check_database'
        task = pending(page, request)
        expect(path).to_be_enabled()
        path.fill('/new-database')
        task.update(status='completed', result={'database_check': {'tool': 'hybas', 'id': 'kraken', 'path': '/original-database', 'status': 'Layout recognized'}})
        respond(page, {'id': 'late-poll'}, {'async_task': task, 'keep_modal': True})
        expect(path).to_have_value('/new-database')
        assert not errors
        browser.close()
