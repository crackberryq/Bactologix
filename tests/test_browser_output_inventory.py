"""Chromium acceptance for discoverable live outputs and retained histories."""
import json
import os
from pathlib import Path

import pytest

from test_browser import render_component
from test_browser_isolate import responder, capture
from test_isolate_integration import isolate_fixture, original_bytes

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def test_output_browser_live_nested_snapshots_and_native_history(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect

    fixture = isolate_fixture(tmp_path)
    store, run = fixture['store'], fixture['hybas']
    output = Path(run['output'])
    nested = output / 'tool <fixture>'
    nested.mkdir()
    (nested / 'intermediate.bin').write_bytes(b'\x00\xffnative tool bytes')
    retained = output.parent / 'retained'
    retained.mkdir()
    (retained / 'retained-tool-result.txt').write_text('Retained native tool output.')
    saved = Path(run['folder']) / 'output-history' / 'before-attempt'
    (saved / 'outputs').mkdir(parents=True)
    (saved / 'outputs' / 'old-result.txt').write_text('Previously generated result.')
    (saved / 'manifest.json').write_text(json.dumps({
        'status': 'completed', 'created_at': '2026-11-10T10:00:00Z',
        'reason': 'preserved-before-attempt', 'completed': True}))
    before = original_bytes(fixture)
    opened = []
    monkeypatch.setattr('bactologix.launcher.open_folder', lambda path: opened.append(str(path)) or str(path))
    with sync_playwright() as playwright:
        options = {'headless': True}
        if os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'):
            options['executable_path'] = os.environ['BACTOLOGIX_BROWSER_EXECUTABLE']
        browser = playwright.chromium.launch(**options)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
        page.goto('https://workspace.test/')
        render_component(page, store)
        respond = responder(page, store)
        page.get_by_role('link', name='Results & reports', exact=True).click()
        page.locator('[data-action="view-results"][data-id="' + run['id'] + '"]').click()
        respond('inspect_run')
        respond('inspect_results')
        page.get_by_role('button', name='All tool outputs', exact=True).click()
        respond('inspect_output_files')
        dialog = page.get_by_role('dialog', name='All tool outputs', exact=True)
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('assembly-result.txt')
        expect(dialog).to_contain_text('Link · not followed')
        expect(dialog.locator('fixture')).to_have_count(0)
        dialog.get_by_role('button', name='tool <fixture>', exact=True).click()
        respond('inspect_output_files')
        expect(dialog).to_contain_text('intermediate.bin')
        dialog.get_by_role('button', name='Up one folder', exact=True).click()
        respond('inspect_output_files')
        expect(dialog.get_by_role('button', name='tool <fixture>', exact=True)).to_be_visible()
        dialog.get_by_label('Output location', exact=True).select_option(run['id'] + '/before-attempt')
        respond('inspect_output_files')
        expect(dialog).to_contain_text('old-result.txt')
        expect(dialog).to_contain_text('does not identify the producing attempt')
        dialog.get_by_role('button', name='Open folder', exact=True).click()
        respond('open_output_folder')
        assert opened == [str(saved / 'outputs')]
        dialog.get_by_label('Output location', exact=True).select_option('@run-files/' + run['id'])
        respond('inspect_output_files')
        dialog.get_by_role('button', name='outputs', exact=True).click()
        respond('inspect_output_files')
        dialog.get_by_role('button', name='retained', exact=True).click()
        respond('inspect_output_files')
        expect(dialog).to_contain_text('retained-tool-result.txt')
        capture(page, 'all-tool-outputs-desktop')
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(dialog.get_by_label('Output location', exact=True)).to_be_visible()
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        capture(page, 'all-tool-outputs-mobile')
        dialog.get_by_role('button', name='Refresh listing', exact=True).click()
        respond('inspect_output_files')
        expect(dialog).to_contain_text('retained-tool-result.txt')
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        assert not errors
        browser.close()
    assert original_bytes(fixture) == before
