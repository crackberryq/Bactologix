"""Export tests copy only temporary fixtures and retain all original inputs."""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import isolate_export as export
from bactologix.engine import Cancelled
from bactologix.store import Store

REAL_GENERATED = export._generated
REAL_INVENTORY = export._inventory


def file(path, data=b'research fixture'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    original = file(tmp_path / 'isolate/raw/reads.fastq', b'@fixture\nACGT\n+\nIIII\n')
    report = file(store.home / 'runs/run-one/outputs/fixture/report.html', b'<h1>Fixture result</h1>')
    destination = tmp_path / 'usb'
    destination.mkdir()
    inventory = {'schema_version': 'test-inventory', 'sample': {'id': 'fixture', 'path': str(original.parents[1])},
                 'runs': [], 'edges': [], 'jobs': [], 'records': [], 'warnings': [], 'missing': [],
                 'roots': [{'source': str(original.parents[1]), 'relative': 'inputs/fixture', 'kind': 'input', 'label': 'Inputs', 'required': True},
                           {'source': str(store.home / 'runs/run-one'), 'relative': 'analyses/run-one', 'kind': 'run', 'label': 'Run', 'required': True}],
                 'signature': 'fixed-record-identity'}
    monkeypatch.setattr(export, '_inventory', lambda *args: copy.deepcopy(inventory))
    monkeypatch.setattr(export, '_generated', lambda *args: iter([('history/history.json', '{"recorded":true}'), ('README.txt', 'Portable evidence fixture')]))
    return SimpleNamespace(store=store, original=original, report=report, destination=destination, inventory=inventory)


def plan(fixture, **kwargs):
    return export.preview(fixture.store, 'fixture', str(fixture.destination), **kwargs)


def run(fixture, preview, check_cancel=lambda: None):
    jid = fixture.store.submit('isolate_export', {'plan_id': preview['id'], 'token': preview['token']})
    fixture.store.update_job(jid, status='running')
    ctx = SimpleNamespace(store=fixture.store, job=fixture.store.job(jid), check_cancel=check_cancel, write=lambda _: None)
    try:
        result = export.execute(ctx)
    except BaseException:
        fixture.store.update_job(jid, status='failed')
        raise
    fixture.store.update_job(jid, status='succeeded')
    return result


def test_complete_copy_has_verified_manifest_relative_index_and_retains_originals(setup):
    initial = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (setup.original, setup.report)}
    preview = plan(setup)
    assert preview['can_export'] and not Path(preview['destination']).exists()
    assert preview['total_bytes'] == sum(len(value[0]) for value in initial.values())
    result = run(setup, preview)
    root = Path(result['destination'])
    assert result['status'] == 'completed' and result['verification'] == 'verified-local'
    assert result['cloud_verification'] == 'not-verified'
    assert result['copied_files'] == preview['file_count'] == 2
    manifest = json.loads((root / 'manifest.json').read_text())
    for row in manifest['files']:
        data = (root / row['relative']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256']
        assert len(data) == row['bytes']
    marker = json.loads((root / 'EXPORT-COMPLETE.json').read_text())
    assert marker['manifest_sha256'] == hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()
    assert marker['checksums_sha256'] == hashlib.sha256((root / 'SHA256SUMS').read_bytes()).hexdigest()
    index = (root / 'index.html').read_text()
    assert 'href="analyses/run-one/outputs/fixture/report.html"' in index
    assert 'href="history/history.json"' in index
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == value for p, value in initial.items())
    assert export.receipt(setup.store, result['id']) == result



def test_native_retained_artifacts_and_failed_process_streams_are_verified_and_preserved(setup):
    native = setup.report.parent / 'work/native-retained/session/events'
    originals = {
        file(native / 'removed/files/iteration_1.bam', b'retained binary\x00\xff'): b'retained binary\x00\xff',
        file(native / 'failed/stdout.bin', b'partial output\n'): b'partial output\n',
        file(native / 'failed/stderr.bin', b'failed tool details\n'): b'failed tool details\n',
        file(native / 'failed/manifest.json', b'{"state":"complete","returncode":1}'): b'{"state":"complete","returncode":1}',
    }
    preview = plan(setup)
    result = run(setup, preview)
    destination = Path(result['destination'])
    manifest = json.loads((destination / 'manifest.json').read_text())
    copied = {row['relative']: row for row in manifest['files']}
    run_root = setup.report.parents[2]
    for source, expected in originals.items():
        relative = 'analyses/run-one/' + source.relative_to(run_root).as_posix()
        assert (destination / relative).read_bytes() == source.read_bytes() == expected
        assert copied[relative]['sha256'] == hashlib.sha256(expected).hexdigest()
    assert result['verification'] == 'verified-local'


