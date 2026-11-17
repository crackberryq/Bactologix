"""Researcher-facing recovery states with software-only fixtures."""
import os

import pytest

from bactologix.ui_backend import dispatch
from test_browser import render_component
from test_browser_run_progress import progress_page, measured
from test_browser_swap import respond
from test_maintenance import installed
from test_resource_repair import stale_run

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                               reason='Enable browser acceptance explicitly')


def test_vfdb_preview_requires_review_and_preserves_parent(stale_run):
    from playwright.sync_api import sync_playwright, expect
    store, parent = stale_run
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('link', name='Runs', exact=True).click()
        page.get_by_role('link', name=parent['label'], exact=True).click()
        respond(page, store, 'inspect_run')
        page.get_by_role('button', name='Repair VFDB reference', exact=True).click()
        respond(page, store, 'preview_vfdb_repair')
        dialog = page.get_by_role('dialog')
        expect(dialog).to_contain_text('missing file')
        expect(dialog).to_contain_text('other enabled resources')
        assert len(store.all('runs')) == 1 and not store.jobs()
        dialog.get_by_role('button', name='Create revised draft', exact=True).click()
        respond(page, store, 'apply_vfdb_repair')
        expect(page.get_by_role('dialog')).to_have_count(0)
        assert len(store.all('runs')) == 2 and not store.jobs()
        assert store.get('runs', parent['id']) == parent
        assert not errors, errors
        browser.close()


def test_monitoring_error_does_not_claim_native_failure_or_live_progress(progress_page):
    from playwright.sync_api import expect
    page = progress_page
    measured(page)
    page.evaluate("""() => {
      progressJob.monitoring={state:'interrupted',cleanup_confirmed:false,
        detail:'Control database unavailable',updated_at:'fixture'};
      progressRerender();
    }""")
    expect(page.locator('#main > .monitoring-notice')).to_contain_text('exit has not been confirmed')
    expect(page.locator('#main > .run-progress')).to_contain_text('Monitoring interrupted')
    expect(page.locator('.job-progress')).not_to_contain_text('Receiving updates')
    expect(page.get_by_role('region', name='Run controls').get_by_role('button', name='Stop attempted · exit unconfirmed', exact=True)).to_be_disabled()
    page.evaluate("""() => {
      progressJob.status='interrupted';
      progressJob.monitoring.cleanup_confirmed=true;
      progressRerender();
    }""")
    expect(page.locator('#main > .monitoring-notice')).to_contain_text('not a recorded native tool failure')
    expect(page.locator('#main > .run-progress')).to_have_count(0)
