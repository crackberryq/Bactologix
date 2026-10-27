"""Launcher routing, browser handoff and a real packaged server lifecycle."""
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

from bactologix import launcher as l


def test_default_linux_host_is_loopback(monkeypatch):
    monkeypatch.setattr(l, 'is_wsl', lambda: False)
    assert l.default_host() == '127.0.0.1'


@pytest.mark.parametrize('mode,expected', [('mirrored', '127.0.0.1'), ('nat', '172.21.21.143')])
def test_wsl_uses_current_route_address_or_mirrored_loopback(monkeypatch, mode, expected):
    monkeypatch.setattr(l, 'is_wsl', lambda: True)
    monkeypatch.setattr(l.shutil, 'which', lambda name: '/bin/wslinfo' if name == 'wslinfo' else None)
    def output(argv):
        if argv[-1] == '--networking-mode': return mode
        if 'route' in argv: return '[{"dev":"eth0","metric":100}]'
        if 'address' in argv:
            assert argv[-1] == 'eth0'
            return '[{"addr_info":[{"local":"172.21.21.143","scope":"global"}]}]'
        raise AssertionError(argv)
    monkeypatch.setattr(l, 'output', output)
    assert l.default_host() == expected


@pytest.mark.parametrize('addresses,expected', [
    ('127.0.0.1 169.254.1.2 172.21.21.143 172.17.0.1', '172.21.21.143'),
    ('garbage 8.8.8.8 ::1', '127.0.0.1'), ('', '127.0.0.1'),
])
def test_wsl_missing_network_tools_use_bounded_fallback(monkeypatch, addresses, expected):
    monkeypatch.setattr(l, 'is_wsl', lambda: True)
    monkeypatch.setattr(l.shutil, 'which', lambda name: None)
    monkeypatch.setattr(l, 'output', lambda argv: addresses if argv == ['hostname', '-I'] else '')
    assert l.default_host() == expected


@pytest.mark.parametrize('host,url_host', [('127.0.0.1', '127.0.0.1'), ('172.21.21.143', '172.21.21.143'),
                                         ('0.0.0.0', '127.0.0.1'), ('::', '[::1]'), ('[::1]', '[::1]')])
def test_advertised_address_handles_ipv6_and_wildcards(host, url_host):
    assert l.browser_url(host, 8502) == f'http://{url_host}:8502'


@pytest.mark.parametrize('host', ['http://localhost', 'localhost:8501', 'localhost & calc', "a';exit;'", 'fe80::1%eth0&calc'])
def test_host_cannot_inject_browser_commands(host):
    with pytest.raises(ValueError, match='IP address or hostname'):
        l.checked_host(host)


def test_occupied_explicit_port_fails_before_starting_anything():
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        with pytest.raises(ValueError, match='Cannot listen'):
            l.free_port('127.0.0.1', occupied.getsockname()[1])
    with pytest.raises(ValueError, match='between 1 and 65535'):
        l.free_port('127.0.0.1', 0)


def test_auto_port_skips_occupied_default(monkeypatch):
    real = socket.getaddrinfo
    calls = []
    def addresses(host, port, **kwargs):
        calls.append(port)
        if port == 8501:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', busy_port))]
        return real(host, 0, **kwargs)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0)); occupied.listen()
        busy_port = occupied.getsockname()[1]
        monkeypatch.setattr(l.socket, 'getaddrinfo', addresses)
        assert l.free_port('127.0.0.1') == 8502
    assert calls == [8501, 8502]


def test_wsl_browser_opens_windows_default_without_shell(monkeypatch):
    monkeypatch.setattr(l, 'is_wsl', lambda: True)
    monkeypatch.setattr(l.shutil, 'which', lambda name: '/windows/powershell.exe' if name == 'powershell.exe' else None)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(l.subprocess, 'run', run)
    assert l.open_browser('http://172.21.21.143:8502')
    argv, kw = calls[0]
    assert argv[:3] == ['/windows/powershell.exe', '-NoProfile', '-NonInteractive']
    assert argv[-1] == "Start-Process -FilePath 'http://172.21.21.143:8502'"
    assert not kw.get('shell') and kw['timeout'] == 10


