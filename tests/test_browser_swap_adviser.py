"""Researchers review a calculated allowance; nothing is saved or launched for them."""
import os

import pytest
import yaml

from bactologix import swap_adviser
from test_browser import render_component
from test_browser_swap import capacity, respond, start_draft
from test_external_kraken import shared_db
from test_native import installation, sample, workspace

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def configured_database(workspace, tmp_path, monkeypatch):
    capacity(workspace, monkeypatch)
    folder = tmp_path / 'kraken'
    folder.mkdir()
    for name, size in [('hash.k2d', 64 * 1024 ** 3), ('opts.k2d', 4), ('taxo.k2d', 4)]:
        with (folder / name).open('wb') as handle:
            handle.truncate(size)
    cfg = yaml.safe_load(workspace.get('configs', 'hybas'))
    cfg.update(run_kraken=True, kraken_decision_mode='manual', db_root=str(tmp_path), kraken_db_name='kraken')
    workspace.put('configs', 'hybas', yaml.safe_dump(cfg))
    monkeypatch.setattr(swap_adviser.CapacityProbe, 'sample', lambda self: {
        'checked_at': 'fixture', 'errors': [], 'volumes': [
            {'id': 'linux:1', 'label': 'Workspace and logs', 'available_bytes': 100 * 1024 ** 3}]})


def page_for(browser, workspace):
    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
    page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
    page.goto('https://workspace.test/')
    render_component(page, workspace)
    return page


def test_selected_windows_database_is_measured_before_and_after_saving(
        workspace, installation, sample, monkeypatch, shared_db):
    from playwright.sync_api import sync_playwright, expect
    configured_database(workspace, workspace.home, monkeypatch)
    original = workspace.get('configs', 'hybas')
    windows, external = shared_db
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = page_for(browser, workspace)
        page.get_by_role('link', name='Pipelines & databases', exact=True).click()
        databases = page.locator('.setup-steps [data-id="databases"]')
        capacity_step = page.locator('.setup-steps [data-id="capacity"]')
        calculate = page.get_by_role('button', name='Calculate swap recommendation')
        databases.click()
        page.get_by_label('Kraken2 location', exact=True).fill(windows)
        capacity_step.click()
        expect(page.locator('.swap-recommendation')).to_contain_text(windows)
        calculate.click()
        request, result = respond(page, workspace, 'recommend_swap')
        assert request['payload']['kraken_path'] == windows
        assert result['swap_recommendation']['database']['resolved_path'] == str(external)
        assert workspace.get('configs', 'hybas') == original

        databases.click()
        page.get_by_role('button', name='Save for future runs').click()
        respond(page, workspace, 'save_databases')
        capacity_step.click()
        expect(page.get_by_role('button', name='Use recommendation')).to_be_disabled()
        calculate.click()
        request, result = respond(page, workspace, 'recommend_swap')
        assert 'kraken_path' not in request['payload']
        assert result['swap_recommendation']['database']['resolved_path'] == str(external)
        browser.close()


def test_compute_default_recommendation_fills_only_after_review_and_invalidates_changed_ram(
        workspace, installation, sample, tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    configured_database(workspace, tmp_path, monkeypatch)
    original = workspace.get('configs', 'hybas')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = page_for(browser, workspace)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.get_by_role('link', name='Pipelines & databases', exact=True).click()
        page.locator('.setup-steps [data-action="setup-step"][data-id="capacity"]').click()
        calculate = page.get_by_role('button', name='Calculate swap recommendation', exact=True)
        calculate.click()
        expect(page.get_by_role('link', name='Samples', exact=True)).to_be_enabled()
        request, response = respond(page, workspace, 'recommend_swap')
        inputs = response['swap_recommendation']['inputs']
        amount = response['swap_recommendation']['proposal']['swap_mb']
        assert request['payload']['memory_mb'] == 28672
        panel = page.locator('.swap-recommendation')
        for label, value in (
            ('Chosen RAM budget', inputs['ram_budget_mb']),
            ('RAM HybAs can use', inputs['effective_ram_mb']),
            ('Effective HybAs budget with recommended swap', inputs['effective_budget_mb']),
        ):
            row = panel.locator('dl > div').filter(has=page.get_by_text(label, exact=True))
            expect(row.locator('dd')).to_have_text(f'{value:,} MB')
        expect(page.locator('#compute-swap-enabled-hybas')).not_to_be_checked()
        expect(page.locator('.swap-recommendation')).to_contain_text('Disk swap is much slower than RAM')
        page.get_by_role('button', name='Use recommendation', exact=True).click()
        expect(page.locator('#compute-swap-enabled-hybas')).to_be_checked()
        expect(page.get_by_label('Default additional swap allowance (MB)', exact=True)).to_have_value(str(amount))
        expect(page.get_by_label('Default RAM (MB)', exact=True)).to_have_value('28672')
        assert workspace.get('configs', 'hybas') == original and workspace.jobs() == []
        page.get_by_label('Default RAM (MB)', exact=True).fill('24576')
        expect(page.get_by_role('button', name='Use recommendation', exact=True)).to_be_disabled()
        expect(page.locator('[data-swap-stale]')).to_be_visible()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.locator('.swap-recommendation').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        assert errors == []
        browser.close()


def test_run_recommendation_uses_unsaved_fields_and_drops_late_changed_settings(
        workspace, installation, sample, tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    configured_database(workspace, tmp_path, monkeypatch)
    original = workspace.get('configs', 'hybas')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = page_for(browser, workspace)
        start_draft(page, workspace)
        page.get_by_role('button', name='Calculate swap recommendation', exact=True).click()
        payload = page.evaluate('window.acceptanceEvents.at(-1).value.payload')
        assert payload['config']['tool'] == 'hybas'
        assert payload['config']['text'] and isinstance(payload['config']['changes'], dict)
        # A result arriving after a RAM edit must not expose stale Apply values.
        page.get_by_label('Physical RAM budget (MB)', exact=True).fill('24576')
        respond(page, workspace, 'recommend_swap')
        expect(page.get_by_role('button', name='Use recommendation', exact=True)).to_have_count(0)
        page.get_by_role('button', name='Calculate swap recommendation', exact=True).click()
        _, response = respond(page, workspace, 'recommend_swap')
        assert response['swap_recommendation']['inputs']['ram_budget_mb'] == 24576
        page.get_by_role('button', name='Use recommendation', exact=True).click()
        expect(page.get_by_label('Allow disk swap for this run', exact=True)).to_be_checked()
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_value(
            str(response['swap_recommendation']['proposal']['swap_mb']))
        assert workspace.get('configs', 'hybas') == original
        assert workspace.all('runs') == [] and workspace.jobs() == []
        browser.close()
