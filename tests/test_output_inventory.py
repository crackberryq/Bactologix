"""Every output format is discoverable without recursive or content scans."""
import json
from pathlib import Path

import pytest

from bactologix import output_inventory as inventory
from bactologix.store import Store
from bactologix.ui_backend import dispatch


@pytest.fixture
def fixture(tmp_path):
    store = Store(tmp_path / 'workspace')
    folder = store.home / 'runs' / 'parent'
    output = folder / 'outputs' / 'sample'
    output.mkdir(parents=True)
    run = {'id': 'parent', 'folder': str(folder), 'output': str(output)}
    store.put('runs', run['id'], run)
    return store, run, output


def archive(run, name='previous', complete=True):
    root = Path(run['folder']) / 'output-history' / name
    root.mkdir(parents=True)
    if complete:
        (root / 'outputs').mkdir()
        (root / 'outputs' / 'tool.binary').write_bytes(b'old output\x00')
        (root / 'manifest.json').write_text(json.dumps({
            'status': 'completed', 'created_at': '2026-11-10T10:00:00Z',
            'reason': 'preserved-before-attempt', 'completed': True}))
    return root


def test_lists_hidden_binary_and_nested_outputs_without_reading_contents(fixture, monkeypatch):
    store, run, output = fixture
    (output / 'tool-cache').mkdir()
    (output / 'tool-cache' / 'intermediate.bin').write_bytes(b'unchanged')
    (output / '.hidden').write_text('also retained')
    (output / 'alignment.bam').write_bytes(b'\x00\xff')
    (output / 'broken-link').symlink_to(output / 'missing')
    (output / 'tool-cache-link').symlink_to(output / 'tool-cache', target_is_directory=True)
    monkeypatch.setattr(Path, 'read_bytes', lambda *a: pytest.fail('Directory browser read output bytes'))
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: pytest.fail('Directory browser read output text'))
    result = dispatch(store, 'inspect_output_files', {'id': run['id']})
    assert result['keep_modal']
    listing = result['output_files']
    assert listing['rows'][0]['name'] == 'tool-cache'
    assert {x['name']: x['kind'] for x in listing['rows']} == {
        'tool-cache': 'folder', '.hidden': 'file', 'alignment.bam': 'file',
        'broken-link': 'link', 'tool-cache-link': 'link'}
    nested = inventory.inspect(store, run['id'], 'tool-cache')
    assert nested['rows'] == [{'name': 'intermediate.bin', 'relative': 'tool-cache/intermediate.bin',
                               'kind': 'file', 'bytes': 9}]
    assert not listing['snapshots'] and not listing['partial']


@pytest.mark.parametrize('relative', ['../', '/etc', 'tool/../../', '..\\', ['bad']])
def test_rejects_paths_outside_registered_output(fixture, relative):
    store, run, _ = fixture
    with pytest.raises(ValueError):
        inventory.inspect(store, run['id'], relative)


def test_links_are_visible_but_never_traversed(fixture, tmp_path):
    store, run, output = fixture
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    (outside / 'secret').write_text('not listed')
    (output / 'linked').symlink_to(outside, target_is_directory=True)
    assert inventory.inspect(store, run['id'])['rows'][0]['kind'] == 'link'
    with pytest.raises(ValueError, match='symbolic links'):
        inventory.inspect(store, run['id'], 'linked')
    with pytest.raises(ValueError, match='symbolic links'):
        inventory.folder(store, run['id'], 'linked')


