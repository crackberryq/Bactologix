"""The handoff source requires application support, not a new pipeline schema."""
import base64
import copy
import json
from pathlib import Path

import pytest

from bactologix import catalog, dev_catalog, release_contract, update_checks
from bactologix.store import Store


MODEL = {'interface': 1, 'capability': 'app/medaka-model-review.json',
         'helper': 'workflow/lib/medaka_stage.py', 'preflight_rule': 'medaka_model_preflight'}
BROWSER = {'interface': 1, 'environment': 'qc', 'policy': 'install_or_repair_when_missing'}
CHECKED_FILES = {
    'Hybas.smk', 'environment.yaml', 'config.example.yaml',
    'app/medaka-model-review.json', 'workflow/lib/medaka_stage.py',
    'workflow/lib/settings.py', 'workflow/lib/library_inputs.py',
    'workflow/lib/workflow_support.py', 'workflow/lib/hybas_report.py',
    'workflow/lib/rendering_runtime.py', 'workflow/envs/medaka.yml',
    'workflow/envs/medaka.post-deploy.sh', 'workflow/envs/qc.yml',
    'workflow/envs/qc.post-deploy.sh', 'workflow/envs/multiqc.yml',
    'workflow/envs/multiqc.post-deploy.sh',
}


def handoff():
    spec = catalog.PIPELINES['hybas']
    return {'tool': 'hybas', 'repository': spec['repo'], 'commit': 'f' * 40,
            'tag': 'v10.0.0a5', 'channel': 'prerelease', 'development': True,
            'preferred': True, 'adapter_interface': 2, 'configuration_schema': 'hybas-v2',
            'entry_points': {'workflow': spec['snakefile'], 'environment': spec['environment']},
            'runner': {'python': '3.11', 'snakemake': '9.27.1'}, 'tested': True,
            'test_run': 'https://github.com/crackberryq/Bactologix/actions/runs/40906609530',
            'files_sha256': {name: 'b' * 64 for name in CHECKED_FILES},
            'medaka_model_review': copy.deepcopy(MODEL), 'browser_preparation': copy.deepcopy(BROWSER)}


@pytest.fixture
def dev(tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'dev')
    store = Store(tmp_path / 'dev')
    document = json.loads(dev_catalog.BUNDLED.read_text())
    # A workspace last used by an older app only contains interface-1 approvals.
    document['builds'] = [row for row in document['builds'] if row['adapter_interface'] == 1]
    assert document['builds']
    store.put('dev_catalog', 'registry', document)
    store.put('dev_catalog', 'checked_at', 'previous-successful-check')
    return store


def test_interface_two_is_supported_with_both_checked_capabilities():
    assert release_contract.reason('hybas', handoff()) is None


@pytest.mark.parametrize('interface', [True, False, 2.0, '2', 3, None])
def test_unsupported_interface_types_and_future_versions_fail_closed(interface):
    row = handoff()
    row['adapter_interface'] = interface
    assert 'unsupported adapter interface' in release_contract.reason('hybas', row)


@pytest.mark.parametrize('tool,schema', [('basis', 'basis-v1'), ('micpscope', 'micpscope-v1'),
                                       ('hybas', 'hybas-v1')])
def test_interface_two_is_not_an_approval_for_other_adapters(tool, schema):
    row = handoff()
    row.update(tool=tool, configuration_schema=schema)
    assert 'unsupported adapter interface' in release_contract.reason(tool, row)


@pytest.mark.parametrize('name', ['medaka_model_review', 'browser_preparation'])
@pytest.mark.parametrize('change', ['missing', 'wrong', 'extra', 'bool_interface'])
def test_interface_two_requires_exact_capabilities(name, change):
    row = handoff()
    if change == 'missing':
        row.pop(name)
    elif change == 'wrong':
        row[name]['interface'] = 3 if name == 'medaka_model_review' else 2
    elif change == 'extra':
        row[name]['unreviewed'] = True
    else:
        row[name]['interface'] = True
    assert 'capabilities' in release_contract.reason('hybas', row)


