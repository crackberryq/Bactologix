"""Portable isolate evidence crosses the UI bridge and serialized worker."""
from __future__ import annotations

import base64
import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from bactologix.runs import register_sample, tree_digest
from bactologix.store import Store, now


def isolate_fixture(tmp_path):
    """Tiny software records; no biological pipeline is run by these tests."""
    store = Store(tmp_path / 'isolate workspace')
    original = tmp_path / 'original isolate inputs'
    original.mkdir()
    (original / 'original reads.txt').write_text('Unchanged original input fixture.\n')
    sample = register_sample(store, 'isolate-07', str(original), 'illumina_only')
    store.put('settings', 'private-unrelated-fixture', 'NEVER_EXPORT_UNRELATED_SETTING')

    def saved_run(rid, tool, inputs, parent=None):
        folder = store.home / 'runs' / rid
        output = folder / 'outputs' / sample['id']
        output.mkdir(parents=True)
        workflow = folder / 'workflow'
        workflow.mkdir()
        (workflow / 'README.txt').write_text('Software fixture, no executable analysis.\n')
        config = 'threads: 1\nfixture: true\n'
        (folder / 'config.yaml').write_text(config)
        run = {
            'id': rid, 'tool': tool, 'sample': copy.deepcopy(inputs),
            'folder': str(folder), 'output': str(output), 'parent': parent,
            'label': rid + ' <fixture>', 'config': config, 'submitted': config,
            'config_sha256': hashlib.sha256(config.encode()).hexdigest(),
            'workflow_sha256': tree_digest(workflow), 'commit': '1' * 40,
            'installation': 'not-installed-' + tool, 'controller': str(tmp_path / 'controller'),
            'cores': 1, 'memory_mb': 1024, 'dry_run_ok': False,
            'created': now(), 'inputs': [], 'reuse': 'software-fixture',
        }
        (folder / 'bactologix-run.json').write_text(json.dumps(run))
        store.put('runs', rid, run)
        return run

    hybas = saved_run('run-export-hybas', 'hybas', sample)
    primary = Path(hybas['output']) / 'assembly-result.txt'
    primary.write_text('HybAs retained result fixture.\n')
    (Path(hybas['output']) / 'original-link.txt').symlink_to(original / 'original reads.txt')
    derived = {**sample, 'path': hybas['output']}
    basis = saved_run('run-export-basis', 'basis', derived, hybas['id'])
    (Path(basis['output']) / 'screening-result.txt').write_text('BaSIS retained result fixture.\n')
    gene_inputs = {**derived, 'micp_handoff': {'source_run': hybas['id'], 'source_commit': hybas['commit']}}
    genescope = saved_run('run-export-genescope', 'micpscope', gene_inputs)
    (Path(genescope['output']) / 'descriptor-result.txt').write_text('GeneScope retained result fixture.\n')

    unrelated_folder = tmp_path / 'different original input'
    unrelated_folder.mkdir()
    unrelated = saved_run('run-unrelated-same-name', 'hybas', {**sample, 'path': str(unrelated_folder)})
    (Path(unrelated['output']) / 'must-not-export.txt').write_text('NEVER_EXPORT_UNRELATED_ANALYSIS')

    jobs = []
    for run, status in ((hybas, 'failed'), (basis, 'succeeded')):
        jid = store.submit('workflow', {'run_id': run['id'], 'action': 'run'})
        store.update_job(jid, status=status, started=now(), finished=now(),
                         error='Synthetic failed attempt' if status == 'failed' else None)
        store.log_path(jid).write_text('Recorded ' + status + ' attempt; no real pipeline ran.\n')
        jobs.append(jid)
    store.put('run_visibility', basis['id'], {'id': basis['id'], 'archived': True})
    return {'store': store, 'sample': sample, 'original': original,
            'hybas': hybas, 'basis': basis, 'genescope': genescope,
            'unrelated': unrelated, 'jobs': jobs}


def original_bytes(fixture):
    """Track content, not access time, because exporting necessarily reads files."""
    roots = [fixture['original'], *(Path(fixture[t]['folder']) for t in ('hybas', 'basis', 'genescope', 'unrelated'))]
    return {str(path): path.read_bytes() for root in roots for path in root.rglob('*') if path.is_file()}


