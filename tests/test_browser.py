"""Real Chromium -> Streamlit -> SQLite checks, enabled in the browser CI job."""
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
import yaml
from bactologix.store import Store
from bactologix.catalog import PIPELINES
from bactologix.runs import create_run
from test_databases import busco
from test_maintenance import installed

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def test_sample_picker_configuration_and_report(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    workspace = tmp_path / 'workspace'
    store = Store(workspace)
    for tool, recipe in [('hybas', 'workflow/envs/qc.yml'), ('basis', 'envs/diamond.yml')]:
        if tool == 'hybas':
            source = Path(os.environ['BACTOLOGIX_HYBAS_SOURCE'])
        else:
            source = tmp_path / (tool + '-ui-fixture')
            (source / recipe).parent.mkdir(parents=True)
            (source / recipe).write_text('dependencies: [python=3.11]\n')
        item = {'id': tool + '-ui-fixture', 'tool': tool, 'source': str(source),
                'controller': '/ui-fixture/runner', 'ready': True,
                'version': {'commit': PIPELINES[tool]['commit'], 'ref': 'Browser UI fixture'}}
        store.put('installs', item['id'], item)
        store.put('settings', 'active-' + tool, item['id'])
    old_cache = tmp_path / 'previous-environments'
    (old_cache / 'fixture/conda-meta').mkdir(parents=True)
    (old_cache / 'fixture/conda-meta/history').write_text('retained software fixture')
    old_id = 'hybas-older-ui-fixture'
    old_source = store.home / 'sources' / old_id
    hybas = store.get('installs', 'hybas-ui-fixture')
    shutil.copytree(hybas['source'], old_source)
    store.put('installs', old_id, {**hybas, 'id': old_id, 'source': str(old_source),
              'original_source': hybas['source'], 'rule_cache': str(old_cache),
              'version': {**hybas['version'], 'ref': 'Older reusable fixture'}})
    conda = tmp_path / 'conda-fixture'
    conda.write_text('#!/bin/sh\nexit 1\n')
    conda.chmod(0o755)
    store.put('settings', 'conda', str(conda))
    download = tmp_path / 'download destination with spaces'
    download.mkdir()
    card = download / 'card'
    card.mkdir()
    (card / 'card.json').write_text('{"software_fixture": true}')
    store.put('database_downloads', 'database-browser-fixture', {
        'id': 'database-browser-fixture', 'tool': 'basis', 'resource': 'rgi', 'destination': str(download),
        'result_path': str(card), 'status': 'completed', 'job': 'software-fixture-job'})
    sample = tmp_path / 'sample folder'
    (sample / 'raw/illumina').mkdir(parents=True)
    for mate in (1, 2):
        (sample / f'raw/illumina/fixture_R{mate}.fastq').write_text('@fixture\nACGT\n+\nIIII\n')
    parent = create_run(store, 'hybas', {'id': 'browser.fixture', 'path': str(sample), 'mode': 'illumina_only'},
                        'run_busco: true\nrun_kraken: false\ncustom_note: preserve parent\n', label='Pre-scan draft')
    lineage = busco(tmp_path / 'saved database with spaces/bacteria_odb10')
    report = tmp_path / 'report.html'
    report.write_text('<html><body><h1>Browser software fixture</h1></body></html>')
    store.put('reports', 'report-fixture', {'id': 'report-fixture', 'name': 'Fixture report',
              'path': str(report), 'sample': 'Software fixture', 'run': 'External report'})
    with socket.socket() as port_socket:
        port_socket.bind(('127.0.0.1', 0))
        port = port_socket.getsockname()[1]
    log = (tmp_path / 'server.log').open('w')
    proc = subprocess.Popen([str(Path(sys.executable).with_name('bactologix')), '--home', str(workspace),
                             '--host', '127.0.0.1', '--port', str(port), '--no-browser'],
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/_stcore/health', timeout=1) as response:
                    if response.status == 200: break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail((tmp_path / 'server.log').read_text())
                time.sleep(.2)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            def capture(name):
                destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
                if destination:
                    folder = Path(destination)
                    folder.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(folder / (name + '.png')), full_page=True)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.locator('#queue-status')).to_have_text('No active jobs')
            expect(page.locator('.local-status')).not_to_contain_text('connected')
            page.get_by_role('button', name='Add samples', exact=True).click()
            page.get_by_role('button', name='Browse folders', exact=True).click()
            page.locator('#folder-path').fill(str(sample))
            page.get_by_role('button', name='Go', exact=True).click()
            expect(page.locator('.folder-location')).to_contain_text(str(sample))
            page.get_by_role('button', name='Select this folder', exact=True).click()
            page.locator('#sample-id').fill('browser.fixture')
            page.get_by_role('button', name='Register sample', exact=True).click()
            expect(page.get_by_role('heading', name='Your input folders.', exact=True)).to_be_visible()
            assert Store(workspace).get('samples', 'browser.fixture')['path'] == str(sample)
            page.get_by_role('link', name='Configuration', exact=True).click()
            expect(page.get_by_role('heading', name='Your defaults, your settings.', exact=True)).to_be_visible()
            def defaults_tab(name):
                page.locator('.defaults-editor [data-action="guided-run-tab"][data-id="' + name + '"]').click()
            def defaults_pipeline(name):
                page.locator('[data-action="config-tool"][data-id="' + name + '"]').click()
            expect(page.locator('.defaults-editor .mode-card')).to_have_count(4)
            expect(page.get_by_label('Full configuration', exact=True)).to_have_count(0)
            capture('configuration-hybas')
            defaults_tab('quality')
            page.locator('[data-run-name="run_busco"]').uncheck()
            expect(page.locator('#default-save-status')).to_have_text('Unsaved changes')
            defaults_pipeline('basis')
            page.locator('[data-run-name="modules.rgi.enabled"]').uncheck()
            capture('configuration-basis')
            defaults_pipeline('hybas')
            expect(page.locator('[data-run-name="run_busco"]')).not_to_be_checked()
            assert Store(workspace).get('configs', 'hybas') is None
            assert Store(workspace).get('configs', 'basis') is None
            defaults_tab('advanced')
            page.get_by_label('Search all settings', exact=True).fill('run_busco')
            expect(page.locator('[data-run-name="run_busco"]')).not_to_be_checked()
            capture('configuration-advanced')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('.defaults-editor').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            expect(page.get_by_role('button', name='Save defaults', exact=True)).to_be_visible()
            capture('configuration-mobile')
            page.get_by_role('button', name='Open navigation', exact=True).click()
            expect(page.get_by_role('link', name='Databases & models', exact=True)).to_be_visible()
            expect(page.get_by_role('link', name='Install & update', exact=True)).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.get_by_role('button', name='Open navigation', exact=True)).to_be_focused()
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('button', name='Full YAML', exact=True).click()
            defaults_yaml = page.get_by_label('Full configuration', exact=True)
            assert yaml.safe_load(defaults_yaml.input_value())['run_busco'] is False
            defaults_yaml.fill('broken: [')
            page.get_by_role('button', name='Back to controls', exact=True).click()
            expect(page.get_by_role('alert')).not_to_be_empty()
            expect(defaults_yaml).to_have_value('broken: [')
            assert Store(workspace).get('configs', 'hybas') is None
            imported_defaults = 'threads: 2\ncustom_note: browser acceptance\ncustom_defaults: {literal.key: [false, 0]}\n'
            page.locator('#yaml-upload').set_input_files({'name': 'defaults.yaml', 'mimeType': 'text/yaml',
                                                        'buffer': imported_defaults.encode()})
            expect(defaults_yaml).to_have_value(imported_defaults)
            page.get_by_role('button', name='Back to controls', exact=True).click()
            page.get_by_label('Search all settings', exact=True).fill('custom_note')
            expect(page.locator('[data-run-name="custom_note"]')).to_have_value('browser acceptance')
            with page.expect_download() as exported:
                page.get_by_role('button', name='Download YAML', exact=True).click()
            exported_config = yaml.safe_load(Path(exported.value.path()).read_text())
            assert exported_config['custom_defaults'] == {'literal.key': [False, 0]}
            page.get_by_role('button', name='Save defaults', exact=True).first.click()
            expect(page.locator('#toast')).to_contain_text('Defaults saved')
            expect(page.locator('#default-save-status')).to_have_text('Saved defaults')
            assert 'browser acceptance' in Store(workspace).get('configs', 'hybas')
            assert Store(workspace).get('runs', parent['id']) == parent
            defaults_pipeline('basis')
            expect(page.locator('[data-run-name="modules.rgi.enabled"]')).not_to_be_checked()
            page.get_by_role('button', name='Reload saved defaults', exact=True).click()
            expect(page.locator('[data-run-name="modules.rgi.enabled"]')).to_be_checked()
            defaults_pipeline('hybas')
            page.get_by_role('link', name='Databases & models', exact=True).click()
            page.get_by_role('button', name=PIPELINES['basis']['name'], exact=True).click()
            card_panel = page.locator('.database-card').filter(has=page.get_by_role('heading', name='CARD / RGI', exact=True))
            card_panel.get_by_role('button', name='Download and prepare', exact=True).click()
            page.get_by_label('Download location', exact=True).fill(str(tmp_path))
            page.get_by_role('button', name='Browse', exact=True).last.click()
            page.locator('#folder-path').fill(str(download))
            page.get_by_role('button', name='Go', exact=True).click()
            expect(page.locator('.folder-location')).to_contain_text(str(download))
            page.get_by_role('button', name='Select this folder', exact=True).click()
            expect(page.get_by_label('Download location', exact=True)).to_have_value(str(download))
            expect(page.get_by_role('dialog').get_by_role('button', name='Download and prepare', exact=True)).to_be_enabled()
            expect(page.get_by_role('dialog')).to_contain_text('may first install the tool environment')
            capture('database-download-review')
            page.get_by_role('button', name='Close dialog', exact=True).click()
            card_panel.get_by_role('button', name='Select downloaded location', exact=True).click()
            expect(page.get_by_label('CARD / RGI location', exact=True)).to_have_value(str(card / 'card.json'))
            page.get_by_role('button', name='Save for future runs', exact=True).first.click()
            expect(page.locator('#toast')).to_contain_text('Database locations saved')
            assert yaml.safe_load(Store(workspace).get('configs', 'basis'))['modules']['rgi']['database'] == str(card / 'card.json')
            page.get_by_role('button', name=PIPELINES['hybas']['name'], exact=True).click()
            page.get_by_label('BUSCO lineage location', exact=True).fill(str(lineage))
            page.get_by_role('button', name='Save for future runs', exact=True).first.click()
            expect(page.locator('#toast')).to_contain_text('Database locations saved')
            assert Store(workspace).get('runs', parent['id'])['config'] == parent['config']
            page.get_by_role('button', name='Update saved drafts', exact=True).click()
            page.locator('[data-apply-run="' + parent['id'] + '"]').check()
            page.get_by_role('button', name='Review database changes', exact=True).click()
            expect(page.locator('[data-apply-resource="busco"]')).to_be_checked()
            expect(page.locator('[data-apply-resource="kraken"]')).to_be_disabled()
            expect(page.locator('.database-apply-run')).to_contain_text(str(lineage))
            page.get_by_role('button', name='Use saved databases', exact=True).click()
            expect(page.get_by_role('heading', name='Pre-scan draft · saved databases', exact=True)).to_be_visible()
            child, = [r for r in Store(workspace).all('runs') if r.get('parent') == parent['id']]
            cfg = yaml.safe_load(child['config'])
            assert (Path(cfg['db_root']) / 'busco' / cfg['busco_lineage']).resolve() == lineage
            assert cfg['custom_note'] == 'preserve parent' and cfg['run_busco'] is True
            assert not child['dry_run_ok']
            assert Store(workspace).get('runs', parent['id'])['config'] == parent['config']
            page.get_by_role('button', name='Methods', exact=True).click()
            expect(page.locator('.methods-prose')).to_contain_text('Illumina-only input')
            expect(page.locator('.methods-status')).to_contain_text('Planned methods')
            capture('run-methods')
            with page.expect_download() as methods_download:
                page.get_by_role('button', name='Reproducibility ZIP', exact=True).click()
            import zipfile
            with zipfile.ZipFile(methods_download.value.path()) as methods_zip:
                assert methods_zip.read('config.yaml').decode() == child['config']
                assert b'Planned methods' in methods_zip.read('methods.txt')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('.methods-export').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            capture('run-methods-mobile')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('button', name='Use saved databases', exact=True).click()
            expect(page.locator('[data-apply-resource="busco"]')).to_be_disabled()
            expect(page.locator('.database-apply-run')).to_contain_text('Already using this saved location')
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.get_by_role('link', name='Samples', exact=True).click()
            expect(page.locator('.saved-runs')).to_have_count(0)
            page.locator('.registered-samples').get_by_role('button', name='Remove sample', exact=True).click()
            expect(page.locator('.registered-samples')).to_contain_text('0 folders')
            assert Store(workspace).get('samples', 'browser.fixture') is None
            page.get_by_role('link', name='Runs', exact=True).click()
            expect(page.locator('.registered-samples')).to_have_count(0)
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(0)
            page.locator('[data-action="run-list"][data-id="archived"]').click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(2)
            page.locator('[data-run-row="' + parent['id'] + '"]').get_by_role('button', name='Restore', exact=True).click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(1)
            page.locator('[data-action="run-list"][data-id="current"]').click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(1)
            page.locator('[data-run-row="' + parent['id'] + '"]').get_by_role('button', name='Archive', exact=True).click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(0)
            assert Store(workspace).get('runs', parent['id']) == parent
            for mate in (1, 2):
                assert (sample / f'raw/illumina/fixture_R{mate}.fastq').read_text() == '@fixture\nACGT\n+\nIIII\n'
            def register_again():
                page.get_by_role('link', name='Samples', exact=True).click()
                page.get_by_role('button', name='Add sample', exact=True).first.click()
                page.locator('#sample-id').fill('browser.fixture')
                page.locator('#sample-path').fill(str(sample))
                page.locator('#sample-mode').select_option('illumina_only')
                page.get_by_role('button', name='Register sample', exact=True).click()
                expect(page.locator('.registered-samples')).to_contain_text('1 folder')
            register_again()
            page.get_by_role('link', name='Runs', exact=True).click()
            page.get_by_role('button', name='Create run drafts', exact=True).first.click()
            page.locator('#run-cores').fill('3')
            page.locator('[data-sample="browser.fixture"]').check()
            page.locator('.run-wizard').get_by_role('button', name='Remove sample', exact=True).click()
            expect(page.get_by_role('heading', name='Create run drafts', exact=True)).to_be_visible()
            expect(page.locator('#run-cores')).to_have_value('3')
            expect(page.locator('[data-sample="browser.fixture"]')).to_have_count(0)
            expect(page.get_by_role('button', name='Continue to settings', exact=True)).to_be_disabled()
            assert Store(workspace).get('samples', 'browser.fixture') is None
            page.get_by_role('button', name='Save & exit', exact=True).click()
            expect(page.locator('.draft-recovery')).to_be_visible()
            register_again()
            page.get_by_role('link', name='Runs', exact=True).click()
            page.get_by_role('button', name='Create run drafts', exact=True).first.click()
            expect(page.locator('[data-sample="browser.fixture"]')).not_to_be_checked()
            page.locator('[data-sample="browser.fixture"]').check()
            saved_defaults = Store(workspace).get('configs', 'hybas')
            page.get_by_role('button', name='Continue to settings', exact=True).click()
            capture('hybas-kraken')
            def category(name):
                page.locator('[data-action="guided-run-tab"][data-id="' + name + '"]').click()
            def choice(name, value):
                return page.locator('[data-choice-name="' + name + '"][data-value="' + value + '"]')
            choice('kraken_decision_mode', 'off').click()
            expect(choice('kraken_decision_mode', 'off')).to_have_attribute('aria-pressed', 'true')
            choice('kraken_decision_mode', 'aware').click()
            expect(choice('kraken_decision_mode', 'aware')).to_have_attribute('aria-pressed', 'true')
            choice('kraken_decision_mode', 'off').click()
            category('assembly')
            choice('polish_mode', 'racon').click()
            page.locator('[data-run-name="racon_rounds"]').fill('0')
            choice('polish_mode', 'none').click()
            capture('hybas-assembly')
            category('quality')
            page.locator('[data-run-name="run_busco"]').uncheck()
            category('databases')
            expect(page.locator('[data-run-name="db_root"]')).not_to_have_value('db')
            # Each pipeline retains its own edits while switching within the dialog.
            page.locator('#run-tool').select_option('basis')
            page.locator('[data-run-name="modules.rgi.enabled"]').uncheck()
            capture('basis-modules')
            category('aware')
            expect(choice('deep.aware.preset', 'auto-medium')).to_have_attribute('aria-pressed', 'true')
            category('deep')
            expect(page.locator('[data-run-name="deep.enabled"]')).not_to_be_checked()
            category('resources')
            expect(page.locator('[data-run-name="resources.directory"]')).to_be_visible()
            page.locator('#run-tool').select_option('hybas')
            category('kraken')
            expect(choice('kraken_decision_mode', 'off')).to_have_attribute('aria-pressed', 'true')
            # Narrow layouts retain reachable controls and no horizontal overflow.
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(choice('kraken_decision_mode', 'off')).to_be_visible()
            assert page.locator('.run-wizard').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            expect(page.get_by_role('dialog')).to_have_count(0)
            expect(page.locator('#wizard-save-status')).to_contain_text('Edits saved in this browser tab')
            capture('hybas-mobile')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            category('advanced')
            page.locator('#run-setting-search').fill('run_kraken')
            expect(page.locator('[data-run-name="run_kraken"]')).not_to_be_checked()
            page.get_by_role('button', name='Full YAML', exact=True).click()
            run_yaml = page.get_by_label('Run configuration YAML', exact=True)
            original_yaml = run_yaml.input_value()
            configured = yaml.safe_load(original_yaml)
            assert configured['kraken_decision_mode'] == 'off' and configured['run_kraken'] is False
            assert configured['run_busco'] is False and configured['racon_rounds'] == 0
            assert configured['polish_mode'] == 'none'
            run_yaml.fill('broken: [')
            page.reload()
            expect(run_yaml).to_have_value('broken: [')
            expect(page.locator('#run-cores')).to_have_value('3')
            expect(page.get_by_text('Recovered unfinished edits', exact=True)).to_be_visible()
            assert page.url.endswith('#drafts/new')
            page.get_by_role('button', name='Run settings', exact=True).click()
            expect(page.get_by_role('alert')).not_to_be_empty()
            expect(run_yaml).to_have_value('broken: [')
            assert len(Store(workspace).all('runs')) == 2
            run_yaml.fill(original_yaml + '\ncustom_menu: {literal.key: [false, 0, preserve]}\n')
            page.get_by_role('button', name='Run settings', exact=True).click()
            expect(page.locator('#run-setting-search')).to_be_visible()
            page.get_by_role('button', name='Review drafts', exact=True).click()
            expect(page.get_by_role('heading', name='Ready to create your drafts?', exact=True)).to_be_visible()
            expect(page.locator('.run-wizard .review-stats')).to_contain_text('3 cores')
            page.locator('#run-cores').fill('2')
            expect(page.locator('[data-review-budget="cores"]')).to_have_text('2 cores')
            page.locator('#run-cores').fill('3')
            capture('draft-review')
            page.get_by_role('button', name='Create drafts', exact=True).click()
            expect(page.get_by_role('heading', name='browser.fixture', exact=True)).to_be_visible()
            assert page.evaluate('Object.keys(sessionStorage).filter(k => k.startsWith("bactologix:run-editor:")).length') == 0
            created, = [r for r in Store(workspace).all('runs') if r['id'] not in {parent['id'], child['id']}]
            final_config = yaml.safe_load(created['config'])
            assert final_config['kraken_decision_mode'] == 'off' and final_config['run_kraken'] is False
            assert final_config['polish_mode'] == 'none' and final_config['run_busco'] is False
            assert final_config['custom_menu']['literal.key'] == [False, 0, 'preserve']
            assert final_config['custom_note'] == 'browser acceptance'
            assert created['cores'] == 3 and not created['dry_run_ok']
            assert Store(workspace).get('configs', 'hybas') == saved_defaults
            assert Store(workspace).get('runs', parent['id']) == parent
            # Saved-run details use the same visual categories, without exposed YAML.
            expect(page.get_by_role('heading', name='Your saved run', exact=True)).to_be_visible()
            expect(page.locator('.saved-code')).to_have_count(0)
            expect(page.locator('.readiness-item')).to_have_count(8)
            expect(page.locator('[data-readiness="destinations"]')).to_contain_text('Permissions checked')
            expect(page.locator('[data-readiness="storage"]')).to_contain_text('GB free')
            expect(page.locator('[data-readiness="databases"]')).to_contain_text('Frozen in this draft')
            expect(page.get_by_role('button', name='Validate run', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Check environments', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Run / resume', exact=True)).to_be_disabled()
            expect(page.get_by_role('dialog')).to_have_count(0)
            assert page.url.endswith('#runs/' + created['id'] + '/overview')
            expect(page.get_by_role('link', name='Runs', exact=True)).to_have_attribute('aria-current', 'page')
            capture('saved-run-overview')
            page.get_by_text('Folders and source version', exact=True).scroll_into_view_if_needed()
            page.get_by_role('link', name='Runs', exact=True).click()
            expect(page.get_by_role('heading', name='Drafts, activity and history.', exact=True)).to_be_in_viewport()
            page.locator('[data-run-row="' + created['id'] + '"] a.sample-name').click()
            expect(page.get_by_role('heading', name=created['label'], exact=True)).to_be_in_viewport()
            def saved_tab(name):
                target = page.locator('.saved-run-tabs [data-action="saved-run-tab"][data-id="' + name + '"]')
                if not target.is_visible():
                    page.locator('.saved-settings-nav > summary').click()
                target.click()
            saved_tab('kraken')
            expect(page.locator('[data-saved-setting="kraken_decision_mode"]')).to_have_text('Off')
            # Saved run URLs survive reload and browser Back/Forward.
            assert page.url.endswith('/kraken')
            page.reload()
            expect(page.locator('[data-saved-setting="kraken_decision_mode"]')).to_have_text('Off')
            page.get_by_role('link', name='Runs', exact=True).click()
            expect(page.locator('.saved-runs')).to_be_visible()
            page.go_back()
            expect(page.locator('[data-saved-setting="kraken_decision_mode"]')).to_have_text('Off')
            page.go_forward()
            expect(page.locator('.saved-runs')).to_be_visible()
            page.locator('[data-run-row="' + created['id'] + '"] a.sample-name').click()
            expect(page.locator('.run-workspace')).to_be_visible()
            saved_tab('databases')
            expect(page.locator('[data-saved-database="busco"]')).to_have_text(str(lineage))
            saved_tab('advanced')
            expect(page.locator('.saved-code').first).not_to_be_visible()
            page.locator('summary').filter(has_text='Frozen YAML').click()
            expect(page.locator('.saved-code').last).to_contain_text('custom_menu')
            saved_tab('overview')
            page.get_by_role('button', name='Edit settings', exact=True).click()
            expect(page.get_by_role('heading', name='Edit run settings', exact=True)).to_be_visible()
            expect(choice('kraken_decision_mode', 'off')).to_have_attribute('aria-pressed', 'true')
            choice('kraken_decision_mode', 'aware').click()
            # Navigating away and refreshing retain edits; discarding needs an explicit confirmation.
            page.get_by_role('button', name='Save & exit', exact=True).click()
            page.reload()
            expect(page.locator('.draft-recovery')).to_be_visible()
            page.get_by_role('button', name='Resume editing', exact=True).click()
            expect(choice('kraken_decision_mode', 'aware')).to_have_attribute('aria-pressed', 'true')
            page.get_by_role('button', name='Discard edits', exact=True).click()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            expect(choice('kraken_decision_mode', 'aware')).to_have_attribute('aria-pressed', 'true')
            page.get_by_role('button', name='Discard edits', exact=True).click()
            page.get_by_role('button', name='Discard unfinished edits', exact=True).click()
            page.locator('[data-run-row="' + created['id'] + '"] a.sample-name').click()
            expect(page.get_by_role('heading', name='Your saved run', exact=True)).to_be_visible()
            assert len(Store(workspace).all('runs')) == 3
            assert Store(workspace).get('runs', created['id']) == created
            page.get_by_role('button', name='Edit settings', exact=True).click()
            expect(choice('kraken_decision_mode', 'off')).to_have_attribute('aria-pressed', 'true')
            choice('kraken_decision_mode', 'aware').click()
            page.locator('#revision-cores').fill('5')
            page.locator('#revision-memory').fill('12000')
            category('quality')
            page.locator('[data-run-name="run_multiqc"]').uncheck()
            capture('edit-saved-run')
            category('advanced')
            page.get_by_role('button', name='Full YAML', exact=True).click()
            revision_yaml = page.get_by_label('Run configuration YAML', exact=True)
            revision_yaml.fill(revision_yaml.input_value() + '\nrevision_note: saved-run editor test\n')
            page.get_by_role('button', name='Back to controls', exact=True).click()
            page.get_by_role('button', name='Review drafts', exact=True).click()
            expect(page.locator('.run-wizard .review-stats')).to_contain_text('5 cores')
            page.get_by_role('button', name='Create revised draft', exact=True).click()
            expect(page.get_by_role('heading', name='browser.fixture · revised', exact=True)).to_be_visible()
            revised, = [r for r in Store(workspace).all('runs') if r.get('parent') == created['id']]
            revised_config = yaml.safe_load(revised['config'])
            assert revised['cores'] == 5 and revised['memory_mb'] == 12000
            assert revised['inputs'] == created['inputs'] and revised['installation'] == created['installation']
            assert revised['output'] != created['output'] and not revised['dry_run_ok']
            assert revised_config['run_kraken'] is True and revised_config['kraken_decision_mode'] == 'aware'
            assert revised_config['run_multiqc'] is False and revised_config['revision_note'] == 'saved-run editor test'
            assert revised_config['custom_menu']['literal.key'] == [False, 0, 'preserve']
            assert revised_config['db_root'] == final_config['db_root']
            assert Store(workspace).get('runs', created['id']) == created
            assert Store(workspace).get('configs', 'hybas') == saved_defaults
            page.get_by_role('button', name='Back to runs', exact=True).click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(2)
            page.get_by_label('Search runs', exact=True).fill(created['id'])
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(1)
            page.get_by_label('Status', exact=True).select_option('completed')
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(0)
            page.get_by_role('button', name='Reset filters', exact=True).click()
            for run in (created, revised): page.locator('[data-select="' + run['id'] + '"]').check()
            page.locator('#run-filter-tool').select_option('basis')
            expect(page.locator('.selection-bar')).to_contain_text('2 outside this filter')
            page.get_by_role('button', name='Reset filters', exact=True).click()
            capture('runs-filters-selection')
            page.get_by_role('button', name='Compare settings', exact=True).click()
            expect(page.get_by_role('link', name='Comparisons', exact=True)).to_have_attribute('aria-current', 'page')
            expect(page.locator('.run-compare')).to_have_count(2)
            expect(page.get_by_role('link', name='Compare variants', exact=True)).to_have_count(0)
            capture('comparisons-saved-runs')
            page.get_by_role('button', name='Create variants', exact=True).click()
            page.locator('[data-run-row="' + created['id'] + '"]').get_by_role('button', name='Use as parent', exact=True).click()
            expect(page.get_by_label('Parent run', exact=True)).to_have_value(created['id'])
            page.get_by_role('button', name='Close dialog', exact=True).click()
            assert len(store.all('runs')) == 4
            page.get_by_role('link', name='Samples', exact=True).click()
            expect(page.locator('.saved-runs')).to_have_count(0)
            page.get_by_label('Search registered samples', exact=True).fill('unmatched sample')
            expect(page.get_by_role('heading', name='No matching samples.', exact=True)).to_be_visible()
            page.get_by_label('Search registered samples', exact=True).fill('')
            capture('samples-input-folders')
            page.get_by_role('button', name='View runs', exact=True).click()
            expect(page.locator('.filter-context')).to_contain_text('browser.fixture')
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(2)
            page.get_by_role('button', name='Show all samples', exact=True).click()
            page.get_by_role('link', name='Overview', exact=True).click()
            expect(page.get_by_role('heading', name='Pick up where you left off.', exact=True)).to_be_visible()
            expect(page.locator('.experiment-card')).to_have_count(0)
            capture('overview-daily-work')
            # A clean defaults editor refreshes after database selections change.
            page.get_by_role('link', name='Configuration', exact=True).click()
            defaults_tab('databases')
            expect(page.locator('[data-run-name="db_root"]')).to_have_value(final_config['db_root'])
            # An edited copy cannot overwrite newer defaults from another window.
            defaults_tab('quality')
            page.locator('[data-run-name="run_multiqc"]').uncheck()
            newer_defaults = saved_defaults + '\nnewer_selection: preserve this change\n'
            Store(workspace).put('configs', 'hybas', newer_defaults)
            page.get_by_role('button', name='Save defaults', exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('Saved defaults changed')
            expect(page.get_by_role('button', name='Save defaults', exact=True)).to_be_disabled()
            assert Store(workspace).get('configs', 'hybas') == newer_defaults
            page.get_by_role('button', name='Reload saved defaults', exact=True).click()
            expect(page.get_by_role('button', name='Save defaults', exact=True)).to_be_enabled()
            expect(page.locator('#default-save-status')).to_have_text('Saved defaults')
            page.get_by_role('link', name='Install & update', exact=True).click()
            expect(page.locator('.setup-guide')).to_be_visible()
            expect(page.locator('.setup-step')).to_have_count(5)
            capture('guided-setup-capacity')
            page.get_by_label('Pipeline to set up', exact=True).select_option('basis')
            page.locator('.setup-steps [data-id="environments"]').click()
            expect(page.locator('.guide-environments')).to_contain_text('BaSIS AWARE')
            expect(page.locator('.guide-environments').get_by_role('button', name='Install environments', exact=True)).to_be_enabled()
            page.locator('.setup-steps [data-id="databases"]').click()
            expect(page.get_by_label('CARD / RGI location', exact=True)).to_have_value(str(card / 'card.json'))
            page.locator('.setup-steps [data-id="review"]').click()
            expect(page.locator('.guide-review')).to_contain_text('This does not certify a run as ready')
            capture('guided-setup-review')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('.setup-guide').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            capture('guided-setup-mobile')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.locator('.guide-review').get_by_role('button', name='Create run drafts', exact=True).click()
            expect(page.locator('#run-tool')).to_have_value('basis')
            expect(page.locator('.run-wizard')).to_be_visible()
            page.get_by_role('button', name='Discard edits', exact=True).click()
            page.get_by_role('button', name='Discard unfinished edits', exact=True).click()
            page.get_by_role('link', name='Install & update', exact=True).click()
            page.get_by_role('button', name='Pipelines & environments', exact=True).click()
            page.locator('[data-installation="basis-ui-fixture"]').get_by_role('button', name='Check environments', exact=True).click()
            expect(page.get_by_role('heading', name='Check environments', exact=True)).to_be_visible()
            expect(page.locator('.environment-row')).to_contain_text('VFDB and BacMet')
            expect(page.get_by_role('button', name='Check selected', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_have_count(0)
            expect(page.get_by_label('Environment cache folder', exact=True)).not_to_be_visible()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            current_card = page.locator('[data-installation="hybas-ui-fixture"]')
            expect(page.get_by_role('button', name='Reuse existing environments', exact=True)).to_have_count(0)
            current_card.get_by_role('button', name='Install environments', exact=True).click()
            expect(page.get_by_role('heading', name='Install environments', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Check selected', exact=True)).to_have_count(0)
            expect(page.get_by_label('Environment cache folder', exact=True)).not_to_be_visible()
            capture('setup-install-environments')
            page.get_by_role('button', name='Clear selection', exact=True).click()
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_be_disabled()
            page.get_by_role('button', name='Select all', exact=True).click()
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_be_enabled()
            page.locator('#environment-storage summary').click()
            page.get_by_label('Environment cache folder', exact=True).fill(str(old_cache))
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_be_disabled()
            expect(page.locator('#environment-cache-note')).to_contain_text('Save the folder change')
            page.get_by_role('button', name='Save folder', exact=True).click()
            expect(page.get_by_role('heading', name='Install environments', exact=True)).to_be_visible()
            expect(page.locator('#environment-cache-hybas-ui-fixture')).to_have_value(str(old_cache))
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Save folder', exact=True)).to_be_disabled()
            capture('advanced-environment-storage')
            assert Store(workspace).get('installs', hybas['id'])['rule_cache'] == str(old_cache)
            assert Store(workspace).get('runs', parent['id']) == parent
            page.get_by_role('button', name='Close dialog', exact=True).click()
            current_card.get_by_text('Maintenance & installation details', exact=True).click()
            current_card.get_by_role('button', name='Uninstall version', exact=True).click()
            expect(page.get_by_role('button', name='Uninstall this version', exact=True)).to_be_disabled()
            expect(page.locator('.modal')).to_contain_text('Select another active version')
            page.get_by_role('button', name='Close dialog', exact=True).click()
            old_card = page.locator('[data-installation="' + old_id + '"]')
            old_card.get_by_text('Maintenance & installation details', exact=True).click()
            old_card.get_by_role('button', name='Uninstall version', exact=True).click()
            expect(page.get_by_role('button', name='Uninstall this version', exact=True)).to_be_enabled()
            expect(page.locator('.uninstall-kept')).to_contain_text(str(old_cache))
            capture('uninstall-version-review')
            page.get_by_role('button', name='Uninstall this version', exact=True).click()
            expect(page.locator('[data-installation="' + old_id + '"]')).to_have_count(0)
            assert not old_source.exists() and not Store(workspace).get('installs', old_id)
            assert (old_cache / 'fixture/conda-meta/history').read_text() == 'retained software fixture'
            assert Path(hybas['source']).is_dir()
            expect(current_card.locator('.installation-cache')).to_contain_text(str(old_cache))
            expect(current_card).to_contain_text('Runner set up')
            capture('install-environment-controls')
            if not current_card.get_by_role('button', name='Repair environments', exact=True).is_visible():
                current_card.get_by_text('Maintenance & installation details', exact=True).click()
            current_card.get_by_role('button', name='Repair environments', exact=True).click()
            expect(page.get_by_role('heading', name='Repair environments', exact=True)).to_be_visible()
            expect(page.get_by_role('dialog')).to_contain_text('Plotly 6.1.2')
            expect(page.get_by_role('button', name='Repair selected', exact=True)).to_be_enabled()
            page.get_by_role('button', name='Clear selection', exact=True).click()
            expect(page.get_by_role('button', name='Repair selected', exact=True)).to_be_disabled()
            capture('repair-environments')
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.get_by_role('button', name='PC capacity', exact=True).click()
            expect(page.get_by_role('heading', name='GPU & CUDA', exact=True)).to_be_visible()
            expect(page.locator('.power-card')).to_have_count(3)
            page.get_by_role('button', name='Detect again', exact=True).click()
            expect(page.locator('#toast')).to_contain_text('Hardware detection refreshed')
            page.get_by_label('Apply defaults to', exact=True).select_option('hybas')
            page.get_by_role('button', name='Use Medium', exact=True).click()
            expect(page.locator('#toast')).to_contain_text('Medium saved for future drafts')
            budget = store.get('resource_defaults', 'hybas')
            assert budget['preset'] == 'medium' and not store.get('resource_defaults', 'basis')
            saved = yaml.safe_load(store.get('configs', 'hybas'))
            assert saved['threads'] == budget['cores'] and saved['assembly_mem_mb'] == budget['memory_mb']
            assert store.get('runs', parent['id']) == parent
            capture('hardware-presets')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('.hardware-presets').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            capture('hardware-presets-mobile')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('link', name='Runs', exact=True).click()
            page.get_by_role('button', name='Create run drafts', exact=True).first.click()
            page.locator('#run-tool').select_option('hybas')
            expect(page.get_by_label('CPU cores per run', exact=True)).to_have_value(str(budget['cores']))
            expect(page.get_by_label('Memory budget (MB)', exact=True)).to_have_value(str(budget['memory_mb']))
            page.get_by_label('CPU cores per run', exact=True).fill('3')
            page.locator('#run-tool').select_option('basis')
            page.get_by_label('CPU cores per run', exact=True).fill('2')
            page.reload()
            expect(page.locator('#run-tool')).to_have_value('basis')
            expect(page.get_by_label('CPU cores per run', exact=True)).to_have_value('2')
            page.locator('#run-tool').select_option('hybas')
            expect(page.get_by_label('CPU cores per run', exact=True)).to_have_value('3')
            page.get_by_role('button', name='Discard edits', exact=True).click()
            page.get_by_role('button', name='Discard unfinished edits', exact=True).click()
            page.get_by_role('link', name='Results & reports', exact=True).click()
            page.get_by_role('button', name='Open report', exact=True).click()
            expect(page.frame_locator('.html-report-frame').get_by_role('heading', name='Browser software fixture')).to_be_visible()
            assert page.locator('.html-report-frame').get_attribute('sandbox') == 'allow-scripts allow-downloads'
            page.get_by_role('button', name='Close dialog', exact=True).click()
            # Receipt fixtures exercise run guidance without installing or running biology.
            from bactologix.readiness import identity
            checked = {**created, 'dry_run_ok': True, 'validated_controller': hybas['controller'],
                       'validated': '2026-10-27T00:00:00+00:00'}
            store.put('runs', checked['id'], checked)
            prefix = Path(checked['rule_cache']) / ('b' * 32)
            (prefix / 'conda-meta').mkdir(parents=True, exist_ok=True)
            (prefix / 'conda-meta/history').write_text('software fixture retained')
            store.put('run_environment_checks', checked['id'], {
                'id': checked['id'], 'identity': identity(store, checked), 'status': 'checked',
                'checked_at': '2026-10-27T00:00:00+00:00',
                'rows': [{'name': 'software fixture', 'path': str(prefix)}]})
            page.get_by_role('link', name='Runs', exact=True).click()
            page.locator('[data-action="run-detail"][data-id="' + checked['id'] + '"]').first.click()
            expect(page.locator('[data-readiness="environments"]')).to_contain_text('1 need installation')
            expect(page.get_by_role('button', name='Run / resume', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Install environments', exact=True)).to_be_enabled()
            capture('saved-run-missing-environment')
            prefix.with_suffix('.env_setup_done').touch()
            page.get_by_role('button', name='Refresh checks', exact=True).click()
            expect(page.locator('[data-readiness="environments"]')).to_contain_text('1 available')
            expect(page.get_by_role('button', name='Run / resume', exact=True)).to_be_enabled()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('.run-workspace').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            page.get_by_role('button', name='Run / resume', exact=True).scroll_into_view_if_needed()
            expect(page.get_by_role('button', name='Run / resume', exact=True)).to_be_in_viewport()
            assert page.locator('#toast').evaluate('(el) => {const r=el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth;}')
            capture('saved-run-checks-mobile')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('button', name='Back to runs', exact=True).click()
            store.put('runs', created['id'], created)
            # Active-job polling works outside Jobs and does not interrupt focused edits.
            jid = store.submit('workflow', {'run_id': created['id'], 'action': 'run'})
            store.update_job(jid, status='running')
            page.get_by_role('button', name='Refresh workspace', exact=True).click()
            expect(page.locator('#queue-status')).to_contain_text('1 running')
            page.get_by_role('link', name='Configuration', exact=True).click()
            defaults_tab('advanced')
            search = page.get_by_label('Search all settings', exact=True)
            search.fill('literal edit stays')
            page.wait_for_timeout(7000)
            expect(page.locator('#toast.show')).to_have_count(0)
            expect(search).to_be_focused()
            expect(search).to_have_value('literal edit stays')
            page.get_by_role('link', name='Overview', exact=True).click()
            expect(page.locator('#queue-status')).to_contain_text('1 running')
            store.log_path(jid).write_text('BUSCO lineage database missing at /software-fixture')
            store.update_job(jid, status='failed', error='Command exited with code 1; see the job log')
            expect(page.locator('#queue-status')).to_have_text('No active jobs', timeout=12000)
            # A later successful validation does not erase an earlier failed execution.
            validation_job = store.submit('workflow', {'run_id': created['id'], 'action': 'dry-run'})
            store.update_job(validation_job, status='succeeded')
            page.get_by_role('button', name='Refresh workspace', exact=True).click()
            expect(page.locator('.attention-card')).to_contain_text('Check the database location')
            capture('overview-needs-attention')
            page.get_by_role('link', name='Jobs & progress', exact=True).click()
            expect(page.locator('.job-guidance')).to_contain_text('Check the database location')
            expect(page.locator('[data-job-id="' + jid + '"]').get_by_role('button', name='Review run', exact=True)).to_be_visible()
            capture('job-actionable-error')
            # A failed run, its own report and an actual software-only failed launch stay in context.
            page.locator('[data-job-id="' + jid + '"]').get_by_role('button', name='Review run', exact=True).click()
            saved_tab('activity')
            expect(page.locator('.run-activity')).to_contain_text('Check the database location')
            capture('run-activity')
            store.put('reports', 'run-html-fixture', {'id': 'run-html-fixture', 'name': 'Run report fixture',
                      'path': str(report), 'sample': 'browser.fixture', 'run': created['id']})
            page.get_by_role('button', name='Refresh workspace', exact=True).click()
            saved_tab('results')
            expect(page.locator('.run-reports')).to_contain_text('Run report fixture')
            capture('run-results')
            page.get_by_role('button', name='Open report', exact=True).click()
            expect(page.frame_locator('.html-report-frame').get_by_role('heading', name='Browser software fixture')).to_be_visible()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            assert page.url.endswith('/results')
            saved_tab('overview')
            page.get_by_role('button', name='Validate run', exact=True).click()
            expect(page.locator('.run-activity')).to_be_visible()
            assert page.url.endswith('/activity')
            expect(page.locator('.run-activity')).to_contain_text('Cause not identified', timeout=30000)
            page.goto(url + '#runs/run-not-in-this-workspace/overview')
            expect(page.get_by_role('heading', name='Run not found.', exact=True)).to_be_visible()
            page.get_by_role('button', name='Back to runs', exact=True).click()
            page.evaluate('window.__acceptanceBeforeRestart = true')
            # Simulate the verified worker result without replacing CI's own package.
            # The real supervisor must wait for success, exec a new server and refresh this tab.
            from bactologix import __version__
            live, = [x for x in store.all('launchers') if x['status'] == 'ready']
            before = {kind: store.all(kind) for kind in ('samples', 'runs', 'configs', 'installs')}
            jid = store.submit('app_update', {'release': {'version': __version__}, 'launcher_id': live['id']})
            store.update_job(jid, status='running')
            store.put('settings', 'app-update', {'job': jid, 'status': 'installed', 'version': __version__,
                      'launcher_id': live['id'], 'environment': sys.prefix, 'pip_started_at': time.time()})
            page.get_by_role('link', name='Jobs & progress', exact=True).click()
            page.get_by_role('button', name='Refresh', exact=True).click()
            page.wait_for_function('!!window.__bactologixRestart')
            assert store.get('launchers', live['id'])['status'] == 'ready'
            capture('automatic-update-waiting')
            store.update_job(jid, status='succeeded')
            page.wait_for_function('!window.__acceptanceBeforeRestart', timeout=45000)
            expect(page.get_by_role('link', name='Runs', exact=True)).to_be_visible(timeout=30000)
            page.wait_for_function('!window.__bactologixRestart')
            assert page.url.split('#')[0].rstrip('/') == url
            assert page.url.endswith('#jobs')
            with urlopen(url + '/app/static/runtime.json', timeout=5) as response:
                import json
                runtime = json.load(response)
            assert runtime['id'] != live['id'] and runtime['version'] == __version__
            assert store.get('settings', 'app-update')['restart_status'] == 'ready'
            for kind, items in before.items(): assert store.all(kind) == items
            capture('automatic-update-ready')
            assert not errors, errors
            browser.close()
    finally:
        import signal
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
        log.close()


def render_component(page, store):
    import bactologix
    from bactologix.ui_backend import snapshot
    ui = Path(bactologix.__file__).parent / 'ui'
    page.set_content('<div id="component-host"></div>')
    page.evaluate('''async ({html, css, js, workspace}) => {
        const host = document.querySelector('#component-host');
        const shadow = host.attachShadow({mode: 'open'});
        shadow.innerHTML = '<style>' + css + '</style>' + html;
        const url = URL.createObjectURL(new Blob([js], {type: 'text/javascript'}));
        const component = (await import(url)).default;
        URL.revokeObjectURL(url);
        window.acceptanceEvents = [];
        window.acceptanceWorkspace = workspace;
        window.acceptanceRender = response => component({parentElement: shadow,
            data: {workspace, response},
            setTriggerValue: (trigger, value) => window.acceptanceEvents.push({trigger, value})});
        window.acceptanceRender(null);
    }''', {'html': (ui / 'shell.html').read_text(), 'css': (ui / 'styles.css').read_text(),
           'js': (ui / 'workspace.js').read_text(), 'workspace': snapshot(store)})


def test_reviewed_support_snapshot_and_zip(tmp_path):
    import zipfile
    from playwright.sync_api import sync_playwright, expect
    from bactologix.ui_backend import dispatch, snapshot
    store = Store(tmp_path / 'support-component')
    jid = store.submit('smoke', {})
    store.update_job(jid, status='failed', error='Failed at /home/Private User/Secret Project')
    store.log_path(jid).write_text('API_TOKEN=private-token\n'
        'File "/home/Private User/worker.py", line 40\nValueError: software fixture\n'
        'Private note to remove manually\n</textarea><script>window.supportInjected=true</script>\n')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100}, accept_downloads=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('button', name='Get support', exact=True).first.click()
        dialog = page.get_by_role('dialog', name='Get support', exact=True)
        download_button = dialog.get_by_role('button', name='Download reviewed snapshot', exact=True)
        expect(download_button).to_be_disabled()
        log_choice = dialog.locator('#support-log')
        expect(log_choice).not_to_be_checked()
        expect(log_choice).to_be_disabled()
        expect(dialog.locator('#support-text')).to_have_count(0)
        dialog.get_by_label('Job to troubleshoot', exact=True).select_option(jid)
        expect(log_choice).to_be_enabled()
        dialog.get_by_role('button', name='Generate preview', exact=True).click()
        def respond(expected):
            request = page.evaluate('window.acceptanceEvents.at(-1).value')
            assert request['kind'] == expected
            result = dispatch(store, request['kind'], request['payload'])
            page.evaluate('({response,workspace}) => {Object.assign(window.acceptanceWorkspace,workspace);window.acceptanceRender(response);}',
                          {'response': {'id': request['id'], 'ok': True, **result}, 'workspace': snapshot(store)})
            return request
        request = respond('support_preview')
        assert request['payload'] == {'job_id': jid, 'include_log': False}
        editor = dialog.get_by_label('Review and edit snapshot', exact=True)
        assert 'ValueError: software fixture' not in editor.input_value()
        expect(download_button).to_be_disabled()
        review = dialog.locator('#support-reviewed')
        review.check()
        expect(download_button).to_be_enabled()
        editor.fill(editor.input_value()+'\nEdited note')
        expect(review).not_to_be_checked()
        expect(download_button).to_be_disabled()
        log_choice.check()
        expect(dialog).to_contain_text('Selection changed')
        expect(download_button).to_be_disabled()
        dialog.get_by_role('button', name='Generate new preview', exact=True).click()
        request = respond('support_preview')
        assert request['payload']['include_log'] is True
        preview = editor.input_value()
        assert 'private-token' not in preview and '/home/Private User' not in preview
        assert 'ValueError: software fixture' in preview and 'Private note to remove manually' in preview
        assert page.evaluate('window.supportInjected') is None
        edited = preview.replace('Private note to remove manually', '[User removed private note]')+'\nReviewed edit: café\n</textarea><script>window.supportInjected=true</script>\n'
        editor.fill(edited)
        review.check()
        assert page.evaluate('window.supportInjected') is None
        with page.expect_download() as download:
            download_button.click()
            request = respond('download_support')
        assert request['payload']['text'] == edited and request['payload']['reviewed'] is True
        target = tmp_path / 'bactologix-support.txt'
        download.value.save_as(target)
        assert target.read_bytes() == edited.encode()
        store.log_path(jid).write_text('NEW PRIVATE LOG CONTENT')
        with page.expect_download() as download:
            dialog.get_by_role('button', name='Download ZIP', exact=True).click()
            respond('download_support')
        target = tmp_path / 'bactologix-support.zip'
        download.value.save_as(target)
        with zipfile.ZipFile(target) as archive:
            assert archive.read('bactologix-support.txt') == edited.encode()
            assert set(archive.namelist()) == {'bactologix-support.txt', 'SHA256SUMS', 'README.txt'}
        page.set_viewport_size({'width': 390, 'height': 844})
        assert dialog.evaluate('(node) => node.scrollWidth <= node.clientWidth+1')
        assert dialog.locator('.modal-body').evaluate('(node) => node.scrollWidth <= node.clientWidth+1')
        destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
        if destination:
            Path(destination).mkdir(parents=True, exist_ok=True)
            dialog.locator('.modal-body').evaluate('(node) => node.scrollTop=0')
            page.screenshot(path=str(Path(destination) / 'reviewed-support-mobile.png'), full_page=True)
        # Changing selection invalidates approval; closing removes the editable preview.
        dialog.get_by_label('Job to troubleshoot', exact=True).select_option('')
        expect(review).not_to_be_checked()
        expect(download_button).to_be_disabled()
        dialog.get_by_role('button', name='Close dialog', exact=True).click()
        page.get_by_role('button', name='Open navigation', exact=True).click()
        page.get_by_role('button', name='Get support', exact=True).last.click()
        expect(dialog.locator('#support-text')).to_have_count(0)
        expect(download_button).to_be_disabled()
        assert errors == []
        assert len(store.jobs()) == 1 and not store.all('samples') and not store.all('runs')
        browser.close()


def test_job_progress_resource_history_and_downloads(tmp_path):
    import json
    import zipfile
    from playwright.sync_api import sync_playwright, expect
    from bactologix import telemetry
    from bactologix.ui_backend import dispatch, snapshot
    from bactologix.store import now
    store = Store(tmp_path / 'usage-component')
    jid = store.submit('workflow', {'run_id': 'run-software-fixture', 'action': 'run'})
    store.update_job(jid, status='running')
    store.log_path(jid).write_text('localrule software_check:\n    jobid: 2\n1 of 4 steps (25%) done\n')
    recorder = telemetry.Recorder(store, store.job(jid))
    recorder.history = [dict(start_s=0, end_s=2, cpu_s=3, rss_peak_mb=400,
                            processes_peak=3, observed=1, unavailable=0),
                        dict(start_s=2, end_s=4, cpu_s=0, rss_peak_mb=None,
                            processes_peak=None, observed=0, unavailable=1)]
    recorder.tick(force=True)
    record = store.get('job_telemetry', jid)
    record.update(elapsed_s=4, samples=1, unavailable_samples=1, observed_cpu_s=3, peak_rss_mb=400,
                  latest={'cpu_percent': 150, 'rss_mb': 400, 'processes': 3})
    store.put('job_telemetry', jid, record)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100}, accept_downloads=True)
        page.clock.install(time='2026-10-27T12:00:00Z')
        page.clock.pause_at('2026-10-27T12:00:10Z')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('link', name='Jobs & progress', exact=True).click()
        card = page.locator('.job-card')
        expect(card).to_contain_text('1 / 4 tasks in current plan')
        expect(card).to_contain_text('Active steps: software_check')
        expect(card).to_contain_text('150%')
        page.get_by_role('button', name='Resource use', exact=True).click()
        def respond(expected_kind):
            request = page.evaluate('window.acceptanceEvents.at(-1).value')
            assert request['kind'] == expected_kind
            result = dispatch(store, request['kind'], request['payload'])
            page.evaluate('({response,workspace}) => {Object.assign(window.acceptanceWorkspace,workspace);window.acceptanceRender(response);}',
                          {'response': {'id': request['id'], 'ok': True, **result}, 'workspace': snapshot(store)})
        respond('job_metrics')
        dialog = page.get_by_role('dialog', name='Resource use & progress', exact=True)
        expect(dialog).to_be_visible()
        expect(dialog.locator('.usage-charts svg')).to_have_count(2)
        expect(dialog).to_contain_text('100% = one logical CPU')
        page.clock.fast_forward(4100)
        respond('job_metrics')  # The open dialog polls the measurement endpoint.
        with page.expect_download() as download:
            dialog.get_by_role('button', name='Download record (ZIP)', exact=True).click()
            respond('download_metrics')
        archive_path = tmp_path / 'usage.zip'
        download.value.save_as(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            saved = json.loads(archive.read('resource-use.json'))
            assert saved['id'] == jid and saved['summary']['progress']['total'] == 4
            assert 'resource-use.csv' in archive.namelist()
        with page.expect_download() as download:
            dialog.get_by_role('button', name='CSV', exact=True).click()
            respond('download_metrics')
        csv_path = tmp_path / 'usage.csv'
        download.value.save_as(csv_path)
        assert 'peak_process_rss_sum_mb' in csv_path.read_text()
        # Simulate a checkpoint expanding the plan, then worker interruption.
        record['progress'].update(completed=2, total=8, percent=25, dynamic_plan=True)
        record['updated_at'] = now()
        store.put('job_telemetry', jid, record)
        dialog.get_by_role('button', name='Refresh', exact=True).click()
        respond('job_metrics')
        expect(dialog).to_contain_text('2 / 8 tasks in current plan')
        expect(dialog).to_contain_text('Checkpoints can change the task total')
        record['updated_at'] = '2000-01-01T00:00:00+00:00'
        store.put('job_telemetry', jid, record)
        dialog.get_by_role('button', name='Refresh', exact=True).click()
        respond('job_metrics')
        expect(dialog).to_contain_text('Updates paused')
        expect(dialog).not_to_contain_text('150%')
        store.update_job(jid, status='interrupted')
        dialog.get_by_role('button', name='Refresh', exact=True).click()
        respond('job_metrics')
        expect(dialog).to_contain_text('Partial record')
        expect(dialog).to_contain_text('Last reported steps: software_check')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert dialog.evaluate('(node) => node.scrollWidth <= node.clientWidth+1')
        assert dialog.locator('.modal-body').evaluate('(node) => node.scrollWidth <= node.clientWidth+1')
        destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
        if destination:
            Path(destination).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(destination) / 'job-resource-use-mobile.png'), full_page=True)
        dialog.get_by_role('button', name='Close dialog', exact=True).click()
        expect(card).to_contain_text('Partial record')
        assert card.evaluate('(node) => node.scrollWidth <= node.clientWidth+1')
        assert errors == []
        assert not store.all('samples') and not store.all('runs')
        browser.close()


def test_background_poll_preserves_and_queues_a_foreground_action(tmp_path):
    """Exercise the real component with a controlled response boundary, without a worker."""
    from playwright.sync_api import sync_playwright, expect
    store = Store(tmp_path / 'component-workspace')
    jid = store.submit('smoke', {})
    store.update_job(jid, status='running')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        render_component(page, store)
        page.wait_for_function('window.acceptanceEvents.length === 1', timeout=10000)
        first = page.evaluate('window.acceptanceEvents[0].value')
        assert first['kind'] == 'refresh'
        page.get_by_role('button', name='Add samples', exact=True).click()
        page.get_by_label('Sample ID', exact=True).fill('queued.fixture')
        page.get_by_label('Input folder', exact=True).fill('/software-fixture/inputs')
        page.get_by_role('button', name='Register sample', exact=True).click()
        assert page.evaluate('window.acceptanceEvents.length') == 1
        expect(page.get_by_role('button', name='Register sample', exact=True)).to_be_disabled()
        page.evaluate('response => window.acceptanceRender(response)', {'id': first['id'], 'ok': True})
        expect(page.get_by_role('dialog', name='Add a sample', exact=True)).to_be_visible()
        expect(page.get_by_label('Sample ID', exact=True)).to_have_value('queued.fixture')
        second = page.evaluate('window.acceptanceEvents[1].value')
        assert second['kind'] == 'add_sample'
        assert second['payload']['id'] == 'queued.fixture'
        assert second['payload']['path'] == '/software-fixture/inputs'
        page.evaluate('response => window.acceptanceRender(response)',
                      {'id': second['id'], 'ok': True, 'navigate': 'samples', 'message': 'Sample registered.'})
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.get_by_role('heading', name='Your input folders.', exact=True)).to_be_visible()
        assert page.evaluate('window.acceptanceEvents.length') == 2
        assert store.all('samples') == []  # UI boundary fixture; no backend mutation occurred.
        browser.close()


@pytest.mark.parametrize('action', ['install', 'check'])
def test_setup_environment_actions_need_no_sample_or_run(installed, action):
    """Click through the real component, stopping at the queued request boundary."""
    from playwright.sync_api import sync_playwright, expect
    store, item = installed
    assert not store.all('samples') and not store.all('runs')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        render_component(page, store)
        page.get_by_role('link', name='Install & update', exact=True).click()
        page.get_by_role('button', name='Pipelines & environments', exact=True).click()
        card = page.locator('[data-installation="' + item['id'] + '"]')
        expect(page.get_by_role('button', name='Reuse existing environments', exact=True)).to_have_count(0)
        card.get_by_role('button', name=action.capitalize() + ' environments', exact=True).click()
        expect(page.get_by_role('dialog')).to_contain_text('No sample or run draft is needed')
        expect(page.get_by_label('Environment cache folder', exact=True)).not_to_be_visible()
        assert page.evaluate('window.acceptanceEvents.length') == 0  # Opening only reviews.
        page.get_by_role('button', name='Clear selection', exact=True).click()
        confirm = page.get_by_role('button', name=action.capitalize() + ' selected', exact=True)
        expect(confirm).to_be_disabled()
        page.locator('[data-environment="qc"]').check()
        expect(page.locator('#environment-selection-count')).to_have_text('1 of 2 selected')
        expect(confirm).to_be_enabled()
        confirm.click()
        events = page.evaluate('window.acceptanceEvents')
        assert len(events) == 1
        request = events[0]['value']
        assert request['kind'] == 'manage_environments'
        assert request['payload'] == {'id': item['id'], 'action': action, 'selected': ['qc']}
        assert not store.all('samples') and not store.all('runs') and not store.jobs()
        browser.close()


def test_workstation_check_and_failure_recovery_in_streamlit(tmp_path):
    """Real browser -> worker -> dated record -> report and native JSON download."""
    import json
    from playwright.sync_api import sync_playwright, expect
    store = Store(tmp_path / 'workstation acceptance')
    failure = store.submit('smoke', {})
    store.log_path(failure).write_text('Error in rule software_fixture:\nPermissionError: [Errno 13] Permission denied <script>window.recoveryInjected=true</script>\n')
    store.update_job(failure, status='failed', error='Command exited with code 1; see the job log')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (tmp_path / 'server.log').open('w') as log:
        proc = subprocess.Popen([str(Path(sys.executable).with_name('bactologix')), '--home', str(store.home),
                                 '--host', '127.0.0.1', '--port', str(port), '--no-browser'],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/_stcore/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail((tmp_path / 'server.log').read_text())
                time.sleep(.2)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100}, accept_downloads=True)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_role('link', name='Jobs & progress', exact=True).click()
            panel = page.locator('.recovery-panel')
            panel.locator('summary').click()
            expect(panel).to_contain_text('Possible cause')
            expect(panel).to_contain_text('Reported failed step: software_fixture')
            expect(panel.locator('.recovery-evidence')).to_contain_text('<script>')
            assert page.evaluate('window.recoveryInjected') is None
            panel.get_by_role('button', name='Open setup', exact=True).click()
            page.get_by_role('button', name='Workstation check', exact=True).click()
            expect(page.get_by_label('Runner to check', exact=True)).to_have_value('')
            assert len(store.jobs()) == 1  # Visiting the panel never starts probes.
            page.get_by_role('button', name='Start workstation check', exact=True).click()
            page.get_by_role('button', name='Review check', exact=True).click()
            dialog = page.get_by_role('dialog', name='Workstation check', exact=True)
            expect(dialog).to_contain_text('succeeded', timeout=30000)
            expect(dialog).to_contain_text('Process cancellation')
            expect(dialog).to_contain_text('App-only checks were selected')
            expect(dialog.locator('.workstation-row').filter(has_text='I opened and could read').get_by_role('button')).to_be_disabled()
            jid = next(j['id'] for j in store.jobs() if j['kind'] == 'workstation')
            assert not store.get('workstation_checks', jid)['observations']
            dialog.get_by_role('button', name='Open check report', exact=True).click()
            expect(page.frame_locator('.html-report-frame').get_by_role('heading', name='Bactologix workstation check')).to_be_visible()
            page.get_by_role('button', name='Back to check', exact=True).click()
            report_row = dialog.locator('.workstation-row').filter(has_text='I opened and could read')
            report_row.get_by_role('button', name='Confirm observation', exact=True).click()
            expect(report_row).to_contain_text('Confirmed by user')
            with page.expect_download() as downloaded:
                dialog.get_by_role('button', name='Download check JSON', exact=True).click()
            destination = tmp_path / 'workstation.json'
            downloaded.value.save_as(destination)
            record = json.loads(destination.read_text())
            assert record['id'] == jid and record['job_status'] == 'succeeded'
            assert record['observations']['report']['source'] == 'user-reported'
            assert 'download' not in record['observations']
            download_row = dialog.locator('.workstation-row').filter(has_text='I downloaded and could open')
            download_row.get_by_role('button', name='Confirm observation', exact=True).click()
            expect(download_row).to_contain_text('Confirmed by user')
            expect(dialog.locator('.workstation-row').filter(has_text='I launched Bactologix normally')).to_contain_text('Not confirmed')
            download_row.get_by_role('button', name='Clear confirmation', exact=True).click()
            expect(download_row).to_contain_text('Not confirmed')
            def capture(name):
                folder = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
                if folder:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(folder) / name), full_page=True)
            capture('workstation-check-desktop.png')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            assert dialog.locator('.modal-body').evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            capture('workstation-check-mobile.png')
            dialog.get_by_role('button', name='Close dialog', exact=True).click()
            page.reload()
            expect(page.get_by_role('button', name='Review check', exact=True)).to_be_visible()
            page.get_by_role('button', name='Review check', exact=True).click()
            expect(page.get_by_role('dialog')).to_contain_text('Confirmed by user')
            assert not store.all('samples') and not store.all('runs')
            assert errors == []
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_queue_controls_persist_and_recover_in_real_browser(tmp_path):
    """UI controls use real transactions and a worker; no sample workflow is run."""
    from playwright.sync_api import sync_playwright, expect
    from bactologix.workstation import request as workstation_request
    store = Store(tmp_path / 'queue workspace')
    interrupted = store.submit('smoke', {})
    store.update_job(interrupted, status='running')  # Abandoned worker fixture for recovery.
    workstation = store.submit('workstation', workstation_request(store))
    held = store.submit('smoke', {})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (tmp_path / 'server.log').open('w') as log:
        proc = subprocess.Popen([str(Path(sys.executable).with_name('bactologix')), '--home', str(store.home),
                                 '--host', '127.0.0.1', '--port', str(port), '--no-browser'],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/_stcore/health', timeout=1) as response:
                    if response.status == 200: break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail((tmp_path / 'server.log').read_text())
                time.sleep(.2)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_role('link', name='Jobs & progress', exact=True).click()
            controls = page.locator('.queue-controls')
            page.get_by_role('button', name='Stop after current job', exact=True).click()
            expect(controls).to_contain_text('Finishing current job · queue paused')
            assert not store.job(interrupted)['cancel']
            second = controls.locator('[data-waiting-job="' + held + '"]')
            second.get_by_role('button', name='Hold job', exact=True).click()
            expect(second.get_by_role('button', name='Release hold', exact=True)).to_be_visible()
            second.get_by_role('button', name='Move up', exact=True).click()
            expect(controls.locator('.waiting-job').first).to_have_attribute('data-waiting-job', held)
            expect(second.get_by_role('button', name='Move up', exact=True)).to_be_disabled()
            page.reload()
            expect(controls.locator('.waiting-job').first).to_have_attribute('data-waiting-job', held)
            expect(second.get_by_role('button', name='Release hold', exact=True)).to_be_visible()
            page.get_by_role('link', name='Overview', exact=True).click()
            expect(page.locator('.queue-banner')).to_contain_text('Waiting and newly submitted jobs will not start')
            page.get_by_role('button', name='Review queue', exact=True).click()
            page.get_by_role('button', name='Start / recover queue', exact=True).click()
            expect(controls).to_contain_text('Review interrupted work before resuming', timeout=15000)
            assert store.job(interrupted)['status'] == 'interrupted'
            assert store.job(workstation)['status'] == store.job(held)['status'] == 'queued'
            assert store.queue_snapshot()['paused']
            def capture(name):
                folder = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
                if folder:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(folder) / name), full_page=True)
            capture('queue-controls-desktop.png')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert controls.evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            assert controls.locator('.waiting-jobs').evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            capture('queue-controls-mobile.png')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('button', name='Resume queue', exact=True).click()
            expect(page.locator('.job-card').filter(has_text=workstation)).to_contain_text('succeeded', timeout=30000)
            expect(controls).to_contain_text('All waiting jobs are held')
            assert store.job(held)['status'] == 'queued' and store.job(interrupted)['status'] == 'interrupted'
            second.get_by_role('button', name='Release hold', exact=True).click()
            # This smoke job has no configured runner. Its expected failure must not alter history.
            expect(page.locator('.job-card').filter(has_text=held)).to_contain_text('failed', timeout=30000)
            expect(controls).to_contain_text('No waiting jobs')
            assert store.job(interrupted)['status'] == 'interrupted'
            assert store.job(workstation)['status'] == 'succeeded'
            page.get_by_role('button', name='Pause queue', exact=True).click()
            expect(page.locator('#queue-status')).to_contain_text('Queue paused')
            page.reload()
            expect(controls.get_by_role('button', name='Resume queue', exact=True)).to_be_visible()
            assert errors == []
            assert not store.all('samples') and not store.all('runs')
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_results_centre_downloads_and_attempt_provenance(tmp_path, installed):
    """Real browser downloads match frozen config, original HTML and attempt records."""
    import json
    from types import SimpleNamespace
    from playwright.sync_api import sync_playwright, expect
    from bactologix import results, telemetry
    from bactologix.store import now
    store, item = installed
    sample = tmp_path / 'result sample'
    (sample / 'raw/illumina').mkdir(parents=True)
    for mate in (1, 2):
        (sample / f'raw/illumina/fixture_R{mate}.fastq').write_text('@fixture\nACGT\n+\nIIII\n')
    run = create_run(store, 'hybas', {'id': 'result.fixture', 'path': str(sample), 'mode': 'illumina_only'},
                     'custom_note: frozen results fixture\n', label='Results fixture <script>')
    archived = create_run(store, 'hybas', run['sample'], 'custom_note: archived\n', label='Archived fixture')
    store.put('run_visibility', archived['id'], {'id': archived['id'], 'archived': True})
    jid = store.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    store.update_job(jid, status='running', started=now())
    ctx = SimpleNamespace(store=store, job=store.job(jid), write=lambda text: None)
    results.observe(ctx, run, 'before')
    report = Path(run['output']) / 'reports/multiqc/multiqc_report.html'
    report.parent.mkdir(parents=True)
    report.write_text('<html><h1>Results centre software fixture</h1></html>')
    store.log_path(jid).write_text('Attempt log fixture\n')
    telemetry.Recorder(store, store.job(jid)).finish('failed')
    results.observe(ctx, run, 'after')
    store.update_job(jid, status='failed', finished=now())
    check = store.submit('workflow', {'run_id': run['id'], 'action': 'dry-run'})
    store.update_job(check, status='succeeded', finished=now())
    original_run = store.get('runs', run['id'])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    with (tmp_path / 'results-server.log').open('w') as log:
        proc = subprocess.Popen([str(Path(sys.executable).with_name('bactologix')), '--home', str(store.home),
                                 '--host', '127.0.0.1', '--port', str(port), '--no-browser'],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/_stcore/health', timeout=1) as response:
                    if response.status == 200: break
            except OSError:
                if time.monotonic() >= deadline: pytest.fail((tmp_path / 'results-server.log').read_text())
                time.sleep(.2)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_role('link', name='Results & reports', exact=True).click()
            picker = page.locator('.result-run-picker')
            expect(picker.locator('.result-run:visible')).to_have_count(1)
            page.get_by_label('Include archived runs', exact=True).check()
            expect(picker.locator('.result-run:visible')).to_have_count(2)
            page.get_by_label('Find a run', exact=True).fill('no match')
            expect(page.locator('#results-no-matches')).to_be_visible()
            page.get_by_label('Find a run', exact=True).fill('Results fixture')
            expect(picker.locator('.result-run:visible')).to_have_count(1)
            picker.get_by_role('button', name='Results & downloads', exact=True).filter(visible=True).click()
            centre = page.locator('.results-centre')
            expect(centre).to_contain_text('Metadata observed after ' + jid)
            expect(centre).to_contain_text('Producing attempt: not recorded')
            expect(centre.locator('.results-provenance')).to_contain_text('failed')
            expect(centre.locator('.result-attempt')).to_have_count(2)
            expect(centre.locator('.result-attempt').first).to_contain_text('Validation')
            expect(centre.locator('.result-attempt').last).to_contain_text('Execution')
            assert page.locator('.page-heading script').count() == 0
            with page.expect_download() as event:
                page.get_by_role('button', name='Frozen YAML', exact=True).click()
            assert Path(event.value.path()).read_text() == run['config']
            with page.expect_download() as event:
                page.get_by_role('button', name='Results manifest', exact=True).click()
            manifest = json.loads(Path(event.value.path()).read_text())
            assert manifest['latest_execution']['id'] == jid and manifest['status'] == 'failed'
            with page.expect_download() as event:
                page.get_by_role('button', name='Download HTML', exact=True).click()
            assert Path(event.value.path()).read_bytes() == report.read_bytes()
            attempt = centre.locator('[data-result-attempt="' + jid + '"]')
            with page.expect_download() as event:
                attempt.get_by_role('button', name='Download log', exact=True).click()
            assert Path(event.value.path()).read_text() == 'Attempt log fixture\n'
            attempt.get_by_role('button', name='Resource usage', exact=True).click()
            expect(page.locator('.modal')).to_contain_text(jid)
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.get_by_role('button', name='Open report', exact=True).click()
            expect(page.frame_locator('.html-report-frame').get_by_role('heading', name='Results centre software fixture')).to_be_visible()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.get_by_role('button', name='Methods & downloads', exact=True).click()
            expect(page.locator('.methods-export')).to_be_visible()
            page.locator('[data-action="saved-run-tab"][data-id="results"]').click()
            expect(centre).to_contain_text('Metadata observed after ' + jid)
            def capture(name):
                folder = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
                if folder:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(folder) / name), full_page=True)
            expect(page.get_by_role('button', name='Refresh results', exact=True)).to_be_enabled()
            capture('results-centre-desktop.png')
            attempt.scroll_into_view_if_needed()
            capture('results-attempts-desktop.png')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert centre.evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            centre.locator('.results-report-list').scroll_into_view_if_needed()
            capture('results-centre-mobile.png')
            attempt.scroll_into_view_if_needed()
            capture('results-attempts-mobile.png')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.reload()
            expect(centre).to_contain_text('Metadata observed after ' + jid)
            report.write_text('<html>Changed outside the attempt</html>')
            page.get_by_role('button', name='Refresh results', exact=True).click()
            expect(centre).to_contain_text('No matching attempt observation')
            assert errors == [] and store.get('runs', run['id']) == original_run
            assert len(store.jobs()) == 2
            browser.close()
    finally:
        proc.terminate(); proc.wait(timeout=15)


