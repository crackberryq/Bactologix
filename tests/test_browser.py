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
            expect(page.get_by_role('heading', name='One sample, a clear history.', exact=True)).to_be_visible()
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
            expect(page.get_by_role('link', name='Databases & models', exact=True)).to_be_visible()
            expect(page.get_by_role('link', name='Install & update', exact=True)).to_be_visible()
            capture('configuration-mobile')
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
            page.get_by_role('button', name='Use saved databases', exact=True).click()
            expect(page.locator('[data-apply-resource="busco"]')).to_be_disabled()
            expect(page.locator('.database-apply-run')).to_contain_text('Already using this saved location')
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.get_by_role('link', name='Samples & variants', exact=True).click()
            page.locator('.registered-samples').get_by_role('button', name='Remove sample', exact=True).click()
            expect(page.locator('.registered-samples')).to_contain_text('0 folders')
            assert Store(workspace).get('samples', 'browser.fixture') is None
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(0)
            page.locator('[data-action="run-list"][data-id="archived"]').click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(2)
            page.locator('[data-run-row="' + parent['id'] + '"]').get_by_role('button', name='Restore', exact=True).click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(1)
            page.locator('[data-action="run-list"][data-id="current"]').click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(1)
            page.locator('[data-run-row="' + parent['id'] + '"]').get_by_role('button', name='Remove from list', exact=True).click()
            expect(page.locator('.saved-runs [data-run-row]')).to_have_count(0)
            assert Store(workspace).get('runs', parent['id']) == parent
            for mate in (1, 2):
                assert (sample / f'raw/illumina/fixture_R{mate}.fastq').read_text() == '@fixture\nACGT\n+\nIIII\n'
            def register_again():
                page.get_by_role('button', name='Add sample', exact=True).first.click()
                page.locator('#sample-id').fill('browser.fixture')
                page.locator('#sample-path').fill(str(sample))
                page.locator('#sample-mode').select_option('illumina_only')
                page.get_by_role('button', name='Register sample', exact=True).click()
                expect(page.locator('.registered-samples')).to_contain_text('1 folder')
            register_again()
            page.get_by_role('button', name='Create run drafts', exact=True).click()
            page.locator('#run-cores').fill('3')
            page.locator('[data-sample="browser.fixture"]').check()
            page.get_by_role('dialog').get_by_role('button', name='Remove sample', exact=True).click()
            expect(page.get_by_role('heading', name='Create run drafts', exact=True)).to_be_visible()
            expect(page.locator('#run-cores')).to_have_value('3')
            expect(page.locator('[data-sample="browser.fixture"]')).to_have_count(0)
            expect(page.get_by_role('button', name='Continue to settings', exact=True)).to_be_disabled()
            assert Store(workspace).get('samples', 'browser.fixture') is None
            page.get_by_role('button', name='Close dialog', exact=True).click()
            register_again()
            page.get_by_role('button', name='Create run drafts', exact=True).click()
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
            assert page.get_by_role('dialog').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            expect(page.locator('.modal-foot')).to_contain_text('Draft not saved yet')
            expect(page.locator('.modal-foot > small')).to_be_visible()
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
            page.get_by_role('button', name='Run settings', exact=True).click()
            expect(page.locator('#action-error')).not_to_be_empty()
            expect(run_yaml).to_have_value('broken: [')
            assert len(Store(workspace).all('runs')) == 2
            run_yaml.fill(original_yaml + '\ncustom_menu: {literal.key: [false, 0, preserve]}\n')
            page.get_by_role('button', name='Run settings', exact=True).click()
            expect(page.locator('#run-setting-search')).to_be_visible()
            page.get_by_role('button', name='Review drafts', exact=True).click()
            expect(page.get_by_role('heading', name='Ready to create your drafts?', exact=True)).to_be_visible()
            expect(page.locator('.review-stats')).to_contain_text('3 cores')
            capture('draft-review')
            page.get_by_role('button', name='Create drafts', exact=True).click()
            expect(page.get_by_role('heading', name='browser.fixture', exact=True)).to_be_visible()
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
            expect(page.locator('.readiness-item')).to_have_count(5)
            expect(page.locator('[data-readiness="databases"]')).to_contain_text('Frozen in this draft')
            expect(page.get_by_role('button', name='Validate run', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Check environments', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Run / resume', exact=True)).to_be_disabled()
            capture('saved-run-overview')
            def saved_tab(name):
                page.locator('[data-action="saved-run-tab"][data-id="' + name + '"]').click()
            saved_tab('kraken')
            expect(page.locator('[data-saved-setting="kraken_decision_mode"]')).to_have_text('Off')
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
            # Cancelling cannot change the saved run or leave a half-created draft.
            page.get_by_role('button', name='Close dialog', exact=True).click()
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
            page.get_by_role('button', name='Review revision', exact=True).click()
            expect(page.locator('.review-stats')).to_contain_text('5 cores')
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
            page.get_by_role('button', name='Close dialog', exact=True).click()
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
            page.locator('[data-installation="basis-ui-fixture"]').get_by_role('button', name='Check / install environments', exact=True).click()
            expect(page.get_by_role('heading', name='Tool environments', exact=True)).to_be_visible()
            expect(page.locator('.environment-row')).to_contain_text('VFDB and BacMet')
            expect(page.get_by_role('button', name='Check selected', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_be_enabled()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            current_card = page.locator('[data-installation="hybas-ui-fixture"]')
            current_card.get_by_role('button', name='Reuse existing environments', exact=True).click()
            expect(page.get_by_role('heading', name='Reuse existing environments', exact=True)).to_be_visible()
            page.locator('#reuse-choice-hybas-ui-fixture').select_option(str(old_cache))
            expect(page.locator('#reuse-cache-hybas-ui-fixture')).to_have_value(str(old_cache))
            capture('reuse-existing-environments')
            page.get_by_role('button', name='Use selected cache', exact=True).click()
            expect(page.get_by_role('heading', name='Tool environments', exact=True)).to_be_visible()
            expect(page.locator('#environment-cache-hybas-ui-fixture')).to_have_value(str(old_cache))
            assert Store(workspace).get('installs', hybas['id'])['rule_cache'] == str(old_cache)
            assert Store(workspace).get('runs', parent['id']) == parent
            page.get_by_role('button', name='Close dialog', exact=True).click()
            current_card.get_by_role('button', name='Uninstall version', exact=True).click()
            expect(page.get_by_role('button', name='Uninstall this version', exact=True)).to_be_disabled()
            expect(page.locator('.modal')).to_contain_text('Select another active version')
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.locator('[data-installation="' + old_id + '"]').get_by_role('button', name='Uninstall version', exact=True).click()
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
            page.get_by_role('link', name='Samples & variants', exact=True).click()
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
            assert page.get_by_role('dialog').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            expect(page.get_by_role('button', name='Run / resume', exact=True)).to_be_in_viewport()
            assert page.locator('#toast').evaluate('(el) => {const r=el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth;}')
            capture('saved-run-checks-mobile')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('button', name='Close dialog', exact=True).click()
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
            page.get_by_role('link', name='Jobs & progress', exact=True).click()
            expect(page.locator('.job-guidance')).to_contain_text('Check the database location')
            expect(page.get_by_role('button', name='Review run', exact=True)).to_be_visible()
            capture('job-actionable-error')
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
            page.wait_for_function("performance.getEntriesByType('navigation')[0]?.type === 'reload'", timeout=45000)
            expect(page.get_by_role('link', name='Samples & variants', exact=True)).to_be_visible(timeout=30000)
            page.wait_for_function('!window.__bactologixRestart')
            assert page.url.rstrip('/') == url
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
