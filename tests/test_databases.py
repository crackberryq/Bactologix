"""Filesystem-only fixtures: resource selection, not biological validation."""
import json
from pathlib import Path

import pytest
import yaml

from bactologix import databases as dbs
from bactologix.runs import bind_paths
from bactologix.store import Store
from bactologix.ui_backend import dispatch


def files(root, names):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('software fixture\n')
    return root


def kraken(path):
    return files(path, ['hash.k2d', 'opts.k2d', 'taxo.k2d'])


def busco(path):
    return files(path, ['dataset.cfg', 'scores_cutoff', 'lengths_cutoff', 'hmms/test.hmm'])


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'workspace')


def test_hybas_independent_folders_preserve_native_paths_and_old_layout(store, tmp_path):
    k1 = kraken(tmp_path / 'disk one/classifier')
    b1 = busco(tmp_path / 'disk two/bacteria_odb10')
    cfg = {'custom': {'keep': [1, True]}, 'busco_download_lineages': ['other_odb10'], 'kraken_decision_mode': 'off'}
    store.put('configs', 'hybas', yaml.safe_dump(cfg))
    text = dbs.save(store, 'hybas', {'kraken': str(k1), 'busco': str(b1)}, dbs.panel(store, 'hybas')['revision'])
    frozen = bind_paths(yaml.safe_load(text), 'hybas', store, {})
    root = Path(frozen['db_root'])
    assert (root / frozen['kraken_db_name']).resolve() == k1
    assert (root / 'busco' / frozen['busco_lineage']).resolve() == b1
    assert frozen['custom'] == cfg['custom']
    assert frozen['kraken_decision_mode'] == 'off'
    assert frozen['busco_download_lineages'] == ['bacteria_odb10', 'other_odb10']
    k2 = kraken(tmp_path / 'replacement')
    dbs.save(store, 'hybas', {'kraken': str(k2)}, dbs.panel(store, 'hybas')['revision'])
    new = yaml.safe_load(store.get('configs', 'hybas'))
    assert new['db_root'] != str(root)
    assert (root / frozen['kraken_db_name']).resolve() == k1
    assert (Path(new['db_root']) / 'busco/bacteria_odb10').resolve() == b1
    assert sorted(p.name for p in k1.iterdir()) == ['hash.k2d', 'opts.k2d', 'taxo.k2d']


def test_scanner_recognizes_contents_keeps_saved_choice_and_matching_lineage(store, tmp_path, monkeypatch):
    root = tmp_path / 'databases'
    old = kraken(root / 'z-original')
    kraken(root / 'a-newer-name')
    matching = busco(root / 'bacteria_odb10')
    busco(root / 'different_odb10')
    files(root / 'kraken2-but-incomplete', ['hash.k2d'])
    (root / 'cycle').symlink_to(root, target_is_directory=True)
    store.put('configs', 'hybas', yaml.safe_dump({'db_root': str(root), 'kraken_db_name': 'z-original'}))
    monkeypatch.setattr(dbs, '_roots', lambda *args: [root])
    result = dbs.scan(store, 'hybas')
    assert result['candidates']['kraken'][0]['path'] == str(old)
    assert result['candidates']['kraken'][0]['reason'] == 'Current valid location'
    assert len(result['candidates']['kraken']) == 2
    assert result['candidates']['busco'][0]['path'] == str(matching)
    assert result['candidates']['busco'][0]['reason'] == 'Matches configured lineage'
    assert len([c for c in result['candidates']['kraken'] if c['preferred']]) == 1
    assert result['visited'] < 20


def test_scan_reports_limits_and_never_changes_configs(store, tmp_path, monkeypatch):
    root = tmp_path / 'scan'
    kraken(root / 'first/second/third')
    original = dbs.panel(store, 'hybas')['revision']
    monkeypatch.setattr(dbs, '_roots', lambda *args: [root])
    monkeypatch.setattr(dbs, 'MAX_DEPTH', 1)
    result = dbs.scan(store, 'hybas')
    assert result['limited'] and 'depth limit' in result['limits']
    assert not result['candidates']['kraken']
    assert dbs.panel(store, 'hybas')['revision'] == original
    monkeypatch.setattr(dbs, 'MAX_DIRS', 1)
    assert 'folder/time limit' in dbs.scan(store, 'hybas')['limits']