def test_staged_input_links_are_copied_once_with_resolvable_aliases(setup):
    first = setup.report.parent / 'reads.fastq'
    second = setup.report.parent / 'nested/another-read-link.fastq'
    second.parent.mkdir()
    first.symlink_to(setup.original)
    second.symlink_to(setup.original)
    preview = plan(setup)
    assert preview['alias_count'] == 2
    assert preview['logical_bytes'] == preview['total_bytes'] + 2 * setup.original.stat().st_size
    result = run(setup, preview)
    root = Path(result['destination'])
    manifest = json.loads((root / 'manifest.json').read_text())
    assert len(manifest['aliases']) == 2
    for row in manifest['aliases']:
        assert row['target_relative'] == 'inputs/fixture/raw/reads.fastq'
        assert (root / row['target_relative']).read_bytes() == setup.original.read_bytes()
        assert not (root / row['relative']).exists()
        assert row['target_relative'] in (root / 'index.html').read_text()
    assert not any(path.is_symlink() for path in root.rglob('*'))


def test_external_explicit_input_is_materialized_but_foreign_link_is_blocked(setup, tmp_path):
    external = file(tmp_path / 'external/annotation.faa')
    linked = setup.report.parent / 'annotation.faa'
    linked.symlink_to(external)
    blocked = plan(setup)
    assert not blocked['can_export'] and any('scope' in x for x in blocked['blocked'])
    setup.inventory['roots'].append({'source': str(external), 'relative': 'external-inputs/annotation.faa', 'kind': 'input', 'label': 'Registered annotation', 'required': True})
    preview = plan(setup)
    assert preview['can_export'] and preview['alias_count'] == 1
    result = run(setup, preview)
    assert (Path(result['destination']) / 'external-inputs/annotation.faa').read_bytes() == external.read_bytes()


def test_missing_history_is_explicit_partial_scope_not_false_complete(setup, tmp_path):
    setup.inventory['missing'].append('Older follow-up output is unavailable.')
    setup.inventory['roots'].append({'source': str(tmp_path / 'deleted/report.html'), 'relative': 'history-files/old-report.html', 'kind': 'report', 'label': 'Old report', 'required': True})
    preview = plan(setup)
    assert preview['can_export'] and preview['scope_status'] == 'partial'
    assert len(preview['missing']) == 2
    result = run(setup, preview)
    assert result['status'] == 'completed' and result['scope_status'] == 'partial'
    manifest = json.loads((Path(result['destination']) / 'manifest.json').read_text())
    assert manifest['scope_status'] == 'partial' and manifest['missing'] == preview['missing']


