"""Dependency and concurrent-workspace regressions using disposable files only."""
import json
import os
from pathlib import Path
import threading

import pytest

from bactologix import storage_cleanup as cleanup
from bactologix.storage_guard import activity
from bactologix.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'application-data'))
    # Process inspection has separate coverage; these fixtures exercise storage
    # ownership and workspace locks without depending on the host process list.
    monkeypatch.setattr(cleanup, '_check_processes', lambda paths: None)
    return Store(tmp_path / 'regular')


def saved_run(store, key, **changes):
    folder = store.home / 'runs' / key
    folder.mkdir()
    output = folder / 'output'
    output.mkdir()
    row = {'id': key, 'tool': 'basis', 'folder': str(folder), 'output': str(output),
           'sample': {'id': 'specimen', 'path': str(store.home.parent / 'original-inputs')},
           'inputs': [], 'config': '{}\n', 'label': key, **changes}
    store.put('runs', key, row)
    return row


def execute(store, plan):
    assert not plan['blocked'], plan['blocked']
    jid = store.submit('storage_cleanup', {'plan_id': plan['id'], 'signature': plan['signature']})
    store.update_job(jid, status='running')

    class Context:
        def __init__(self):
            self.store, self.job = store, store.job(jid)

        def check_cancel(self):
            pass

        def write(self, text):
            pass

    try:
        result = cleanup.execute(Context())
    except BaseException:
        store.update_job(jid, status='failed')
        raise
    store.update_job(jid, status='succeeded')
    return result


def test_manifest_sample_deletes_only_explicit_inputs(store, tmp_path):
    manifest_root = tmp_path / 'manifest-folder'
    unrelated = manifest_root / 'raw/illumina/unrelated.fastq.gz'
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b'keep unrelated legacy inputs')
    declared = tmp_path / 'declared.fastq.gz'
    declared.write_bytes(b'declared input')
    sample = {'id': 'manifest-sample', 'path': str(manifest_root),
              'inputs': [{'path': str(declared)}], 'input_manifest': {'libraries': []}}
    store.put('samples', sample['id'], sample)
    plan = cleanup.preview(store, [{'kind': 'sample_files', 'id': sample['id']}])
    assert [row['path'] for row in plan['paths']] == [str(declared)]
    assert execute(store, plan)['status'] == 'completed'
    assert not declared.exists()
    assert unrelated.read_bytes() == b'keep unrelated legacy inputs'
    assert store.get('samples', sample['id']) is None


def test_registered_aware_sample_protects_source_without_child_run(store):
    source = saved_run(store, 'run-source')
    bundle = Path(source['output']) / 'reports/aware'
    bundle.mkdir(parents=True)
    reads = bundle / 'primary.fastq.gz'
    reads.write_bytes(b'derived input')
    store.put('samples', 'derived', {'id': 'derived', 'path': str(bundle),
              'inputs': [{'path': str(reads)}], 'input_manifest': {'libraries': []},
              'aware_provenance': {'bundle_path': str(bundle / 'manifest.json'),
                                   'files': [{'path': str(reads)}]}})
    plan = cleanup.preview(store, [{'kind': 'run', 'id': source['id']}])
    assert any('samples derived' in reason for reason in plan['blocked'])
    assert reads.read_bytes() == b'derived input'
    assert len(store.all('runs')) == 1


def test_followup_cleanup_keeps_parent_shared_outputs(store):
    parent = saved_run(store, 'run-screening')
    report = Path(parent['output']) / 'followup-report.html'
    report.write_text('saved shared report')
    child = saved_run(store, 'run-followup', parent=parent['id'],
                      run_type='followup', output=parent['output'])
    parent_plan = cleanup.preview(store, [{'kind': 'run', 'id': parent['id']}])
    assert any('child run' in reason for reason in parent_plan['blocked'])
    child_plan = cleanup.preview(store, [{'kind': 'run', 'id': child['id']}])
    assert execute(store, child_plan)['status'] == 'completed'
    assert not Path(child['folder']).exists()
    assert report.read_text() == 'saved shared report'
    assert store.get('runs', parent['id']) == parent


def test_selected_hardlinked_inputs_complete_in_one_cleanup(store, tmp_path):
    original = tmp_path / 'one.fastq.gz'
    original.write_bytes(b'shared inode')
    second = tmp_path / 'two.fastq.gz'
    os.link(original, second)
    store.put('samples', 'linked', {'id': 'linked', 'path': str(tmp_path / 'manifest'),
              'inputs': [{'path': str(original)}, {'path': str(second)}]})
    plan = cleanup.preview(store, [{'kind': 'sample_files', 'id': 'linked'}])
    assert plan['logical_bytes'] == len(b'shared inode')
    assert execute(store, plan)['status'] == 'completed'
    assert not original.exists() and not second.exists()


