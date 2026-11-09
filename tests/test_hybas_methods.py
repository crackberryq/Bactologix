"""HybAs Methods accept metadata only after checking its native identity."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from bactologix import hybas_methods, methods
from bactologix.runs import tree_digest


@pytest.fixture
def run(tmp_path):
    folder = tmp_path / 'run'
    (folder / 'workflow').mkdir(parents=True)
    (folder / 'workflow/Hybas.smk').write_text('# frozen source fixture\n')
    output = folder / 'outputs/isolate'
    (output / 'metadata').mkdir(parents=True)
    config = 'threads: 4\n'
    return {'id': 'run-isolate', 'tool': 'hybas', 'sample': {'id': 'isolate'},
            'folder': str(folder), 'output': str(output), 'config': config,
            'config_sha256': hashlib.sha256(config.encode()).hexdigest(),
            'workflow_sha256': tree_digest(folder / 'workflow'),
            'commit': 'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d'}


def write(run, summary=None, *, versions='tool\tversion\nsnakemake\t9.6.2\nunicycler\t0.5.1\n', provenance=True):
    root = Path(run['output'], 'metadata')
    summary = {'isolate': 'isolate', 'workflow_version': 'HybAs-9.0.3-AWARE',
               'run_mode': 'hybrid', 'executed_stages': ['racon1', 'medaka'],
               'skipped_stages': {'polypolish': 'no_mapped_reads'},
               'final_source_stage': 'medaka'} if summary is None else summary
    (root / 'run_summary.json').write_text(json.dumps(summary))
    if versions is not None:
        (root / 'tool_versions.tsv').write_text(versions)
    if provenance:
        source = Path(run['folder'], 'workflow/Hybas.smk').read_bytes()
        (root / 'workflow_provenance.tsv').write_text('field\tvalue\nconfig_sha256\t' + run['config_sha256'] +
                                                     '\nsource_sha256\t' + hashlib.sha256(source).hexdigest() + '\n')
    return root


def collect(run):
    return hybas_methods.collect(run, methods._read)


def test_verified_source_distinguishes_runtime_from_environment_inventory(run):
    root = write(run)
    result = collect(run)
    assert not result['warnings']
    assert result['versions'] == {'snakemake': '9.6.2', 'unicycler': '0.5.1'}
    assert result['version_scopes']['snakemake'] == 'Recorded workflow runtime version'
    assert result['version_scopes']['unicycler'] == hybas_methods.PACKAGE_SCOPE
    assert {'sample identity', 'configuration hash', 'workflow source hash'} <= set(result['identity']['checks'])
    for item in result['files']:
        path = Path(run['output']) / item['path']
        assert item['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert item['bytes'] == path.stat().st_size
    assert result['decisions']['executed_stages'] == ['racon1', 'medaka']


@pytest.mark.parametrize('sample', [None, '', 'unknown', 'another-isolate', 17])
def test_absent_or_wrong_sample_cannot_supply_versions(run, sample):
    write(run, {'isolate': sample, 'executed_stages': ['medaka']})
    result = collect(run)
    assert result['warnings'] and not result['versions'] and not result['decisions']


def test_missing_summary_does_not_accept_unassociated_version_table(run):
    root = write(run)
    (root / 'run_summary.json').unlink()
    result = collect(run)
    assert not result['versions'] and 'summary is missing' in result['warnings'][0]


@pytest.mark.parametrize('field', ['config_sha256', 'source_sha256'])
def test_native_configuration_and_source_conflicts_block_metadata(run, field):
    root = write(run)
    rows = (root / 'workflow_provenance.tsv').read_text().splitlines()
    (root / 'workflow_provenance.tsv').write_text('\n'.join(field + '\t' + 'f' * 64 if row.startswith(field + '\t') else row for row in rows) + '\n')
    result = collect(run)
    assert not result['versions'] and not result['decisions']
    assert any('does not match' in warning for warning in result['warnings'])


def test_unreviewed_source_keeps_native_versions_with_conservative_scope(run):
    run['commit'] = 'f' * 40
    write(run)
    result = collect(run)
    assert result['versions']['unicycler'] == '0.5.1'
    assert result['versions']['snakemake'] == '9.6.2'
    assert result['version_scopes']['snakemake'] == 'Recorded version metadata; execution not established'


def test_missing_native_baselines_are_not_invented(run):
    write(run, provenance=False)
    result = collect(run)
    assert result['versions']['snakemake'] == '9.6.2'
    assert 'native configuration hash' in result['identity']['missing']
    assert 'native workflow source hash' in result['identity']['missing']
    assert 'runtime' not in result['version_scopes']['snakemake']


def test_changed_source_tree_cannot_promote_engine_version_to_runtime_scope(run):
    write(run)
    Path(run['folder'], 'workflow/changed.txt').write_text('changed source tree')
    result = collect(run)
    assert result['versions']['snakemake'] == '9.6.2'
    assert 'runtime' not in result['version_scopes']['snakemake']


@pytest.mark.parametrize('row', ['unicycler\t0.5.1\nunicycler\t0.6\n',
                               'unicycler\t0.5.1\nUnicycler\t0.6\n',
                               'unicycler\n', 'unicycler\t0.5.1\textra\n'])
def test_ambiguous_or_malformed_version_rows_are_rejected(run, row):
    write(run, {'isolate': 'isolate', 'tool_unicycler_version': '0.5.1'},
          versions='tool\tversion\n' + row)
    result = collect(run)
    assert not result['versions'] and result['warnings']


def test_conflicting_embedded_and_sidecar_versions_are_not_mixed(run):
    write(run, {'isolate': 'isolate', 'tool_unicycler_version': 'different'})
    result = collect(run)
    assert not result['versions'] and not result['decisions']
    assert 'disagree' in result['warnings'][0]


def test_summary_versions_are_available_when_sidecar_was_not_retained(run):
    write(run, {'isolate': 'isolate', 'tool_unicycler_version': '0.5.1', 'tool_medaka_version': 'NA'}, versions=None)
    result = collect(run)
    assert result['versions'] == {'unicycler': '0.5.1', 'medaka': 'Not recorded'}


def test_effective_model_and_assembly_route_remain_distinct_from_request(run):
    write(run, {'isolate': 'isolate', 'medaka_model': 'requested-model',
                'medaka_model_effective': 'recorded-model', 'medaka_model_sha256': 'd' * 64,
                'medaka_model_selection': 'resolved', 'executed_stages': ['medaka'],
                'assembler': 'flye', 'long_read_platform': 'pacbio', 'input_strategy': 'pacbio_only',
                'kraken_selected_target_taxid': '123', 'kraken_selected_target_name': 'Recorded name',
                'kraken_selected_target_rank': 'S', 'kraken_selected_target_source': 'manual'})
    result = collect(run)
    decisions = result['decisions']
    assert decisions['medaka_model_requested'] == 'requested-model'
    assert decisions['medaka_model_effective'] == 'recorded-model'
    assert decisions['medaka_model_sha256'] == 'd' * 64
    assert 'medaka_model' not in decisions
    assert decisions['assembler'] == 'flye' and decisions['long_read_platform'] == 'pacbio'
    assert decisions['kraken_selected_target_taxid'] == '123'


def test_old_model_request_never_becomes_an_effective_model(run):
    write(run, {'isolate': 'isolate', 'medaka_model': 'configured', 'executed_stages': ['medaka']})
    decisions = collect(run)['decisions']
    assert decisions['medaka_model_requested'] == 'configured'
    assert 'medaka_model_effective' not in decisions


@pytest.mark.parametrize('invalid', [
    {'executed_stages': 'medaka'}, {'executed_stages': [False]},
    {'executed_stages': ['medaka', 'medaka']}, {'skipped_stages': ['medaka']},
    {'skipped_stages': {'medaka': None}},
    {'executed_stages': ['medaka'], 'skipped_stages': {'medaka': 'disabled'}},
    {'medaka_model_effective': {'model': 'invalid'}}, {'medaka_model_sha256': 'not-a-hash'},
])
def test_malformed_or_contradictory_stage_metadata_is_rejected(run, invalid):
    write(run, {'isolate': 'isolate', **invalid})
    result = collect(run)
    assert result['warnings'] and not result['versions'] and not result['decisions']


def test_duplicate_json_identity_is_rejected(run):
    root = write(run)
    (root / 'run_summary.json').write_text('{"isolate":"wrong","isolate":"isolate"}')
    result = collect(run)
    assert not result['versions'] and 'Duplicate JSON' in result['warnings'][0]


def test_database_metadata_is_fingerprinted_without_opening_database_paths(run):
    write(run)
    folder = Path(run['output']).parent / 'provenance/databases'
    folder.mkdir(parents=True)
    content = 'database_type\tpath\tsha256\nkraken\t/not/accessed/hash.k2d\t' + 'e' * 64 + '\n'
    (folder / 'database_hashes.tsv').write_text(content)
    before = copy.deepcopy(run)
    result = collect(run)
    assert result['database_hashes'] == [{'database_type': 'kraken', 'path': '/not/accessed/hash.k2d', 'sha256': 'e' * 64}]
    assert any(item['path'] == '../provenance/databases/database_hashes.tsv' for item in result['files'])
    assert run == before


def test_reader_link_and_size_failures_remain_separate(run, monkeypatch, tmp_path):
    root = Path(run['output'], 'metadata')
    private = tmp_path / 'private.json'
    private.write_text('{"private":"not metadata"}')
    (root / 'run_summary.json').symlink_to(private)
    (root / 'tool_versions.tsv').write_text('x' * 128)
    monkeypatch.setattr(methods, 'LIMIT', 100)
    result = collect(run)
    assert not result['files'] and not result['versions']
    assert len(result['warnings']) == 2
    assert 'not metadata' not in json.dumps(result)
