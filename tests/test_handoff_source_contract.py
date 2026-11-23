"""Registration checks for the preferred HybAs Dev handoff source.

Native hooks are never executed here. Optional source checks bind the application's
capability decisions to the exact Git checkout used by the adapter CI jobs.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from bactologix import dev_catalog, hybas_methods, maintenance, medaka_review
from bactologix import methods, methods_narrative, methods_parameters
from bactologix import release_contract, setup_advice_source
from bactologix.runs import tree_digest
from bactologix.store import Store


ROOT = Path(__file__).resolve().parents[1]
BROWSER = {'interface': 1, 'environment': 'qc', 'policy': 'install_or_repair_when_missing'}
MODEL = {'interface': 2, 'capability': 'app/medaka-model-review.json',
         'helper': 'workflow/lib/medaka_stage.py', 'preflight_rule': 'medaka_model_preflight'}
HANDOFF_FILES = {
    'Hybas.smk', 'environment.yaml', 'config.example.yaml',
    'app/medaka-model-review.json', 'workflow/lib/medaka_stage.py',
    'workflow/lib/settings.py', 'workflow/lib/library_inputs.py',
    'workflow/lib/workflow_support.py', 'workflow/lib/hybas_report.py',
    'workflow/lib/rendering_runtime.py', 'workflow/scripts/check_database.py',
    'workflow/envs/medaka.yml', 'workflow/envs/medaka.post-deploy.sh',
    'workflow/envs/qc.yml', 'workflow/envs/qc.post-deploy.sh',
    'workflow/envs/multiqc.yml', 'workflow/envs/multiqc.post-deploy.sh',
}


@pytest.fixture
def dev_store(tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
    return Store(tmp_path / 'workspace')


@pytest.fixture
def preferred(dev_store):
    document = dev_catalog.document(dev_store)
    rows = [row for row in document['builds'] if row['tool'] == 'hybas' and row.get('preferred')]
    assert len(rows) == 1, 'Exactly one reviewed HybAs Dev source must be preferred'
    return rows[0]


@pytest.fixture
def native_source():
    source = os.environ.get('BACTOLOGIX_HYBAS_V2_SOURCE')
    if not source and os.environ.get('BACTOLOGIX_DEV_TOOL') in {None, 'hybas'}:
        source = os.environ.get('BACTOLOGIX_DEV_SOURCE')
    if not source:
        pytest.skip('Enabled by the HybAs v2 or HybAs development source-contract CI job')
    return Path(source).resolve(strict=True)


def test_development_registry_copies_match():
    assert (ROOT / 'compatibility/development-v1.json').read_bytes() == dev_catalog.BUNDLED.read_bytes()


def test_preferred_handoff_source_has_complete_reviewed_contract(preferred):
    assert preferred['tag'] == 'v10.0.0a12'
    assert preferred['database_downloads'] == 1
    assert preferred['adapter_interface'] == 2, 'Older apps must not approve a source requiring browser and model-review controls'
    assert release_contract.reason('hybas', preferred) is None
    assert preferred['configuration_schema'] == 'hybas-v2'
    assert preferred['medaka_model_review'] == MODEL
    assert preferred['browser_preparation'] == BROWSER
    assert type(preferred['browser_preparation']['interface']) is int
    assert type(preferred['medaka_model_review']['interface']) is int
    assert HANDOFF_FILES <= preferred['files_sha256'].keys()
    assert re.fullmatch('[0-9a-f]{40}', preferred['tested_source_commit'])
    assert re.fullmatch('[0-9a-f]{40}', preferred['tested_source_tree'])
    # The new review must keep the established output-retention and swap contracts.
    assert preferred['output_retention'] == {
        'interface': 1, 'strategy': 'archive-before-replace', 'helper': 'workflow/lib/artifact_retention.py'}
    assert preferred['compute_memory'] == {
        'interface': 1, 'swap_policy': 'physical_ram_plus_explicit_swap_v1', 'helper': 'workflow/lib/settings.py'}


def test_preferred_source_is_registered_for_methods_and_adviser(preferred):
    commit = preferred['commit']
    assert commit in methods.DEFAULTS['saved_settings_sources']['hybas']
    assert commit not in methods.DEFAULTS.get('equivalent_sources', {}).get('hybas', [])
    assert commit in hybas_methods.RUNTIME_SOURCES
    assert commit in methods_parameters.HYBAS_DEV
    assert commit in methods_narrative.EXPERIMENTAL_HYBAS
    assert setup_advice_source.HYBAS_PROFILES[commit] == 'hybas10'


@pytest.fixture
def saved_run(dev_store, preferred):
    folder = dev_store.home / 'runs/run-handoff-source'
    workflow = folder / 'workflow'
    workflow.mkdir(parents=True)
    (workflow / 'Hybas.smk').write_text('# Inert publication metadata fixture; no native tools.\n')
    output = folder / 'outputs/isolate'
    output.mkdir(parents=True)
    config = 'threads: 3\nmemory_mb: 5120\n'
    (folder / 'config.yaml').write_text(config)
    return {'id': 'run-handoff-source', 'tool': 'hybas', 'commit': preferred['commit'],
            'sample': {'id': 'isolate', 'mode': 'hybrid'}, 'folder': str(folder),
            'output': str(output), 'config': config,
            'config_sha256': hashlib.sha256(config.encode()).hexdigest(),
            'workflow_sha256': tree_digest(workflow), 'cores': 3, 'memory_mb': 5120, 'inputs': []}


def test_preferred_methods_keep_experimental_topology_and_saved_settings(dev_store, saved_run):
    warnings = []
    saved, effective, known = methods._configuration(saved_run, warnings)
    assert known and saved == effective == {'threads': 3, 'memory_mb': 5120}
    document = methods.build(dev_store, saved_run)
    assert document['flow']['known_source']
    assert any('Experimental library topology' in note for note in document['flow']['warnings'])
    assert all(edge['kind'] == 'unresolved' for edge in document['flow']['edges']), \
        'A missing controller trace must not produce the regular HybAs 9 assembly topology'
    assert any('experimental HybAs library workflow' in paragraph for paragraph in document['narrative']['paragraphs'])
    assert not any('No source-matched workflow description' in note for note in document['warnings'])


def test_preferred_methods_accept_recorded_runtime_and_model_review(saved_run):
    metadata = Path(saved_run['output']) / 'metadata'
    metadata.mkdir()
    (metadata / 'run_summary.json').write_text(json.dumps({
        'isolate': 'isolate', 'executed_stages': [], 'skipped_stages': {},
        'medaka_model_review_status': 'unsupported_basecaller',
        'medaka_model_review_acknowledged': True,
        'medaka_model_review_reason': 'Synthetic engineering comparison',
        'medaka_model_reviewed_model': 'fixture-reviewed-model'}))
    (metadata / 'tool_versions.tsv').write_text('tool\tversion\nsnakemake\t9.27.1\nmedaka\t2.2.0\n')
    digest = hashlib.sha256(Path(saved_run['folder'], 'workflow/Hybas.smk').read_bytes()).hexdigest()
    (metadata / 'workflow_provenance.tsv').write_text(
        'field\tvalue\nconfig_sha256\t' + saved_run['config_sha256'] + '\nsource_sha256\t' + digest + '\n')
    evidence = methods.collect(saved_run)
    assert evidence['version_scopes']['snakemake'] == 'Recorded workflow runtime version'
    assert evidence['version_scopes']['medaka'] == hybas_methods.PACKAGE_SCOPE
    assert evidence['decisions']['medaka_model_review_acknowledged'] is True
    assert evidence['decisions']['medaka_model_reviewed_model'] == 'fixture-reviewed-model'


def test_actual_source_preflight_and_explicit_browser_permissions(dev_store, preferred, native_source):
    head = subprocess.check_output(['git', '-C', str(native_source), 'rev-parse', 'HEAD'], text=True).strip()
    assert head == preferred['commit'], 'Adapter CI must use the exact newly preferred native source'
    before = {name: hashlib.sha256((native_source / name).read_bytes()).hexdigest()
              for name in preferred['files_sha256']}
    assert before == preferred['files_sha256']
    release_contract.preflight(dev_store, 'hybas', head, native_source)
    medaka_review.require_adapter(dev_store, 'hybas', head,
                                 {'medaka_model_review_acknowledged': False}, native_source)
    for action in ('install', 'repair', 'prepare'):
        assert maintenance.browser_preparation_env(dev_store, 'hybas', head, native_source, action) == {'HYBAS_PREPARE_BROWSER': '1'}
    for action in ('check', 'run', 'continue', 'dry-run'):
        assert maintenance.browser_preparation_env(dev_store, 'hybas', head, native_source, action) == {'HYBAS_PREPARE_BROWSER': '0'}
    assert before == {name: hashlib.sha256((native_source / name).read_bytes()).hexdigest() for name in before}


@pytest.mark.parametrize('name', sorted(HANDOFF_FILES))
def test_actual_source_changed_contract_hash_cannot_pass_preflight(dev_store, preferred, native_source, name):
    # Alter only a disposable review document, never the native checkout.
    document = copy.deepcopy(dev_catalog.document(dev_store))
    row = next(item for item in document['builds'] if item['tool'] == 'hybas' and item.get('preferred'))
    row['files_sha256'][name] = '0' * 64
    dev_store.put('dev_catalog', 'registry', document)
    with pytest.raises(ValueError, match='preflight failed'):
        release_contract.preflight(dev_store, 'hybas', preferred['commit'], native_source)
