"""Exercise NanoPlot's renderer with synthetic numeric arrays, never sample data."""
from importlib import metadata
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import plotly
import plotly.io as pio
from packaging.requirements import Requirement


versions = {name: metadata.version(name) for name in ('nanoplot', 'plotly', 'kaleido')}
print('Installed rendering packages: ' + json.dumps(versions, sort_keys=True), flush=True)
for package in ('nanoplot', 'multiqc'):
    try:
        dependencies = metadata.requires(package) or []
    except metadata.PackageNotFoundError:
        continue
    for raw in dependencies:
        requirement = Requirement(raw)
        if requirement.name.lower() in ('plotly', 'kaleido') and (not requirement.marker or requirement.marker.evaluate()):
            actual = metadata.version(requirement.name)
            if actual not in requirement.specifier:
                raise RuntimeError(f'{package} requires {requirement}; installed {actual}. '
                                   'Use a reviewed compatible recipe; NanoPlot 1.46.2 and MultiQC 1.32 need separate environments.')

# NanoPlot versions attempt a browser download at import or during export. A
# check must be read-only. This override lasts for this isolated check process;
# the real renderer still discovers/launches the installed browser below.
def _no_browser_download(*args, **kwargs):
    raise RuntimeError('Browser downloads are disabled during checks. Install Chrome/Chromium explicitly, then check again.')
pio.get_chrome = _no_browser_download
from nanoplotter.nanoplotter_main import scatter


with TemporaryDirectory(prefix='bactologix-plot-check-') as folder:
    rng = np.random.default_rng(42)
    scatter(x=pd.Series(rng.uniform(1, 100, 120)), y=pd.Series(rng.uniform(1, 20, 120)),
            legacy={}, names=['X', 'Y'], path=str(Path(folder) / 'test'),
            plots={'dot': True, 'kde': True}, color='#4CB391', colormap='Greens',
            settings={'no_static': False, 'format': ['png'], 'dpi': 100, 'include_js': 'cdn'})
    for name in ('test_dot', 'test_kde'):
        html, png = Path(folder) / (name + '.html'), Path(folder) / (name + '.png')
        if not html.is_file() or 'Plotly.newPlot' not in html.read_text():
            raise RuntimeError('NanoPlot did not generate ' + name + '.html')
        data = png.read_bytes() if png.is_file() else b''
        if (len(data) < 45 or not data.startswith(b'\x89PNG\r\n\x1a\n')
                or data[12:16] != b'IHDR' or not all(struct.unpack('>II', data[16:24]))
                or data[-12:] != b'\x00\x00\x00\x00IEND\xaeB`\x82'):
            raise RuntimeError('NanoPlot did not generate a complete PNG for ' + name + '. '
                               'Check the renderer log and explicitly provision Chrome/Chromium for Kaleido 1; '
                               'an HTML-only result does not pass this check.')
print('PASS: NanoPlot scatter and density HTML/PNG rendering; Plotly ' + plotly.__version__)
