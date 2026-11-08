"""Manifest review, frozen inputs and v2 capability boundary contracts."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from bactologix import manifest_inputs, run_config
from bactologix.runs import create_run, check_run_files, report_candidates
from bactologix.release_contract import reason
from test_native import workspace, installation


def manifest(tmp_path):
    reads = tmp_path / 'arbitrary reads.fq'
    reads.write_text('@example\nACGT\n+\nIIII\n')
    path = tmp_path / 'inputs.yaml'
    data = {'schema_version': 1, 'samples': [{'sample_id': 'sample', 'libraries': [
        {'library_id': 'hifi', 'platform': 'pacbio', 'chemistry': 'hifi',
         'read_groups': [{'read_group_id': 'lane', 'run_id': 'run', 'reads': reads.name}]}]}]}
    path.write_text(yaml.safe_dump(data))
    return path, reads


def approve(workspace, installation):
    source = Path(installation['source'])
    row = {'tool': 'hybas', 'repository': 'crackberryq/Bactomics_HybAs',
           'commit': installation['version']['commit'], 'tag': 'v10.0.0a1', 'channel': 'prerelease',
           'adapter_interface': 1, 'configuration_schema': 'hybas-v2',
           'entry_points': {'workflow': 'Hybas.smk', 'environment': 'environment.yaml'},
           'runner': {'python': '3.11', 'snakemake': '9.27.1'}, 'tested': True,
           'test_run': 'https://github.com/crackberryq/Bactomics_HybAs/actions/runs/1',
           'files_sha256': {'Hybas.smk': hashlib.sha256((source/'Hybas.smk').read_bytes()).hexdigest(),
                            'environment.yaml': '0'*64}}
    assert reason('hybas', row) is None
    workspace.put('release_approvals', 'hybas:' + row['commit'], row)
    return row


def registered(workspace, tmp_path):
    path, reads = manifest(tmp_path)
    review = manifest_inputs.review(path)
    manifest_inputs.commit(workspace, path, review['token'])
    return workspace.get('samples', 'sample'), path, reads


def test_review_and_atomic_import(workspace, tmp_path):
    path, reads = manifest(tmp_path)
    review = manifest_inputs.review(path)
    assert review['rows'][0]['mode'] == 'pacbio_only'
    assert review['rows'][0]['input_manifest']['libraries'][0]['read_groups'][0]['reads'] == str(reads)
    path.write_text(path.read_text() + '# changed\n')
    with pytest.raises(ValueError, match='changed'):
        manifest_inputs.commit(workspace, path, review['token'])
    assert workspace.all('samples') == []
    fresh = manifest_inputs.review(path)
    manifest_inputs.commit(workspace, path, fresh['token'])
    with pytest.raises(ValueError, match='already registered'):
        manifest_inputs.commit(workspace, path, fresh['token'])
    assert len(workspace.all('samples')) == 1


def test_old_runner_blocked_before_draft(workspace, installation, tmp_path):
    sample, _, _ = registered(workspace, tmp_path)
    with pytest.raises(ValueError, match='v2 adapter'):
        create_run(workspace, 'hybas', sample, 'polish_mode: none')
    assert workspace.all('runs') == []


def test_manifest_frozen_resume_and_drift(workspace, installation, tmp_path):
    approve(workspace, installation)
    sample, original, reads = registered(workspace, tmp_path)
    run = create_run(workspace, 'hybas', sample, 'polish_mode: auto\nthreads: auto')
    frozen = Path(run['frozen_manifest']['path'])
    assert yaml.safe_load(run['config'])['input_manifest'] == str(frozen)
    assert yaml.safe_load(run['config'])['threads'] == 'auto'
    assert len(run['inputs']) == 1 and run['input_layout'] == []
    original.unlink()  # Frozen declarations no longer depend on the editable source manifest.
    check_run_files(run)
    contents = frozen.read_bytes(); frozen.write_text('{}')
    with pytest.raises(ValueError, match='manifest changed'):
        check_run_files(run)
    frozen.write_bytes(contents); reads.write_text('changed')
    with pytest.raises(ValueError, match='Input changed'):
        check_run_files(run)
    assert ('HybAs research report', 'reports/hybas_research/hybas_report.html') in report_candidates(workspace, run)


def test_auto_defaults_only_for_v2_preserve_saved_overrides(workspace, installation):
    old = run_config.defaults(workspace, 'hybas')
    assert yaml.safe_load(old)['polish_mode'] != 'auto'
    approve(workspace, installation)
    text = run_config.defaults(workspace, 'hybas')
    cfg = yaml.safe_load(text)
    assert all(cfg[key] == 'auto' for key in run_config.AUTO_KEYS)
    run_config.validate('hybas', text)
    fields = {r['name']: r for r in run_config.editor('hybas', text)['fields']}
    assert fields['run_busco']['options'] == ['auto', 'true', 'false']
    assert fields['input_manifest']['managed'] if 'input_manifest' in fields else True
    changes = {json.dumps(['run_busco'], separators=(',', ':')): 'false',
               json.dumps(['assembly_mem_mb'], separators=(',', ':')): '2048'}
    changed = run_config.compile_config('hybas', text, changes)
    assert yaml.safe_load(changed)['run_busco'] is False
    assert yaml.safe_load(changed)['assembly_mem_mb'] == 2048
    run_config.validate('hybas', changed)
    workspace.put('configs', 'hybas', 'run_busco: false\npolish_mode: none')
    assert run_config.defaults(workspace, 'hybas') == 'run_busco: false\npolish_mode: none'


def test_changed_registered_reads_block_new_draft(workspace, installation, tmp_path):
    approve(workspace, installation)
    sample, _, reads = registered(workspace, tmp_path)
    reads.write_text('changed')
    with pytest.raises(ValueError, match='changed since registration'):
        create_run(workspace, 'hybas', sample, 'polish_mode: none')
    assert workspace.all('runs') == []