def test_check_and_save_reject_missing_or_empty_indices(store, tmp_path):
    root = files(tmp_path / 'empty-db', ['hash.k2d', 'opts.k2d'])
    (root / 'taxo.k2d').touch()
    with pytest.raises(ValueError, match='taxo.k2d'):
        dbs.inspect(store, 'hybas', 'kraken', str(root))
    k = kraken(tmp_path / 'working')
    dbs.inspect(store, 'hybas', 'kraken', str(k))
    (k / 'opts.k2d').unlink()
    before = dbs.panel(store, 'hybas')['revision']
    with pytest.raises(ValueError, match='opts.k2d'):
        dbs.save(store, 'hybas', {'kraken': str(k)}, before)
    assert dbs.panel(store, 'hybas')['revision'] == before


def test_stale_revision_does_not_overwrite_new_defaults(store, tmp_path):
    old = dbs.panel(store, 'basis')['revision']
    store.put('configs', 'basis', 'custom: newer\n')
    card = files(tmp_path / 'card', ['card.json'])
    with pytest.raises(ValueError, match='Defaults changed'):
        dbs.save(store, 'basis', {'rgi': str(card)}, old)
    assert store.get('configs', 'basis') == 'custom: newer\n'


def test_basis_file_database_and_companions_preserve_unrelated_settings(store, tmp_path):
    folder = files(tmp_path / 'vfdb', ['vfdb_setA.dmnd', 'VFDB_setA_pro.normalized.fas'])
    manifest = folder / 'resource-provenance.json'
    manifest.write_text(json.dumps({'provider': 'vfdb', 'resolved_version': 'fixture-release'}))
    original = {'custom': {'keep': [True, 4]}, 'deep': {'enabled': False}, 'modules': {'vfdb': {'enabled': False, 'threads': 2}, 'rgi': {'enabled': False}}, 'resources': {'policy': 'manual'}}
    store.put('configs', 'basis', yaml.safe_dump(original))
    text = dbs.save(store, 'basis', {'vfdb': str(folder)}, dbs.panel(store, 'basis')['revision'])
    cfg = yaml.safe_load(text)
    values = cfg['modules']['vfdb']
    assert values['database'] == str(folder / 'vfdb_setA.dmnd')
    assert values['reference_fasta'] == str(folder / 'VFDB_setA_pro.normalized.fas')
    assert values['metadata'] == str(manifest)
    assert values['database_version'] == 'fixture-release'
    assert not values['enabled'] and values['threads'] == 2
    assert cfg['deep'] == original['deep'] and cfg['custom'] == original['custom']
    assert cfg['resources']['policy'] == 'manual'
    dbs.save(store, 'basis', {'vfdb': None}, dbs.panel(store, 'basis')['revision'])
    cfg = yaml.safe_load(store.get('configs', 'basis'))
    assert cfg['modules']['vfdb'] == {'enabled': False, 'threads': 2}


def test_receipt_import_ignores_executables_and_fails_broken_companions(store, tmp_path):
    resource = files(tmp_path / 'resource', ['card.json'])
    receipt = tmp_path / 'receipt'
    receipt.mkdir()
    result = {'module_updates': {'rgi': {'database': str(resource / 'card.json'), 'executable': '/do-not-run', 'enabled': True, 'threads': 999}}, 'deep_updates': {'enabled': True}}
    (receipt / 'provenance.json').write_text(json.dumps({'status': 'completed', 'result': result}))
    item = dbs.inspect(store, 'basis', 'rgi', str(receipt))
    assert item['updates'] == {'modules.rgi.database': str(resource / 'card.json')}
    text = dbs.save(store, 'basis', {'rgi': str(receipt)}, dbs.panel(store, 'basis')['revision'])
    assert yaml.safe_load(text)['modules']['rgi'] == {'database': str(resource / 'card.json')}
    result['module_updates']['rgi']['metadata'] = str(tmp_path / 'missing-metadata.json')
    (receipt / 'provenance.json').write_text(json.dumps({'status': 'completed', 'result': result}))
    with pytest.raises(ValueError, match='missing or empty'):
        dbs.inspect(store, 'basis', 'rgi', str(receipt))


def test_bundle_models_require_identity_and_both_weight_sets(store, tmp_path):
    root = tmp_path / 'models'
    model = files(root / 'model', ['config.json', 'spiece.model', 'pytorch_model.bin'])
    (model / 'config.json').write_text(json.dumps({'model_type': 't5', 'd_model': 1024}))
    files(root / 'classifier_weights', ['model.pt', *[f'weights_model{n}.pt' for n in range(1, 5)]])
    with pytest.raises(ValueError, match='model-identity'):
        dbs.inspect(store, 'basis', 'prott5', str(root))
    (root / 'model-identity.json').write_text(json.dumps({'repository': 'fixture', 'revision': 'test-only'}))
    item = dbs.inspect(store, 'basis', 'prott5', str(root))
    assert item['updates']['modules.pathogenfinder2.prot_t5_identity'] == 'fixture@test-only'
    dbs.save(store, 'basis', {'prott5': str(root)}, dbs.panel(store, 'basis')['revision'])
    assert next(r for r in dbs.panel(store, 'basis')['rows'] if r['id'] == 'prott5')['path'] == str(root)
    assert dbs.inspect(store, 'basis', 'prott5', str(root))['path'] == str(root)
    (root / 'classifier_weights/weights_model4.pt').unlink()
    with pytest.raises(ValueError, match='weights_model4'):
        dbs.inspect(store, 'basis', 'prott5', str(root))