def test_storage_overview_is_explicit_persistent_and_read_only(tmp_path):
    """Real UI requests measurements; no implicit traversal, cleanup or execution."""
    import json
    from playwright.sync_api import sync_playwright, expect
    store = Store(tmp_path / 'storage workspace')
    cache = store.home / 'envs/hybas'
    backup = cache / '.bactologix-repair-backups/job-fixture/env'
    backup.mkdir(parents=True)
    (backup / 'retained').write_bytes(b'x' * 4096)
    external = tmp_path / 'external database'
    external.mkdir()
    payload = external / 'database.bin'; payload.write_bytes(b'y' * 8192)
    store.put('database_downloads', 'database-storage-fixture', {'id': 'database-storage-fixture',
              'tool': 'basis', 'resource': 'rgi', 'destination': str(external), 'status': 'completed'})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    with (tmp_path / 'storage-server.log').open('w') as log:
        proc = subprocess.Popen([str(Path(sys.executable).with_name('bactologix')), '--home', str(store.home),
                                 '--host', '127.0.0.1', '--port', str(port), '--no-browser'],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/_stcore/health', timeout=1) as response:
                    if response.status == 200: break
            except OSError:
                if time.monotonic() >= deadline: pytest.fail((tmp_path / 'storage-server.log').read_text())
                time.sleep(.2)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_role('link', name='Storage', exact=True).click()
            expect(page.get_by_role('button', name='Measure storage', exact=True)).to_be_enabled()
            expect(page.locator('#main')).to_contain_text('No measurement saved yet')
            assert store.get('storage', 'latest') is None and not store.jobs()
            page.get_by_role('button', name='Measure storage', exact=True).click()
            expect(page.locator('#main')).to_contain_text('Latest saved measurement')
            rows = page.locator('.storage-locations')
            expect(rows).to_contain_text('External · not selected')
            saved = store.get('storage', 'latest')
            assert not saved['include_external']
            assert any(r['category'] == 'backups' and r['logical_bytes'] == 4096 for r in saved['locations'])
            page.get_by_role('checkbox', name='Include registered external locations', exact=False).check()
            page.get_by_role('button', name='Measure storage', exact=True).click()
            expect(page.locator('#main')).to_contain_text('Workspace + registered external locations')
            saved = store.get('storage', 'latest')
            assert next(r for r in saved['locations'] if r['path'] == str(external))['logical_bytes'] == 8192
            with page.expect_download() as event:
                page.get_by_role('button', name='Download overview', exact=True).click()
            assert json.loads(Path(event.value.path()).read_text()) == saved
            page.get_by_label('Category', exact=True).select_option('backups')
            expect(rows.locator('article')).to_have_count(1)
            expect(rows).to_contain_text('Retained repair folders')
            def capture(name):
                folder = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
                if folder:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(folder) / name), full_page=True)
            page.get_by_label('Category', exact=True).select_option('')
            page.get_by_role('heading', name='Latest saved measurement', exact=True).scroll_into_view_if_needed()
            capture('storage-desktop.png')
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.get_by_role('button', name='Measure storage', exact=True)).to_be_enabled()
            assert rows.evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            assert page.locator('.storage-overview').evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            page.locator('.storage-totals').scroll_into_view_if_needed()
            capture('storage-mobile.png')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.reload()
            expect(page.locator('#main')).to_contain_text('Latest saved measurement')
            assert store.get('storage', 'latest') == saved
            # Registration changes require a fresh location review, not an automatic rescan.
            added = tmp_path / 'new database'; added.mkdir()
            store.put('database_downloads', 'database-added', {'id': 'database-added', 'tool': 'basis',
                      'resource': 'rgi', 'destination': str(added), 'status': 'completed'})
            page.get_by_role('button', name='Refresh locations', exact=True).click()
            expect(page.locator('#main')).to_contain_text('Registered locations changed')
            assert store.get('storage', 'latest') == saved
            page.get_by_label('Category', exact=True).select_option('databases')
            row = rows.locator('article').filter(has_text=str(external))
            row.locator('summary').click()
            row.get_by_role('button', name='Review databases', exact=True).click()
            expect(page.get_by_role('heading', name='Find installed databases', exact=True)).to_be_visible()
            assert not store.jobs() and payload.read_bytes() == b'y' * 8192
            assert (backup / 'retained').read_bytes() == b'x' * 4096 and errors == []
            browser.close()
    finally:
        proc.terminate(); proc.wait(timeout=15)


