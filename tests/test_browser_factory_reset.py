"""Reviewed regular-channel reset dialog, using isolated software fixtures only."""
import os
from pathlib import Path

import pytest
import yaml

from bactologix.ui_backend import dispatch
from test_browser import render_component
from test_browser_swap import respond
from test_maintenance import installed


pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                               reason='Enable browser acceptance explicitly')


def task_response(page, request, payload):
    page.evaluate('(response) => window.acceptanceRender(response)', {'id': request['id'], 'ok': True, **payload})


def pending(page, request):
    task = {'token': 'fixture-task', 'request_id': request['id'], 'kind': request['kind'], 'status': 'running'}
    task_response(page, request, {'async_task': task, 'keep_modal': True})
    return task


@pytest.fixture
def reviewed_workspace(installed, tmp_path, monkeypatch):
    store, item = installed
    # The browser test queues the reviewed action; it never starts a worker.
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda _: None)
    monkeypatch.setattr('psutil.process_iter', lambda: [])
    old = tmp_path / 'manual-databases/kraken'
    old.mkdir(parents=True)
    for name in ('hash.k2d', 'opts.k2d', 'taxo.k2d'):
        (old / name).write_text('Original manual database fixture\n')
    config = yaml.safe_dump({'db_root': str(old.parent), 'kraken_db_name': old.name})
    store.put('configs', 'hybas', config)
    store.put('database_locations', 'hybas', {'kraken': {'path': str(old)}})
    cached = store.home / 'cache/pip/disposable-fixture.whl'
    cached.parent.mkdir(parents=True)
    cached.write_text('Reviewed disposable cache fixture\n')
    return store, item, old, config, cached


@pytest.fixture
def browser_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page
        assert not errors, errors
        browser.close()


def open_reset(page, store):
    render_component(page, store)
    page.get_by_role('link', name='Storage', exact=True).click()
    respond(page, store, 'inspect_storage')
    page.locator('summary').filter(has_text='Developer tools · reset this workspace').click()
    page.get_by_role('button', name='Review workspace reset', exact=True).click()
    return page.get_by_role('dialog')


def test_reset_requires_reviewed_paths_both_confirmations_and_new_scope(reviewed_workspace, browser_page):
    from playwright.sync_api import expect
    store, _, old, config, cached = reviewed_workspace
    page = browser_page
    dialog = open_reset(page, store)
    include = dialog.locator('#reset-databases')
    confirm = dialog.get_by_role('button', name='Permanently reset reviewed data', exact=True)
    expect(include).not_to_be_checked()
    expect(confirm).to_be_disabled()
    dialog.get_by_role('button', name='Preview reset', exact=True).click()
    _, result = respond(page, store, 'factory_reset_preview')
    plan = result['reset_plan']
    assert not plan['blocked'] and plan['delete_databases'] is False
    expect(dialog).to_contain_text(str(cached.parent))
    expect(confirm).to_be_disabled()
    dialog.get_by_label('Type RESET THIS WORKSPACE', exact=True).fill('RESET THIS WORKSPACE')
    expect(confirm).to_be_disabled()
    dialog.locator('#reset-acknowledged').check()
    expect(confirm).to_be_enabled()
    # Changing database scope discards the first plan and its acknowledgements.
    include.check()
    expect(confirm).to_be_disabled()
    expect(dialog.get_by_label('Type RESET THIS WORKSPACE', exact=True)).to_have_count(0)
    dialog.get_by_role('button', name='Preview reset', exact=True).click()
    _, result = respond(page, store, 'factory_reset_preview')
    second = result['reset_plan']
    assert second['delete_databases'] and second['id'] != plan['id']
    dialog.get_by_label('Type RESET THIS WORKSPACE', exact=True).fill('RESET THIS WORKSPACE')
    dialog.locator('#reset-acknowledged').check()
    expect(confirm).to_be_disabled()
    dialog.get_by_label('Separately type DELETE MANAGED DATABASES', exact=True).fill('DELETE MANAGED DATABASES')
    expect(confirm).to_be_enabled()
    confirm.click()
    request, response = respond(page, store, 'factory_reset_execute')
    assert request['payload']['plan_id'] == second['id']
    assert request['payload']['signature'] == second['signature']
    assert store.job(response['job_id'])['kind'] == 'factory_reset'
    assert cached.exists() and (old / 'hash.k2d').read_text() == 'Original manual database fixture\n'
    assert store.get('configs', 'hybas') == config


def test_reset_scope_change_discards_late_preview_and_keeps_navigation_live(reviewed_workspace, browser_page):
    from playwright.sync_api import expect
    store, _, _, _, cached = reviewed_workspace
    page = browser_page
    dialog = open_reset(page, store)
    dialog.get_by_role('button', name='Preview reset', exact=True).click()
    request = page.evaluate('window.acceptanceEvents.at(-1).value')
    assert request['kind'] == 'factory_reset_preview'
    task = pending(page, request)
    expect(dialog.get_by_role('region', name='Background checks')).to_contain_text('Reviewing workspace reset')
    expect(dialog.locator('#reset-databases')).to_be_enabled()
    dialog.locator('#reset-databases').check()
    # Toggling back does not revive the preview from the old review scope.
    dialog.locator('#reset-databases').uncheck()
    task.update(status='completed', result=dispatch(store, request['kind'], request['payload']))
    task_response(page, {'id': 'late-reset-poll'}, {'async_task': task, 'keep_modal': True})
    expect(dialog.get_by_role('heading', name='Paths eligible for permanent deletion')).to_have_count(0)
    expect(dialog.get_by_role('button', name='Permanently reset reviewed data', exact=True)).to_be_disabled()
    dialog.get_by_role('button', name='Close dialog', exact=True).click()
    page.get_by_role('link', name='Home', exact=True).click()
    assert '#overview' in page.url and cached.exists() and not store.jobs()


