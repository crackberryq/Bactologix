"""Real Chromium -> Streamlit -> SQLite checks, enabled in the browser CI job."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from bactologix.store import Store

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


def test_sample_picker_configuration_and_report(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    workspace = tmp_path / 'workspace'
    store = Store(workspace)
    sample = tmp_path / 'sample folder'
    (sample / 'raw/illumina').mkdir(parents=True)
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