def test_version_compatibility_overview_preserves_saved_bindings(tmp_path, installed):
    """Records-only browser overview, exact export, filters and review navigation."""
    import json
    from playwright.sync_api import sync_playwright, expect
    store, item = installed
    sample = tmp_path / 'compatibility sample'
    (sample / 'raw/illumina').mkdir(parents=True)
    for mate in (1, 2):
        (sample / f'raw/illumina/fixture_R{mate}.fastq').write_text('@fixture\nACGT\n+\nIIII\n')
    run = create_run(store, 'hybas', {'id': 'compatibility.fixture', 'path': str(sample), 'mode': 'illumina_only'},
                     'custom_note: preserve compatibility fixture\n', label='Compatibility <script> fixture')
    archived = create_run(store, 'hybas', run['sample'], 'custom_note: archived\n', label='Archived compatibility')
    store.put('run_visibility', archived['id'], {'id': archived['id'], 'archived': True})
    # Application catalog pins are known to the server. A new active installation must not migrate this run.
    newer = {**item, 'id': 'active-new-fixture', 'version': {'commit': PIPELINES['basis']['commit'], 'ref': 'new selection'}, 'tool': 'basis'}
    store.put('installs', newer['id'], newer)
    store.put('settings', 'active-hybas', 'unregistered-selection')
    store.put('settings', 'active-basis', newer['id'])
    original = store.get('runs', run['id'])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    with (tmp_path / 'compatibility-server.log').open('w') as log:
        proc = subprocess.Popen([str(Path(sys.executable).with_name('bactologix')), '--home', str(store.home),
                                 '--host', '127.0.0.1', '--port', str(port), '--no-browser'],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/_stcore/health', timeout=1) as response:
                    if response.status == 200: break
            except OSError:
                if time.monotonic() >= deadline: pytest.fail((tmp_path / 'compatibility-server.log').read_text())
                time.sleep(.2)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_role('link', name='Install & update', exact=True).click()
            page.get_by_role('button', name='Compatibility', exact=True).click()
            overview = page.locator('.compatibility-overview')
            expect(overview).to_contain_text('Versions and compatibility')
            expect(overview).to_contain_text('unregistered-selection · not registered')
            row = page.locator('[data-compat-run="' + run['id'] + '"]')
            expect(row).to_contain_text('Recorded installation matches')
            expect(row).to_contain_text('Separate from active selection for new drafts')
            expect(row).to_contain_text('No successful validation recorded')
            assert page.url.endswith('#install/compatibility')
            assert not store.jobs() and store.get('runs', run['id']) == original
            expect(page.locator('[data-compat-run]:visible')).to_have_count(1)
            page.get_by_label('Include archived runs', exact=True).check()
            expect(page.locator('[data-compat-run]:visible')).to_have_count(2)
            page.get_by_label('Find a saved run', exact=True).fill('missing text')
            expect(page.locator('#compatibility-no-matches')).to_be_visible()
            page.get_by_label('Find a saved run', exact=True).fill('Compatibility <script> fixture')
            expect(page.locator('[data-compat-run]:visible')).to_have_count(1)
            page.get_by_label('Needs review only', exact=True).check()
            expect(row).to_be_visible()
            with page.expect_download() as event:
                page.get_by_role('button', name='Download overview', exact=True).click()
            doc = json.loads(Path(event.value.path()).read_text())
            exported = next(r for r in doc['runs'] if r['id'] == run['id'])
            assert exported['installation'] == item['id'] and exported['binding'] == 'recorded_match'
            assert doc['app']['running_version'] and 'stored records' in doc['notice']
            page.reload()
            expect(overview).to_contain_text('Versions and compatibility')
            # Change only fixture records to check refresh and mismatch presentation.
            item['version']['commit'] = 'f' * 40
            store.put('installs', item['id'], item)
            page.get_by_role('button', name='Refresh overview', exact=True).click()
            expect(row).to_contain_text('Installation/source mismatch')
            expect(page.locator('[data-compat-installation="' + item['id'] + '"]')).to_contain_text('Unreviewed commit')
            def capture(name):
                folder = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
                if folder:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(folder) / name), full_page=True)
            expect(page.get_by_role('button', name='Refresh overview', exact=True)).to_be_enabled()
            capture('compatibility-desktop.png')
            row.scroll_into_view_if_needed()
            capture('compatibility-bindings-desktop.png')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert overview.evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
            row.scroll_into_view_if_needed()
            capture('compatibility-mobile.png')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            row.get_by_role('button', name='Review saved run', exact=True).click()
            expect(page.locator('.run-workspace')).to_be_visible()
            assert page.url.endswith('/overview')
            assert not store.jobs() and store.get('runs', run['id']) == original and errors == []
            browser.close()
    finally:
        proc.terminate(); proc.wait(timeout=15)


