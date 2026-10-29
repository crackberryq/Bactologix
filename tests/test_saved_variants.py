"""Opaque fixtures: no sequence data, inference or analysis tools."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
import yaml
from bactologix import variants as v
from bactologix.variants_saved import snapshot

@pytest.fixture
def saved(tmp_path):
    source = Path(os.environ.get('BACTOLOGIX_BASIS_SOURCE', '/missing'))
    if not (source / 'workflow/lib/safety/resources.py').is_file():
        pytest.skip('Pinned BaSIS source required')
    old, new = tmp_path / 'parent', tmp_path / 'child'
    for folder in (old, new):
        shutil.copytree(source, folder / 'workflow', ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
        (folder / 'outputs/sample').mkdir(parents=True)
    output = old / 'outputs/sample'
    root = output / 'safety'
    native = root / 'native/pathogenfinder2'
    shared = tmp_path / 'shared'
    shared.mkdir()
    weight = shared / 'opaque-model.txt'
    weight.write_text('Fixture resource identity; never executed')
    receipt = shared / 'provenance.json'
    v.write(receipt, {'schema': 'basis-snakemake-resource-v1', 'status': 'completed',
        'artifacts': {str(weight): {'sha256': v.sha(weight), 'bytes': weight.stat().st_size}}})
    environment = shared / 'pathogenfinder2.json'
    v.write(environment, {'schema': 'basis-snake-environment-v1', 'status': 'completed', 'packages': ['fixture']})
    v.write(root / 'metadata/environments/pathogenfinder2.json', json.loads(environment.read_text()))
    inputs = {}
    for name in ('assembly', 'proteins', 'gff'):
        path = tmp_path / (name + '.txt')
        path.write_text('Opaque input fixture: ' + name)
        inputs[name] = str(path)
    v.write(root / 'metadata/sample.json', dict(inputs, sample_id='sample'))
    settings = {'enabled': True}
    v.write(root / 'metadata/configs/pathogenfinder2.json', {
        'modules': {'pathogenfinder2': settings}, 'execution': {'device': 'cpu'},
        'resource_receipts': [{'path': str(receipt), 'sha256': v.sha(receipt)}],
        'environment_receipts': [str(environment)]})
    native.mkdir(parents=True)
    (native / 'ProteinEmbeddings.h5').write_text('Opaque binary fixture, not an HDF5/model')
    v.write(native / 'embeddings_manifest.json', {'embeddings_path': str(native / 'ProteinEmbeddings.h5'),
        'embeddings_sha256': v.sha(native / 'ProteinEmbeddings.h5')})
    prediction = native / 'predictionPF2'
    (prediction / 'results_PF2').mkdir(parents=True)
    (prediction / 'results_PF2/predictions.tsv').write_text('Opaque previously recorded result')
    v.write(prediction / 'classifier-config.json', {'resource': str(weight)})
    v.write(prediction / 'classifier-weights.json', {'matches_pinned_bundled_ensemble': True, 'sha256': {str(weight): v.sha(weight)}})
    v.write(prediction / 'device.json', {'selected': 'fixture'})
    paths = [p for p in native.rglob('*') if p.is_file()]
    v.write(native / 'parsed.json', {'artifacts': list(map(str, paths)), 'recorded_value': 'unchanged', 'findings': [], 'interpretation': 'inconclusive', 'metrics': {'prediction_mean': 0.0, 'prediction_std': 0.0}})
    evidence = root / 'evidence/pathogenfinder2.json'
    v.write(evidence, {'module': 'pathogenfinder2', 'execution_status': 'completed', 'mode': 'native',
        'effective_config': dict(settings, device_policy='cpu'),
        'input_hashes': {k: v.sha(p) for k, p in inputs.items()},
        'artifact_hashes': {str(p): v.sha(p) for p in [*paths, native / 'parsed.json']}})
    checks = [root / 'metadata/sample.json', root / 'metadata/configs/pathogenfinder2.json', native / 'parsed.json']
    record = {'record_format_version': 6, 'rule': 'normalize_extend', 'incomplete': False,
        'endtime': evidence.stat().st_mtime, 'input_checksums': {str(p): v.sha(p) for p in checks}}
    v.write(old / 'workflow/.snakemake/metadata' / base64.urlsafe_b64encode(str(evidence).encode()).decode(), record)
    cfg = {'modules': {'pathogenfinder2': settings}, 'extend': True}
    parent = {'id': 'parent', 'tool': 'basis', 'folder': str(old), 'output': str(output),
        'config': yaml.safe_dump(cfg), 'controller': sys.prefix, 'completed': v.now(),
        'inputs': [{'path': p} for p in inputs.values()]}
    child = dict(parent, id='child', folder=str(new), output=str(new / 'outputs/sample'))
    cfg['modules']['minced'] = {'enabled': True}
    child['config'] = yaml.safe_dump(cfg)
    return parent, child, {'schema': 1, 'parent': 'parent', 'checkpoints': []}, evidence, native, weight


def test_completed_result_restores_without_invoking_native_stages(saved):
    parent, child, manifest, evidence, native, weight = saved
    originals = {str(p): p.read_bytes() for p in Path(parent['folder']).rglob('*') if p.is_file()}
    manifest['checkpoints'].append({'rule': 'another_module', 'native_cache': 'fixture'})
    snapshot(parent, child, manifest)
    assert len(manifest['checkpoints']) == 2, manifest
    assert manifest['checkpoints'][1]['kind'] == 'completed_result'
    assert manifest['checkpoints'][1]['completion']['original_evidence'] == json.loads(evidence.read_text())
    root = Path(child['folder'])
    v.write(root / 'variant.json', manifest)
    destination = Path(child['output']) / 'safety/native/pathogenfinder2'
    script = f'''from pathlib import Path
OUT = {str(destination)!r}
rule all:
    input: OUT + '/checked.txt'
rule pathogenfinder2_embeddings:
    output: OUT + '/ProteinEmbeddings.h5', OUT + '/embeddings_manifest.json'
    shell: 'exit 91'
rule pathogenfinder2_classification:
    output: directory(OUT + '/predictionPF2')
    shell: 'exit 92'
rule pathogenfinder2_collect:
    output: OUT + '/parsed.json'
    shell: 'exit 93'
rule downstream:
    input: OUT + '/parsed.json'
    output: OUT + '/checked.txt'
    shell: 'cp {{input:q}} {{output:q}}'
'''
    (root / 'workflow/BaSIS.smk').write_text(script)
    result = subprocess.run(['snakemake', '--snakefile', str(root / 'workflow/BactologixVariant.smk'),
        '--directory', str(root / 'workflow'), '--cores', '1', 'all'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads((destination / 'parsed.json').read_text())
    assert parsed['recorded_value'] == 'unchanged'
    assert all(Path(p).is_file() and p.startswith(child['folder']) for p in parsed['artifacts'])
    assert json.loads((destination / 'embeddings_manifest.json').read_text())['embeddings_path'].startswith(child['folder'])
    assert (destination / 'ProteinEmbeddings.h5').stat().st_ino != (native / 'ProteinEmbeddings.h5').stat().st_ino
    assert all(Path(p).read_bytes() == content for p, content in originals.items())
    child.update(variant={'schema': 1}, variant_manifest_sha256=v.sha(root / 'variant.json'),
        variant_restore_sha256=v.sha(root / 'restore_variant.py'))
    v.verify(child)
    v.native_records(child)
    weight.write_text('Changed shared resource')
    with pytest.raises(ValueError, match='dependency changed'):
        v.verify(child)


@pytest.mark.parametrize('change', ['disabled', 'settings', 'execution', 'resources'])
def test_changed_scope_or_configuration_does_not_import(saved, change):
    parent, child, manifest, *_ = saved
    cfg = yaml.safe_load(child['config'])
    if change == 'disabled': cfg['modules']['pathogenfinder2']['enabled'] = False
    if change == 'settings': cfg['modules']['pathogenfinder2']['changed'] = True
    if change == 'execution': cfg['execution'] = {'device': 'changed'}
    if change == 'resources': cfg['resources'] = {'directory': '/different'}
    child['config'] = yaml.safe_dump(cfg)
    snapshot(parent, child, manifest)
    assert not manifest['checkpoints']
    assert 'variant_entry' not in child


@pytest.mark.parametrize('damage', ['artifact', 'input', 'resource', 'receipt', 'environment', 'metadata'])
def test_incomplete_or_changed_provenance_does_not_import(saved, damage):
    parent, child, manifest, evidence, native, weight = saved
    if damage == 'artifact': (native / 'ProteinEmbeddings.h5').write_text('changed')
    if damage == 'input': Path(parent['inputs'][0]['path']).write_text('changed')
    if damage == 'resource': weight.write_text('changed')
    if damage == 'receipt': (weight.parent / 'provenance.json').write_text('{}')
    if damage == 'environment': (weight.parent / 'pathogenfinder2.json').write_text('{}')
    if damage == 'metadata': shutil.rmtree(Path(parent['folder']) / 'workflow/.snakemake/metadata')
    snapshot(parent, child, manifest)
    assert not manifest['checkpoints']
    assert manifest['unknown_changes']


def test_same_stat_artifact_edit_is_rejected(saved):
    parent, child, manifest, evidence, native, weight = saved
    path = native / 'predictionPF2/results_PF2/predictions.tsv'
    st = path.stat()
    path.write_bytes(b'X' * path.stat().st_size)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    snapshot(parent, child, manifest)
    assert not manifest['checkpoints']


def test_reviewed_native_dag_accepts_saved_bundle_without_analysis(saved):
    parent, child, manifest, *_ = saved
    snapshot(parent, child, manifest)
    root = Path(child['folder'])
    v.write(root / 'variant.json', manifest)
    annotation = Path(child['output']) / 'annotation'
    annotation.mkdir()
    # These files only satisfy path discovery in a dry-run; no parser or tool
    # consumes their contents.
    for suffix in ('fna', 'faa', 'gff'):
        (annotation / ('sample.' + suffix)).write_text('Opaque path fixture')
    config = {'bactomics_root': str(root / 'outputs'), 'isolates': ['sample'],
              'threads': 1, 'memory_mb': 16000, 'deep': {'enabled': False}}
    config_path = root / 'native-check.yaml'
    config_path.write_text(yaml.safe_dump(config))
    target = str(Path(child['output']) / 'safety/evidence/pathogenfinder2.json')
    result = subprocess.run(['snakemake', '--snakefile', str(root / 'workflow/BactologixVariant.smk'),
        '--directory', str(root / 'workflow'), '--configfile', str(config_path), '--cores', '1', '--dry-run', target],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'bactologix_saved_result' in result.stdout
    assert 'rule pathogenfinder2_embeddings:' not in result.stdout
    assert 'rule pathogenfinder2_classification:' not in result.stdout
    assert 'rule pathogenfinder2_collect:' not in result.stdout
    assert 'normalize_extend' in result.stdout


def test_second_generation_retains_recorded_results_with_native_normalization(saved):
    parent, child, manifest, evidence, native, weight = saved
    snapshot(parent, child, manifest)
    root = Path(child['folder'])
    v.write(root / 'variant.json', manifest)
    subprocess.run([sys.executable, str(root / 'restore_variant.py'), str(root / 'variant.json'), '0'], check=True)
    old_metadata = Path(parent['output']) / 'safety/metadata'
    metadata = Path(child['output']) / 'safety/metadata'
    for path in old_metadata.rglob('*.json'):
        v.write(metadata / path.relative_to(old_metadata), v.relocate(json.loads(path.read_text()), parent['folder'], child['folder']))
    program = '''import json, sys
from pathlib import Path
from safety.extend import normalize_saved
root = Path(sys.argv[1])
read = lambda p: json.loads(p.read_text())
sample = read(root / 'metadata/sample.json')
config = read(root / 'metadata/configs/pathogenfinder2.json')
validation = {'input_hashes': json.loads(sys.argv[2]), 'protein_coordinates': {}, 'contig_lengths': {}}
result = normalize_saved('pathogenfinder2', sample, config, validation, root / 'native/pathogenfinder2/parsed.json')
output = root / 'evidence/pathogenfinder2.json'
output.parent.mkdir(parents=True)
output.write_text(json.dumps(result))
'''
    source_root = root / 'workflow/workflow'
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source_root / 'lib'), str(source_root / 'vendor')]))
    subprocess.run([sys.executable, '-c', program, str(Path(child['output']) / 'safety'),
                    json.dumps(json.loads(evidence.read_text())['input_hashes'])], env=environment, check=True)
    next_evidence = Path(child['output']) / 'safety/evidence/pathogenfinder2.json'
    previous = manifest['checkpoints'][0]['completion']['native_record']
    record = v.relocate(previous, parent['folder'], child['folder'])
    record['endtime'] = next_evidence.stat().st_mtime
    record['input_checksums'] = {p: v.sha(p) for p in record['input_checksums']}
    v.write(root / 'workflow/.snakemake/metadata' / base64.urlsafe_b64encode(str(next_evidence).encode()).decode(), record)
    child.update(completed=v.now(), variant={'schema': 1})
    third = root.parent / 'grandchild'
    shutil.copytree(Path(parent['folder']) / 'workflow', third / 'workflow', ignore=shutil.ignore_patterns('.snakemake'))
    (third / 'outputs/sample').mkdir(parents=True)
    grandchild = dict(child, id='grandchild', folder=str(third), output=str(third / 'outputs/sample'))
    next_manifest = {'schema': 1, 'parent': child['id'], 'checkpoints': []}
    snapshot(child, grandchild, next_manifest)
    assert len(next_manifest['checkpoints']) == 1, next_manifest
    parsed_copy = next(o['cache'] for o in next_manifest['checkpoints'][0]['outputs'] if o['path'].endswith('/parsed.json'))
    assert json.loads(Path(parsed_copy).read_text())['metrics'] == json.loads((native / 'parsed.json').read_text())['metrics']
    assert all(p.startswith(str(third)) for p in json.loads(Path(parsed_copy).read_text())['artifacts'])
