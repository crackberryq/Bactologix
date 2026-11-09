"""Chromium acceptance for portable isolate exports and recorded history."""
from __future__ import annotations

import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from test_browser import render_component
from test_isolate_integration import (assert_portable_export, finish_workers,
                                     isolate_fixture, original_bytes,
                                     tracked_worker, wait_for_export)

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def responder(page, store):
    from bactologix.ui_backend import dispatch, snapshot

    def respond(kind):
        request = page.evaluate('window.acceptanceEvents.at(-1).value')
        assert request['kind'] == kind
        result = dispatch(store, kind, request['payload'])
        page.evaluate('''({response, workspace}) => {
            Object.assign(window.acceptanceWorkspace, workspace);
            window.acceptanceRender(response);
        }''', {'response': {'id': request['id'], 'ok': True, **result},
               'workspace': snapshot(store)})
        return result

    return respond


def capture(page, name):
    destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / (name + '.png')), full_page=True)


def test_isolate_history_links_evidence_downloads_and_mobile_keyboard(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from bactologix import launcher
    fixture = isolate_fixture(tmp_path)
    store = fixture['store']
    before = original_bytes(fixture)
    jobs_before = store.jobs()
    opened = []
    monkeypatch.setattr(launcher, 'open_folder', lambda path: opened.append(str(path)) or str(path))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        respond = responder(page, store)
        page.get_by_role('link', name='Samples', exact=True).click()
        row = page.locator('[data-sample-row="isolate-07"]')
        row.get_by_role('button', name='Open input folder', exact=True).click()
        respond('open_isolate_folder')
        assert opened == [str(fixture['original'])]
        trigger = row.get_by_role('button', name='Analysis & history', exact=True)
        trigger.focus()
        page.keyboard.press('Enter')
        result = respond('isolate_details')['isolate']
        assert {r['id'] for r in result['runs']} == {fixture[k]['id'] for k in ('hybas', 'basis', 'genescope')}
        dialog = page.get_by_role('dialog', name='Isolate · isolate-07', exact=True)
        expect(dialog).to_be_visible()
        expect(dialog.locator('[data-isolate-run]')).to_have_count(3)
        expect(dialog.locator('[data-isolate-run="run-unrelated-same-name"]')).to_have_count(0)
        expect(dialog).to_contain_text(str(fixture['original']))
        expect(dialog.locator('[data-isolate-run="run-export-basis"]')).to_contain_text('Archived')
        expect(dialog).to_contain_text('run-export-hybas <fixture>')
        assert dialog.locator('fixture').count() == 0  # Labels remain text, not inserted markup.
        capture(page, 'isolate-files-desktop')
        dialog.get_by_role('button', name='Sample history', exact=True).click()
        expect(dialog.get_by_role('group', name='Sample history diagram', exact=True)).to_be_visible()
        graph = result['history']
        assert any(n['status'] == 'failed' for n in graph['nodes'])
        assert any(n['status'] in {'unknown', 'not_recorded', 'planned'} for n in graph['nodes'])
        selectable = dialog.locator('[data-action="isolate-node"]').last
        selectable.focus()
        page.keyboard.press('Enter')
        expect(selectable).to_have_attribute('aria-pressed', 'true')
        expect(dialog.get_by_role('region', name='Selected history step', exact=True)).to_contain_text(selectable.get_attribute('data-id'))
        for fmt, label in [('svg', 'Download SVG'), ('pdf', 'Download PDF'), ('json', 'Download JSON'), ('html', 'Offline HTML')]:
            with page.expect_download() as exported:
                dialog.get_by_role('button', name=label, exact=True).click()
                respond('download_isolate_history')
            content = Path(exported.value.path()).read_bytes()
            if fmt == 'svg':
                assert ET.fromstring(content).tag.endswith('svg')
            elif fmt == 'pdf':
                assert content.startswith(b'%PDF-') and b'%%EOF' in content
            elif fmt == 'json':
                exported_graph = json.loads(content)
                assert {n['id'] for n in exported_graph['nodes']} == {n['id'] for n in graph['nodes']}
            else:
                assert b'<html' in content.lower() and b'isolate-07' in content
        capture(page, 'isolate-history-desktop')
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(dialog).to_be_visible()
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        capture(page, 'isolate-history-mobile')
        dialog.get_by_role('button', name='Close dialog', exact=True).click()
        expect(dialog).to_have_count(0)
        expect(trigger).to_be_focused()
        assert store.jobs() == jobs_before
        assert original_bytes(fixture) == before
        assert not errors, errors
        browser.close()


def test_isolate_export_previews_then_copies_and_shows_verified_local_receipt(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from bactologix.ui_backend import snapshot
    fixture = isolate_fixture(tmp_path)
    store = fixture['store']
    before = original_bytes(fixture)
    destination = tmp_path / 'SharePoint synced folder'
    destination.mkdir()
    other_destination = tmp_path / 'other destination'
    other_destination.mkdir()
    jobs_before = {job['id'] for job in store.jobs()}
    children = tracked_worker(monkeypatch)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            render_component(page, store)
            respond = responder(page, store)
            page.get_by_role('link', name='Samples', exact=True).click()
            page.locator('[data-sample-row="isolate-07"]').get_by_role('button', name='Export isolate', exact=True).click()
            respond('isolate_details')
            dialog = page.get_by_role('dialog', name='Isolate · isolate-07', exact=True)
            copy_button = dialog.get_by_role('button', name='Copy & verify', exact=True)
            expect(copy_button).to_be_disabled()
            dialog.get_by_label('Destination folder', exact=True).fill(str(destination))
            dialog.get_by_label('Destination type', exact=True).select_option('sharepoint_sync')
            expect(dialog).to_contain_text('Local verification and cloud sync are separate')
            dialog.get_by_role('button', name='Preview export', exact=True).click()
            first_plan = respond('preview_isolate_export')['isolate_export_plan']
            assert first_plan['can_export'], first_plan
            expect(copy_button).to_be_enabled()
            expect(dialog.get_by_role('region', name='Reviewed isolate export', exact=True)).to_contain_text(first_plan['destination'])
            assert not list(destination.iterdir())
            assert {job['id'] for job in store.jobs()} == jobs_before
            assert original_bytes(fixture) == before
            # Changing a reviewed destination requires another explicit preview.
            dialog.get_by_label('Destination folder', exact=True).fill(str(other_destination))
            expect(copy_button).to_be_disabled()
            expect(dialog).to_contain_text('Destination changed')
            dialog.get_by_role('button', name='Preview export', exact=True).click()
            plan = respond('preview_isolate_export')['isolate_export_plan']
            assert plan['id'] != first_plan['id']
            expect(copy_button).to_be_enabled()
            capture(page, 'isolate-export-review-desktop')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            expect(copy_button).to_be_visible()
            capture(page, 'isolate-export-review-mobile')
            copy_button.click()
            result = respond('start_isolate_export')
            job_id = result['job_id']
            expect(dialog).to_have_count(0)
            wait_for_export(store, job_id)
            receipt = store.get('isolate_export_receipts', job_id)
            assert_portable_export(fixture, receipt)
            assert not list(destination.iterdir())
            assert original_bytes(fixture) == before
            page.evaluate('''workspace => {
                Object.assign(window.acceptanceWorkspace, workspace);
                window.acceptanceRender(null);
            }''', snapshot(store))
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('link', name='Samples', exact=True).click()
            row = page.locator('[data-isolate-export="' + job_id + '"]')
            row.get_by_role('button', name='View export receipt', exact=True).click()
            respond('isolate_export_receipt')
            receipt_dialog = page.get_by_role('dialog', name='Isolate export receipt', exact=True)
            expect(receipt_dialog).to_contain_text('SHA-256 verified')
            expect(receipt_dialog).to_contain_text('Cloud upload is not verified')
            expect(receipt_dialog).to_contain_text('Original files have not been deleted')
            expect(receipt_dialog.get_by_role('button', name='Open destination folder', exact=True)).to_be_enabled()
            capture(page, 'isolate-export-receipt-desktop')
            assert not errors, errors
            browser.close()
    finally:
        finish_workers(children)


def test_missing_scope_and_unverified_attempt_are_visible_without_copying(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    from bactologix.ui_backend import snapshot
    from bactologix.store import now
    fixture = isolate_fixture(tmp_path)
    store = fixture['store']
    unavailable = Path(fixture['genescope']['output'])
    (unavailable / 'descriptor-result.txt').unlink()
    unavailable.rmdir()
    before = original_bytes(fixture)
    destination = tmp_path / 'selected destination'
    destination.mkdir()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        render_component(page, store)
        respond = responder(page, store)
        page.get_by_role('button', name='Open navigation', exact=True).click()
        page.get_by_role('link', name='Samples', exact=True).click()
        page.locator('[data-sample-row="isolate-07"]').get_by_role('button', name='Export isolate', exact=True).click()
        respond('isolate_details')
        dialog = page.get_by_role('dialog', name='Isolate · isolate-07', exact=True)
        dialog.get_by_label('Destination folder', exact=True).fill(str(destination))
        dialog.get_by_role('button', name='Preview export', exact=True).click()
        plan = respond('preview_isolate_export')['isolate_export_plan']
        assert plan['scope_status'] == 'partial'
        assert str(unavailable) in plan['missing']
        expect(dialog).to_contain_text('Partial scope · review unavailable items')
        expect(dialog).to_contain_text(str(unavailable))
        assert not list(destination.iterdir())
        assert original_bytes(fixture) == before
        capture(page, 'isolate-export-partial-scope-mobile')
        dialog.get_by_role('button', name='Close dialog', exact=True).click()
        # Persisted interrupted attempts may have no copy receipt at all. The
        # UI must still expose them without inventing successful verification.
        jid = store.submit('isolate_export', {'plan_id': plan['id'], 'token': plan['token']})
        store.update_job(jid, status='interrupted', finished=now(), error='Process stopped before copying began.')
        page.evaluate('''workspace => {
            Object.assign(window.acceptanceWorkspace, workspace);
            window.acceptanceRender(null);
        }''', snapshot(store))
        page.locator('[data-isolate-export="' + jid + '"]').get_by_role('button', name='View export receipt', exact=True).click()
        result = respond('isolate_export_receipt')['isolate_export_receipt']
        assert result['job_status'] == 'interrupted'
        assert result['verification'] != 'verified-local'
        receipt = page.get_by_role('dialog', name='Isolate export receipt', exact=True)
        expect(receipt).to_contain_text('Incomplete')
        expect(receipt).to_contain_text('Keep the original files')
        expect(receipt).to_contain_text('No copy receipt is available')
        expect(receipt).not_to_contain_text('SHA-256 verified')
        assert receipt.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not list(destination.iterdir())
        assert original_bytes(fixture) == before
        browser.close()
