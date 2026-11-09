"""Large result payloads do not suppress or invent GeneScope provenance."""
import hashlib
import json
import os

import pytest

from bactologix import genescope_metadata as gm


def document():
    return {'schema_version': '1.3', 'sample_id': 'sample-a', 'software_version': '0.1.0a14',
            'provenance': {'input_sha256': 'a' * 64, 'evidence_sha256': None,
                           'catalog_sha256': None, 'python_version': '3.12.1',
                           'biopython_version': '1.86', 'created_utc': '2026-11-09T10:00:00Z'}}


def write(root, body=None, raw=None):
    folder = root / 'micpscope'
    folder.mkdir(exist_ok=True)
    path = folder / 'results.json'
    path.write_bytes(raw if raw is not None else json.dumps(body or document()).encode())
    return path


def test_large_payload_is_discarded_but_tail_metadata_and_whole_hash_survive(tmp_path):
    body = {'proteins': [{'description': 'x' * 3000} for _ in range(3000)], **document(),
            'reference_search': {'schema_version': '1.0', 'input_sha256': 'a' * 64,
                                 'tool_version': 'diamond version 2.1.10',
                                 'command': ['not', 'retained'], 'queries': {'p': {'hits': []}},
                                 'database': {'id': 'app', 'name': 'Application', 'version': 'v1',
                                              'checksums_sha256': 'b' * 64,
                                              'manifest_sha256': 'c' * 64}},
            'bactologix': {'run_id': 'run-a', 'job_id': 'job-a'}}
    path = write(tmp_path, body)
    projected, identity = gm.read(tmp_path)
    assert path.stat().st_size > 8 * 1024 * 1024
    assert projected['provenance'] == document()['provenance']
    assert projected['bactologix'] == body['bactologix']
    assert projected['reference_search']['tool_version'] == 'diamond version 2.1.10'
    assert projected['reference_search']['database']['checksums_sha256'] == 'b' * 64
    assert 'proteins' not in projected
    assert 'command' not in projected['reference_search']
    assert 'queries' not in projected['reference_search']
    assert identity == {'path': 'micpscope/results.json',
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'bytes': path.stat().st_size}


@pytest.mark.parametrize('suffix', [b' garbage', b' {}', b'\x00', b','])
def test_trailing_data_is_rejected(tmp_path, suffix):
    write(tmp_path, raw=json.dumps(document()).encode() + suffix)
    with pytest.raises(ValueError):
        gm.read(tmp_path)


@pytest.mark.parametrize('raw', [b'{', b'[]', b'null', b'{"x": NaN}', b'{"x":"\xff"}',
                               b'{"x": [1,]}', b'{"x": "unterminated}'])
def test_malformed_or_nonobject_documents_are_rejected(tmp_path, raw):
    write(tmp_path, raw=raw)
    with pytest.raises(ValueError):
        gm.read(tmp_path)


@pytest.mark.parametrize('prefix', [
    '"sample_id":"other",',
    '"sample_\\u0069d":"other",',
    '"bulk":{"id":1,"id":2},',
    '"bulk":[{"id":1,"id":2}],',
])
def test_duplicate_keys_even_in_discarded_payload_are_rejected(tmp_path, prefix):
    raw = ('{' + prefix + json.dumps(document())[1:]).encode()
    write(tmp_path, raw=raw)
    with pytest.raises(ValueError, match='Duplicate key'):
        gm.read(tmp_path)


@pytest.mark.parametrize('field,value', [
    ('sample_id', []), ('sample_id', None), ('sample_id', 7), ('sample_id', ''),
    ('software_version', {}), ('provenance', []), ('reference_search', 'bad'),
    ('bactologix', False), ('schema_version', '1.2'), ('schema_version', 1.3),
])
def test_metadata_types_and_schema_are_checked(tmp_path, field, value):
    body = document()
    body[field] = value
    write(tmp_path, body)
    with pytest.raises(ValueError):
        gm.read(tmp_path)


@pytest.mark.parametrize('value', [True, [], {}, 'incorrect-hash', None])
def test_input_identity_is_a_sha256_string(tmp_path, value):
    body = document()
    body['provenance']['input_sha256'] = value
    write(tmp_path, body)
    with pytest.raises(ValueError):
        gm.read(tmp_path)


def test_dotted_keys_cannot_impersonate_nested_metadata(tmp_path):
    body = document()
    body['provenance.biopython_version'] = 'invented'
    write(tmp_path, body)
    projected, _ = gm.read(tmp_path)
    assert projected['provenance']['biopython_version'] == '1.86'
    assert 'provenance.biopython_version' not in projected


@pytest.mark.parametrize('field', ['schema_version', 'sample_id', 'software_version', 'provenance'])
def test_required_metadata_is_not_inferred(tmp_path, field):
    body = document()
    del body[field]
    write(tmp_path, body)
    with pytest.raises(ValueError):
        gm.read(tmp_path)


