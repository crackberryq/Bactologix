"""Compose a local, unsent results message."""
import base64
from email.message import EmailMessage
from email.policy import SMTP
from html.parser import HTMLParser
from io import BytesIO
import json
import os
from pathlib import Path
import posixpath
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from . import methods, results
from .runs import report_candidates


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {'src', 'href'} and value and not value.startswith(('#', 'data:')):
                url = urlsplit(value)
                path = unquote(url.path)
                if not url.netloc and not url.scheme and path and not path.startswith(('/', '\\')):
                    self.paths.add(path)


def package(store, run):
    root = Path(run['output'])
    pending = {rel for _, rel in report_candidates(store, run) if (root / rel).is_file()}
    if not pending:
        raise ValueError('This run has no native HTML reports to email.')
    allowed = {'.html', '.htm', '.css', '.js', '.png', '.svg', '.jpg', '.jpeg', '.webp',
               '.woff', '.woff2', '.json', '.tsv', '.csv'}
    buffer = BytesIO()
    visited = set()
    total = 0
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            raw = results.read_file(root / name, results.REPORT_LIMIT)
            total += len(raw)
            if total > 128 * 1024**2:
                raise ValueError('The linked report bundle exceeds 128 MiB. Share the report folder separately.')
            archive.writestr(name, raw)
            if Path(name).suffix.lower() in {'.html', '.htm'}:
                links = References()
                links.feed(raw.decode('utf-8', errors='replace'))
                for link in links.paths:
                    rel = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
                    if (not rel.startswith('../') and '\\' not in rel and Path(rel).suffix.lower() in allowed
                            and (root / rel).is_file()):
                        pending.add(rel)
    return buffer.getvalue()


def compose(store, run):
    archive = package(store, run)
    subject = ' '.join(f"{run['label']} — {run['sample']['id']}".splitlines())
    body = methods.render_text(methods.build(store, run))
    filename = run['id'] + '-reports.zip'
    powershell = shutil.which('powershell.exe')
    if powershell and shutil.which('wslpath'):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / filename).write_bytes(archive)
            metadata = directory / 'draft.json'
            metadata.write_text(json.dumps(dict(subject=subject, body=body, filename=filename)), encoding='utf-8')
            location = subprocess.run(['wslpath', '-w', str(metadata)], capture_output=True, text=True)
            if location.returncode == 0:
                env = os.environ.copy()
                env['BACTOLOGIX_DRAFT'] = location.stdout.strip()
                env['WSLENV'] = ':'.join(filter(None, [env.get('WSLENV'), 'BACTOLOGIX_DRAFT']))
                script = """
$ErrorActionPreference='Stop'
$path=$env:BACTOLOGIX_DRAFT
$data=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
$outlook=New-Object -ComObject Outlook.Application
$mail=$outlook.CreateItem(0)
$mail.Subject=$data.subject
$mail.Body=$data.body
$null=$mail.Attachments.Add((Join-Path (Split-Path $path) $data.filename),1)
$mail.Display($false)
"""
                try:
                    result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script],
                                            env=env, capture_output=True, timeout=45)
                    if result.returncode == 0:
                        return {'message': 'Opened your Outlook draft with Methods and the report ZIP.', 'keep_modal': True}
                except (OSError, subprocess.TimeoutExpired):
                    pass
    message = EmailMessage(policy=SMTP)
    message['X-Unsent'] = '1'
    message['Subject'] = subject
    message.set_content(body)
    message.add_attachment(archive, maintype='application', subtype='zip', filename=filename)
    return {'message': 'Open the downloaded email file in Outlook. It contains the Methods and report ZIP.',
            'keep_modal': True, 'download': {'name': run['id'] + '.eml', 'type': 'message/rfc822',
                                            'base64': base64.b64encode(message.as_bytes()).decode('ascii')}}