def test_documented_fallback_requires_new_model_contract_and_preserves_old_contract():
    from bactologix.medaka_review import FALLBACK_CONTRACT
    old = handoff()
    new = handoff()
    new['medaka_model_review'] = dict(FALLBACK_CONTRACT)
    assert release_contract.reason('hybas', old) is None
    assert release_contract.reason('hybas', new) is None
    # The a107 application accepts only the v1 Medaka capability, so a catalog
    # refresh cannot silently expose fallback to a client without its controls.
    assert old['medaka_model_review'] == MODEL
    assert new['medaka_model_review'] != MODEL


@pytest.mark.parametrize('name', sorted(CHECKED_FILES))
def test_interface_two_requires_every_execution_and_preparation_hash(name):
    row = handoff()
    row['files_sha256'].pop(name)
    assert release_contract.reason('hybas', row) is not None


def test_existing_interface_one_approvals_remain_accepted():
    root = Path(__file__).resolve().parents[1]
    for filename, key in [('development-v1.json', 'builds'), ('releases-v1.json', 'releases')]:
        rows = json.loads((root / 'compatibility' / filename).read_text())[key]
        legacy = [row for row in rows if row['adapter_interface'] == 1]
        assert legacy
        for row in legacy:
            assert release_contract.reason(row['tool'], row) is None


@pytest.mark.parametrize('background', [False, True])
def test_legacy_client_rejects_new_document_without_changing_cache(dev, monkeypatch, background):
    old_document = dev.get('dev_catalog', 'registry')
    incoming = copy.deepcopy(old_document)
    row = handoff()
    incoming['builds'].append(row)
    current_reason = release_contract.reason

    def legacy_reason(tool, candidate):
        # The a106 client checks this before all other contract fields. Adding a
        # minimum-app-version field alone cannot protect that older application.
        if candidate.get('adapter_interface') != 1:
            return 'Adapter update required: unsupported adapter interface.'
        return current_reason(tool, candidate)

    unprotected = dict(row, adapter_interface=1, minimum_app_version='999.0')
    assert legacy_reason('hybas', unprotected) is None
    monkeypatch.setattr(release_contract, 'reason', legacy_reason)
    encoded = {'content': base64.b64encode(json.dumps(incoming).encode()).decode()}
    monkeypatch.setattr(catalog, 'api', lambda _: encoded)
    monkeypatch.setattr(update_checks, '_api', lambda _: encoded)
    with pytest.raises(ValueError, match='unsupported adapter interface'):
        if background:
            update_checks._registry(dev)
        else:
            dev_catalog.refresh(dev, 'hybas')
    assert dev.get('dev_catalog', 'registry') == old_document
    assert dev.get('dev_catalog', 'checked_at') == 'previous-successful-check'
    assert all(dev_catalog.approval(dev, item['tool'], item['commit']) == item
               for item in old_document['builds'])
    assert not catalog.supported('hybas', row['commit'], dev)


def test_new_client_adds_handoff_without_losing_older_saved_run_approvals(dev, monkeypatch):
    original = dev.get('dev_catalog', 'registry')
    row = handoff()
    incoming = dict(original, builds=[row])
    encoded = {'content': base64.b64encode(json.dumps(incoming).encode()).decode()}
    monkeypatch.setattr(catalog, 'api', lambda _: encoded)
    result = dev_catalog.refresh(dev, 'hybas')
    assert result['recommended_release'] == row['tag']
    assert dev_catalog.approval(dev, 'hybas', row['commit']) == row
    for old in original['builds']:
        assert dev_catalog.approval(dev, old['tool'], old['commit']) == dict(old, preferred=False)


def test_release_display_reports_the_reviewed_interface(tmp_path, monkeypatch):
    monkeypatch.setenv('BACTOLOGIX_CHANNEL', 'stable')
    store = Store(tmp_path / 'stable')
    row = handoff()
    row.pop('development')
    listing = {'versions': [{'kind': 'release', 'ref': row['tag'], 'commit': row['commit'], 'prerelease': True}]}
    monkeypatch.setattr(catalog, 'fetch_versions', lambda _: listing)
    encoded = {'content': base64.b64encode(json.dumps({'schema_version': 1, 'releases': [row]}).encode()).decode()}
    monkeypatch.setattr(catalog, 'api', lambda _: encoded)
    result = release_contract.refresh(store, 'hybas')
    assert result['versions'][0]['compatibility_reason'] == 'Reviewed adapter contract v2; source preflight required.'