@pytest.mark.parametrize('change', ['content', 'new-file', 'deleted-file', 'metadata'])
def test_changed_preview_requires_new_review_without_destination_writes(setup, change):
    preview = plan(setup)
    if change == 'content':
        before = setup.original.stat()
        setup.original.write_bytes(b'X' * before.st_size)
        os.utime(setup.original, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == 'new-file':
        file(setup.report.parent / 'new-result.txt')
    elif change == 'deleted-file':
        setup.report.unlink()
    else:
        setup.inventory['signature'] = 'changed-records'
    with pytest.raises(ValueError, match='changed'):
        run(setup, preview)
    assert not Path(preview['destination']).exists()
    record = setup.store.all(export.RECEIPTS)[0]
    assert record['status'] == 'failed' and record['verification'] == 'incomplete'


def test_plan_token_tamper_is_rejected(setup):
    preview = plan(setup)
    saved = setup.store.get(export.PLANS, preview['id'])
    saved['total_bytes'] = 0
    setup.store.put(export.PLANS, preview['id'], saved)
    with pytest.raises(ValueError, match='changed or expired'):
        run(setup, preview)
    assert not Path(preview['destination']).exists()


def test_destination_overlap_existing_file_and_symlink_rejected(setup, tmp_path):
    inside = export.preview(setup.store, 'fixture', str(setup.original.parent))
    assert not inside['can_export'] and any('overlaps' in x for x in inside['blocked'])
    preview = plan(setup)
    file(Path(preview['destination']) / 'keep.txt', b'never overwrite')
    with pytest.raises(ValueError, match='already exists'):
        run(setup, preview)
    assert (Path(preview['destination']) / 'keep.txt').read_bytes() == b'never overwrite'
    alias = tmp_path / 'destination-link'
    alias.symlink_to(setup.destination, target_is_directory=True)
    with pytest.raises(OSError):
        export.preview(setup.store, 'fixture', str(alias))


def test_case_collisions_cycles_special_files_and_traversal_are_blocked(setup):
    file(setup.original.parent / 'READS.fastq')
    assert not plan(setup)['can_export']
    (setup.original.parent / 'READS.fastq').unlink()
    loop = setup.original.parent / 'loop'
    loop.symlink_to(setup.original.parents[1], target_is_directory=True)
    assert any('cycle' in x for x in plan(setup)['blocked'])
    loop.unlink()
    fifo = setup.original.parent / 'pipe'
    os.mkfifo(fifo)
    assert any('Special' in x for x in plan(setup)['blocked'])
    fifo.unlink()
    setup.inventory['roots'][0]['relative'] = '../escape'
    assert not plan(setup)['can_export']


def test_cancelled_partial_copy_has_no_completion_marker_and_no_source_changes(setup, monkeypatch):
    preview = plan(setup)
    copy_file = export._copy
    copied = []
    def stop_after_one(fd, row, check, progress=lambda size: None):
        result = copy_file(fd, row, check, progress)
        copied.append(row['relative'])
        raise Cancelled('Fixture cancellation after one copy')
    monkeypatch.setattr(export, '_copy', stop_after_one)
    with pytest.raises(Cancelled):
        run(setup, preview)
    root = Path(preview['destination'])
    assert root.exists() and (root / copied[0]).is_file()
    assert not (root / 'EXPORT-COMPLETE.json').exists()
    assert setup.original.exists() and setup.report.exists()
    assert setup.store.all(export.RECEIPTS)[0]['status'] == 'cancelled'


def test_verification_detects_destination_corruption(setup, monkeypatch):
    preview = plan(setup)
    verifier = export._verify
    touched = []
    def corrupt_once(fd, relative, digest, size, check=lambda: None):
        if not touched:
            path = Path(preview['destination']) / relative
            path.write_bytes(b'x' * size)
            touched.append(relative)
        return verifier(fd, relative, digest, size, check)
    monkeypatch.setattr(export, '_verify', corrupt_once)
    with pytest.raises(ValueError, match='verification failed'):
        run(setup, preview)
    assert not (Path(preview['destination']) / 'EXPORT-COMPLETE.json').exists()
    assert setup.store.all(export.RECEIPTS)[0]['verification'] == 'incomplete'


def test_changed_earlier_source_is_detected_after_all_copies(setup, monkeypatch):
    preview = plan(setup)
    original = export._generated
    def change_source(*args):
        setup.original.write_bytes(b'changed after copying')
        yield from original(*args)
    monkeypatch.setattr(export, '_generated', change_source)
    with pytest.raises(ValueError, match='changed during export'):
        run(setup, preview)
    assert not (Path(preview['destination']) / 'EXPORT-COMPLETE.json').exists()


def test_low_space_blocks_preview(setup, monkeypatch):
    monkeypatch.setattr(export.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    assert any('insufficient' in x for x in plan(setup)['blocked'])


def test_active_jobs_and_bounded_inventory_block_preview(setup, monkeypatch):
    jid = setup.store.submit('smoke', {})
    assert any('queued/running jobs' in value for value in plan(setup)['blocked'])
    setup.store.update_job(jid, status='succeeded')
    monkeypatch.setattr(export, 'MAX_FILES', 1)
    assert any('bounded scan' in value for value in plan(setup)['blocked'])


@pytest.mark.parametrize('identity', ['matches', 'changed', 'legacy'])
def test_saved_input_hashes_are_compared_and_historical_mismatch_is_partial(setup, identity):
    expected = {'path': str(setup.original), 'size': setup.original.stat().st_size}
    if identity != 'legacy':
        expected['sha256'] = hashlib.sha256(setup.original.read_bytes() if identity == 'matches' else b'different original content').hexdigest()
    setup.inventory['runs'] = [{'id': 'run-one', 'inputs': [expected]}]
    preview = plan(setup)
    assert preview['can_export']
    result = run(setup, preview)
    check = result['input_verification'][0]
    assert check['status'] == {'matches': 'matched', 'changed': 'mismatch', 'legacy': 'not-recorded'}[identity]
    assert check['source_collection'] == 'inputs'
    assert result['scope_status'] == ('complete' if identity == 'matches' else 'partial')
    manifest = json.loads((Path(result['destination']) / 'manifest.json').read_text())
    marker = json.loads((Path(result['destination']) / 'EXPORT-COMPLETE.json').read_text())
    assert manifest['input_verification'] == result['input_verification']
    assert manifest['scope_status'] == marker['scope_status'] == result['scope_status']
    if identity != 'matches':
        assert any('Historical input identity unavailable' in warning for warning in result['warnings'])
        assert result['missing'] == manifest['missing'] == marker['missing']


def test_current_owner_followup_with_changed_prerequisite_is_partial(setup, monkeypatch):
    # Real inventory must retain the follow-up even though it owns the shared output.
    sample = dict(setup.inventory['sample'], mode='illumina_only')
    parent_folder = setup.store.home / 'runs/run-parent'
    output = parent_folder / 'outputs/fixture'
    prerequisite = file(output / 'safety/evidence.json', b'original screening evidence')
    saved_hash = hashlib.sha256(prerequisite.read_bytes()).hexdigest()
    parent = {'id': 'run-parent', 'tool': 'basis', 'sample': sample, 'folder': str(parent_folder),
              'output': str(output), 'config': '{}\n', 'inputs': []}
    followup = {**parent, 'id': 'run-one', 'folder': str(setup.store.home / 'runs/run-one'),
                'parent': parent['id'], 'run_type': 'followup',
                'followup_sources': [{'path': str(prerequisite), 'sha256': saved_hash}]}
    setup.store.put('samples', sample['id'], sample)
    for item in (parent, followup):
        setup.store.put('runs', item['id'], item)
    setup.store.put('followup_output_owner', parent['id'], followup['id'])
    prerequisite.write_bytes(b'changed screening evidence before export review')
    monkeypatch.setattr(export, '_inventory', REAL_INVENTORY)
    preview = plan(setup)
    assert preview['can_export'] and not preview['missing']
    result = run(setup, preview)
    check = next(row for row in result['input_verification'] if row['source_collection'] == 'followup_sources')
    assert check['run_id'] == followup['id'] and check['status'] == 'mismatch'
    assert result['scope_status'] == 'partial'
    assert any('followup_sources' in value for value in result['missing'])
    root = Path(result['destination'])
    assert (root / check['relative']).read_bytes() == prerequisite.read_bytes()
    for name in ('manifest.json', 'EXPORT-COMPLETE.json'):
        document = json.loads((root / name).read_text())
        assert document['scope_status'] == 'partial' and document['missing'] == result['missing']
    assert 'followup_sources' in (root / 'index.html').read_text()


@pytest.mark.parametrize('scope', ['registered', 'run'])
@pytest.mark.parametrize('source_collection', ['sample.inputs', 'sample.micp_handoff.files',
    'sample.aware_provenance.files', 'sample.aware_provenance.bundle', 'sample.manifest_source'])
def test_other_recorded_source_checksums_are_checked_in_their_own_context(setup, scope, source_collection):
    # Conflicting saved identities must remain visible even when run.inputs matches.
    actual_hash = hashlib.sha256(setup.original.read_bytes()).hexdigest()
    expected = {'path': str(setup.original), 'sha256': hashlib.sha256(b'previous source content').hexdigest()}
    setup.inventory['runs'] = [{'id': 'run-one', 'inputs': [{'path': str(setup.original), 'sha256': actual_hash}],
                                'sample': {}}]
    sample = setup.inventory['sample'] if scope == 'registered' else setup.inventory['runs'][0]['sample']
    parts = source_collection.split('.')[1:]
    owner = sample
    for part in parts[:-1]:
        owner = owner.setdefault(part, {})
    if parts[-1] == 'bundle':
        owner.update(bundle_path=expected['path'], bundle_sha256=expected['sha256'])
    else:
        owner[parts[-1]] = [expected] if parts[-1] in {'files', 'inputs'} else expected
    preview = plan(setup)
    pending = [row for row in preview['input_verification'] if row['source_collection'] == source_collection]
    assert len(pending) == 1 and pending[0]['status'] == 'pending'
    result = run(setup, preview)
    ordinary, historical = result['input_verification'], next(row for row in result['input_verification'] if row['source_collection'] == source_collection)
    assert next(row for row in ordinary if row['source_collection'] == 'inputs')['status'] == 'matched'
    assert historical['status'] == 'mismatch'
    assert historical['run_id'] == (None if scope == 'registered' else 'run-one')
    assert result['scope_status'] == 'partial'
    assert any(source_collection in value for value in result['missing'])


def test_destination_directory_replacement_prevents_completion(setup, monkeypatch):
    preview = plan(setup)
    original = export._generated
    moved = setup.destination / 'moved-by-user'
    def replace_destination(*args):
        Path(preview['destination']).rename(moved)
        Path(preview['destination']).mkdir()
        yield from original(*args)
    monkeypatch.setattr(export, '_generated', replace_destination)
    with pytest.raises(ValueError, match='replaced'):
        run(setup, preview)
    assert not (moved / 'EXPORT-COMPLETE.json').exists()
    assert not list(Path(preview['destination']).iterdir())


def test_unavailable_methods_are_partial_in_receipt_index_manifest_and_marker(setup, monkeypatch):
    # Exercise the real Methods failure path when the saved YAML changed before preview.
    folder = setup.store.home / 'runs/run-one'
    config = '{}\n'
    file(folder / 'config.yaml', b'changed: true\n')
    setup.inventory['runs'] = [{'id': 'run-one', 'folder': str(folder), 'config': config,
                                'config_sha256': hashlib.sha256(config.encode()).hexdigest()}]
    monkeypatch.setattr(export, '_generated', REAL_GENERATED)
    monkeypatch.setattr('bactologix.sample_history.bundle_files', lambda *args: {'history.json': b'{}'})
    result = run(setup, plan(setup))
    root = Path(result['destination'])
    assert result['scope_status'] == 'partial' and result['missing']
    for name in ('manifest.json', 'EXPORT-COMPLETE.json'):
        document = json.loads((root / name).read_text())
        assert document['scope_status'] == 'partial'
        assert document['missing'] == result['missing']
    index = (root / 'index.html').read_text()
    assert '<strong>partial</strong>' in index and 'Methods unavailable' in index
    assert 'frozen configuration file changed' in (root / 'methods/run-one/UNAVAILABLE.txt').read_text()


def test_source_changed_during_final_destination_verification_is_incomplete(setup, monkeypatch):
    verifier = export._verify
    changed = []
    def change_during_final_pass(fd, relative, digest, size, check=lambda: None):
        result = verifier(fd, relative, digest, size, check)
        if relative == 'SHA256SUMS' and not changed:
            setup.original.write_bytes(b'changed during destination verification')
            changed.append(True)
        return result
    monkeypatch.setattr(export, '_verify', change_during_final_pass)
    preview = plan(setup)
    with pytest.raises(ValueError, match='changed during verification'):
        run(setup, preview)
    assert not (Path(preview['destination']) / 'EXPORT-COMPLETE.json').exists()


def test_sharepoint_verifies_local_copy_only(setup):
    preview = plan(setup, destination_kind='sharepoint_sync')
    assert any('Cloud upload' in warning for warning in preview['warnings'])
    result = run(setup, preview)
    assert result['verification'] == 'verified-local'
    assert result['cloud_verification'] == 'not-verified'


def test_new_destination_needed_for_retry_and_receipt_not_rewritten(setup):
    preview = plan(setup)
    result = run(setup, preview)
    with pytest.raises(ValueError, match='already used'):
        export.validate_plan(setup.store, preview['id'], preview['token'])
    ctx = SimpleNamespace(store=setup.store, job=setup.store.job(result['id']), check_cancel=lambda: None, write=lambda _: None)
    with pytest.raises(ValueError, match='already has a receipt'):
        export.execute(ctx)
    assert export.receipt(setup.store, result['id']) == result
