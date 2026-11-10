"""Browser acceptance for source-bound Methods, diagrams and explicit evidence limits."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import zipfile

import pytest

from bactologix import methods
from bactologix.ui_backend import dispatch, snapshot
from test_browser import render_component
from test_methods import run
from test_native import workspace, installation, sample

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def respond(page, store, kind, edit=None):
    request = page.evaluate('window.acceptanceEvents.at(-1).value')
    assert request['kind'] == kind
    result = dispatch(store, kind, request['payload'])
    if edit:
        edit(result)
    page.evaluate('''({workspace,response}) => {
        Object.assign(window.acceptanceWorkspace, workspace);
        window.acceptanceRender(response);
    }''', {'workspace': snapshot(store), 'response': {'id': request['id'], 'ok': True, **result}})
    return request, result


def open_methods(page, store, run, edit=None):
    page.route('https://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
    page.goto('https://workspace.test/')
    render_component(page, store)
    page.get_by_role('link', name='Runs', exact=True).click()
    page.locator('[data-action="run-detail"][data-id="' + run['id'] + '"]').first.click()
    respond(page, store, 'inspect_run')
    page.get_by_role('button', name='Methods & citation', exact=True).click()
    return respond(page, store, 'inspect_methods', edit)[1]['methods']


def capture(page, name):
    destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / (name + '.png')), full_page=True)


def test_run_specific_methods_diagram_tools_and_readonly_exports(workspace, run):
    from playwright.sync_api import sync_playwright, expect
    original_run = copy.deepcopy(workspace.get('runs', run['id']))
    original_jobs = workspace.jobs()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        doc = open_methods(page, workspace, run)
        panel = page.locator('.methods-export')
        expect(page.get_by_role('heading', name='Run-specific Methods', exact=True)).to_be_visible()
        expect(panel.locator('.methods-validation')).to_contain_text('scientific validation not established')
        expect(panel.locator('.methods-status')).to_contain_text('Planned methods')
        controller = page.get_by_role('complementary', name='Workflow controller', exact=True)
        expect(controller).to_contain_text('Snakemake')
        expect(controller).to_contain_text('Not recorded')
        expect(controller).to_contain_text('orchestration software')
        graph = page.get_by_role('region', name='Run-specific data flow', exact=True)
        expect(graph).to_be_visible()
        assert doc['flow']['run_id'] == run['id'] and doc['flow']['known_source']
        assert doc['workflow_steps']
        image = graph.locator('.methods-flow-figure img')
        expect(image).to_have_attribute('src', __import__('re').compile(r'^data:image/svg\+xml;'))
        expect(image).to_have_attribute('alt', __import__('re').compile(run['id']))
        page.wait_for_function('''() => {
            const image = document.querySelector('#component-host').shadowRoot.querySelector('.methods-flow-figure img');
            return image && image.complete && image.naturalWidth > 0;
        }''')
        expect(graph.locator('.methods-tool-card')).to_have_count(len(doc['workflow_steps']))
        first = graph.locator('.methods-tool-card').first
        expect(first).to_contain_text('What it does')
        expect(first).to_contain_text('Why it is included')
        first.locator('summary').click()
        expect(first).to_contain_text('Inputs')
        expect(first).to_contain_text('Outputs')
        assert graph.locator('.methods-flow-figure svg, .methods-flow-scroll svg').count() == 0, 'The generated SVG must remain isolated as an image.'
        for label, fmt in [('Diagram SVG', 'svg'), ('Diagram JSON', 'json'), ('Reproducibility ZIP', 'zip')]:
            with page.expect_download() as event:
                page.get_by_role('button', name=label, exact=True).click()
                request, _ = respond(page, workspace, 'download_methods')
            assert request['payload'] == {'id': run['id'], 'format': fmt}
            downloaded = Path(event.value.path())
            if fmt == 'svg':
                assert '<svg' in downloaded.read_text() and run['id'] in downloaded.read_text()
            elif fmt == 'json':
                assert json.loads(downloaded.read_text())['run_id'] == run['id']
            else:
                with zipfile.ZipFile(downloaded) as archive:
                    assert {'methods.html', 'methods-flow.svg', 'methods-flow.json'} <= set(archive.namelist())
                    assert json.loads(archive.read('methods-flow.json'))['run_id'] == run['id']
                    assert 'scientific validation' in archive.read('methods.html').decode().lower()
        graph.scroll_into_view_if_needed()
        capture(page, 'run-methods-flow-desktop')
        page.set_viewport_size({'width': 390, 'height': 844})
        graph.scroll_into_view_if_needed()
        assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        summary = graph.get_by_text('View diagram at full size', exact=True)
        summary.focus()
        page.keyboard.press('Enter')
        full = page.get_by_role('region', name='Full-size data-flow diagram', exact=True)
        expect(full).to_be_visible()
        full.focus()
        expect(full).to_be_focused()
        assert full.evaluate('(el) => el.scrollWidth > el.clientWidth')
        page.keyboard.press('ArrowRight')
        assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        capture(page, 'run-methods-flow-mobile')
        assert workspace.get('runs', run['id']) == original_run
        assert workspace.jobs() == original_jobs and errors == []
        assert {item['value']['kind'] for item in page.evaluate('window.acceptanceEvents')} <= {
            'inspect_run', 'inspect_methods', 'download_methods'}
        browser.close()


def test_legacy_unknown_source_keeps_limits_and_escapes_methods_fields(workspace, run, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    monkeypatch.setitem(methods.DEFAULTS['sources'], 'hybas', 'f' * 40)
    run['completed'] = '2026-10-27T10:00:00+00:00'
    workspace.put('runs', run['id'], run)
    job = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(job, status='succeeded')
    original_jobs = copy.deepcopy(workspace.jobs())
    original_run = copy.deepcopy(workspace.get('runs', run['id']))
    injected = '<script>window.methodsInjected=true</script>'
    def edit(result):
        # Display fixtures stress the HTML boundary without altering stored evidence.
        doc = result['methods']
        doc['validation_status'] += ' ' + injected
        doc['flow']['caption'] += ' ' + injected
        doc['paragraphs'].append(injected)
        doc['software'] = [row for row in doc['software'] if row['tool'] != 'snakemake'] + [
            {'tool': 'snakemake', 'label': 'Snakemake', 'version': '9.6.2-display-fixture',
             'scope': 'Display fixture only; no runtime claim. ' + injected}]
        doc['flow_svg'] = doc['flow_svg'].replace('</svg>',
            '<script>window.methodsInjected=true</script></svg>')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        doc = open_methods(page, workspace, run, edit)
        assert doc['flow']['known_source'] is False
        panel = page.locator('.methods-export')
        expect(panel.locator('.methods-status')).to_contain_text('Completed workflow')
        expect(panel.locator('.methods-validation')).to_contain_text('scientific validation not established')
        expect(panel.locator('.methods-validation')).to_contain_text(injected)
        expect(panel.locator('.methods-flow-caption')).to_contain_text(injected)
        assert panel.locator('script').count() == 0
        assert page.evaluate('window.methodsInjected') is None
        expect(panel.locator('.methods-prose')).to_contain_text(injected)
        expect(panel.locator('.methods-flow-legend')).to_contain_text('does not establish that every tool ran')
        expect(panel.locator('.methods-controller')).to_contain_text('Snakemake')
        expect(panel.locator('.methods-controller')).to_contain_text('9.6.2-display-fixture')
        expect(panel.locator('.methods-controller')).to_contain_text(injected)
        assert workspace.get('runs', run['id']) == original_run
        assert workspace.jobs() == original_jobs and errors == []
        browser.close()