def test_other_workspace_draft_lock_prevents_cleanup(store):
    source = saved_run(store, 'run-old')
    output = Path(source['output']) / 'result.txt'
    output.write_text('keep while counterpart is copying')
    other = Store(store.home.parent / 'dev')
    plan = cleanup.preview(store, [{'kind': 'run', 'id': source['id']}])
    started, release = threading.Event(), threading.Event()
    errors = []

    def draft_copy():
        try:
            with activity(other):
                started.set()
                if not release.wait(10):
                    raise RuntimeError('Test did not release the draft lock')
        except BaseException as exc:
            errors.append(exc)
            started.set()

    thread = threading.Thread(target=draft_copy)
    thread.start()
    try:
        assert started.wait(5)
        assert not errors
        with pytest.raises(ValueError, match='workspace operation is in progress'):
            execute(store, plan)
        assert output.read_text() == 'keep while counterpart is copying'
        assert store.get('runs', source['id']) == source
        assert not store.all('storage_cleanup_receipts')
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors


def test_other_workspace_queued_work_prevents_cleanup(store):
    source = saved_run(store, 'run-old')
    other = Store(store.home.parent / 'dev')
    plan = cleanup.preview(store, [{'kind': 'run', 'id': source['id']}])
    other.submit('smoke', {})
    with pytest.raises(ValueError, match='related workspace has queued/running jobs'):
        execute(store, plan)
    assert Path(source['folder']).is_dir()
    assert store.get('runs', source['id']) == source


def test_variant_manifest_dependency_protects_another_run(store):
    source = saved_run(store, 'run-resources')
    weights = Path(source['output']) / 'saved-model-weights.bin'
    weights.write_bytes(b'resource referenced by a saved-result manifest')
    variant = saved_run(store, 'run-variant', variant={'schema': 1})
    (Path(variant['folder']) / 'variant.json').write_text(json.dumps({
        'saved_result_dependencies': {str(weights): 'recorded-checksum'},
        'saved_result_resources': []}))
    plan = cleanup.preview(store, [{'kind': 'run', 'id': source['id']}])
    assert plan['blocked'], 'A retained variant dependency must prevent resource deletion'
    assert weights.exists()


def test_related_workspace_variant_protects_its_external_dependency(store):
    source = saved_run(store, 'run-resources')
    weights = Path(source['output']) / 'saved-model-weights.bin'
    weights.write_bytes(b'shared saved dependency')
    other = Store(store.home.parent / 'dev')
    variant = saved_run(other, 'run-variant', variant={'schema': 1})
    (Path(variant['folder']) / 'variant.json').write_text(json.dumps({
        'saved_result_dependencies': {str(weights): 'recorded-checksum'}}))

    plan = cleanup.preview(store, [{'kind': 'run', 'id': source['id']}])
    assert plan['blocked'], 'Related workspace variant dependencies must also be checked'
    assert weights.read_bytes() == b'shared saved dependency'


def test_unreadable_retained_variant_dependencies_block_cleanup(store):
    source = saved_run(store, 'run-resources')
    variant = saved_run(store, 'run-variant', variant={'schema': 1})
    (Path(variant['folder']) / 'variant.json').write_text('{incomplete manifest')

    plan = cleanup.preview(store, [{'kind': 'run', 'id': source['id']}])
    assert plan['blocked'], 'Unknown dependencies cannot authorize deletion'
    assert Path(source['folder']).is_dir()


def test_owned_run_hardlinks_preserve_unselected_external_copy(store, tmp_path):
    run = saved_run(store, 'run-hardlinks')
    output = Path(run['output']) / 'result.bin'
    output.write_bytes(b'transferred hard-linked copy')
    internal_alias = Path(run['output']) / 'result-again.bin'
    transferred_copy = tmp_path / 'transferred.bin'
    os.link(output, internal_alias)
    os.link(output, transferred_copy)

    plan = cleanup.preview(store, [{'kind': 'run', 'id': run['id']}])
    assert execute(store, plan)['status'] == 'completed'
    assert not Path(run['folder']).exists()
    assert transferred_copy.read_bytes() == b'transferred hard-linked copy'