def tracked_worker(monkeypatch):
    """Keep the real worker lifecycle observable and bounded in acceptance tests."""
    children = []

    def launch(store):
        children.append(subprocess.Popen(
            [sys.executable, '-m', 'bactologix.worker', '--home', str(store.home)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))

    monkeypatch.setattr('bactologix.engine.launch_worker', launch)
    return children


def wait_for_export(store, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.job(job_id)
        if job['status'] not in {'queued', 'running'}:
            assert job['status'] == 'succeeded', store.log_path(job_id).read_text()
            return job
        time.sleep(.05)
    pytest.fail('Isolate export worker did not finish: ' + store.log_path(job_id).read_text())


def finish_workers(children):
    for child in children:
        try:
            _, errors = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            _, errors = child.communicate(timeout=5)
            pytest.fail('Export worker did not release its idle lease: ' + errors)
        assert child.returncode == 0, errors


def assert_portable_export(fixture, receipt):
    """Validate the copied artifact independently of the copy implementation."""
    root = Path(receipt['destination'])
    assert receipt['status'] == 'completed'
    assert receipt['verification'] == 'verified-local'
    assert receipt['cloud_verification'] == 'not-verified'
    assert (root / 'EXPORT-COMPLETE.json').is_file()
    assert (root / 'index.html').is_file()
    completion = json.loads((root / 'EXPORT-COMPLETE.json').read_text())
    assert completion['manifest_sha256'] == hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()
    assert completion['checksums_sha256'] == hashlib.sha256((root / 'SHA256SUMS').read_bytes()).hexdigest()
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, relative = line.split('  ', 1)
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['files']
    for entry in manifest['files']:
        relative = Path(entry['relative'])
        assert not relative.is_absolute() and '..' not in relative.parts
        target = root / relative
        assert target.is_file() and not target.is_symlink()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry['sha256']
        assert target.stat().st_size == entry['bytes']
    assert not any(path.is_symlink() for path in root.rglob('*'))
    for name, content in (
        ('original reads.txt', b'Unchanged original input fixture.\n'),
        ('assembly-result.txt', b'HybAs retained result fixture.\n'),
        ('screening-result.txt', b'BaSIS retained result fixture.\n'),
        ('descriptor-result.txt', b'GeneScope retained result fixture.\n'),
    ):
        copies = list(root.rglob(name))
        assert copies, name
        assert all(path.read_bytes() == content for path in copies)
    # Staged links are recorded as portable aliases rather than dangling symlinks.
    staged = [row for row in manifest.get('aliases', [])
              if Path(row['relative']).name == 'original-link.txt']
    assert staged
    assert all((root / row['target_relative']).read_bytes() == b'Unchanged original input fixture.\n'
               for row in staged)
    assert not list(root.rglob('must-not-export.txt'))
    records = (root / 'records/isolate-inventory.json').read_text()
    assert fixture['unrelated']['id'] not in records
    assert 'NEVER_EXPORT_UNRELATED_SETTING' not in records
    assert {run['id'] for run in json.loads(records)['runs']} == {
        fixture[key]['id'] for key in ('hybas', 'basis', 'genescope')}
    assert (root / 'history/history.pdf').read_bytes().startswith(b'%PDF-')
    assert (root / 'history/history.svg').is_file()
    history = json.loads((root / 'history/history.json').read_text())
    assert any(node['status'] == 'failed' for node in history['nodes'])
    assert any(node['status'] == 'unknown' for node in history['nodes'])
    assert (root / 'history/history.html').is_file()
    for key in ('hybas', 'basis', 'genescope'):
        with ZipFile(root / 'methods' / fixture[key]['id'] / 'reproducibility.zip') as methods:
            assert methods.read('config.yaml').decode() == fixture[key]['config']
            assert methods.read('methods.txt')
            assert methods.read('citations/Bactologix-CITATION.cff')

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                href = dict(attrs).get('href', '')
                parsed = urlsplit(href)
                assert not parsed.scheme and not parsed.netloc, href
                target = (root / unquote(parsed.path)).resolve()
                assert target.is_relative_to(root.resolve()) and target.is_file(), href

    Links().feed((root / 'index.html').read_text())
    return root


def test_export_bridge_queues_verified_copy_and_retains_all_originals(tmp_path, monkeypatch):
    import fcntl
    from bactologix.ui_backend import dispatch
    fixture = isolate_fixture(tmp_path)
    store = fixture['store']
    before = original_bytes(fixture)
    destination = tmp_path / 'removable destination'
    destination.mkdir()
    jobs_before = {job['id'] for job in store.jobs()}
    plan = dispatch(store, 'preview_isolate_export', {
        'id': fixture['sample']['id'], 'destination': str(destination),
        'destination_kind': 'sharepoint_sync'})['isolate_export_plan']
    assert plan['can_export'], plan
    assert not list(destination.iterdir())
    assert {job['id'] for job in store.jobs()} == jobs_before
    assert original_bytes(fixture) == before
    children = tracked_worker(monkeypatch)
    try:
        # Submission may queue work, but a different worker owns the execution lease.
        with (store.home / 'worker.lock').open('a+') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            result = dispatch(store, 'start_isolate_export', {'plan_id': plan['id'], 'token': plan['token']})
            job_id = result['job_id']
            assert result['navigate'] == 'jobs'
            time.sleep(.2)
            assert store.job(job_id)['status'] == 'queued'
            assert not list(destination.iterdir())
        wait_for_export(store, job_id)
        receipt = dispatch(store, 'isolate_export_receipt', {'id': job_id})['isolate_export_receipt']
        assert receipt['destination_kind'] == 'sharepoint_sync'
        assert_portable_export(fixture, receipt)
        assert original_bytes(fixture) == before
        assert not store.all('storage_cleanup_receipts')
    finally:
        finish_workers(children)


def test_open_isolate_folders_resolves_saved_relationships_not_supplied_paths(tmp_path, monkeypatch):
    from bactologix import launcher
    from bactologix.ui_backend import dispatch
    fixture = isolate_fixture(tmp_path)
    store = fixture['store']
    opened = []
    monkeypatch.setattr(launcher, 'open_folder', lambda path: opened.append(str(path)) or str(path))
    dispatch(store, 'open_isolate_folder', {'id': fixture['sample']['id'], 'kind': 'inputs', 'path': '/ignored'})
    dispatch(store, 'open_isolate_folder', {'id': fixture['sample']['id'], 'kind': 'run',
                                          'run_id': fixture['genescope']['id'], 'path': '/ignored'})
    assert opened == [str(fixture['original']), fixture['genescope']['output']]
    with pytest.raises(ValueError, match='linked'):
        dispatch(store, 'open_isolate_folder', {'id': fixture['sample']['id'], 'kind': 'run',
                                              'run_id': fixture['unrelated']['id']})
    assert len(opened) == 2


def test_changed_inputs_require_a_new_preview_before_any_export_is_queued(tmp_path, monkeypatch):
    from bactologix.ui_backend import dispatch
    fixture = isolate_fixture(tmp_path)
    store = fixture['store']
    destination = tmp_path / 'reviewed destination'
    destination.mkdir()
    plan = dispatch(store, 'preview_isolate_export', {
        'id': fixture['sample']['id'], 'destination': str(destination)})['isolate_export_plan']
    assert plan['can_export']
    jobs_before = store.jobs()
    (fixture['original'] / 'new input.txt').write_text('New data added after review.\n')
    changed = original_bytes(fixture)
    launches = []
    monkeypatch.setattr('bactologix.engine.launch_worker', lambda store: launches.append(store.home))
    with pytest.raises(ValueError, match='source files changed'):
        dispatch(store, 'start_isolate_export', {'plan_id': plan['id'], 'token': plan['token']})
    assert not launches
    assert store.jobs() == jobs_before
    assert not list(destination.iterdir())
    assert original_bytes(fixture) == changed


def test_history_downloads_preserve_recorded_failures_and_unknown_execution(tmp_path):
    from bactologix.ui_backend import dispatch
    fixture = isolate_fixture(tmp_path)
    store = fixture['store']
    before = original_bytes(fixture)
    jobs_before = store.jobs()
    formats = {'json': 'application/json', 'svg': 'image/svg+xml',
               'pdf': 'application/pdf', 'html': 'text/html'}
    contents = {}
    for fmt, mime in formats.items():
        result = dispatch(store, 'download_isolate_history', {'id': fixture['sample']['id'], 'format': fmt})
        assert result['keep_modal']
        download = result['download']
        assert download['type'] == mime
        assert download['name'] == 'history.' + fmt
        contents[fmt] = base64.b64decode(download['base64'], validate=True)
    graph = json.loads(contents['json'])
    assert any(node['status'] == 'failed' for node in graph['nodes'])
    assert any(node['status'] == 'unknown' for node in graph['nodes'])
    assert fixture['unrelated']['id'] not in contents['json'].decode()
    assert ET.fromstring(contents['svg']).tag.endswith('svg')
    assert contents['pdf'].startswith(b'%PDF-') and b'%%EOF' in contents['pdf']
    assert b'<html' in contents['html'].lower() and b'isolate-07' in contents['html']
    assert store.jobs() == jobs_before
    assert original_bytes(fixture) == before
