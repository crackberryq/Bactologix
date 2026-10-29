"""Pinned-source software integration checks. No native analysis tools are run.

Set BACTOLOGIX_HYBAS_SOURCE and BACTOLOGIX_BASIS_SOURCE to clean checkouts.
Snakemake runs directly in the test Python; Conda deployment is not exercised.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from bactologix.catalog import PIPELINES
from bactologix.followup import create_followup
from bactologix.runs import check_run_files, command, create_run, template, tree_digest
from bactologix.store import Store
from bactologix import databases, maintenance


def native_source(tool):
    value = os.environ.get('BACTOLOGIX_' + tool.upper() + '_SOURCE')
    if not value:
        pytest.skip('Pinned source checkout not supplied')
    source = Path(value).resolve()
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    assert commit == PIPELINES[tool]['commit']
    assert importlib.util.find_spec('snakemake'), 'Install Snakemake for pinned-source tests'
    return source


@pytest.mark.parametrize('tool,key', [
    (tool, spec['id']) for tool in ('hybas', 'basis')
    for spec in databases.CATALOG[tool] if not spec.get('managed')])
def test_database_admin_target_is_sample_free(tmp_path, tool, key):
    source = native_source(tool)
    folder = tmp_path / 'maintenance with spaces'
    folder.mkdir()
    payload = {'tool': tool, 'resource': key, 'destination': str(tmp_path / 'download with spaces'),
               'cores': 2, 'memory_mb': 16000, 'lineage': 'bacteria_odb10'}
    cfg, target, result_path = maintenance.download_config(payload, folder)
    configfile = folder / 'config.yaml'
    configfile.write_text(yaml.safe_dump(cfg))
    result = subprocess.run([sys.executable, '-m', 'snakemake', '--snakefile', str(source / PIPELINES[tool]['snakefile']),
                             '--directory', str(folder), target, '--configfile', str(configfile), '--cores', '2',
                             '--resources', 'mem_mb=16000', 'gpu=0', 'kraken_jobs=1', '--dry-run'],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    assert result.returncode == 0, result.stdout[-12000:]
    assert ('prepare_resource' if tool == 'basis' else 'download_') in result.stdout
    for forbidden in ('sample_setup_ready', 'validate_inputs', 'native_tool', 'unicycler_assembly', 'deepsafe_prepare'):
        assert forbidden not in result.stdout, result.stdout
    assert not Path(payload['destination']).exists()


@pytest.mark.parametrize('tool', ['hybas', 'basis'])
def test_environment_admin_dag_uses_all_native_recipes_without_samples(tmp_path, tool):
    source = native_source(tool)
    rows = maintenance.recipes({'tool': tool, 'source': str(source)})
    assert len(rows) == len(maintenance.RECIPES[tool])
    snakefile = maintenance.environment_snakefile(tmp_path, rows)
    result = subprocess.run([sys.executable, '-m', 'snakemake', '--snakefile', str(snakefile),
                             '--directory', str(tmp_path), '--cores', '1', '--dry-run'],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    assert result.returncode == 0, result.stdout
    for index in range(len(rows)):
        assert 'environment_' + str(index) in result.stdout


def setup_run(tmp_path, tool):
    source = native_source(tool)
    store = Store(tmp_path / 'workspace with spaces')
    store.put('installs', 'native-fixture', {'id': 'native-fixture', 'tool': tool,
        'source': str(source), 'controller': sys.prefix, 'ready': True,
        'version': {'commit': PIPELINES[tool]['commit'], 'ref': 'pinned software test'}})
    store.put('settings', 'active-' + tool, 'native-fixture')
    original = tmp_path / 'software input'
    if tool == 'hybas':
        raw = original / 'raw/illumina'
        raw.mkdir(parents=True)
        for mate in (1, 2):
            (raw / f'synthetic_R{mate}.fastq').write_text('@fixture\nACGT\n+\nIIII\n')
    else:
        annotation = original / 'annotation'
        annotation.mkdir(parents=True)
        for ext, text in {'fna': '>contig1\nATGAAATTTTAA\n', 'faa': '>synthetic_cds\nMKF\n',
                          'gff': '##gff-version 3\ncontig1\tfixture\tCDS\t1\t12\t.\t+\t0\tID=synthetic_cds;locus_tag=synthetic_cds;transl_table=11\n'}.items():
            (annotation / ('synthetic.' + ext)).write_text(text)
    cfg = yaml.safe_load(template(tool))
    if tool == 'hybas':
        # Parsing an input-routing contract, with no reference resources or tools.
        cfg.update(run_kraken=False, kraken_decision_mode='off', run_busco=False,
                   run_prokka=False, run_multiqc=False, polish_mode='none')
    run = create_run(store, tool, {'id': 'synthetic', 'path': str(original), 'mode': 'illumina_only'},
                     yaml.safe_dump(cfg), cores=1, memory_mb=16000)
    return store, run


def snake(store, run, action='dry-run', monkeypatch=None, expected_returncode=0):
    if monkeypatch:
        monkeypatch.setattr('bactologix.engine.find_conda', lambda _: '/test-only/conda')
    argv = command(store, run, action)
    args = argv[argv.index('snakemake') + 1:]
    args.remove('--use-conda')
    idx = args.index('--conda-prefix')
    del args[idx:idx + 2]
    result = subprocess.run([sys.executable, '-m', 'snakemake', *args],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    assert result.returncode == expected_returncode, result.stdout[-15000:]
    return result.stdout


def test_saved_database_revision_fixes_native_missing_busco(tmp_path, monkeypatch):
    from bactologix import database_runs
    from test_databases import busco
    store, initial = setup_run(tmp_path, 'hybas')
    cfg = yaml.safe_load(initial['config'])
    cfg['run_busco'] = True
    parent = create_run(store, 'hybas', initial['sample'], yaml.safe_dump(cfg))
    failed = snake(store, parent, monkeypatch=monkeypatch, expected_returncode=1)
    assert "BUSCO lineage database 'bacteria_odb10' missing at" in failed
    target = busco(tmp_path / 'saved database with spaces/bacteria_odb10')
    databases.save(store, 'hybas', {'busco': str(target)}, databases.panel(store, 'hybas')['revision'])
    plan = database_runs.preview(store, [parent['id']])
    child, = database_runs.apply(store, [parent['id']], {parent['id']: ['busco']}, plan['signature'])
    output = snake(store, child, monkeypatch=monkeypatch)
    assert 'Job stats' in output or 'Job statistics' in output
    assert 'busco' in output
    assert store.get('runs', parent['id'])['config'] == parent['config']
    check_run_files(child)


@pytest.mark.parametrize('tool', ['hybas', 'basis'])
def test_run_editor_settings_reach_native_dry_run(tmp_path, monkeypatch, tool):
    from bactologix.ui_backend import dispatch
    store, initial = setup_run(tmp_path, tool)
    store.put('samples', initial['sample']['id'], initial['sample'])
    store.put('configs', tool, initial['config'])
    panel = dispatch(store, 'run_config_editor', {'tool': tool})['run_config']
    values = ({'run_kraken': False, 'kraken_decision_mode': 'off', 'polish_mode': 'none', 'racon_rounds': '0'}
              if tool == 'hybas' else {'extend': False, 'modules.rgi.enabled': False})
    changes = {f['key']: values[f['name']] for f in panel['fields'] if f['name'] in values}
    result = dispatch(store, 'create_runs', {'tool': tool, 'ids': [initial['sample']['id']], 'cores': 1,
        'memory_mb': 16000, 'config': {'tool': tool, 'text': panel['text'], 'changes': changes}})
    child = store.get('runs', result['open_run'])
    output = snake(store, child, monkeypatch=monkeypatch)
    assert 'Job stats' in output or 'Job statistics' in output
    assert store.get('configs', tool) == initial['config']
    check_run_files(child)


@pytest.mark.parametrize('tool', ['hybas', 'basis'])
def test_pinned_source_accepts_generated_draft(tmp_path, monkeypatch, tool):
    store, run = setup_run(tmp_path, tool)
    before = tree_digest(Path(run['folder']) / 'workflow')
    output = snake(store, run, monkeypatch=monkeypatch)
    assert 'Job stats' in output or 'Job statistics' in output
    check_run_files(run)
    assert tree_digest(Path(run['folder']) / 'workflow') == before
    assert not list(Path(run['output']).glob('safety/evidence/*.json'))


def test_saved_followup_native_parse_and_empty_execution(tmp_path, monkeypatch):
    store, parent = setup_run(tmp_path, 'basis')
    source = native_source('basis')
    # Exercise upstream input validation/reporting with zero findings. This
    # constructs a software fixture, not results from an analysis or safety claim.
    sys.path[:0] = [str(source / 'workflow/lib'), str(source / 'workflow/vendor')]
    from basis.config import default_config
    from safety.setup import validate
    from safety.reporting import render
    isolate = Path(parent['output'])
    safety = isolate / 'safety'
    sample = {'sample_id': 'synthetic', 'isolate_dir': str(isolate), 'safety_dir': str(safety),
              'input_mode': 'external', 'taxon': ''}
    for key, ext in [('assembly', 'fna'), ('proteins', 'faa'), ('gff', 'gff')]:
        sample[key] = str(isolate / 'annotation' / ('synthetic.' + ext))
    validate(sample, None, safety / 'metadata')
    saved = json.loads((safety / 'metadata/sample.json').read_text())
    hashes = saved['input_validation']['input_hashes']
    def write(rel, value):
        path = safety / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + '\n')
        return path
    write('metadata/setup.ready.json', {'schema': 'basis-snake-setup-v1', 'status': 'completed',
          'inputs': hashes, 'resources': [], 'environments': []})
    deep = default_config()['deep']
    deep.update(enabled=False, domain_scan=False, motifs=False, phylogeny=False,
                auto_phylogeny=False, family_placement=False, prosite_profiles=False)
    config = write('metadata/config.json', {'version': '0.4.0a2', 'engine': 'snakemake',
                   'options': {'deep': False}, 'modules': {'minced': {'enabled': True}}, 'deep': deep})
    evidence = write('evidence/minced.json', {'schema_version': 1, 'sample_id': 'synthetic', 'module': 'minced',
                     'execution_status': 'completed', 'input_hashes': hashes, 'findings': [], 'metrics': {}, 'artifacts': []})
    render(safety / 'metadata/sample.json', config, [evidence], safety, view='screening', evidence_root=safety)
    page = (safety / 'report.html').read_text()
    assert 'Results by tool' in page and 'Tools finished' in page
    assert (safety / 'tables/screening-findings.tsv').is_file()
    before = {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
              for p in safety.rglob('*') if p.is_file()}
    parent.update(completed='software fixture', dry_run_ok=True)
    store.put('runs', parent['id'], parent)
    child = create_followup(store, parent['id'], 'deep: {enabled: true}\n', True)
    output = snake(store, child, monkeypatch=monkeypatch)
    assert 'capture_followup_review' in output
    snake(store, child, 'run', monkeypatch)
    assert (safety / 'deepsafe-report/report.html').is_file()
    result = json.loads((safety / 'evidence/deepsafe.json').read_text())
    assert result['commands'] == [] and result['metrics']['selected_candidates'] == 0
    assert 'Nothing to be done' in snake(store, child, monkeypatch=monkeypatch)
    for name, expected in before.items():
        path = Path(name)
        assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == expected
    check_run_files(child)
