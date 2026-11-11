"""Exercise background refresh through the real Streamlit host, not an HTML stub."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from bactologix.store import Store
from test_browser import worker_lease


pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


@pytest.fixture
def polling_host(tmp_path, worker_lease):
    """Only delay delivery; use the actual component, callbacks and SQLite backend."""
    import bactologix

    store = Store(tmp_path / 'workspace')
    worker_lease(store)
    job = store.submit('smoke', {})
    store.update_job(job, status='running')
    app = Path(bactologix.__file__).with_name('app.py')
    wrapper = tmp_path / 'slow_host.py'
    wrapper.write_text(f'''
import json
from pathlib import Path
import runpy
import time
import streamlit as st
import bactologix.ui_backend as backend
import bactologix.update_checks as update_checks

# Stable across Streamlit reruns: wrapping repeatedly would delay each request twice.
if not hasattr(backend, "_polling_test_original"):
    backend._polling_test_original = backend.handle_request
    def delayed_request(store, request, session):
        marker = store.home / "request-started.json"
        marker.write_text(json.dumps(request))
        deadline = time.monotonic() + 25
        while not (store.home / ("release-" + request["id"])).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Browser test did not release its delayed request")
            time.sleep(.02)
        return backend._polling_test_original(store, request, session)
    backend.handle_request = delayed_request
# These tests must not contact release services or start a software worker.
update_checks.tick = lambda store: None
runpy.run_path({str(app)!r}, run_name="__main__")
st.write("Native host sentinel")
@st.dialog("Native host dialog")
def native_dialog():
    st.write("Native dialog content remains visible")
if st.button("Open native host dialog"):
    native_dialog()
''')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    log_path = tmp_path / 'server.log'
    with log_path.open('w') as log:
        proc = subprocess.Popen(
            [sys.executable, '-m', 'streamlit', 'run', str(wrapper),
             '--server.address=127.0.0.1', f'--server.port={port}',
             '--server.headless=true', '--browser.gatherUsageStats=false'],
            env={**os.environ, 'BACTOLOGIX_HOME': str(store.home)},
            stdout=log, stderr=subprocess.STDOUT)
        url = f'http://127.0.0.1:{port}'
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urlopen(url + '/_stcore/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if proc.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail(log_path.read_text())
                    time.sleep(.1)
            yield store, job, url, proc
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def effective_opacity(locator):
    """A shadow component may be opaque itself while a host ancestor fades it."""
    return locator.evaluate('''element => {
        let opacity = 1;
        while (element) {
            opacity *= Number(getComputedStyle(element).opacity);
            element = element.parentElement || element.getRootNode().host;
        }
        return opacity;
    }''')


def request_started(store, previous=None):
    deadline = time.monotonic() + 10
    marker = store.home / 'request-started.json'
    while time.monotonic() < deadline:
        try:
            request = json.loads(marker.read_text())
            if request['id'] != previous:
                return request
        except (OSError, ValueError):
            pass
        time.sleep(.05)
    pytest.fail('The real Streamlit callback did not receive the browser request')


def release_request(store, request):
    (store.home / ('release-' + request['id'])).touch()


def test_slow_background_refresh_keeps_workspace_readable_and_interactive(polling_host, tmp_path):
    from playwright.sync_api import sync_playwright, expect

    store, job, url, _ = polling_host
    with sync_playwright() as p:
        executable = os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE')
        browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 800})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.locator('.bacto-root')).to_be_visible(timeout=30000)
        request = request_started(store)
        assert request['kind'] == 'refresh'
        host = page.locator('.st-key-workspace-ui[data-testid="stElementContainer"]')
        expect(host).to_have_attribute('data-stale', 'true')
        expect(page.locator('[data-testid="stApp"]')).to_have_attribute('data-test-script-state', 'running')

        # Sample throughout the 0.5s delay + 1s native fade, rather than checking
        # the instant before Streamlit's transition has started.
        samples = []
        for _ in range(12):
            samples.append(effective_opacity(page.locator('.bacto-root')))
            page.wait_for_timeout(200)
        assert min(samples) == pytest.approx(1), samples
        assert effective_opacity(page.get_by_text('Native host sentinel', exact=True)) < .5
        assert 'is-busy' not in page.locator('.bacto-root').get_attribute('class')

        page.get_by_role('link', name='Activity', exact=True).click()
        stop = page.locator('.job-card [data-action="cancel-job"]')
        expect(stop).to_be_enabled()
        stop.focus()
        expect(stop).to_be_focused()
        scroll = page.locator('[data-testid="stMain"]')
        scroll.evaluate('element => { element.scrollTop = 180; }')
        before_scroll = scroll.evaluate('element => element.scrollTop')
        if destination := os.environ.get('BACTOLOGIX_UI_PREVIEWS'):
            folder = Path(destination)
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / 'steady-workspace-during-real-refresh.png'), full_page=True)
        release_request(store, request)
        expect(host).to_have_attribute('data-stale', 'false')
        expect(stop).to_be_focused()
        assert scroll.evaluate('element => element.scrollTop') == pytest.approx(before_scroll, abs=1)

        # Foreground mutations still lock controls and cannot double-submit.
        page.get_by_role('link', name='Samples', exact=True).click()
        page.get_by_role('button', name='Add sample', exact=True).first.click()
        sample = tmp_path / 'sample-inputs'
        sample.mkdir()
        page.get_by_label('Sample ID', exact=True).fill('host.fixture')
        page.get_by_label('Input folder', exact=True).fill(str(sample))
        submit = page.get_by_role('button', name='Register sample', exact=True)
        submit.click()
        expect(submit).to_be_disabled()
        mutation = request_started(store, request['id'])
        # An automatic poll can already be pending; the foreground request is
        # deliberately queued until its response arrives.
        if mutation['kind'] == 'refresh':
            release_request(store, mutation)
            mutation = request_started(store, mutation['id'])
        assert mutation['kind'] == 'add_sample'
        expect(submit).to_be_disabled()
        assert store.get('samples', 'host.fixture') is None
        release_request(store, mutation)
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.locator('[data-sample-row="host.fixture"]')).to_be_visible()
        assert store.get('samples', 'host.fixture')['path'] == str(sample)
        assert len([item for item in store.all('samples') if item['id'] == 'host.fixture']) == 1
        assert errors == []
        browser.close()


def test_workspace_opacity_fix_preserves_native_dialog_and_disconnect(polling_host):
    from playwright.sync_api import sync_playwright, expect

    store, _, url, proc = polling_host
    with sync_playwright() as p:
        executable = os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE')
        browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 800})
        page.goto(url)
        expect(page.locator('.bacto-root')).to_be_visible(timeout=30000)
        page.get_by_role('button', name='Open native host dialog', exact=True).click()
        dialog = page.get_by_role('dialog', name='Native host dialog', exact=True)
        expect(dialog).to_be_visible()
        assert effective_opacity(dialog) == pytest.approx(1)
        expect(dialog.get_by_text('Native dialog content remains visible')).to_be_visible()
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        request = request_started(store)
        assert request['kind'] == 'refresh'
        host = page.locator('.st-key-workspace-ui[data-testid="stElementContainer"]')
        expect(host).to_have_attribute('data-stale', 'true')
        assert effective_opacity(page.locator('.bacto-root')) == pytest.approx(1)
        # A real connection loss must leave the guarded rule. No DOM attribute
        # spoofing: terminate this test's server while its callback is pending.
        proc.terminate()
        proc.wait(timeout=10)
        expect(page.locator('[data-testid="stApp"]')).not_to_have_attribute(
            'data-test-connection-state', 'CONNECTED', timeout=15000)
        page.wait_for_timeout(1700)
        assert effective_opacity(page.locator('.bacto-root')) < .5
        browser.close()
