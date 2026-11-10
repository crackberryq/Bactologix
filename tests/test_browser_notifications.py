"""Chromium checks for local notification controls and opt-in desktop delivery."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bactologix import update_checks
from bactologix.store import Store, now
from bactologix.ui_backend import dispatch, snapshot
from test_browser import render_component

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


@pytest.fixture
def notification_store(tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_UPDATE_CHECKS', '0')
    store = Store(tmp_path / 'workspace')
    store.put('notification_state', 'initialized', True)
    store.put('settings', 'automatic-update-checks', False)
    store.put('app_catalog', 'releases', {'checked_at': now(), 'releases': []})
    candidates = [{'id': 'app-new', 'category': 'updates',
                   'title': 'Bactologix update available',
                   'body': 'A compatible release is available. Review before installing.',
                   'target': {'kind': 'app_update'}}]
    monkeypatch.setattr(update_checks, 'candidates', lambda _: candidates)
    return store, candidates


def finish_job(store, status='succeeded'):
    jid = store.submit('smoke', {})
    store.update_job(jid, status=status, finished=now())
    return jid


def setup_page(page, store, permission='default'):
    page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
    page.goto('https://workspace.test/')
    page.evaluate('''permission => {
        window.notificationPermissionRequests = 0;
        window.notificationPopups = [];
        window.notificationConstructorFails = false;
        class TestNotification {
            static permission = permission;
            static requestPermission() {
                window.notificationPermissionRequests += 1;
                return Promise.resolve(TestNotification.permission = 'granted');
            }
            constructor(title, options) {
                if(window.notificationConstructorFails)throw new TypeError('Unsupported constructor');
                window.notificationPopups.push({title, options, instance: this});
            }
            close() {this.closed = true;}
        }
        window.Notification = TestNotification;
    }''', permission)
    render_component(page, store)


def refresh(page, store, response=None):
    page.evaluate('''({workspace,response}) => {
        Object.assign(window.acceptanceWorkspace, workspace);
        window.acceptanceRender(response);
    }''', {'workspace': snapshot(store), 'response': response})


def respond(page, store, kind):
    request = page.evaluate('window.acceptanceEvents.at(-1).value')
    assert request['kind'] == kind
    result = dispatch(store, kind, request['payload'])
    refresh(page, store, {'id': request['id'], 'ok': True, **result})
    return request, result


def capture(page, name):
    destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / (name + '.png')), full_page=True)


def test_notification_center_persists_read_state_and_opens_update_review(notification_store):
    from playwright.sync_api import sync_playwright, expect
    store, candidates = notification_store
    finish_job(store, 'failed')
    original_jobs = store.jobs()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        setup_page(page, store)
        bell = page.locator('[data-action="notifications"]')
        expect(bell).to_have_attribute('aria-label', 'Notifications, 2 unread')
        bell.focus()
        page.keyboard.press('Enter')
        dialog = page.get_by_role('dialog', name='Notifications', exact=True)
        expect(dialog).to_be_visible()
        assert page.evaluate('window.notificationPermissionRequests') == 0
        expect(dialog).to_contain_text('Job failed')
        expect(dialog).to_contain_text('Bactologix update available')
        # Escaping is required even for persisted, user-edited local records.
        row = next(x for x in store.all('notifications') if x['category'] == 'updates')
        row['body'] = 'Review <sample> before installing.'
        store.put('notifications', row['id'], row)
        refresh(page, store)
        expect(dialog).to_contain_text('Review <sample> before installing.')
        assert dialog.locator('sample').count() == 0
        dialog.get_by_role('button', name='Check for updates', exact=True).click()
        respond(page, store, 'check_notification_updates')
        assert store.jobs() == original_jobs
        # Preferences affect only notifications and checking, never job submission.
        dialog.locator('.notification-preferences > summary').click()
        dialog.get_by_label('Job outcomes', exact=True).uncheck()
        dialog.get_by_role('button', name='Save preferences', exact=True).click()
        req, _ = respond(page, store, 'save_notification_settings')
        assert req['payload'] == {'jobs': False, 'review': True, 'updates': True, 'auto_update_checks': False}
        expect(dialog.get_by_role('button', name='Close dialog', exact=True)).to_be_focused()
        update = dialog.locator('.notification-item').filter(has_text='Bactologix update available')
        update.get_by_role('button', name='Open alert', exact=True).click()
        respond(page, store, 'mark_notifications_read')
        expect(page.locator('.modal[data-modal-kind="app-update"]')).to_be_visible()
        assert all(row['kind'] not in {'update_app', 'install'} for row in page.evaluate('window.acceptanceEvents.map(x=>x.value)'))
        page.keyboard.press('Escape')
        expect(bell).to_be_focused()
        expect(bell).to_have_attribute('aria-label', 'Notifications')
        bell.click()
        expect(page.get_by_role('dialog', name='Notifications', exact=True)).to_contain_text('0 unread')
        assert store.jobs() == original_jobs
        assert not errors
        browser.close()


def test_desktop_opt_in_baseline_and_single_delivery(notification_store):
    from playwright.sync_api import sync_playwright, expect
    store, _ = notification_store
    finish_job(store)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1100})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        setup_page(page, store)
        assert page.evaluate('window.notificationPermissionRequests') == 0
        assert page.evaluate('window.notificationPopups.length') == 0
        page.locator('[data-action="notifications"]').click()
        dialog = page.get_by_role('dialog', name='Notifications', exact=True)
        dialog.get_by_role('button', name='Enable desktop alerts', exact=True).click()
        expect(dialog.get_by_role('button', name='Turn off desktop alerts', exact=True)).to_be_visible()
        assert page.evaluate('window.notificationPermissionRequests') == 1
        assert page.evaluate('window.notificationPopups.length') == 0
        assert not page.evaluate('window.acceptanceEvents')
        # Existing unread alerts form the opt-in baseline. Only future events are claimed.
        job_ids = {finish_job(store, 'failed') for _ in range(4)}
        refresh(page, store)
        req, result = respond(page, store, 'claim_desktop_notifications')
        assert len(req['payload']['ids']) == 3
        assert len(result['notification_delivery']) == 3
        assert all(row['target']['id'] in job_ids for row in result['notification_delivery'])
        assert page.evaluate('window.notificationPopups.length') == 3
        # The fourth new alert must not trigger another immediate delivery request.
        assert page.evaluate("window.acceptanceEvents.filter(x=>x.value.kind==='claim_desktop_notifications').length") == 1
        popup = page.evaluate('window.notificationPopups[0].options')
        assert 'workspace' not in popup['body'] and 'sample' not in popup['body']
        count = page.evaluate('window.acceptanceEvents.length')
        refresh(page, store)
        assert page.evaluate('window.notificationPopups.length') == 3
        assert page.evaluate('window.acceptanceEvents.length') == count
        # Reopening the same workspace in another tab retains opt-in without replaying alerts.
        second = context.new_page()
        setup_page(second, store, permission='granted')
        assert second.evaluate('window.notificationPermissionRequests') == 0
        assert second.evaluate('window.notificationPopups.length') == 0
        assert not second.evaluate('window.acceptanceEvents')
        second.close()
        dialog.get_by_role('button', name='Turn off desktop alerts', exact=True).click()
        finish_job(store, 'cancelled')
        refresh(page, store)
        assert page.evaluate('window.notificationPopups.length') == 3
        assert page.evaluate('window.acceptanceEvents.length') == count
        expect(dialog).to_contain_text('Job cancelled')
        assert not errors
        browser.close()


def test_denied_unsupported_and_failed_desktop_delivery_keep_saved_alerts(notification_store):
    from playwright.sync_api import sync_playwright, expect
    store, _ = notification_store
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        setup_page(page, store, permission='denied')
        bell = page.locator('[data-action="notifications"]')
        bell.click()
        dialog = page.get_by_role('dialog', name='Notifications', exact=True)
        expect(dialog.get_by_role('button', name='Enable desktop alerts', exact=True)).to_be_disabled()
        expect(dialog).to_contain_text('blocked by your browser')
        assert page.evaluate('window.notificationPermissionRequests') == 0
        page.evaluate('window.Notification = undefined')
        refresh(page, store)
        expect(dialog).to_contain_text('unavailable in this app window')
        expect(dialog).to_contain_text('Bactologix update available')
        capture(page, 'notifications-mobile')
        assert dialog.evaluate('(node)=>node.scrollWidth <= node.clientWidth + 1')
        page.keyboard.press('Escape')
        expect(bell).to_be_focused()
        page.set_viewport_size({'width': 1440, 'height': 1100})
        # Recreate the fixture host with granted permission and exercise constructor failure.
        setup_page(page, store, permission='granted')
        page.locator('[data-action="notifications"]').click()
        dialog = page.get_by_role('dialog', name='Notifications', exact=True)
        dialog.get_by_role('button', name='Enable desktop alerts', exact=True).click()
        page.evaluate('window.notificationConstructorFails = true')
        finish_job(store, 'failed')
        refresh(page, store)
        respond(page, store, 'claim_desktop_notifications')
        expect(dialog).to_contain_text('could not display a desktop alert')
        expect(dialog).to_contain_text('Job failed')
        expect(dialog.get_by_role('button', name='Enable desktop alerts', exact=True)).to_be_visible()
        assert page.evaluate('window.notificationPopups.length') == 0
        capture(page, 'notifications-desktop')
        browser.close()
