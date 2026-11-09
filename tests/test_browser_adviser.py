"""Browser contract checks for explicit, run-local setup advice."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

from bactologix import run_config
from bactologix.store import Store
from bactologix.ui_backend import dispatch, snapshot
from test_browser import customize_compute, render_component

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def adviser_fixture(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = tmp_path / 'source'
    source.mkdir()
    controller = tmp_path / 'controller'
    controller.mkdir()
    item = {'id': 'adviser-fixture', 'tool': 'hybas', 'source': str(source),
            'controller': str(controller), 'ready': True,
            'version': {'commit': '1' * 40, 'ref': 'Browser contract fixture'}}
    store.put('installs', item['id'], item)
    store.put('settings', 'active-hybas', item['id'])
    for name in ('one', 'two'):
        folder = tmp_path / name
        folder.mkdir()
        store.put('samples', name, {'id': name, 'path': str(folder), 'mode': 'hybrid'})
    store.put('configs', 'hybas', 'polish_mode: racon\nracon_rounds: 5\ncustom_note: keep me\n')
    return store


def advice_document(payload, store):
    """Synthetic advice exercises the UI contract; source verification has unit coverage."""
    text = run_config.compile_config(payload['tool'], payload['config']['text'], payload['config'].get('changes'))
    cfg = yaml.safe_load(text)
    current = cfg.get('racon_rounds')
    item = store.get('installs', 'adviser-fixture')
    return {'tool': payload['tool'], 'signature': hashlib.sha256(text.encode()).hexdigest(),
            'source': {'commit': item['version']['commit'], 'label': item['version']['ref'],
                       'verified': True, 'config_path': 'config.yaml', 'config_sha256': 'a' * 64},
            'samples': [{'id': sid, 'mode': 'hybrid', 'inputs': [
                {'label': 'Read files', 'status': 'Declared', 'detail': 'File presence does not establish read quality.'}]
                } for sid in payload['ids']],
            'checks': [{'level': 'warning', 'title': 'Review <sample> quality',
                        'detail': 'No quality estimate is inferred from a folder name.'}],
            'settings': [{'name': 'racon_rounds', 'key': '["racon_rounds"]', 'current': current,
                          'current_origin': 'Draft setting', 'default': 2,
                          'origin': 'Selected source template: config.yaml',
                          'detail': 'A starting value, not an estimate of the optimal number of rounds.',
                          'applicable': True, 'change': 2 if current != 2 else None},
                         {'name': 'medaka_model', 'key': '["medaka_model"]', 'current': None,
                          'current_origin': 'Not set; native default not inferred', 'default': None,
                          'origin': 'Not recorded', 'detail': 'Basecaller metadata is unavailable.',
                          'applicable': False, 'change': None}],
            'limits': ['Workflow starting values are not sample-specific optimal values.',
                       'A native dry-run is still required.']}


def contract_responder(page, store):
    def respond(kind):
        request = page.evaluate('window.acceptanceEvents.at(-1).value')
        assert request['kind'] == kind
        payload = request['payload']
        if kind == 'inspect_setup_advice':
            result = {'setup_advice': advice_document(payload, store), 'keep_modal': True}
        elif kind == 'apply_setup_advice':
            reviewed = advice_document(payload, store)
            assert payload['signature'] == reviewed['signature']
            selected = next(row for row in reviewed['settings'] if row['name'] == payload['setting'])
            text = run_config.compile_config(payload['tool'], payload['config']['text'], payload['config'].get('changes'))
            text = run_config.compile_config(payload['tool'], text, {selected['key']: selected['change']})
            updated = {**payload, 'config': {'tool': payload['tool'], 'text': text, 'changes': {}}}
            result = {'run_config': run_config.editor(payload['tool'], text),
                      'run_config_view': 'settings', 'setup_advice': advice_document(updated, store),
                      'message': 'Source value applied to this unsaved draft.', 'keep_modal': True}
        else:
            result = dispatch(store, kind, payload)
        page.evaluate('''({response, workspace}) => {
            Object.assign(window.acceptanceWorkspace, workspace);
            window.acceptanceRender(response);
        }''', {'response': {'id': request['id'], 'ok': True, **result}, 'workspace': snapshot(store)})
        return request
    return respond


def open_settings(page, respond):
    page.get_by_role('link', name='Runs', exact=True).click()
    page.get_by_role('button', name='Create run drafts', exact=True).first.click()
    page.locator('[data-action="run-purpose"][data-id="hybas"]').click()
    page.get_by_role('button', name='Continue to inputs', exact=True).click()
    respond('run_config_editor')
    page.locator('[data-sample="one"]').check()
    page.get_by_role('button', name='Continue to settings', exact=True).click()


def test_advice_requires_explicit_apply_and_expires_after_edits(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    store = adviser_fixture(tmp_path)
    original = copy.deepcopy({name: store.all(name) for name in ('configs', 'samples', 'runs')})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.route('http://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
        page.goto('http://workspace.test/')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        respond = contract_responder(page, store)
        open_settings(page, respond)
        panel = page.get_by_role('region', name='Setup adviser', exact=True)
        expect(panel).to_be_visible()
        assert page.evaluate('window.acceptanceEvents.length') == 1
        panel.get_by_role('button', name='Review setup', exact=True).focus()
        page.keyboard.press('Enter')
        request = respond('inspect_setup_advice')
        assert request['payload']['ids'] == ['one']
        assert request['payload']['tool'] == 'hybas'
        assert request['payload']['config']['text'] == store.get('configs', 'hybas')
        expect(panel.get_by_role('heading', name='Setup adviser', exact=True)).to_be_focused()
        expect(panel).to_contain_text('starting values, not sample-specific optima')
        expect(panel).to_contain_text('Review <sample> quality')
        assert panel.locator('sample').count() == 0
        setting = panel.locator('[data-advice-setting="racon_rounds"]')
        expect(setting).to_contain_text('Draft setting')
        expect(setting).to_contain_text('Selected-source starting value')
        expect(panel.locator('[data-advice-setting="medaka_model"] button')).to_have_count(0)
        setting.get_by_role('button', name='Use source value', exact=True).focus()
        page.keyboard.press('Enter')
        request = respond('apply_setup_advice')
        assert request['payload']['setting'] == 'racon_rounds' and request['payload']['signature']
        expect(setting.get_by_role('button', name='Use source value', exact=True)).to_have_count(0)
        expect(panel.get_by_role('heading', name='Setup adviser', exact=True)).to_be_focused()
        page.locator('[data-action="guided-run-tab"][data-id="assembly"]').click()
        rounds = page.locator('[data-run-name="racon_rounds"]')
        expect(rounds).to_have_value('2')
        rounds.fill('7')
        expect(panel.locator('[data-setup-advice-status]')).to_contain_text('Setup changed')
        panel.get_by_role('button', name='Refresh review', exact=True).click()
        request = respond('inspect_setup_advice')
        assert request['payload']['config']['changes']['["racon_rounds"]'] == '7'
        expect(setting.get_by_role('button', name='Use source value', exact=True)).to_be_enabled()
        customize_compute(page)
        page.get_by_label('CPU cores per run', exact=True).fill('3')
        expect(setting.get_by_role('button', name='Use source value', exact=True)).to_be_disabled()
        panel.get_by_role('button', name='Refresh review', exact=True).click()
        request = respond('inspect_setup_advice')
        assert request['payload']['cores'] == 3
        # Checking settings serializes edits but does not change their values or invalidate advice.
        page.get_by_role('button', name='Review drafts', exact=True).click()
        respond('run_config_editor')
        expect(setting.get_by_role('button', name='Use source value', exact=True)).to_be_enabled()
        page.locator('[data-action="run-config-tab"][data-id="settings"]').first.click()
        page.locator('[data-action="guided-run-tab"][data-id="advanced"]').click()
        page.get_by_role('button', name='Full YAML', exact=True).click()
        respond('run_config_editor')
        editor = page.get_by_label('Run configuration YAML', exact=True)
        assert yaml.safe_load(editor.input_value())['custom_note'] == 'keep me'
        editor.fill(editor.input_value() + '\ncustom_more: [false, 0, retained]\n')
        page.get_by_role('button', name='Back to controls', exact=True).click()
        respond('run_config_editor')
        expect(setting.get_by_role('button', name='Use source value', exact=True)).to_be_disabled()
        panel.get_by_role('button', name='Refresh review', exact=True).click()
        respond('inspect_setup_advice')
        # Changing then restoring the sample selection still requires another review.
        page.locator('[data-action="run-config-tab"][data-id="samples"]').first.click()
        page.locator('[data-sample="two"]').check()
        page.locator('[data-sample="two"]').uncheck()
        page.get_by_role('button', name='Continue to settings', exact=True).click()
        expect(setting.get_by_role('button', name='Use source value', exact=True)).to_be_disabled()
        panel.get_by_role('button', name='Refresh review', exact=True).click()
        respond('inspect_setup_advice')
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            expect(panel.get_by_role('button', name='Refresh review', exact=True)).to_be_visible()
            assert panel.evaluate('(node) => node.scrollWidth <= node.clientWidth + 1')
            assert page.locator('.run-wizard').evaluate('(node) => node.scrollWidth <= node.clientWidth + 1')
        destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
        if destination:
            Path(destination).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(destination) / 'setup-adviser-mobile.png'), full_page=True)
        assert {name: store.all(name) for name in original} == original
        assert not store.jobs()
        assert not errors, errors
        browser.close()


def test_advice_source_changes_and_browser_recovery_require_fresh_review(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    store = adviser_fixture(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.route('http://workspace.test/**', lambda route: route.fulfill(body='<html><body></body></html>'))
        page.goto('http://workspace.test/')
        render_component(page, store)
        respond = contract_responder(page, store)
        open_settings(page, respond)
        panel = page.get_by_role('region', name='Setup adviser', exact=True)
        panel.get_by_role('button', name='Review setup', exact=True).click()
        respond('inspect_setup_advice')
        item = store.get('installs', 'adviser-fixture')
        item['version']['commit'] = '2' * 40
        store.put('installs', item['id'], item)
        page.evaluate('workspace => {Object.assign(window.acceptanceWorkspace, workspace); window.acceptanceRender(null);}', snapshot(store))
        expect(panel.get_by_role('button', name='Use source value', exact=True)).to_be_disabled()
        panel.get_by_role('button', name='Refresh review', exact=True).click()
        respond('inspect_setup_advice')
        expect(panel.get_by_role('button', name='Use source value', exact=True)).to_be_enabled()
        # Advice is never serialized with browser recovery; edited settings still are.
        saved = json.loads(page.evaluate('sessionStorage.getItem("bactologix:run-editor:v1:" + window.acceptanceWorkspace.workspace)'))
        assert 'setupAdvice' not in saved['saved'] and 'setupAdviceContext' not in saved['saved']
        page.reload()
        render_component(page, store)
        expect(page.get_by_text('Recovered unfinished edits', exact=True)).to_be_visible()
        expect(panel.get_by_role('button', name='Review setup', exact=True)).to_be_visible()
        expect(panel.get_by_role('button', name='Use source value', exact=True)).to_have_count(0)
        assert not store.all('runs') and not store.jobs()
        browser.close()
