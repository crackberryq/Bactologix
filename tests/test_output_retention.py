import errno
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import output_retention as retention
from bactologix.store import Store


@pytest.fixture
def attempt(tmp_path):
    store = Store(tmp_path / 'workspace')
    folder = store.home / 'runs/run-one'
    output = folder / 'outputs/sample'
    output.mkdir(parents=True)
    run = {'id': 'run-one', 'folder': str(folder), 'output': str(output)}
    ctx = SimpleNamespace(store=store, job={'id': 'job-one'}, write=lambda text: None, check_cancel=lambda: None)
    return ctx, run, output


def history(run, job='job-one'):
    return Path(run['folder']) / 'output-history' / job


def test_retains_every_file_empty_outputs_and_directories_with_verified_independent_bytes(attempt):
    ctx, run, output = attempt
    (output / 'tool-native').mkdir()
    (output / 'tool-native/empty-directory').mkdir()
    (output / 'tool-native/raw.tsv').write_bytes(b'raw native bytes')
    (output / 'tool-native/empty.txt').touch()
    (output / 'tool-native/.hidden-metadata').write_text('recorded')
    result = retention.preserve(ctx, run)
    assert result['status'] == 'completed' and result['file_count'] == 3
    copy = history(run) / 'outputs/tool-native/raw.tsv'
    assert copy.stat().st_ino != (output / 'tool-native/raw.tsv').stat().st_ino
    (output / 'tool-native/raw.tsv').write_bytes(b'replaced in-place')
    assert copy.read_bytes() == b'raw native bytes'
    assert (history(run) / 'outputs/tool-native/empty-directory').is_dir()
    manifest = json.loads((history(run) / 'manifest.json').read_text())
    row = next(r for r in manifest['files'] if r['path'].endswith('raw.tsv'))
    assert row['sha256'] == hashlib.sha256(copy.read_bytes()).hexdigest()
    assert 'producing attempt is not inferred' in manifest['attribution']


def test_followup_preserves_shared_parent_outputs_in_its_own_history(attempt):
    ctx, run, output = attempt
    (output / 'native.log').write_text('parent output')
    child = ctx.store.home / 'runs/run-followup'
    child.mkdir()
    run.update(id='run-followup', folder=str(child), parent='run-one')
    retention.preserve(ctx, run)
    assert (history(run) / 'outputs/native.log').read_text() == 'parent output'


def test_empty_or_absent_outputs_do_not_create_spurious_snapshots(attempt):
    ctx, run, output = attempt
    assert retention.preserve(ctx, run) is None
    output.rmdir()
    assert retention.preserve(ctx, run) is None
    assert not history(run).parent.exists()


@pytest.mark.parametrize('failure', ['disk', 'cancel', 'modified'])
def test_failure_retains_partial_archive_and_never_modifies_live_output(attempt, monkeypatch, failure):
    ctx, run, output = attempt
    source = output / 'result.tsv'
    source.write_text('original')
    original = retention._copy_file
    def broken(row, target, cancel):
        if failure == 'disk':
            target.write_text('partial')
            raise OSError(errno.ENOSPC, 'No space left')
        if failure == 'cancel':
            raise RuntimeError('Cancelled')
        result = original(row, target, cancel)
        source.write_text('external modification')
        return result
    monkeypatch.setattr(retention, '_copy_file', broken)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        retention.preserve(ctx, run)
    assert source.read_text() == ('external modification' if failure == 'modified' else 'original')
    assert (history(run) / 'started.json').exists()
    assert not (history(run) / 'manifest.json').exists()
    assert (history(run) / 'failure.json').exists()


def test_existing_history_cannot_be_overwritten(attempt):
    ctx, run, output = attempt
    (output / 'raw.dat').write_text('original')
    retention.preserve(ctx, run)
    (output / 'raw.dat').write_text('next attempt')
    with pytest.raises(FileExistsError):
        retention.preserve(ctx, run)
    assert (history(run) / 'outputs/raw.dat').read_text() == 'original'
    ctx.job['id'] = 'job-two'
    retention.preserve(ctx, run)
    assert (history(run, 'job-two') / 'outputs/raw.dat').read_text() == 'next attempt'


