"""Read-only probe behavior; stubs here do not validate native rendering."""
import base64
from importlib import metadata
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from bactologix.catalog import DATA


def probe_stubs(monkeypatch, *, kaleido='1.0.0', complete=True):
    versions = {'nanoplot': '1.46.2', 'plotly': '6.1.2', 'kaleido': kaleido}
    monkeypatch.setattr(metadata, 'version', versions.__getitem__)
    monkeypatch.setattr(metadata, 'requires', lambda name: ['kaleido>=1.0.0'] if name == 'nanoplot' else [])
    plotly, pio, renderer = ModuleType('plotly'), ModuleType('plotly.io'), ModuleType('nanoplotter.nanoplotter_main')
    plotly.__version__, plotly.io = versions['plotly'], pio
    def download(*args, **kwargs):
        pytest.fail('A read-only probe attempted a browser download')
    pio.get_chrome = download
    calls = []
    def scatter(**kwargs):
        # NanoPlot may request this during export, after its module was imported.
        with pytest.raises(RuntimeError, match='downloads are disabled'):
            pio.get_chrome()
        calls.append(kwargs)
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPuoAAAAASUVORK5CYII=')
        for suffix in ('_dot', '_kde'):
            Path(kwargs['path'] + suffix + '.html').write_text('<script>Plotly.newPlot()</script>')
            Path(kwargs['path'] + suffix + '.png').write_bytes(png if complete else png[:8])
    renderer.scatter = scatter
    for name, value in {
        'plotly': plotly, 'plotly.io': pio, 'nanoplotter': ModuleType('nanoplotter'),
        'nanoplotter.nanoplotter_main': renderer,
        'numpy': SimpleNamespace(random=SimpleNamespace(default_rng=lambda _: SimpleNamespace(uniform=lambda *args: []))),
        'pandas': SimpleNamespace(Series=lambda value: value),
    }.items():
        monkeypatch.setitem(sys.modules, name, value)
    return calls


def test_probe_blocks_browser_download_even_during_export(monkeypatch):
    calls = probe_stubs(monkeypatch)
    runpy.run_path(str(DATA / 'plot_probe.py'))
    assert len(calls) == 1


def test_probe_rejects_incompatible_actual_package_metadata(monkeypatch):
    calls = probe_stubs(monkeypatch, kaleido='0.2.1')
    with pytest.raises(RuntimeError, match='nanoplot requires kaleido>=1.0.0'):
        runpy.run_path(str(DATA / 'plot_probe.py'))
    assert calls == []


def test_probe_rejects_truncated_png_instead_of_reporting_success(monkeypatch):
    probe_stubs(monkeypatch, complete=False)
    with pytest.raises(RuntimeError, match='complete PNG'):
        runpy.run_path(str(DATA / 'plot_probe.py'))
