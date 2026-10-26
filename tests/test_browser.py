"""Real Chromium -> Streamlit -> SQLite checks, enabled in the browser CI job."""
import os
from pathlib import Path
import socket
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
    parent = create_run(store, 'hybas', {'id': 'browser_fixture', 'path': str(sample), 'mode': 'illumina_only'},
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
    proc = subprocess.Popen([sys.executable, '-m', 'bactologix.cli', '--home', str(workspace), '--port', str(port)],
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
            page.get_by_role('button', name='Add samples', exact=True).click()
            page.get_by_role('button', name='Browse folders', exact=True).click()
            page.locator('#folder-path').fill(str(sample))
            page.get_by_role('button', name='Go', exact=True).click()
            expect(page.locator('.folder-location')).to_contain_text(str(sample))
            page.get_by_role('button', name='Select this folder', exact=True).click()
            page.locator('#sample-id').fill('browser_fixture')
            page.get_by_role('button', name='Register sample', exact=True).click()
            expect(page.get_by_role('heading', name='One sample, a clear history.', exact=True)).to_be_visible()
            assert Store(workspace).get('samples', 'browser_fixture')['path'] == str(sample)
            page.get_by_role('link', name='Configuration', exact=True).click()
            page.get_by_label('Full configuration', exact=True).fill('threads: 2\ncustom_note: browser acceptance\n')
            page.get_by_role('button', name='Save defaults', exact=True).first.click()
            expect(page.locator('#toast')).to_contain_text('Defaults saved')
            assert 'browser acceptance' in Store(workspace).get('configs', 'hybas')
            page.get_by_role('link', name='Databases & models', exact=True).click()
            page.get_by_role('button', name=PIPELINES['basis']['name'], exact=True).click()
            card_panel = page.locator('.database-card').filter(has=page.get_by_role('heading', name='CARD / RGI', exact=True))
            card_panel.get_by_role('button', name='Download database', exact=True).click()
            page.get_by_label('Download location', exact=True).fill(str(tmp_path))
            page.get_by_role('button', name='Browse', exact=True).last.click()
            page.locator('#folder-path').fill(str(download))
            page.get_by_role('button', name='Go', exact=True).click()
            expect(page.locator('.folder-location')).to_contain_text(str(download))
            page.get_by_role('button', name='Select this folder', exact=True).click()
            expect(page.get_by_label('Download location', exact=True)).to_have_value(str(download))
            expect(page.get_by_role('button', name='Download and prepare', exact=True)).to_be_enabled()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            card_panel.get_by_role('button', name='Use downloaded', exact=True).click()
            expect(page.get_by_label('CARD / RGI location', exact=True)).to_have_value(str(card / 'card.json'))
            page.get_by_role('button', name='Save locations', exact=True).first.click()
            expect(page.locator('#toast')).to_contain_text('Database locations saved')
            assert yaml.safe_load(Store(workspace).get('configs', 'basis'))['modules']['rgi']['database'] == str(card / 'card.json')
            page.get_by_role('button', name=PIPELINES['hybas']['name'], exact=True).click()
            page.get_by_label('BUSCO lineage location', exact=True).fill(str(lineage))
            page.get_by_role('button', name='Save locations', exact=True).first.click()
            expect(page.locator('#toast')).to_contain_text('Database locations saved')
            assert Store(workspace).get('runs', parent['id'])['config'] == parent['config']
            page.get_by_role('button', name='Use saved databases for runs', exact=True).click()
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
            assert Store(workspace).get('samples', 'browser_fixture') is None
            assert Store(workspace).get('runs', parent['id']) == parent
            for mate in (1, 2):
                assert (sample / f'raw/illumina/fixture_R{mate}.fastq').read_text() == '@fixture\nACGT\n+\nIIII\n'
            def register_again():
                page.get_by_role('button', name='Add sample', exact=True).first.click()
                page.locator('#sample-id').fill('browser_fixture')
                page.locator('#sample-path').fill(str(sample))
                page.locator('#sample-mode').select_option('illumina_only')
                page.get_by_role('button', name='Register sample', exact=True).click()
                expect(page.locator('.registered-samples')).to_contain_text('1 folders')
            register_again()
            page.get_by_role('button', name='Create run drafts', exact=True).click()
            page.locator('#run-cores').fill('3')
            page.locator('#run-sample-browser_fixture').check()
            page.get_by_role('dialog').get_by_role('button', name='Remove sample', exact=True).click()
            expect(page.get_by_role('heading', name='Create run drafts', exact=True)).to_be_visible()
            expect(page.locator('#run-cores')).to_have_value('3')
            expect(page.locator('#run-sample-browser_fixture')).to_have_count(0)
            expect(page.get_by_role('button', name='Create drafts', exact=True)).to_be_disabled()
            assert Store(workspace).get('samples', 'browser_fixture') is None
            page.get_by_role('button', name='Close dialog', exact=True).click()
            register_again()
            page.get_by_role('button', name='Create run drafts', exact=True).click()
            expect(page.locator('#run-sample-browser_fixture')).not_to_be_checked()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.get_by_role('link', name='Install & update', exact=True).click()
            page.get_by_role('button', name='Check / install environments', exact=True).last.click()
            expect(page.get_by_role('heading', name='Tool environments', exact=True)).to_be_visible()
            expect(page.locator('.environment-row')).to_contain_text('VFDB and BacMet')
            expect(page.get_by_role('button', name='Check selected', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Install selected', exact=True)).to_be_enabled()
            page.get_by_role('button', name='Close dialog', exact=True).click()
            page.get_by_role('link', name='Results & reports', exact=True).click()
            page.get_by_role('button', name='View HTML', exact=True).click()
            expect(page.frame_locator('.html-report-frame').get_by_role('heading', name='Browser software fixture')).to_be_visible()
            assert page.locator('.html-report-frame').get_attribute('sandbox') == 'allow-scripts allow-downloads'
            assert not errors, errors
            browser.close()
    finally:
        import signal
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
        log.close()