def test_bundle_receipt_requires_companions_and_does_not_enable_analysis(store, tmp_path):
    root = files(tmp_path / 'bundle', ['references.faa', 'references.tsv', 'Pfam-A.hmm', 'profiles/test.yaml', 'motif.json', 'placement.json'])
    values = dict(zip(dbs.DEEP_PATHS, [str(root / p) for p in ['references.faa', 'references.tsv', 'Pfam-A.hmm', 'profiles', 'motif.json', 'placement.json']]))
    (root / 'provenance.json').write_text(json.dumps({'status': 'completed', 'result': {'deep_updates': dict(values, enabled=True, family_placement=True)}}))
    store.put('configs', 'basis', 'deep: {enabled: false, family_placement: false}\n')
    text = dbs.save(store, 'basis', {'deep_catalogue': str(root)}, dbs.panel(store, 'basis')['revision'])
    cfg = yaml.safe_load(text)
    assert cfg['deep']['enabled'] is False and cfg['deep']['family_placement'] is False
    assert cfg['deep']['reference_metadata'] == values['reference_metadata']
    (root / 'references.tsv').unlink()
    with pytest.raises(ValueError, match='missing or empty'):
        dbs.inspect(store, 'basis', 'deep_catalogue', str(root))


def test_tool_managed_entries_cannot_pretend_to_redirect_database(store, tmp_path):
    for tool, key in [('hybas', 'prokka'), ('basis', 'isescan'), ('basis', 'minced')]:
        with pytest.raises(ValueError, match='supports a local folder'):
            dbs.inspect(store, tool, key, str(tmp_path))


def test_failed_batch_does_not_partially_save(store, tmp_path):
    card = files(tmp_path / 'card', ['card.json'])
    original = dbs.panel(store, 'basis')['revision']
    with pytest.raises(ValueError):
        dbs.save(store, 'basis', {'rgi': str(card), 'amrfinder': str(tmp_path / 'missing')}, original)
    assert dbs.panel(store, 'basis')['revision'] == original


def test_changed_managed_alias_is_not_overwritten(store, tmp_path):
    k = kraken(tmp_path / 'original')
    dbs.save(store, 'hybas', {'kraken': str(k)}, dbs.panel(store, 'hybas')['revision'])
    cfg = yaml.safe_load(store.get('configs', 'hybas'))
    alias = Path(cfg['db_root']) / 'kraken'
    alias.unlink()
    alias.symlink_to(tmp_path / 'unrelated')
    with pytest.raises(ValueError, match='link changed'):
        dbs.save(store, 'hybas', {'kraken': str(k)}, dbs.panel(store, 'hybas')['revision'])
    assert alias.resolve() == tmp_path / 'unrelated'


def test_dispatch_rechecks_paths_and_ignores_client_updates(store, tmp_path):
    card = files(tmp_path / 'card', ['card.json'])
    p = {'tool': 'basis', 'id': 'rgi', 'path': str(card), 'updates': {'modules.rgi.executable': 'malicious'}}
    result = dispatch(store, 'check_database', p)
    assert result['database_check']['updates'] == {'modules.rgi.database': str(card / 'card.json')}
    dispatch(store, 'save_databases', {'tool': 'basis', 'changes': {'rgi': str(card)}, 'revision': dbs.panel(store, 'basis')['revision']})
    assert 'executable' not in store.get('configs', 'basis')


