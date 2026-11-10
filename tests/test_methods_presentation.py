"""Exported Methods explain evidence and workflow roles without validation claims."""
import base64
import copy
import io
import json
import zipfile

import pytest

from bactologix import methods
from test_methods import run, basis_run
from test_methods_genescope import genescope
from test_native import workspace, installation, sample


@pytest.mark.parametrize('pipeline', ['hybas', 'basis', 'micpscope'])
def test_all_pipeline_exports_are_explicit_experimental_run_descriptions(workspace, run, genescope, monkeypatch, pipeline):
    # The GeneScope fixture shares the managed run folder; restore the exact
    # selected YAML before constructing either of the other pipeline fixtures.
    from pathlib import Path
    Path(run['folder'], 'config.yaml').write_text(run['config'])
    selected = basis_run(run, monkeypatch) if pipeline == 'basis' else genescope if pipeline == 'micpscope' else run
    Path(selected['folder'], 'config.yaml').write_text(selected['config'])
    before = copy.deepcopy(workspace.jobs())
    doc = methods.build(workspace, selected)
    assert doc['paragraphs'][0] == methods.VALIDATION_STATUS
    assert 'scientific validation' in doc['validation_status']
    assert 'has not been established' in doc['validation_status']
    assert doc['flow']['run_id'] == selected['id']
    assert doc['flow']['source_commit'] == selected['commit']
    assert doc['flow']['config_sha256'] == selected['config_sha256']
    assert doc['workflow_steps']
    assert all(row['what'] and row['why'] and row['evidence'] for row in doc['workflow_steps'])
    for format in ('txt', 'md', 'html'):
        result = methods.download(workspace, selected, format)['text']
        assert 'scientific validation' in result and 'has not been established' in result
        assert 'Inputs' in result and 'Outputs' in result
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(methods.download(workspace, selected, 'zip')['base64'])))
    assert {'methods-flow.svg', 'methods-flow.json', 'methods-provenance.json'} <= set(archive.namelist())
    flow = json.loads(archive.read('methods-flow.json'))
    assert flow['run_id'] == selected['id'] and flow['config_sha256'] == selected['config_sha256']
    assert '<svg' in archive.read('methods.html').decode()
    assert 'flow_svg' not in json.loads(archive.read('methods-provenance.json'))
    assert workspace.jobs() == before


def test_legacy_unknown_source_still_explains_missing_controller_version(workspace, run):
    run = dict(run, commit='e' * 40, completed='2026-11-10T09:00:00+00:00')
    job = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(job, status='succeeded')
    doc = methods.build(workspace, run)
    controller = next(row for row in doc['software'] if row['tool'] == 'snakemake')
    assert controller['version'] == 'Not recorded'
    assert 'cannot reconstruct a missing historical version' in doc['text']
    assert not doc['flow']['known_source']
    assert not doc['flow']['coverage']['observed_file_transfers']
    assert 'source-matched workflow description' in doc['text']
