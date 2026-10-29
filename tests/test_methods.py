"""Publication exports follow frozen settings and retained execution evidence."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
import yaml

from bactologix import methods
from bactologix.runs import create_run, tree_digest
from bactologix.ui_backend import dispatch
from test_native import workspace, installation, sample


@pytest.fixture
def run(workspace, installation, sample, monkeypatch):
    monkeypatch.setitem(methods.DEFAULTS['sources'], 'hybas', installation['version']['commit'])
    return create_run(workspace, 'hybas', sample,
        "kraken_decision_mode: 'off'\nrun_kraken: false\nrun_busco: false\nrun_prokka: false\nrun_multiqc: false\npolish_mode: none\ncustom: {retain: 17}\n")


def rewrite(run, cfg):
    result = copy.deepcopy(run)
    result['config'] = yaml.safe_dump(cfg)
    result['config_sha256'] = hashlib.sha256(result['config'].encode()).hexdigest()
    Path(run['folder'], 'config.yaml').write_text(result['config'])
    return result


def test_draft_is_frozen_and_does_not_use_current_defaults(workspace, run):
    before = copy.deepcopy(run)
    workspace.put('configs', 'hybas', 'kraken_decision_mode: aware\nrun_busco: true\n')
    doc = dispatch(workspace, 'inspect_methods', {'id': run['id']})['methods']
    assert doc['state'] == 'Planned methods'
    prose = '\n'.join(doc['paragraphs'])
    assert 'were disabled' in prose and 'compatible planned stages were none' in prose
    assert 'BUSCO completeness' not in prose and 'Genome annotation' not in prose
    assert 'assembly quality assessment with QUAST' in prose
    assert any(p['setting'] == 'custom.retain' and p['value'] == '17' for p in doc['parameters'])
    assert workspace.get('runs', run['id']) == before
    assert not workspace.jobs()


@pytest.mark.parametrize('mode,polish,expected,absent', [
    ('illumina_only', 'tripolish', 'Polypolish', 'Medaka'),
    ('ont_only', 'tripolish', 'Racon (2 rounds), Medaka', 'Polypolish'),
    ('hybrid', 'racon', 'Racon (2 rounds)', 'Medaka'),
    ('hybrid', 'medaka', 'Racon (2 rounds), Medaka', 'Polypolish'),
    ('hybrid', 'none', 'planned stages were none', 'Racon'),
])
def test_read_mode_and_polishing_routes(workspace, run, mode, polish, expected, absent):
    cfg = yaml.safe_load(run['config'])
    cfg.update(polish_mode=polish, run_polypolish=True)
    changed = rewrite(run, cfg)
    changed['sample']['mode'] = mode
    paragraphs = '\n'.join(methods.build(workspace, changed)['paragraphs'])
    assert expected in paragraphs and absent not in paragraphs


def write_hybas_evidence(run):
    root = Path(run['output'], 'metadata')
    root.mkdir()
    (root / 'run_summary.json').write_text(json.dumps({'isolate': run['sample']['id'],
        'executed_stages': [], 'skipped_stages': {'polypolish': 'no_mapped_reads'},
        'final_source_stage': 'unicycler', 'kraken_decision': 'passthrough'}))
    (root / 'tool_versions.tsv').write_text('tool\tversion\nunicycler\t0.5.1\nbusco\t5.8.2\n')
    return root


def test_completion_snapshot_survives_output_changes_and_failed_resume(workspace, run):
    root = write_hybas_evidence(run)
    run['completed'] = '2026-10-27T10:00:00+00:00'
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    workspace.update_job(jid, status='succeeded')
    methods.capture(workspace, run, jid)
    (root / 'tool_versions.tsv').write_text('tool\tversion\nunicycler\t99.9\n')
    doc = methods.build(workspace, run)
    assert doc['state'] == 'Completed workflow' and doc['evidence_origin'] == 'Completion-time snapshot'
    assert 'unicycler 0.5.1' in '\n'.join(doc['paragraphs'])
    assert '99.9' not in doc['text']
    assert not any(x['tool'] == 'busco' for x in doc['software'])
    workspace.update_job(jid, status='failed')
    failed = methods.build(workspace, run)
    assert failed['state'] == 'Incomplete execution'
    assert not failed['evidence'] and 'unicycler 0.5.1' not in failed['text']


def test_dry_run_success_does_not_become_completed_methods(workspace, run):
    write_hybas_evidence(run)
    jid = workspace.submit('workflow', {'run_id': run['id'], 'action': 'dry-run'})
    workspace.update_job(jid, status='succeeded')
    run['dry_run_ok'] = True
    doc = methods.build(workspace, run)
    assert doc['state'] == 'Planned methods' and not doc['evidence']


def test_config_drift_blocks_export_source_drift_removes_inferred_prose(workspace, run):
    Path(run['folder'], 'config.yaml').write_text('changed: true')
    with pytest.raises(ValueError, match='frozen configuration file changed'):
        methods.download(workspace, run, 'zip')
    Path(run['folder'], 'config.yaml').write_text(run['config'])
    Path(run['folder'], 'workflow/Hybas.smk').write_text('changed workflow')
    doc = methods.build(workspace, run)
    assert 'Unicycler' not in '\n'.join(doc['paragraphs'])
    assert any('workflow copy changed' in w for w in doc['warnings'])


def test_unknown_source_never_borrows_current_defaults(workspace, run):
    run['commit'] = 'f' * 40
    doc = methods.build(workspace, run)
    assert all(p['origin'] == 'Saved run' for p in doc['parameters'])
    assert not doc['software']


def test_previous_basis_release_retains_equivalent_methods_defaults(run, monkeypatch):
    saved = basis_run(run, monkeypatch)
    saved['commit'] = '1d2d96b266e98d956254d1a518ff5b437ab7a10a'
    warnings = []
    _, _, known = methods._configuration(saved, warnings)
    assert known and not warnings
    saved['commit'] = 'e' * 40
    _, _, known = methods._configuration(saved, [])
    assert not known


def test_derived_memory_default_and_quoted_boolean(workspace, run):
    cfg = yaml.safe_load(run['config'])
    cfg.update(assembly_mem_mb=6000, run_busco='false')
    run = rewrite(run, cfg)
    doc = methods.build(workspace, run)
    assert next(p for p in doc['parameters'] if p['setting'] == 'spades_memory_gb')['value'] == '5'
    assert 'BUSCO completeness' not in '\n'.join(doc['paragraphs'])


def basis_run(run, monkeypatch, **overrides):
    monkeypatch.setitem(methods.DEFAULTS['sources'], 'basis', run['commit'])
    cfg = {'threads': 3, 'memory_mb': 5000, 'extend': False,
           'modules': {'rgi': {'enabled': False}},
           'sample_module_overrides': {run['sample']['id']: {'mlst': {'enabled': False, 'reason': 'declared scope'}}},
           'deep': {'enabled': False, 'aware': {'enabled': True, 'preset': 'auto-strict'}}}
    cfg.update(overrides)
    result = rewrite(run, cfg)
    result.update(tool='basis', cores=3, memory_mb=5000)
    return result


def test_basis_flags_and_per_sample_exclusions(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch)
    doc = methods.build(workspace, run)
    requested = next(p for p in doc['paragraphs'] if 'requested modules' in p)
    assert 'CARD / RGI' not in requested and 'PathogenFinder2' not in requested and 'MLST' not in requested
    assert 'AMRFinderPlus' in requested
    assert 'enabled with preset auto-strict' in doc['text']
    assert 'DeepSafe follow-up was disabled' in doc['text']
    assert next(p for p in doc['parameters'] if p['setting'] == 'modules.vfdb.threads')['value'] == '3'


def test_basis_success_can_have_incomplete_scope(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch)
    run['completed'] = '2026-10-27T12:00:00+00:00'
    root = Path(run['output'], 'safety')
    root.mkdir()
    (root / 'evidence.json').write_text(json.dumps({'run': {'completeness': 'incomplete'},
        'records': [{'sample_id': run['sample']['id'], 'module': 'amrfinder', 'execution_status': 'completed', 'tool_version': '4.0'},
                    {'sample_id': run['sample']['id'], 'module': 'icescreen', 'execution_status': 'not_assessed', 'reason': 'scope_unavailable'},
                    {'sample_id': 'another', 'module': 'rgi', 'execution_status': 'completed'}]}))
    doc = methods.build(workspace, run)
    assert 'Native report scope: incomplete' in doc['execution']
    assert 'completion of AMRFinderPlus.' in doc['text']
    assert 'ICEScreen' not in next(p for p in doc['paragraphs'] if 'retained module records' in p)
    assert any('different sample identity' in w for w in doc['warnings'])


def test_replaced_followup_cannot_borrow_another_runs_outputs(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch, deep={'enabled': True})
    run.update(run_type='followup', parent='screening-run', completed='2026-10-27T12:00:00+00:00')
    workspace.put('followup_output_owner', run['parent'], 'newer-followup')
    doc = methods.build(workspace, run)
    assert not doc['evidence']
    assert any('no longer owns' in w for w in doc['warnings'])


def test_evidence_links_and_oversize_are_not_followed(workspace, run, monkeypatch, tmp_path):
    root = Path(run['output'], 'metadata')
    root.mkdir()
    secret = tmp_path / 'private.json'
    secret.write_text('{"secret":"DO NOT READ"}')
    (root / 'run_summary.json').symlink_to(secret)
    (root / 'tool_versions.tsv').write_text('x' * 128)
    monkeypatch.setattr(methods, 'LIMIT', 100)
    evidence = methods.collect(run)
    assert not evidence['files'] and len(evidence['warnings']) == 2
    assert 'DO NOT READ' not in json.dumps(evidence)


def test_downloads_escape_html_and_keep_exact_yaml(workspace, run):
    cfg = yaml.safe_load(run['config'])
    cfg['custom'] = '<script>alert(1)</script>'
    run = rewrite(run, cfg)
    workspace.put('runs', run['id'], run)
    response = dispatch(workspace, 'download_methods', {'id': run['id'], 'format': 'zip'})
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(response['download']['base64'])))
    assert archive.read('config.yaml').decode() == run['config']
    assert b'<script>alert' not in archive.read('methods.html')
    assert b'&lt;script&gt;' in archive.read('methods.html')
    assert {'methods.txt', 'methods.md', 'methods.html', 'settings.tsv', 'methods-provenance.json'} <= set(archive.namelist())
    for fmt in ('txt', 'md', 'html'):
        result = methods.download(workspace, run, fmt)
        assert result['name'].endswith('.' + fmt) and 'Methods' in result['text']
    with pytest.raises(ValueError):
        methods.download(workspace, run, '../config.yaml')


def test_followup_inherits_verified_screening_deep_configuration(workspace, run, monkeypatch):
    run = basis_run(run, monkeypatch, deep={'enabled': True})
    run.update(run_type='followup', parent='screening-run')
    path = Path(run['output'], 'safety/metadata/config.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'deep': {'enabled': False, 'aware': {'enabled': False, 'preset': 'auto-loose'}, 'maximum_candidates': 7}}))
    run['followup_sources'] = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
    doc = methods.build(workspace, run)
    assert 'screening tools' in doc['text'].lower()
    assert 'BaSIS AWARE policy was disabled' in doc['text']
    assert not any(r['tool'] == 'rgi' for r in doc['software'])
    assert next(p for p in doc['parameters'] if p['setting'] == 'deep.maximum_candidates')['value'] == '7'


def test_worker_captures_after_success_and_capture_failure_is_nonfatal(workspace, run, monkeypatch):
    from types import SimpleNamespace
    from bactologix.runs import execute_workflow
    monkeypatch.setattr('bactologix.runs.validate_launch', lambda *a, **kw: copy.deepcopy(run))
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/fixture/conda')
    lines = []
    ctx = SimpleNamespace(store=workspace, job={'id': 'job-fixture', 'payload': {'run_id': run['id'], 'action': 'run'}},
                          write=lines.append, command=lambda *a, **kw: None)
    execute_workflow(ctx)
    receipt = workspace.get('methods_evidence', run['id'])
    assert receipt['completed'] == workspace.get('runs', run['id'])['completed']
    assert receipt['config_sha256'] == run['config_sha256']
    def failed_capture(*a):
        raise OSError('metadata unavailable')
    monkeypatch.setattr(methods, 'capture', failed_capture)
    execute_workflow(ctx)
    assert workspace.get('runs', run['id'])['completed']
    assert any('Methods metadata could not be captured' in line for line in lines)


@pytest.mark.parametrize('tool', ['hybas', 'basis'])
def test_methods_with_real_pinned_source(tmp_path, tool):
    from test_adapter_contracts import setup_run
    store, run = setup_run(tmp_path, tool)
    doc = methods.build(store, run)
    assert methods.DEFAULTS['sources'][tool] == run['commit']
    assert not any('No verified Methods template' in w for w in doc['warnings'])
    assert ('Unicycler' if tool == 'hybas' else 'AMRFinderPlus') in '\n'.join(doc['paragraphs'])
    # Check native per-run CPU semantics, not only the presence of a UI field.
    if tool == 'basis':
        settings = {p['setting']: p['value'] for p in doc['parameters']}
        assert settings['modules.vfdb.threads'] == '1'
        assert settings['execution.cores'] == '1'