def test_job_history_filters_pages_export_and_active_visibility(tmp_path, installed):
    """Real Chromium component uses stored attempts without submitting or editing work."""
    import json
    from playwright.sync_api import sync_playwright, expect
    from bactologix.ui_backend import snapshot, dispatch
    store, item = installed
    sample = tmp_path / 'history sample'
    (sample / 'raw/illumina').mkdir(parents=True)
    for mate in (1, 2):
        (sample / f'raw/illumina/fixture_R{mate}.fastq').write_text('@fixture\nACGT\n+\nIIII\n')
    run = create_run(store, 'hybas', {'id': 'history.sample', 'path': str(sample), 'mode': 'illumina_only'},
                     'custom_note: unchanged\n', label='History <script> fixture')
    history = []
    for i in range(28):
        jid = store.submit('workflow', {'run_id': run['id'], 'action': 'run' if i % 2 else 'dry-run',
                                       'secret_fixture': 'never export payload'})
        store.update_job(jid, status=['succeeded', 'failed', 'cancelled', 'interrupted'][i % 4])
        history.append(jid)
    runner_check = store.submit('check', {'installation': item['id']})
    store.update_job(runner_check, status='succeeded')
    app_check = store.submit('smoke', {})
    store.update_job(app_check, status='succeeded')
    missing = store.submit('workflow', {'run_id': 'missing-run', 'action': 'run'})
    store.update_job(missing, status='failed')
    waiting = store.submit('smoke', {})
    running = store.submit('smoke', {})
    store.update_job(running, status='running')
    store.put('run_visibility', run['id'], {'id': run['id'], 'archived': True})
    original = store.jobs()
    original_run = store.get('runs', run['id'])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.clock.install(time='2026-10-28T12:00:00Z')
        page.clock.pause_at('2026-10-28T12:00:10Z')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('link', name='Jobs & progress', exact=True).click()
        active = page.get_by_role('region', name='Active jobs', exact=True)
        panel = page.get_by_role('region', name='Job history', exact=True)
        expect(active.locator('.job-card')).to_have_count(2)
        expect(panel.locator('.job-card')).to_have_count(25)
        expect(panel).to_contain_text('1–25 of 31 matching attempts')
        page.get_by_role('button', name='Next page', exact=True).click()
        expect(panel.locator('.job-card')).to_have_count(6)
        expect(panel).to_contain_text('26–31 of 31 matching attempts')
        page.get_by_label('Search history', exact=True).fill('  HISTORY.SAMPLE  ')
        expect(panel).to_contain_text('1–25 of 28 matching attempts')
        expect(page.get_by_label('Search history', exact=True)).to_be_focused()
        with page.expect_download() as event:
            page.get_by_role('button', name='Download matching history', exact=True).click()
        doc = json.loads(Path(event.value.path()).read_text())
        assert doc['count'] == 28 and [j['id'] for j in doc['jobs']] == history[::-1]
        assert 'never export payload' not in json.dumps(doc) and 'payload' not in doc['jobs'][0]
        assert all(j['pipeline'] == 'hybas' and j['run_label'] == run['label'] for j in doc['jobs'])
        assert doc['jobs'][0]['started'] is None and doc['jobs'][0]['finished'] is None
        page.get_by_label('History status', exact=True).select_option('succeeded')
        expect(panel.locator('.job-card')).to_have_count(7)
        page.get_by_label('History order', exact=True).select_option('oldest')
        expect(panel.locator('.job-card').first).to_have_attribute('data-job-id', history[0])
        page.get_by_label('History pipeline', exact=True).select_option('basis')
        expect(panel).to_contain_text('No matching attempts')
        expect(page.get_by_role('button', name='Download matching history', exact=True)).to_be_disabled()
        expect(active.locator('.job-card')).to_have_count(2)
        expect(page.locator('[data-waiting-job="' + waiting + '"]')).to_be_visible()
        page.get_by_role('button', name='Clear history filters', exact=True).click()
        page.get_by_label('History pipeline', exact=True).select_option('hybas')
        expect(panel).to_contain_text('of 29 matching attempts')
        page.get_by_label('Job type', exact=True).select_option('check')
        expect(panel.locator('.job-card')).to_have_count(1)
        expect(panel.locator('.job-card')).to_have_attribute('data-job-id', runner_check)
        page.get_by_role('button', name='Clear history filters', exact=True).click()
        page.get_by_label('History pipeline', exact=True).select_option('unassigned')
        expect(panel.locator('.job-card')).to_have_count(2)
        page.get_by_label('Search history', exact=True).fill('missing-run')
        expect(panel.locator('.job-card')).to_have_count(1)
        expect(panel.get_by_role('button', name='Review run', exact=True)).to_have_count(0)
        # Refresh with a completed active attempt: history filtering remains local and intact.
        store.update_job(running, status='succeeded')
        page.evaluate('workspace => {Object.assign(window.acceptanceWorkspace,workspace);window.acceptanceRender(null);}', snapshot(store))
        expect(active.locator('.job-card')).to_have_count(1)
        expect(page.get_by_label('Search history', exact=True)).to_have_value('missing-run')
        expect(panel).to_contain_text('1–1 of 1 matching attempts · 32 total history')
        page.get_by_role('button', name='Clear history filters', exact=True).click()
        page.get_by_label('Search history', exact=True).fill(history[0])
        expect(panel.locator('.job-card')).to_have_count(1)
        expect(panel).to_contain_text('History <script> fixture')
        assert page.evaluate('window.historyInjected') is None
        def capture(name):
            folder = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(folder) / name), full_page=True)
        capture('history-desktop.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        panel.scroll_into_view_if_needed()
        assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        capture('history-mobile.png')
        page.set_viewport_size({'width': 1440, 'height': 1100})
        panel.get_by_role('button', name='Review run', exact=True).click()
        request = page.evaluate('window.acceptanceEvents.at(-1).value')
        assert request['kind'] == 'inspect_run' and request['payload']['id'] == run['id']
        result = dispatch(store, request['kind'], request['payload'])
        page.evaluate('response => window.acceptanceRender(response)', {'id': request['id'], 'ok': True, **result})
        expect(page.locator('.run-workspace')).to_be_visible()
        page.get_by_role('link', name='Jobs & progress', exact=True).click()
        expect(page.get_by_label('Search history', exact=True)).to_have_value(history[0])
        assert len(store.jobs()) == len(original) and store.get('runs', run['id']) == original_run
        assert [j for j in store.jobs() if j['id'] != running] == [j for j in original if j['id'] != running]
        assert [e['value']['kind'] for e in page.evaluate('window.acceptanceEvents')] == ['inspect_run']
        assert errors == []
        browser.close()


def test_full_saved_configuration_comparison(tmp_path, installed):
    import json
    from playwright.sync_api import sync_playwright, expect
    from bactologix.ui_backend import snapshot, dispatch
    store, item = installed
    sample = tmp_path / 'comparison sample'
    (sample / 'raw/illumina').mkdir(parents=True)
    for mate in (1, 2):
        (sample / f'raw/illumina/fixture_R{mate}.fastq').write_text('@fixture\nACGT\n+\nIIII\n')
    common = ''.join(f'custom_{i:02d}: {i}\n' for i in range(65))
    runs = [create_run(store, 'hybas', {'id': 'comparison.fixture', 'path': str(sample), 'mode': 'illumina_only'},
                      common + text, label=label) for text, label in [
        ('literal.key: true\nnested: {key: null}\nlong: ' + 'x'*1100 + '\n', 'Left <script> fixture'),
        ('literal.key: 1\nnested: {key: false}\n', 'Right fixture'),
        ('literal.key: false\n', 'Third fixture')]]
    original = store.all('runs')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('link', name='Comparisons', exact=True).click()
        page.get_by_label('Select ' + runs[0]['label'], exact=True).check()
        panel = page.get_by_role('region', name='Full saved configuration comparison', exact=True)
        expect(panel.get_by_role('button', name='Compare configurations', exact=True)).to_be_disabled()
        page.locator('.comparison-picker summary').click()
        page.get_by_label('Select ' + runs[1]['label'], exact=True).check()
        page.get_by_label('Reference run', exact=True).select_option(runs[0]['id'])
        def respond(kind):
            req = page.evaluate('window.acceptanceEvents.at(-1).value')
            assert req['kind'] == kind
            result = dispatch(store, kind, req['payload'])
            page.evaluate('({response,workspace}) => {Object.assign(window.acceptanceWorkspace,workspace);window.acceptanceRender(response);}',
                          {'response': {'id': req['id'], 'ok': True, **result}, 'workspace': snapshot(store)})
            return result
        page.get_by_role('button', name='Compare configurations', exact=True).click()
        result = respond('compare_saved_runs')
        assert result['comparison']['reference_id'] == runs[0]['id']
        expect(panel).to_contain_text('This does not compare results')
        page.get_by_label('Search setting paths', exact=True).fill('literal.key')
        expect(panel.locator('tbody tr')).to_have_count(1)
        expect(panel.locator('tbody tr')).to_contain_text('boolean')
        expect(panel.locator('tbody tr')).to_contain_text('integer')
        expect(panel.locator('tbody tr')).to_have_attribute('data-comparison-path', '["literal.key"]')
        page.get_by_label('Search setting paths', exact=True).fill('nested.key')
        expect(panel.locator('tbody tr')).to_contain_text('null')
        expect(panel.locator('tbody tr')).to_contain_text('boolean')
        page.get_by_label('Search setting paths', exact=True).fill('long')
        expect(panel).to_contain_text('shortened; full value in download')
        expect(panel.locator('tbody tr')).to_contain_text('Not recorded')
        with page.expect_download() as event:
            page.get_by_role('button', name='Refresh & download comparison', exact=True).click()
            refreshed = respond('download_comparison')
        doc = json.loads(Path(event.value.path()).read_text())
        assert doc == refreshed['comparison'] and len(doc['rows']) > 65
        assert len(next(r for r in doc['rows'] if r['path'] == ['long'])['cells'][0]['yaml']) == 1100
        assert any(not r['changed'] for r in doc['rows'])  # Export includes rows hidden by filters.
        page.get_by_label('Search setting paths', exact=True).fill('')
        page.get_by_label('Differences only', exact=True).uncheck()
        page.get_by_label('Compare section', exact=True).select_option('configuration')
        expect(panel.locator('tbody tr')).to_have_count(50)
        page.get_by_role('button', name='Next rows', exact=True).click()
        expect(panel).to_contain_text('51–')
        page.get_by_label('Search setting paths', exact=True).fill('not a recorded key')
        expect(panel).to_contain_text('No matching rows')
        page.get_by_label('Reference run', exact=True).select_option(runs[1]['id'])
        expect(panel).to_contain_text('Selection changed')
        expect(panel.locator('table')).to_have_count(0)
        expect(page.get_by_role('button', name='Refresh & download comparison', exact=True)).to_have_count(0)
        page.get_by_role('button', name='Compare configurations', exact=True).click()
        assert respond('compare_saved_runs')['comparison']['reference_id'] == runs[1]['id']
        page.get_by_label('Search setting paths', exact=True).fill('literal.key')
        expect(panel.locator('thead th').nth(1)).to_contain_text('Right fixture')
        page.locator('.comparison-picker summary').click()
        page.get_by_label('Select ' + runs[2]['label'], exact=True).check()
        expect(panel).to_contain_text('Selection changed')
        page.get_by_role('button', name='Compare configurations', exact=True).click()
        assert len(respond('compare_saved_runs')['comparison']['runs']) == 3
        expect(panel.locator('thead th')).to_have_count(4)
        page.get_by_label('Search setting paths', exact=True).fill('')
        page.get_by_label('Differences only', exact=True).check()
        def capture(name):
            folder = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(folder) / name), full_page=True)
        panel.scroll_into_view_if_needed(); capture('configuration-comparison-desktop.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        panel.scroll_into_view_if_needed()
        capture('configuration-comparison-mobile.png')
        assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1'), panel.evaluate('(el) => [...el.children].map(x=>[x.className,x.clientWidth,x.scrollWidth])')
        expect(panel.locator('.table-wrap')).to_be_visible()
        assert store.all('runs') == original and not store.jobs() and errors == []
        browser.close()


def test_reviewed_sample_import_preview_staleness_and_commit(tmp_path):
    import csv
    import io
    from playwright.sync_api import sync_playwright, expect
    from bactologix.ui_backend import snapshot, dispatch
    from bactologix.runs import register_sample
    store = Store(tmp_path/'import workspace')
    folder = tmp_path/'original inputs, with spaces'; folder.mkdir()
    original = folder/'unchanged.txt'; original.write_text('untouched input fixture')
    def text(rows):
        out = io.StringIO(); writer = csv.writer(out)
        writer.writerow(['sample_id','input_folder','read_mode']); writer.writerows(rows)
        return out.getvalue()
    invalid = text([['first', folder, ''], ['FIRST', folder, 'hybrid'], ['<script>fixture</script>', '/missing', 'bad']])
    valid = text([[f'sample{i:02d}', folder, '' if i % 2 else 'illumina_only'] for i in range(27)])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('link', name='Samples', exact=True).click()
        page.get_by_role('button', name='Import table', exact=True).click()
        dialog = page.get_by_role('dialog', name='Import a sample table', exact=True)
        expect(dialog.get_by_role('button', name='Import reviewed samples', exact=True)).to_be_disabled()
        contents = dialog.get_by_label('Table contents', exact=True)
        contents.fill(invalid)
        def respond(kind, failure=False):
            req = page.evaluate('window.acceptanceEvents.at(-1).value')
            assert req['kind'] == kind
            try:
                result = dispatch(store, kind, req['payload'])
                assert not failure
                response = {'id': req['id'], 'ok': True, **result}
            except ValueError as exc:
                assert failure
                response = {'id': req['id'], 'ok': False, 'message': str(exc)}
            page.evaluate('({response,workspace}) => {Object.assign(window.acceptanceWorkspace,workspace);window.acceptanceRender(response);}',
                          {'response': response, 'workspace': snapshot(store)})
            return response
        dialog.get_by_role('button', name='Preview table', exact=True).click()
        respond('preview_samples')
        expect(dialog).to_contain_text('0 ready · 3 need correction')
        expect(dialog).to_contain_text('<script>fixture</script>')
        expect(dialog.get_by_role('button', name='Import reviewed samples', exact=True)).to_be_disabled()
        assert not store.all('samples') and not store.jobs()
        contents.fill(valid)
        expect(dialog).to_contain_text('Table changed')
        expect(dialog.locator('#sample-import-preview')).to_be_hidden()
        dialog.get_by_role('button', name='Preview table', exact=True).click()
        respond('preview_samples')
        expect(dialog).to_contain_text('27 ready · 0 need correction')
        expect(dialog.locator('tbody tr')).to_have_count(25)
        expect(dialog.get_by_role('button', name='Import 27 reviewed samples', exact=True)).to_be_enabled()
        dialog.get_by_role('button', name='Next rows', exact=True).click()
        expect(dialog.locator('tbody tr')).to_have_count(2)
        dialog.get_by_label('Show problem rows only', exact=True).check()
        expect(dialog).to_contain_text('No matching rows')
        dialog.get_by_label('Show problem rows only', exact=True).uncheck()
        expect(dialog.locator('tbody tr')).to_have_count(25)
        # Uploading a changed table invalidates review just like typing.
        page.locator('#sample-upload').set_input_files({'name': 'samples.csv', 'mimeType': 'text/csv', 'buffer': (valid+'\n').encode()})
        expect(contents).to_have_value(valid.replace('\r\n','\n')+'\n')
        expect(dialog.get_by_role('button', name='Import reviewed samples', exact=True)).to_be_disabled()
        dialog.get_by_role('button', name='Preview table', exact=True).click(); respond('preview_samples')
        register_sample(store, 'elsewhere', str(folder))
        dialog.get_by_role('button', name='Import 27 reviewed samples', exact=True).click()
        respond('import_samples', failure=True)
        expect(dialog.get_by_role('alert')).to_contain_text('changed')
        expect(dialog.get_by_role('button', name='Import reviewed samples', exact=True)).to_be_disabled()
        assert len(store.all('samples')) == 1
        dialog.get_by_role('button', name='Preview table', exact=True).click(); respond('preview_samples')
        def capture(name):
            destination = os.environ.get('BACTOLOGIX_UI_PREVIEWS')
            if destination:
                Path(destination).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(destination)/name), full_page=True)
        expect(page.locator('#toast.show')).to_have_count(0)
        dialog.locator('.modal-body').evaluate('(el) => el.scrollTop=0'); capture('sample-import-desktop.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        dialog.locator('.modal-body').evaluate('(el) => el.scrollTop=0')
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth+1')
        expect(dialog.get_by_role('button', name='Import 27 reviewed samples', exact=True)).to_be_visible()
        capture('sample-import-mobile.png')
        dialog.locator('#sample-import-rows').scroll_into_view_if_needed()
        capture('sample-import-rows-mobile.png')
        page.set_viewport_size({'width': 1440, 'height': 1100})
        dialog.get_by_role('button', name='Import 27 reviewed samples', exact=True).click()
        respond('import_samples')
        expect(dialog).to_have_count(0)
        expect(page.locator('[data-sample-row]')).to_have_count(28)
        assert store.get('samples','sample01')['mode'] == 'hybrid'
        assert store.get('samples','sample00')['path'] == str(folder)
        assert original.read_text() == 'untouched input fixture' and not store.jobs() and not store.all('runs')
        # Reopening never reuses a previously approved import.
        page.get_by_role('button', name='Import table', exact=True).click()
        expect(dialog.get_by_role('button', name='Import reviewed samples', exact=True)).to_be_disabled()
        assert errors == []
        browser.close()


def test_responsive_navigation_and_workspace_tools(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    from bactologix.ui_backend import dispatch, snapshot
    store = Store(tmp_path / 'workspace')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.clock.install(time='2026-10-28T12:00:00Z')
        page.clock.pause_at('2026-10-28T12:00:01Z')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('link', name='Install & update', exact=True).click()
        for width in (1440, 1024, 390):
            page.set_viewport_size({'width': width, 'height': 844})
            for name in ('Guided setup', 'Pipelines & environments', 'PC capacity', 'Workstation check', 'Compatibility'):
                page.get_by_role('button', name=name, exact=True).click()
                if name == 'Compatibility':
                    req = page.evaluate('window.acceptanceEvents.at(-1)?.value')
                    if req and req['kind'] == 'inspect_compatibility':
                        result = {'id': req['id'], 'ok': True, **dispatch(store, req['kind'], req['payload'])}
                        page.evaluate('({r,s})=>{Object.assign(window.acceptanceWorkspace,s);window.acceptanceRender(r)}', {'r': result, 's': snapshot(store)})
                assert page.locator('.bacto-root').evaluate('(el) => el.scrollWidth <= innerWidth+1')
                for tab in page.locator('.setup-tabs button').all():
                    expect(tab).to_be_visible()
        toggle = page.get_by_role('button', name='Open navigation', exact=True)
        expect(page.locator('.sidebar')).to_be_hidden()
        toggle.click()
        expect(toggle).to_have_attribute('aria-expanded', 'true')
        expect(page.get_by_role('link', name='Overview', exact=True)).to_be_focused()
        page.locator('.sidebar a').first.focus()
        page.keyboard.press('Shift+Tab')
        expect(page.locator('.sidebar button').last).to_be_focused()
        page.keyboard.press('Escape')
        expect(toggle).to_be_focused()
        expect(page.locator('.sidebar')).to_be_hidden()
        toggle.click()
        page.get_by_role('link', name='Samples', exact=True).click()
        expect(page.locator('.sidebar')).to_be_hidden()
        expect(page.locator('.workspace-shell')).not_to_have_attribute('inert', '')
        page.locator('.workspace-tools > summary').click()
        page.locator('#main h1').click(position={'x': 5, 'y': 5})
        expect(page.locator('.workspace-tools')).not_to_have_attribute('open', '')
        page.locator('.workspace-tools > summary').click()
        page.get_by_role('button', name='Desktop window', exact=True).click()
        expect(page.get_by_role('dialog')).to_contain_text('Bactologix opens in app mode by default')
        dialog = page.get_by_role('dialog')
        dialog.evaluate('el => {const group=document.createElement("div");group.hidden=true;group.innerHTML="<button>Hidden action</button>";el.append(group)}')
        dialog.get_by_role('button', name='Close dialog', exact=True).focus()
        page.keyboard.press('Shift+Tab')
        expect(dialog.get_by_role('button', name='Close', exact=True)).to_be_focused()
        page.keyboard.press('Tab')
        expect(dialog.get_by_role('button', name='Close dialog', exact=True)).to_be_focused()
        expect(page.locator('.workspace-tools')).not_to_have_attribute('open', '')
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.locator('.workspace-tools > summary')).to_be_focused()
        toggle.click()
        page.get_by_role('button', name='Get support', exact=True).click()
        page.keyboard.press('Escape')
        expect(toggle).to_be_focused()
        page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(page.get_by_role('link', name='Overview', exact=True)).to_be_visible()
        page.locator('a.skip').focus()
        page.keyboard.press('Enter')
        expect(page.locator('#main')).to_be_focused()
        assert errors == [] and not store.jobs() and not store.all('runs')
        browser.close()


def test_job_status_column_and_browser_switch(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    store = Store(tmp_path / 'workspace')
    for state in ('succeeded', 'cancelled', 'failed'):
        jid = store.submit('smoke', {})
        store.update_job(jid, status=state)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        render_component(page, store)
        page.get_by_role('link', name='Jobs & progress', exact=True).click()
        badges = page.locator('.job-card > div:nth-child(2) > .pill')
        expect(badges).to_have_count(3)
        positions = badges.evaluate_all('(nodes)=>nodes.map(n=>n.getBoundingClientRect().left)')
        assert max(positions)-min(positions) < 1
        page.locator('.workspace-tools > summary').click()
        page.get_by_role('button', name='Restart as browser', exact=True).click()
        req = page.evaluate('window.acceptanceEvents.at(-1).value')
        assert req['kind'] == 'restart_as_browser' and req['payload'] == {}
        browser.close()
