"""Browser coverage for saved pipeline defaults and run-local swap overrides."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml

from bactologix import hardware
from bactologix.ui_backend import dispatch, snapshot
from test_browser import customize_compute, render_component
from test_native import installation, sample, workspace

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def respond(page, store, kind):
    request = page.evaluate('window.acceptanceEvents.at(-1).value')
    assert request['kind'] == kind
    result = dispatch(store, kind, request['payload'])
    page.evaluate('''({workspace,response}) => {
        Object.assign(window.acceptanceWorkspace, workspace);
        window.acceptanceRender(response);
    }''', {'workspace': snapshot(store), 'response': {'id': request['id'], 'ok': True, **result}})
    return request, result


def capacity(store, monkeypatch):
    info = hardware.detect(store.home)
    info['cpu'].update(available=12, logical=12)
    info['memory'] = {'total_mb': 31744, 'available_mb': 30720, 'swap_mb': 500000,
                      'swap_available_mb': 500000, 'combined_total_mb': 531744,
                      'combined_available_mb': 530720}
    monkeypatch.setattr(hardware, 'inventory', lambda store, refresh=False: copy.deepcopy(info))
    store.put('resource_defaults', 'hybas', {'cores': 12, 'memory_mb': 28672, 'preset': 'custom'})
    store.put('configs', 'hybas', 'run_kraken: false\nkraken_decision_mode: "off"\npolish_mode: none\nassembly_mem_mb: 8192\npolishing_mem_mb: 4096\nspades_memory_gb: 8\n')


def capture(page, name):
    destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / (name + '.png')), full_page=True)


def start_draft(page, store):
    page.get_by_role('link', name='Runs', exact=True).click()
    page.get_by_role('button', name='Create run drafts', exact=True).first.click()
    page.locator('[data-action="run-purpose"][data-id="hybas"]').click()
    page.get_by_role('button', name='Continue to inputs', exact=True).click()
    respond(page, store, 'run_config_editor')
    page.locator('[data-sample="sample"]').check()
    page.get_by_role('button', name='Continue to settings', exact=True).click()
    customize_compute(page)


def test_swap_opt_in_recovery_save_revision_and_opt_out(workspace, installation, sample, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    capacity(workspace, monkeypatch)
    original_defaults = workspace.get('configs', 'hybas')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
        page.goto('https://workspace.test/')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, workspace)
        start_draft(page, workspace)
        toggle = page.get_by_label('Allow disk swap for this run', exact=True)
        expect(toggle).not_to_be_checked()
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_count(0)
        expect(page.get_by_label('Physical RAM budget (MB)', exact=True)).to_have_value('28672')
        toggle.check()
        page.get_by_label('Additional swap allowance (MB)', exact=True).fill('98304')
        expect(page.locator('[data-scheduling-total="run"]')).to_have_text('126,976 MB')
        expect(page.locator('.compute-summary')).to_contain_text('500,000 MB of Linux/WSL swap available')
        capture(page, 'swap-compute-desktop')
        page.get_by_role('button', name='Review drafts', exact=True).click()
        respond(page, workspace, 'run_config_editor')
        expect(page.locator('[data-review-budget="swap"]')).to_have_text('98304 MB')
        expect(page.locator('.review-stats')).to_contain_text('126,976 MB')
        page.get_by_role('button', name='Leave editor', exact=True).click()
        page.reload()
        render_component(page, workspace)
        page.get_by_role('button', name='Resume editing', exact=True).click()
        expect(page.locator('[data-review-budget="swap"]')).to_have_text('98304 MB')
        page.get_by_role('button', name='Back', exact=True).click()
        customize_compute(page)
        expect(toggle).to_be_checked()
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_value('98304')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.locator('.compute-summary').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        capture(page, 'swap-compute-mobile')
        page.set_viewport_size({'width': 1440, 'height': 1100})
        page.get_by_role('button', name='Review drafts', exact=True).click()
        respond(page, workspace, 'run_config_editor')
        page.get_by_role('button', name='Create drafts', exact=True).click()
        request, _ = respond(page, workspace, 'create_runs')
        assert request['payload']['swap_mb'] == 98304 and request['payload']['memory_mb'] == 28672
        parent, = workspace.all('runs')
        original_parent = copy.deepcopy(parent)
        cfg = yaml.safe_load(parent['config'])
        assert cfg['bactologix_compute']['swap_mb'] == 98304
        assert (cfg['assembly_mem_mb'], cfg['polishing_mem_mb'], cfg['spades_memory_gb']) == (8192, 4096, 8)
        expect(page.locator('.review-stats')).to_contain_text('98304 MB')
        expect(page.locator('.review-stats')).to_contain_text('126,976 MB')
        page.get_by_role('button', name='Edit settings', exact=True).click()
        respond(page, workspace, 'edit_run_settings')
        customize_compute(page)
        expect(toggle).to_be_checked()
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_value('98304')
        toggle.uncheck()
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_count(0)
        page.get_by_role('button', name='Review drafts', exact=True).click()
        respond(page, workspace, 'run_config_editor')
        expect(page.locator('[data-review-budget="swap"]')).to_have_text('Off')
        page.get_by_role('button', name='Create revised draft', exact=True).click()
        request, _ = respond(page, workspace, 'revise_run')
        assert request['payload']['swap_mb'] == 0 and request['payload']['memory_mb'] == 28672
        child, = [run for run in workspace.all('runs') if run['id'] != parent['id']]
        assert yaml.safe_load(child['config'])['bactologix_compute']['swap_mb'] == 0
        assert child['parent'] == parent['id']
        assert workspace.get('runs', parent['id']) == original_parent
        assert workspace.get('configs', 'hybas') == original_defaults
        # A fresh draft never opts into disk swap because a previous run did.
        start_draft(page, workspace)
        expect(toggle).not_to_be_checked()
        assert not errors, errors
        browser.close()


def test_pipeline_swap_default_in_setup_and_independent_run_overrides(workspace, installation, sample, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    capacity(workspace, monkeypatch)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
        page.goto('https://workspace.test/')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, workspace)
        page.get_by_role('link', name='Pipelines & databases', exact=True).click()
        page.locator('[data-action="setup-step"][data-id="capacity"]').click()
        toggle = page.locator('#compute-swap-enabled-hybas')
        expect(toggle).not_to_be_checked()
        toggle.check()
        page.get_by_label('Default additional swap allowance (MB)', exact=True).fill('98304')
        expect(page.locator('[data-default-scheduling-total="hybas"]')).to_have_text('126,976 MB')
        page.get_by_role('button', name='Save compute defaults', exact=True).click()
        request, _ = respond(page, workspace, 'save_compute_defaults')
        assert request['payload']['swap_mb'] == 98304
        assert yaml.safe_load(workspace.get('configs', 'hybas'))['bactologix_compute']['swap_mb'] == 98304
        assert hardware.run_defaults(workspace, 'basis')['swap_mb'] == 0
        assert workspace.jobs() == []
        expect(toggle).to_be_checked()
        capture(page, 'pipeline-swap-default-desktop')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.locator('.compute-defaults').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        capture(page, 'pipeline-swap-default-mobile')
        page.set_viewport_size({'width': 1440, 'height': 1100})
        # Presets adjust physical resources and retain the explicit saved swap choice.
        page.get_by_role('button', name='Use Full power', exact=True).click()
        respond(page, workspace, 'apply_resource_preset')
        expect(toggle).to_be_checked()
        expect(page.get_by_label('Default additional swap allowance (MB)', exact=True)).to_have_value('98304')
        page.locator('[data-action="setup-tab"][data-id="hardware"]').click()
        page.get_by_label('Pipeline compute defaults', exact=True).select_option('basis')
        expect(page.locator('#compute-swap-enabled-basis')).not_to_be_checked()
        page.get_by_label('Pipeline compute defaults', exact=True).select_option('hybas')
        expect(toggle).to_be_checked()
        start_draft(page, workspace)
        run_toggle = page.get_by_label('Allow disk swap for this run', exact=True)
        expect(run_toggle).to_be_checked()
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_value('98304')
        page.get_by_label('Additional swap allowance (MB)', exact=True).fill('32768')
        # A saved-default change elsewhere must not reset an unfinished override.
        changed = yaml.safe_load(workspace.get('configs', 'hybas'))
        changed['bactologix_compute']['swap_mb'] = 65536
        workspace.put('configs', 'hybas', yaml.safe_dump(changed))
        page.evaluate('workspace => {Object.assign(window.acceptanceWorkspace,workspace);window.acceptanceRender(null)}', snapshot(workspace))
        page.get_by_label('Pipeline', exact=True).select_option('basis')
        respond(page, workspace, 'run_config_editor')
        customize_compute(page)
        expect(run_toggle).not_to_be_checked()
        page.get_by_label('Pipeline', exact=True).select_option('hybas')
        customize_compute(page)
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_value('32768')
        run_toggle.uncheck()
        page.get_by_role('button', name='Review drafts', exact=True).click()
        respond(page, workspace, 'run_config_editor')
        expect(page.locator('[data-review-budget="swap"]')).to_have_text('Off')
        page.get_by_role('button', name='Create drafts', exact=True).click()
        request, _ = respond(page, workspace, 'create_runs')
        assert request['payload']['swap_mb'] == 0
        run, = workspace.all('runs')
        assert yaml.safe_load(run['config'])['bactologix_compute']['swap_mb'] == 0
        assert hardware.run_defaults(workspace, 'hybas')['swap_mb'] == 65536
        # A fresh draft now starts from the latest saved defaults, not previous overrides.
        start_draft(page, workspace)
        expect(run_toggle).to_be_checked()
        expect(page.get_by_label('Additional swap allowance (MB)', exact=True)).to_have_value('65536')
        assert not errors, errors
        browser.close()


def test_yaml_default_save_refreshes_compute_controls(workspace, installation, sample, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    capacity(workspace, monkeypatch)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
        page.goto('https://workspace.test/')
        render_component(page, workspace)
        page.get_by_role('link', name='Settings', exact=True).click()
        respond(page, workspace, 'default_config_editor')
        expect(page.locator('#compute-swap-enabled-hybas')).not_to_be_checked()
        page.get_by_role('button', name='Full YAML', exact=True).click()
        respond(page, workspace, 'default_config_editor')
        config = yaml.safe_load(workspace.get('configs', 'hybas'))
        config['bactologix_compute'] = {'swap_mb': 65536, 'gpu_mode': 'cpu'}
        page.get_by_label('Full configuration', exact=True).fill(yaml.safe_dump(config))
        page.get_by_role('button', name='Save defaults', exact=True).click()
        respond(page, workspace, 'save_default_config')
        expect(page.locator('#compute-swap-enabled-hybas')).to_be_checked()
        expect(page.get_by_label('Default additional swap allowance (MB)', exact=True)).to_have_value('65536')
        expect(page.get_by_label('GPU / CUDA policy', exact=True)).to_have_value('cpu')
        page.get_by_role('button', name='Save compute defaults', exact=True).click()
        request, _ = respond(page, workspace, 'save_compute_defaults')
        assert request['payload']['swap_mb'] == 65536
        assert request['payload']['gpu_mode'] == 'cpu'
        assert hardware.run_defaults(workspace, 'hybas')['swap_mb'] == 65536
        browser.close()