@pytest.mark.parametrize('key,names', [
    ('amrfinder', ['version.txt', 'AMRProt.fa', 'AMRProt.fa.phr', 'AMR.LIB', *['AMR.LIB.h3'+s for s in 'fimp']]),
    ('bacmet', ['bacmet_exp.dmnd', 'BacMet2_EXP_database.fasta']),
    ('genomad', ['version.txt', 'genomad_marker_metadata.tsv', 'names.dmp', 'nodes.dmp', *[p+s for p in ['genomad_db','genomad_mini_db','genomad_integrase_db'] for s in ['', '.dbtype', '.index']]]),
    ('mlst', ['pubmlst/test/test.txt', *['blast/mlst.fa.'+s for s in ['nhr','nin','nsq']]]),
    ('crisprcasfinder', ['CRISPRCasFinder.pl', 'sel392v2.so', 'models/CASFinder/metadata.yml', 'models/CASFinder/profiles/test.hmm']),
    ('prosite', ['prosite.dat', 'prosite.aux', 'prosite.doc', 'ps_reldt.txt', 'ps_scan.pl', 'prorule.dat', 'provenance.json']),
    ('mobsuite', ['ncbi_plasmid_full_seqs.fas', 'ncbi_plasmid_full_seqs.fas.msh', 'repetitive.dna.fas', 'rep.dna.fas', 'mob.proteins.faa', 'mpf.proteins.faa', 'orit.fas', 'clusters.txt', 'taxa.sqlite', *[p+'.'+s for p in ['ncbi_plasmid_full_seqs.fas', 'repetitive.dna.fas'] for s in ['nhr','nin','nsq']]]),
])
def test_database_layouts_and_native_path_types(store, tmp_path, key, names):
    root = files(tmp_path / key, names)
    item = dbs.inspect(store, 'basis', key, str(root))
    text = dbs.save(store, 'basis', {key: str(root)}, dbs.panel(store, 'basis')['revision'])
    cfg = bind_paths(yaml.safe_load(text), 'basis', store, {})
    expected = root / 'bacmet_exp.dmnd' if key == 'bacmet' else root
    assert item['path'] == str(expected)
    assert dbs._get(cfg, 'deep.prosite_snapshot' if key == 'prosite' else 'modules.'+key+'.database') == str(expected)
    (root / names[0]).unlink()
    with pytest.raises((ValueError, OSError)):
        dbs.inspect(store, 'basis', key, str(root))


def test_receipt_does_not_import_directory_as_metadata(store, tmp_path):
    root = files(tmp_path / 'card', ['card.json'])
    (root / 'provenance.json').write_text(json.dumps({'status': 'completed', 'result': {'module_updates': {'rgi': {'database': str(root / 'card.json'), 'metadata': str(root)}}}}))
    with pytest.raises(ValueError, match='missing or empty'):
        dbs.inspect(store, 'basis', 'rgi', str(root))


def test_scan_bounds_file_heavy_folders_and_reports_inaccessible(store, tmp_path, monkeypatch):
    root = files(tmp_path / 'many-files', [str(i) for i in range(12)])
    monkeypatch.setattr(dbs, '_roots', lambda *args: [root, root / 'not-accessible'])
    monkeypatch.setattr(dbs, 'MAX_ENTRIES', 5)
    result = dbs.scan(store, 'basis')
    assert 'entries per folder limit' in result['limits']
    assert result['inaccessible'] == 1


def test_database_picker_starts_at_existing_parent_and_accepts_file(store, tmp_path):
    folder = files(tmp_path / 'db', ['card.json'])
    response = dispatch(store, 'browse_folders', {'path': str(folder / 'card.json'), 'nearest': True})
    assert response['browser']['path'] == str(folder)
    response = dispatch(store, 'browse_folders', {'path': str(folder / 'not/installed/yet'), 'nearest': True})
    assert response['browser']['path'] == str(folder)


def test_antismash_bundle_requires_compiled_pfam_and_companion_folders(store, tmp_path):
    root = files(tmp_path / 'antismash', ['pfam/fixture/Pfam-A.hmm', *['pfam/fixture/Pfam-A.hmm.h3'+s for s in 'fimp']])
    with pytest.raises(ValueError, match='as-js'):
        dbs.inspect(store, 'basis', 'antismash', str(root))
    for name in ['as-js','mite','resfam','tigrfam','clusterblast','knownclusterblast','clustercompare','comparippson','nrps_pks']:
        files(root / name, ['fixture'])
    assert dbs.inspect(store, 'basis', 'antismash', str(root))['updates']['modules.antismash.database'] == str(root)
    (root / 'pfam/fixture/Pfam-A.hmm.h3p').unlink()
    with pytest.raises(ValueError, match='HMMER indices'):
        dbs.inspect(store, 'basis', 'antismash', str(root))


def test_save_rechecks_revision_after_filesystem_validation(store, tmp_path, monkeypatch):
    root = files(tmp_path / 'card', ['card.json'])
    original = dbs.panel(store, 'basis')['revision']
    inspect = dbs.inspect
    def change_during_check(*args, **kwargs):
        item = inspect(*args, **kwargs)
        store.put('configs', 'basis', 'custom: concurrent-change\n')
        return item
    monkeypatch.setattr(dbs, 'inspect', change_during_check)
    with pytest.raises(ValueError, match='while checking'):
        dbs.save(store, 'basis', {'rgi': str(root)}, original)
    assert store.get('configs', 'basis') == 'custom: concurrent-change\n'
