"""Setup advice is a source-bound, explicit edit of an unsaved draft."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from bactologix import setup_advice
from bactologix.runs import register_sample
from bactologix.ui_backend import dispatch
from test_native import workspace


@pytest.fixture
def source(monkeypatch, workspace):
    row = {'verified': True, 'commit': 'a' * 40, 'label': 'Fixture selected source',
           'config_path': 'config.example.yaml', 'config_sha256': 'b' * 64,
           'installation': 'fixture-install', 'profile': 'hybas9',
           'defaults': {'polish_mode': 'tripolish', 'racon_rounds': 2,
                        'run_polypolish': True, 'medaka_model': ''}}
    workspace.put('settings', 'active-hybas', row['installation'])
    monkeypatch.setattr(setup_advice.setup_advice_source, 'load', lambda *_: deepcopy(row))
    return row


@pytest.fixture
def make_sample(workspace, tmp_path):
    def make(sid='sample', mode='hybrid', annotations=()):
        folder = tmp_path / ('inputs-' + sid)
        folder.mkdir()
        for platform in {'hybrid': ('illumina', 'nanopore'),
                         'ont_only': ('nanopore',), 'illumina_only': ('illumina',)}[mode]:
            raw = folder / 'raw' / platform
            raw.mkdir(parents=True)
            names = ('reads_R1.fastq', 'reads_R2.fastq') if platform == 'illumina' else ('reads.fastq',)
            for name in names:
                (raw / name).write_text('@read\nACGT\n+\nIIII\n')
        if annotations:
            (folder / 'annotation').mkdir()
            for suffix in annotations:
                (folder / 'annotation' / (sid + '.' + suffix)).write_text('fixture\n')
        return register_sample(workspace, sid, str(folder), mode)
    return make


def request(*samples, tool='hybas', **overrides):
    values = {'polish_mode': 'medaka', 'racon_rounds': 4,
              'medaka_model': 'researcher-selected-model', 'run_polypolish': True,
              'run_kraken': False, 'kraken_decision_mode': 'off',
              'custom': {'keep': ['manual', 9]}}
    if tool != 'hybas':
        values = {'custom': {'keep': ['manual', 9]}}
    values.update(overrides)
    return {'tool': tool, 'ids': [s['id'] for s in samples],
            'config': {'tool': tool, 'text': yaml.safe_dump(values, sort_keys=False)},
            'cores': 4, 'memory_mb': 16000}


def settings(doc):
    return {row['name']: row for row in doc['settings']}


def checks(doc):
    return '\n'.join(row['title'] + ': ' + row['detail'] for row in doc['checks'])


def apply_payload(payload, doc, name='racon_rounds'):
    return dict(payload, signature=doc['signature'], setting=name)


def test_review_preserves_inputs_configuration_and_workspace(workspace, source, make_sample):
    sample = make_sample()
    payload = request(sample)
    original = deepcopy(payload)
    saved = 'custom: workspace-default\n'
    workspace.put('configs', 'hybas', saved)
    workspace.put('runs', 'old', {'id': 'old', 'config': 'unchanged'})
    doc = dispatch(workspace, 'inspect_setup_advice', payload)['setup_advice']
    assert doc['signature'] == setup_advice.review(workspace, payload)['signature']
    assert settings(doc)['racon_rounds']['current'] == 4
    assert settings(doc)['racon_rounds']['default'] == 2
    assert settings(doc)['racon_rounds']['change'] == 2
    assert doc['source']['commit'] == source['commit']
    assert 'defaults' not in doc['source'] and 'profile' not in doc['source']
    assert all(row['status'] == 'present' for row in doc['samples'][0]['inputs'])
    assert payload == original
    assert workspace.get('configs', 'hybas') == saved
    assert workspace.all('runs') == [{'id': 'old', 'config': 'unchanged'}]


def test_apply_uses_server_default_and_changes_only_unsaved_config(workspace, source, make_sample):
    payload = request(make_sample())
    payload['config']['changes'] = {json.dumps(['custom', 'keep'], separators=(',', ':')): '[manual, 11]'}
    saved = 'workspace: untouched\n'
    workspace.put('configs', 'hybas', saved)
    doc = setup_advice.review(workspace, payload)
    result = dispatch(workspace, 'apply_setup_advice',
                      dict(apply_payload(payload, doc), value=999, change=999))
    actual = yaml.safe_load(result['run_config']['text'])
    assert actual['racon_rounds'] == source['defaults']['racon_rounds'] == 2
    assert actual['medaka_model'] == 'researcher-selected-model'
    assert actual['polish_mode'] == 'medaka'
    assert actual['custom'] == {'keep': ['manual', 11]}
    assert workspace.get('configs', 'hybas') == saved
    assert workspace.all('runs') == []
    assert settings(result['setup_advice'])['racon_rounds']['change'] is None
    assert result['setup_advice']['signature'] != doc['signature']


@pytest.mark.parametrize(('name', 'default', 'overrides'), [
    ('racon_rounds', 0, {}),
    ('run_polypolish', False, {'polish_mode': 'tripolish'}),
])
def test_false_and_zero_are_real_explicit_changes(workspace, source, make_sample, name, default, overrides):
    source['defaults'][name] = default
    payload = request(make_sample(), **overrides)
    doc = setup_advice.review(workspace, payload)
    assert settings(doc)[name]['change'] == default
    result = setup_advice.apply(workspace, apply_payload(payload, doc, name))
    actual = yaml.safe_load(result['run_config']['text'])
    assert actual[name] == default and type(actual[name]) is type(default)


@pytest.mark.parametrize('mutation', ['text', 'changes', 'inputs', 'source', 'active', 'sample', 'budget'])
def test_apply_rejects_a_stale_review(workspace, source, make_sample, mutation):
    sample = make_sample()
    payload = request(sample)
    doc = setup_advice.review(workspace, payload)
    if mutation == 'text':
        payload['config']['text'] += 'additional: manual\n'
    elif mutation == 'changes':
        payload['config']['changes'] = {'["racon_rounds"]': '8'}
    elif mutation == 'inputs':
        path = Path(sample['path']) / 'raw/nanopore/reads.fastq'
        path.write_text(path.read_text() + '\n')
    elif mutation == 'source':
        source['config_sha256'] = 'c' * 64
    elif mutation == 'active':
        workspace.put('settings', 'active-hybas', 'another-install')
    elif mutation == 'sample':
        workspace.put('samples', sample['id'], dict(sample, mode='ont_only'))
    elif mutation == 'budget':
        payload['memory_mb'] = 12000
    with pytest.raises(ValueError, match='out of date'):
        setup_advice.apply(workspace, apply_payload(payload, doc))
    assert workspace.all('runs') == []


@pytest.mark.parametrize('broken', ['empty', 'missing', 'directory'])
def test_missing_empty_and_nonregular_reads_disable_apply(workspace, source, make_sample, broken):
    sample = make_sample()
    path = Path(sample['path']) / 'raw/nanopore/reads.fastq'
    if broken == 'empty':
        path.write_text('')
    else:
        path.unlink()
        if broken == 'directory':
            path.mkdir()
    doc = setup_advice.review(workspace, request(sample))
    states = {row['status'] for row in doc['samples'][0]['inputs']}
    assert {'empty': 'empty', 'missing': 'missing', 'directory': 'unreadable'}[broken] in states
    assert all(row['change'] is None for row in doc['settings'])


def test_mixed_batch_has_per_sample_routes_but_no_common_apply(workspace, source, make_sample):
    short = make_sample('short', 'illumina_only')
    long = make_sample('long', 'ont_only')
    doc = setup_advice.review(workspace, request(short, long, polish_mode='tripolish'))
    content = checks(doc)
    assert 'Different read types' in content
    assert 'Illumina-only inputs skip Racon and Medaka' in content
    assert 'long · polishing route' in content
    assert 'Polypolish has no Illumina reads' in content
    assert all(row['change'] is None for row in doc['settings'])


def test_illumina_only_never_offers_racon_or_medaka_changes(workspace, source, make_sample):
    doc = setup_advice.review(workspace, request(make_sample(mode='illumina_only'), polish_mode='tripolish'))
    rows = settings(doc)
    assert not rows['racon_rounds']['applicable'] and rows['racon_rounds']['change'] is None
    assert not rows['medaka_model']['applicable'] and rows['medaka_model']['change'] is None
    assert 'Illumina-only inputs skip Racon and Medaka' in checks(doc)


def test_ont_route_model_and_rounds_are_not_reported_as_optimal(workspace, source, make_sample):
    doc = setup_advice.review(workspace, request(make_sample(mode='ont_only')))
    rows = settings(doc)
    assert rows['medaka_model']['change'] is None
    assert rows['medaka_model']['current'] == 'researcher-selected-model'
    assert not rows['run_polypolish']['applicable']
    assert 'configured Racon rounds 4' in checks(doc)
    assert 'Medaka is included' in checks(doc)
    assert 'cannot select a Medaka model or an optimal round count' in checks(doc)
    assert any('not sample-specific' in text for text in doc['limits'])


@pytest.mark.parametrize('verified, profile', [(False, 'hybas9'), (True, None)])
def test_unverified_or_unreviewed_source_cannot_offer_apply(workspace, source, make_sample, verified, profile):
    source.update(verified=verified, profile=profile, error='Source changed')
    doc = setup_advice.review(workspace, request(make_sample()))
    assert all(row['change'] is None for row in doc['settings'])
    if not verified:
        assert all(row['default'] is None for row in doc['settings'])
        assert 'Source changed' in checks(doc)
    assert not any('· polishing route' in row['title'] for row in doc['checks'])


def test_no_static_catalog_value_substituted_for_missing_source_setting(workspace, source, make_sample):
    source['defaults'].pop('racon_rounds')
    doc = setup_advice.review(workspace, request(make_sample()))
    row = settings(doc)['racon_rounds']
    assert row['current'] == 4
    assert row['default'] is None and row['change'] is None


def test_omitted_draft_setting_does_not_claim_template_as_effective(workspace, source, make_sample):
    payload = request(make_sample())
    cfg = yaml.safe_load(payload['config']['text'])
    del cfg['racon_rounds']
    payload['config']['text'] = yaml.safe_dump(cfg)
    doc = setup_advice.review(workspace, payload)
    row = settings(doc)['racon_rounds']
    assert row['default'] == 2
    assert row['current'] is None
    assert 'configured Racon rounds 2' not in checks(doc)


def test_omitted_polypolish_setting_does_not_claim_disabled_execution(workspace, source, make_sample):
    payload = request(make_sample(), polish_mode='tripolish')
    cfg = yaml.safe_load(payload['config']['text'])
    del cfg['run_polypolish']
    payload['config']['text'] = yaml.safe_dump(cfg)
    doc = setup_advice.review(workspace, payload)
    assert settings(doc)['run_polypolish']['current'] is None
    assert 'Polypolish is not enabled' not in checks(doc)


def test_no_polishing_does_not_offer_active_round_or_model_setting(workspace, source, make_sample):
    doc = setup_advice.review(workspace, request(make_sample(), polish_mode='none'))
    assert not settings(doc)['racon_rounds']['applicable']
    assert not settings(doc)['medaka_model']['applicable']
    assert not settings(doc)['run_polypolish']['applicable']
    assert 'Polishing is disabled in this draft' in checks(doc)


@pytest.mark.parametrize('tool, required', [('basis', ('fna', 'faa', 'gff')), ('micpscope', ('faa',))])
def test_annotation_pipelines_expose_no_polishing_changes(workspace, source, make_sample, tool, required):
    sample = make_sample(annotations=required)
    source['profile'] = None
    source['config_path'] = 'config.yaml'
    source['defaults'] = {'deep': {'enabled': False, 'maximum_candidates': 5, 'aware': {'preset': 'auto-medium'}},
                          'reference_min_identity': 30, 'reference_min_coverage': 70, 'reference_max_evalue': 1e-5}
    doc = setup_advice.review(workspace, request(sample, tool=tool))
    assert all(row['change'] is None for row in doc['settings'])
    assert not {'polish_mode', 'racon_rounds', 'medaka_model'} & settings(doc).keys()
    assert 'polishing is selected in HybAs' in checks(doc)
    assert 'deployment safety' in checks(doc)
    for row in doc['samples'][0]['inputs']:
        if row['label'].removeprefix('Annotation .') in required:
            assert row['status'] == 'present'
    assert not any('input review' in row['title'] for row in doc['checks'])


def test_missing_annotation_is_reported_without_claiming_consistency(workspace, source, make_sample):
    sample = make_sample(annotations=('faa',))
    doc = setup_advice.review(workspace, request(sample, tool='basis'))
    inputs = {row['label']: row for row in doc['samples'][0]['inputs']}
    assert inputs['Annotation .fna']['status'] == 'missing'
    assert inputs['Annotation .gff']['status'] == 'missing'
    assert 'does not establish that the assembly, proteins and annotations belong together' in checks(doc)


@pytest.mark.parametrize('field,value', [('cores', True), ('cores', '4'), ('cores', 0),
    ('memory_mb', True), ('memory_mb', 2.5), ('memory_mb', 511)])
def test_budgets_require_supported_integers(workspace, source, make_sample, field, value):
    payload = request(make_sample())
    payload[field] = value
    with pytest.raises(ValueError, match='whole-number'):
        setup_advice.review(workspace, payload)


def test_payload_pipeline_mismatch_and_unregistered_sample_rejected(workspace, source, make_sample):
    payload = request(make_sample())
    payload['config']['tool'] = 'basis'
    with pytest.raises(ValueError, match='current settings'):
        setup_advice.review(workspace, payload)
    payload['config']['tool'] = 'hybas'
    payload['ids'] = ['gone']
    with pytest.raises(ValueError, match='no longer registered'):
        setup_advice.review(workspace, payload)


@pytest.mark.parametrize('name', ['medaka_model', 'custom.keep', 'threads', 'not-a-setting'])
def test_apply_rejects_unoffered_or_managed_setting(workspace, source, make_sample, name):
    payload = request(make_sample())
    doc = setup_advice.review(workspace, payload)
    with pytest.raises(ValueError, match='no applicable'):
        setup_advice.apply(workspace, apply_payload(payload, doc, name))


def test_stage_memory_requests_above_budget_are_explained(workspace, source, make_sample):
    doc = setup_advice.review(workspace, request(make_sample(), assembly_mem_mb=28000))
    assert '28000 MB' in checks(doc) and '16000 MB' in checks(doc)
    assert 'assembly_mem_mb' not in settings(doc)


@pytest.mark.parametrize('run_mode', ['ont_only', 'unknown_future_mode'])
def test_dev_run_mode_mismatch_or_unknown_route_suppresses_advice(workspace, source, make_sample, run_mode):
    source['profile'] = 'hybas10'
    doc = setup_advice.review(workspace, request(make_sample(), run_mode=run_mode))
    assert all(row['change'] is None for row in doc['settings'])
    assert not any('· polishing route' in row['title'] for row in doc['checks'])
    assert 'mode' in checks(doc).lower()


def test_dev_manifest_cannot_use_a_legacy_source_profile(workspace, source, make_sample, monkeypatch):
    sample = make_sample(mode='ont_only')
    sample['input_manifest'] = {'sample_id': sample['id'], 'libraries': []}
    workspace.put('samples', sample['id'], sample)
    real_inventory = setup_advice._input_inventory
    def declared_inventory(sample, tool):
        ordinary = dict(sample)
        ordinary.pop('input_manifest', None)
        return real_inventory(ordinary, tool)
    monkeypatch.setattr(setup_advice, '_input_inventory', declared_inventory)
    source['profile'] = 'hybas9'
    doc = setup_advice.review(workspace, request(sample))
    assert all(row['change'] is None for row in doc['settings'])
    assert not any('· polishing route' in row['title'] for row in doc['checks'])
    assert 'manifest' in checks(doc).lower()


def manifest_sample(workspace, make_sample, platform):
    pytest.importorskip('bactologix.hybas_manifest_schema', reason='Manifest inputs belong to the Dev channel')
    sample = make_sample(mode='ont_only')
    library = {'library_id': 'long', 'platform': platform,
               'read_groups': [{'read_group_id': 'reads', 'run_id': 'sequencing',
                                'reads': str(Path(sample['path']) / 'raw/nanopore/reads.fastq')}]}
    if platform == 'pacbio':
        library['chemistry'] = 'hifi'
    sample['input_manifest'] = {'sample_id': sample['id'], 'mode': 'auto', 'libraries': [library]}
    workspace.put('samples', sample['id'], sample)
    return sample


def test_dev_manifest_platform_takes_precedence_over_legacy_label(workspace, source, make_sample):
    sample = manifest_sample(workspace, make_sample, 'pacbio')
    source['profile'] = 'hybas10'
    doc = setup_advice.review(workspace, request(sample, polish_mode='auto'))
    assert 'PacBio route uses Flye' in checks(doc)
    assert 'configured Racon rounds' not in checks(doc)
    assert all(row['change'] is None for row in doc['settings'])


def test_dev_manifest_with_wrong_sample_identity_is_unreviewed(workspace, source, make_sample):
    sample = manifest_sample(workspace, make_sample, 'ont')
    sample['input_manifest']['sample_id'] = 'different'
    workspace.put('samples', sample['id'], sample)
    source['profile'] = 'hybas10'
    doc = setup_advice.review(workspace, request(sample))
    assert any(row['status'] == 'unchecked' for row in doc['samples'][0]['inputs'])
    assert all(row['change'] is None for row in doc['settings'])