def test_all_fields_checked_by_native_identity_validation_are_preserved(tmp_path):
    body = document()
    identity = dict(run_id='run-a', job_id='job-a', sample_id='sample-a', commit='d' * 40,
                    config_sha256='b' * 64, workflow_sha256='c' * 64)
    body['bactologix'] = identity
    body['provenance'].update(bactologix=identity, source_commit='d' * 40,
                              workflow_commit='d' * 40, config_sha256='b' * 64,
                              workflow_sha256='c' * 64)
    for container in (body, body['provenance']):
        container.update(run_id='run-a', job_id='job-a', bactologix_run_id='run-a',
                         bactologix_job_id='job-a')
    write(tmp_path, body)
    projected, _ = gm.read(tmp_path)
    assert projected == body


def test_nested_identity_duplicates_are_rejected(tmp_path):
    raw = json.dumps(document()).replace('"input_sha256":', '"input_sha256": "' + 'b' * 64 + '", "input_sha256":')
    write(tmp_path, raw=raw.encode())
    with pytest.raises(ValueError, match='Duplicate key'):
        gm.read(tmp_path)


def test_hash_includes_allowed_trailing_whitespace(tmp_path):
    raw = json.dumps(document()).encode() + b' \n\t\r\n'
    write(tmp_path, raw=raw)
    _, identity = gm.read(tmp_path)
    assert identity['sha256'] == hashlib.sha256(raw).hexdigest()
    assert identity['bytes'] == len(raw)


@pytest.mark.parametrize('where', ['file', 'folder', 'root'])
def test_linked_paths_are_rejected(tmp_path, where):
    real = tmp_path / 'real'
    real.mkdir()
    path = write(real)
    if where == 'file':
        path.rename(path.with_name('target.json'))
        path.symlink_to('target.json')
    elif where == 'folder':
        path.parent.rename(real / 'target')
        (real / 'micpscope').symlink_to('target', target_is_directory=True)
    else:
        alias = tmp_path / 'alias'
        alias.symlink_to(real, target_is_directory=True)
        real = alias
    with pytest.raises((ValueError, OSError)):
        gm.read(real)


def test_missing_file_returns_none_and_nonregular_file_is_rejected(tmp_path):
    assert gm.read(tmp_path) is None
    folder = tmp_path / 'micpscope'
    folder.mkdir()
    assert gm.read(tmp_path) is None
    (folder / 'results.json').mkdir()
    with pytest.raises(ValueError, match='regular file'):
        gm.read(tmp_path)


def test_fifo_is_rejected_without_waiting_for_writer(tmp_path):
    (tmp_path / 'micpscope').mkdir()
    os.mkfifo(tmp_path / 'micpscope/results.json')
    with pytest.raises(ValueError, match='regular file'):
        gm.read(tmp_path)


def test_parent_traversal_path_is_rejected(tmp_path):
    write(tmp_path)
    with pytest.raises(ValueError, match='path'):
        gm.read(tmp_path / 'other/..')


@pytest.mark.parametrize('limit,value,match', [
    ('MAX_FILE_BYTES', 16, 'file size'),
    ('MAX_METADATA_BYTES', 16, 'retained metadata'),
    ('MAX_TOKEN_BYTES', 16, 'scalar token'),
    ('MAX_KEY_BYTES', 16, 'object key'),
    ('MAX_DEPTH', 1, 'nesting'),
])
def test_resource_limits_fail_closed(tmp_path, monkeypatch, limit, value, match):
    write(tmp_path)
    monkeypatch.setattr(gm, limit, value)
    with pytest.raises(ValueError, match=match):
        gm.read(tmp_path)


def test_discarded_huge_scalar_is_bounded_before_json_parser(tmp_path, monkeypatch):
    write(tmp_path, raw=b'{"bulk":"' + b'x' * 400 + b'"}')
    monkeypatch.setattr(gm, 'MAX_TOKEN_BYTES', 100)
    monkeypatch.setattr(gm, 'CHUNK_BYTES', 32)
    with pytest.raises(ValueError, match='scalar token'):
        gm.read(tmp_path)


def test_same_size_modification_during_read_is_rejected(tmp_path, monkeypatch):
    path = write(tmp_path)
    original = gm._project

    def changed(reader):
        projected = original(reader)
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
        return projected

    monkeypatch.setattr(gm, '_project', changed)
    with pytest.raises(ValueError, match='changed while being read'):
        gm.read(tmp_path)


def test_replacement_during_read_is_rejected(tmp_path, monkeypatch):
    path = write(tmp_path)
    original = gm._project

    def replaced(reader):
        projected = original(reader)
        replacement = path.with_suffix('.new')
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        return projected

    monkeypatch.setattr(gm, '_project', replaced)
    with pytest.raises(ValueError, match='changed while being read'):
        gm.read(tmp_path)


def test_parent_replacement_during_read_is_rejected(tmp_path, monkeypatch):
    path = write(tmp_path)
    original = gm._project

    def replaced(reader):
        projected = original(reader)
        path.parent.rename(tmp_path / 'previous')
        write(tmp_path)
        return projected

    monkeypatch.setattr(gm, '_project', replaced)
    with pytest.raises(ValueError, match='changed while being read'):
        gm.read(tmp_path)