def test_internal_links_are_materialized_and_external_links_stop_launch(attempt, tmp_path):
    ctx, run, output = attempt
    (output / 'assembly.dat').write_text('generated')
    (output / 'latest.dat').symlink_to(output / 'assembly.dat')
    retention.preserve(ctx, run)
    copy = history(run) / 'outputs/latest.dat'
    assert not copy.is_symlink() and copy.read_text() == 'generated'
    external = tmp_path / 'external.dat'
    external.write_text('outside')
    (output / 'outside.dat').symlink_to(external)
    ctx.job['id'] = 'job-two'
    with pytest.raises(ValueError, match='leaves the analysis folder'):
        retention.preserve(ctx, run)
    assert not history(run, 'job-two').exists()


def test_preservation_failure_stops_worker_before_invocation(attempt, monkeypatch):
    from bactologix import runs
    ctx, run, output = attempt
    run.update(tool='hybas', sample={'id': 'sample'}, commit='a' * 40, workflow_sha256='b' * 64,
               config='threads: 1', completed='earlier success')
    ctx.job['payload'] = {'run_id': run['id'], 'action': 'run'}
    ctx.command = lambda *a, **kw: pytest.fail('Workflow must not start after preservation fails')
    monkeypatch.setattr(runs, 'validate_launch', lambda *a, **kw: run)
    monkeypatch.setattr(runs, 'tree_digest', lambda *a: run['workflow_sha256'])
    monkeypatch.setattr('bactologix.variants.verify', lambda *a: None)
    def unavailable(*a):
        raise OSError(errno.ENOSPC, 'No space for output history')
    monkeypatch.setattr(retention, 'preserve', unavailable)
    with pytest.raises(OSError, match='No space'):
        runs.execute_workflow(ctx)
    assert run['completed'] == 'earlier success'


def test_registered_original_input_links_are_references_not_copies(attempt, tmp_path):
    ctx, run, output = attempt
    original = tmp_path / 'raw.fastq'
    original.write_text('original raw input')
    run['inputs'] = [{'path': str(original), 'sha256': hashlib.sha256(original.read_bytes()).hexdigest()}]
    (output / 'raw').mkdir()
    (output / 'raw/reads.fastq').symlink_to(original)
    (output / 'stage.fasta').write_text('generated assembly fixture')
    result = retention.preserve(ctx, run)
    assert result['file_count'] == 1 and len(result['input_references']) == 1
    assert (history(run) / 'outputs/raw/reads.fastq').is_symlink()
    assert result['input_references'][0]['recorded_identity'] == run['inputs'][0]


def test_only_reviewed_native_retention_can_avoid_full_snapshots(attempt):
    from test_release_contract import review
    from bactologix.release_contract import reason
    ctx, run, output = attempt
    (output / 'result.dat').write_text('previous result')
    contract = review()
    run.update(tool='basis', commit=contract['commit'])
    contract['output_retention'] = {'interface': 1, 'strategy': 'archive-before-replace',
                                    'helper': 'workflow/lib/artifact_retention.py'}
    assert reason('basis', contract) is not None
    contract['files_sha256'][contract['output_retention']['helper']] = 'd' * 64
    assert reason('basis', contract) is None
    ctx.store.put('release_approvals', 'basis:' + contract['commit'], contract)
    assert retention.preserve(ctx, run) is None
    assert not history(run).exists()
    assert (output / 'result.dat').read_text() == 'previous result'


def test_checkpoint_restore_preserves_replaced_products_and_rejects_traversal(tmp_path):
    import runpy
    from bactologix.catalog import DATA
    restore = runpy.run_path(str(DATA / 'restore_variant.py'))
    cache = tmp_path / 'variant-cache/one'
    cache.mkdir(parents=True)
    (cache / 'result.dat').write_text('verified checkpoint')
    target = tmp_path / 'outputs/sample/native'
    target.mkdir(parents=True)
    (target / 'old.dat').write_text('prior generated data')
    row = {'cache': str(cache), 'path': str(target), 'identity': restore['identity'](cache)}
    manifest = tmp_path / 'variant.json'
    value = {'schema': 1, 'parent': 'earlier-run', 'checkpoints': [{'rule': 'saved', 'outputs': [row]}]}
    manifest.write_text(json.dumps(value))
    restore['restore'](manifest, 0)
    assert (target / 'result.dat').read_text() == 'verified checkpoint'
    assert next((tmp_path / 'output-history').glob('*/outputs/sample/native/old.dat')).read_text() == 'prior generated data'
    row['path'] = str(tmp_path / 'outputs/../../outside')
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='parent traversal'):
        restore['restore'](manifest, 0)