def test_wsl_browser_failure_tries_cmd_then_leaves_server_available(monkeypatch):
    monkeypatch.setattr(l, 'is_wsl', lambda: True)
    monkeypatch.setattr(l.shutil, 'which', lambda name: '/windows/' + name if name in {'powershell.exe', 'cmd.exe'} else None)
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1)
    monkeypatch.setattr(l.subprocess, 'run', run)
    assert not l.open_browser('http://172.21.21.143:8502')
    assert [Path(argv[0]).name for argv in calls] == ['powershell.exe', 'cmd.exe']


def test_headless_linux_does_not_start_terminal_browser(monkeypatch):
    monkeypatch.setattr(l, 'is_wsl', lambda: False)
    monkeypatch.setattr(l.platform, 'system', lambda: 'Linux')
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'BROWSER'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(l.subprocess, 'run', lambda *a, **k: pytest.fail('Unexpected browser process'))
    assert not l.open_browser('http://127.0.0.1:8501')


def test_cli_no_browser_preserves_workspace_and_overrides(tmp_path, monkeypatch):
    from bactologix.cli import main
    calls = []
    monkeypatch.setattr(sys, 'argv', ['bactologix', '--home', str(tmp_path), '--host', '127.0.0.1', '--port', '8502', '--no-browser'])
    monkeypatch.setattr(l, 'serve', lambda *args, **kwargs: calls.append((args, kwargs)) or 0)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert calls == [((tmp_path, '127.0.0.1', 8502), {'browser': False})]


def test_failed_server_does_not_open_another_healthy_streamlit(monkeypatch, tmp_path):
    class Failed:
        stdout = io.StringIO('Port is already in use\n')
        def poll(self): return 1
        def wait(self, **kw): return 1
    monkeypatch.setattr(l, 'free_port', lambda *args: 8501)
    monkeypatch.setattr(l.subprocess, 'Popen', lambda *args, **kw: Failed())
    monkeypatch.setattr(l, 'healthy', lambda url: pytest.fail('Failed child has no listening signal'))
    monkeypatch.setattr(l, 'open_browser', lambda url: pytest.fail('Failed server must not open a browser'))
    assert l.serve(tmp_path, '127.0.0.1') == 1


@pytest.mark.parametrize('no_browser', [False, True])
def test_real_console_launcher_ready_handoff_and_shutdown(tmp_path, no_browser):
    executable = Path(sys.executable).with_name('bactologix')
    assert executable.is_file(), 'Install the package so its console command can be exercised'
    receipt = tmp_path / 'browser.json'
    helper = tmp_path / 'browser helper.py'
    helper.write_text('import json, sys\nfrom pathlib import Path\n'
                      'from urllib.request import build_opener, ProxyHandler\n'
                      'url = sys.argv[1]\n'
                      'with build_opener(ProxyHandler({})).open(url + "/_stcore/health", timeout=3) as r:\n'
                      '    Path(' + repr(str(receipt)) + ').write_text(json.dumps({"url": url, "status": r.status}))\n')
    env = dict(os.environ, BROWSER=f'"{sys.executable}" "{helper}" %s')
    # Test local readiness even if the machine has a proxy configured.
    env.update(HTTP_PROXY='http://127.0.0.1:1', HTTPS_PROXY='http://127.0.0.1:1', NO_PROXY='')
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    args = [str(executable), '--home', str(tmp_path / 'workspace'), '--host', '127.0.0.1', '--port', str(port)]
    if no_browser:
        args.append('--no-browser')
    else:
        # Exercise the user's normal command with no flags at all.
        args = [str(executable)]
        env['BACTOLOGIX_HOME'] = str(tmp_path / 'workspace')
    log = tmp_path / 'server.log'
    with log.open('w') as output:
        proc = subprocess.Popen(args, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if proc.poll() is not None: pytest.fail(log.read_text())
                if 'Bactologix is ready:' in log.read_text() and (no_browser or receipt.exists()): break
                time.sleep(.1)
            else:
                pytest.fail('Launcher did not become ready:\n' + log.read_text())
            if not no_browser:
                url = json.loads(receipt.read_text())['url']
                assert url.startswith('http://127.0.0.1:')
                assert 8501 <= int(url.rsplit(':', 1)[1]) <= 8520
            assert l.healthy(url)
            if no_browser:
                assert not receipt.exists()
            else:
                assert json.loads(receipt.read_text()) == {'url': url, 'status': 200}
            proc.send_signal(signal.SIGTERM if no_browser else signal.SIGINT)
            proc.wait(timeout=15)
            assert not l.healthy(url), 'Stopping the launcher must also stop its UI server'
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