def test_shared_followups_can_find_parent_history_and_native_tool_siblings(fixture):
    store, run, output = fixture
    saved = archive(run)
    child = {**run, 'id': 'followup', 'folder': str(store.home / 'runs' / 'followup')}
    Path(child['folder']).mkdir()
    store.put('runs', child['id'], child)
    native = output.parent / 'retained'
    native.mkdir()
    (native / 'old-tool-result').write_text('retained native output')
    listing = inventory.inspect(store, child['id'])
    assert [x['id'] for x in listing['snapshots']] == ['parent/previous']
    assert listing['snapshots'][0]['state'] == 'completed'
    assert 'does not identify' in listing['snapshots'][0]['reason']
    assert {x['id'] for x in listing['run_folders']} == {'@run-files/parent', '@run-files/followup'}
    old = inventory.inspect(store, child['id'], snapshot='parent/previous')
    assert old['path'] == str(saved / 'outputs')
    assert old['rows'][0]['name'] == 'tool.binary'
    native_listing = inventory.inspect(store, child['id'], 'outputs/retained', '@run-files/parent')
    assert native_listing['rows'][0]['name'] == 'old-tool-result'


def test_unrelated_run_and_malformed_snapshot_cannot_be_opened(fixture, tmp_path):
    store, run, _ = fixture
    other = {'id': 'other', 'folder': str(tmp_path / 'other'), 'output': str(tmp_path / 'other/out')}
    store.put('runs', other['id'], other)
    archive(other)
    assert not inventory.inspect(store, run['id'])['snapshots']
    for target in ('other/previous', '@run-files/other', 'parent/..', 'parent/../previous', 'parent/\\', ['bad']):
        with pytest.raises(ValueError):
            inventory.root_for(store, run['id'], target)


def test_missing_live_directory_keeps_history_discoverable(fixture):
    store, run, output = fixture
    archive(run)
    output.rmdir()
    listing = inventory.inspect(store, run['id'])
    assert listing['unavailable'] and listing['rows'] == []
    assert listing['snapshots'][0]['available']
    assert inventory.inspect(store, run['id'], snapshot='parent/previous')['rows']


def test_incomplete_history_remains_visible_without_claiming_completion(fixture):
    store, run, _ = fixture
    root = archive(run, 'partial', complete=False)
    (root / 'manifest.json').write_text('not valid json')
    listing = inventory.inspect(store, run['id'])
    entry = listing['snapshots'][0]
    assert entry['id'] == 'parent/partial' and not entry['available']
    assert entry['state'] == 'Manifest unavailable or status not recorded'


def test_pagination_and_scan_limit_are_explicit(fixture, monkeypatch):
    store, run, output = fixture
    for name in ('c', 'a', 'b', 'e', 'd'):
        (output / name).touch()
    monkeypatch.setattr(inventory, 'PAGE_SIZE', 2)
    listing = inventory.inspect(store, run['id'], page=1)
    assert [x['name'] for x in listing['rows']] == ['c', 'd']
    assert listing['pages'] == 3 and listing['observed_entries'] == 5 and not listing['partial']
    monkeypatch.setattr(inventory, 'MAX_ENTRIES', 3)
    bounded = inventory.inspect(store, run['id'])
    assert bounded['observed_entries'] == 3 and bounded['partial']
    assert len(bounded['rows']) == 2


@pytest.mark.parametrize('page', [True, -1, '1', 101, None])
def test_invalid_pages_rejected(fixture, page):
    store, run, _ = fixture
    with pytest.raises(ValueError, match='page'):
        inventory.inspect(store, run['id'], page=page)


def test_open_folder_uses_registered_roots_and_preserves_bytes(fixture, monkeypatch):
    store, run, output = fixture
    saved = archive(run)
    opened = []
    monkeypatch.setattr('bactologix.launcher.open_folder', lambda path: opened.append(path) or str(path))
    result = dispatch(store, 'open_output_folder', {
        'id': run['id'], 'snapshot': 'parent/previous', 'path': '/ignored/untrusted/path'})
    assert opened == [saved / 'outputs'] and result['keep_modal']
    assert (saved / 'outputs/tool.binary').read_bytes() == b'old output\x00'
    assert not store.jobs()
    with pytest.raises(ValueError):
        dispatch(store, 'open_output_folder', {'id': run['id'], 'relative': '../'})
    assert len(opened) == 1
