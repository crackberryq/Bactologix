"""Native Snakemake tests use text-only fixtures; no sample analyses/tools."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from bactologix import variants
from bactologix.catalog import PIPELINES
from bactologix.runs import create_run
from bactologix.run_revisions import apply, editor
from bactologix.store import now
from test_native import workspace, sample

WORKFLOW = '''
configfile: "config.yaml"
from pathlib import Path
OUT = str(Path(config['base_dir']) / 'sample')
N = config.get('racon_rounds', 1)
def p(name): return OUT + '/' + name
rule all:
    input: p('report.html')
    default_target: True
rule fastp_trim:
    input: p('raw/illumina/sample_R1.fastq')
    output: p('trimmed.txt')
    shell: "cp {input:q} {output:q}; echo trim >> " + p('executed.txt')
rule kraken2_illumina:
    input: p('trimmed.txt')
    output: p('kraken.txt')
    shell: "cp {input:q} {output:q}; echo kraken >> " + p('executed.txt')
rule kraken_decision:
    input: p('kraken.txt')
    output: p('aware.txt')
    shell: "cp {input:q} {output:q}; echo aware >> " + p('executed.txt')
rule unicycler:
    input: p('aware.txt')
    output: p('assembly.txt')
    shell: "cp {input:q} {output:q}; echo assembly >> " + p('executed.txt')
rule racon_consensus:
    input: lambda w: p('assembly.txt') if int(w.i) == 1 else p('stage_racon' + str(int(w.i)-1) + '.fasta')
    output: p('stage_racon{i}.fasta')
    params: rounds=N
    shell: "cp {input:q} {output:q}; echo round{wildcards.i} >> " + p('executed.txt')
rule final_copy:
    input: p('stage_racon' + str(N) + '.fasta')
    output: p('final.txt')
    shell: "cp {input:q} {output:q}; echo final >> " + p('executed.txt')
rule summary_report:
    input: p('final.txt')
    output: p('report.html')
    shell: "cp {input:q} {output:q}; echo report >> " + p('executed.txt')
'''


def native(run, dry=False):
    if not shutil.which('snakemake'):
        pytest.skip('Snakemake required for native result branching tests')
    command = ['snakemake', '--snakefile', str(Path(run['folder'], 'workflow', run.get('variant_entry', PIPELINES[run['tool']]['snakefile']))),
               '--directory', str(Path(run['folder'], 'workflow')), '--configfile', str(Path(run['folder'], 'config.yaml')), '--cores', '1']
    if run.get("variant_entry"):
        command += ["all"]
    if dry:
        command += ['--dry-run']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def complete(store, run):
    native(run)
    run['completed'] = now()
    store.put('runs', run['id'], run)
    jid = store.submit('workflow', {'run_id': run['id'], 'action': 'run'})
    store.update_job(jid, status='succeeded', finished=now())


@pytest.fixture
def parent(workspace, sample, tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    # Shell commands in this fixture quote output paths, including spaces.
    import shlex
    script = WORKFLOW.replace("p('executed.txt')", "__import__('shlex').quote(p('executed.txt'))")
    (source / 'Hybas.smk').write_text(script)
    (source / 'config.yaml').write_text('{}')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['hybas'], 'commit', commit)
    monkeypatch.setitem(variants.COMMITS, 'hybas', {commit})
    install = {'id': 'fixture', 'tool': 'hybas', 'ready': True, 'source': str(source), 'controller': sys.prefix, 'version': {'commit': commit}}
    workspace.put('installs', 'fixture', install)
    workspace.put('settings', 'active-hybas', 'fixture')
    run = create_run(workspace, 'hybas', sample, 'racon_rounds: 1\n', 1, 1024)
    complete(workspace, run)
    return run


def branch(store, parent, **changes):
    panel = editor(store, parent['id'])
    cfg = yaml.safe_load(parent['config'])
    cfg.update(changes)
    return apply(store, {'id': parent['id'], 'signature': panel['edit_run']['signature'], 'variant': True,
                        'config': {'tool': parent['tool'], 'text': yaml.safe_dump(cfg), 'changes': {}}})


def test_extra_round_reuses_actual_upstream_results(workspace, parent):
    original = {str(p): p.read_bytes() for p in Path(parent['output']).rglob('*') if p.is_file()}
    child = branch(workspace, parent, racon_rounds=2)
    plan = json.loads(Path(child['folder'], 'variant.json').read_text())
    assert {'fastp_trim', 'kraken2_illumina', 'kraken_decision', 'unicycler', 'racon_consensus'} <= {r['rule'] for r in plan['checkpoints']}
    assert 'final_copy' not in {r['rule'] for r in plan['checkpoints']}
    assert not Path(child['output'], 'report.html').exists()
    native(child, dry=True)
    complete(workspace, child)
    executed = Path(child['output'], 'executed.txt').read_text().splitlines()
    assert executed == ['round2', 'final', 'report']
    assert Path(child['output'], 'stage_racon1.fasta').read_bytes() == Path(parent['output'], 'stage_racon1.fasta').read_bytes()
    assert Path(child['output'], 'stage_racon1.fasta').stat().st_ino != Path(parent['output'], 'stage_racon1.fasta').stat().st_ino
    assert all(Path(p).read_bytes() == value for p, value in original.items())
    # A second generation preserves both inherited and newly produced rounds.
    grandchild = branch(workspace, child, racon_rounds=3)
    complete(workspace, grandchild)
    assert Path(grandchild['output'], 'executed.txt').read_text().splitlines() == ['round3', 'final', 'report']


def test_unknown_changes_recompute_without_claiming_reuse(workspace, parent):
    child = branch(workspace, parent, arbitrary_setting='changed')
    assert child['variant']['checkpoints'] == 0
    assert child['variant']['unknown_changes'] == ['arbitrary_setting']
    assert 'variant_entry' not in child


def test_cache_tampering_and_same_stat_input_edits_are_rejected(workspace, parent):
    child = branch(workspace, parent, racon_rounds=2)
    manifest = json.loads(Path(child['folder'], 'variant.json').read_text())
    cache = Path(manifest['checkpoints'][0]['outputs'][0]['cache'])
    original = cache.read_bytes()
    cache.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checkpoint changed'):
        variants.verify(child)
    cache.write_bytes(original)
    source = Path(parent['inputs'][0]['path'])
    st = source.stat()
    source.write_bytes(source.read_bytes().replace(b'A', b'T'))
    import os
    os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns))
    with pytest.raises(ValueError, match='input changed'):
        variants.verify(child)


def test_parent_lock_rejects_submissions_and_releases_after_error(workspace, parent):
    with pytest.raises(RuntimeError):
        with variants.freeze(workspace, parent['id']):
            with pytest.raises(ValueError, match='snapshot'):
                workspace.submit('workflow', {'run_id': parent['id'], 'action': 'run'})
            raise RuntimeError('interrupted copy')
    assert workspace.get('variant_lock', parent['id']) is None


def test_failed_copy_rolls_back_child(workspace, parent, monkeypatch):
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(variants, 'copy_verified', fail)
    with pytest.raises(OSError, match='disk full'):
        branch(workspace, parent, racon_rounds=2)
    assert [r['id'] for r in workspace.all('runs')] == [parent['id']]
    assert [p.name for p in (workspace.home / 'runs').iterdir()] == [parent['id']]
    assert workspace.get('variant_lock', parent['id']) is None


def test_basis_native_cache_is_relocated_and_rechecked(tmp_path):
    import os
    source = Path(os.environ.get('BACTOLOGIX_BASIS_SOURCE', '/missing'))
    if not (source / 'workflow/lib/safety/result_store.py').is_file():
        pytest.skip('Pinned BaSIS source required')
    old, new = tmp_path / 'parent', tmp_path / 'child'
    for root in (old, new):
        shutil.copytree(source / 'workflow', root / 'workflow')
        (root / 'outputs/sample').mkdir(parents=True)
    input_file = tmp_path / 'input.txt'
    input_file.write_text('Synthetic cache input; no sequence data\n')
    program = tmp_path / 'fake-tool'
    program.write_text('Fixture executable identity; never executed\n')
    env = {**os.environ, 'PYTHONPATH': str(old / 'workflow/lib')}
    setup = '''
import json, sys
from pathlib import Path
from safety import result_store as r
root, input_file, program = map(Path, sys.argv[1:])
source = root / 'workflow/lib/safety/native.py'
identity = {'schema': r.INPUT_SCHEMA, 'module': 'minced',
 'inputs': {'assembly': {'path': str(input_file), 'sha256': r.sha256(input_file)}},
 'sources': {str(source): r.sha256(source)}, 'environments': [{'fixture': 'v1'}],
 'executable': {'path': str(program), 'sha256': r.sha256(program)},
 'settings': {'enabled': True}, 'extra_inputs': {}}
output = root / 'outputs/sample/safety'
r.run_cached('minced', identity, output / 'native/minced', output / 'completion/minced.json', output / 'results-cache/minced',
 lambda payload: (payload / 'result.txt').write_text('verified first tool result'), lambda payload: None)
loader_identity = dict(identity, module='rgi', settings={'database': 'fixture'})
r.run_cached('rgi_database', loader_identity, output / 'native/rgi/database', output / 'completion/rgi_database.json', output / 'results-cache/rgi_database',
 lambda payload: (payload / 'db.txt').write_text('fixture database'), lambda payload: None)
analysis_identity = dict(identity, module='rgi', extra_inputs={str(output / 'completion/rgi_database.json'): r.sha256(output / 'completion/rgi_database.json')})
r.run_cached('rgi', analysis_identity, output / 'native/rgi/analysis', output / 'completion/rgi.json', output / 'results-cache/rgi',
 lambda payload: (payload / 'result.txt').write_text('fixture dependent result'), lambda payload: None)
'''
    subprocess.run([sys.executable, '-c', setup, str(old), str(input_file), str(program)], env=env, check=True)
    parent = {'id': 'parent', 'folder': str(old), 'output': str(old / 'outputs/sample'),
              'config': 'modules:\n  minced: {enabled: true}\n  mlst: {enabled: false}\n  rgi: {enabled: true}\n'}
    child = {**parent, 'id': 'child', 'folder': str(new), 'output': str(new / 'outputs/sample'),
             'config': 'modules:\n  minced: {enabled: true}\n  mlst: {enabled: true}\n  rgi: {enabled: true}\n'}
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'parent': parent, 'child': child}))
    helper = Path(variants.__file__).parent / 'data/seed_basis_variant.py'
    originals = {str(p): p.read_bytes() for p in old.rglob('*') if p.is_file() and not p.is_symlink()}
    subprocess.run([sys.executable, str(helper), str(request)], check=True, capture_output=True, text=True)
    imported = json.loads((new / 'variant-imported.json').read_text())
    assert len(imported) == 3
    assert imported[0]['parent_key'] != imported[0]['imported_key']  # paths rebound, bytes unchanged
    assert not (new / 'outputs/sample/safety/completion/minced.json').exists()
    verify = '''
import json, sys
from pathlib import Path
from safety import result_store as r
root = Path(sys.argv[1])
cache, = list((root / 'outputs/sample/safety/results-cache/minced').glob('*/cache.json'))
record = json.loads(cache.read_text())
def should_not_execute(payload): raise AssertionError('The existing analysis repeated')
row, reused = r.run_cached('minced', record['identity'], Path(record['destination']), root / 'outputs/sample/safety/completion/minced.json', cache.parent.parent,
 should_not_execute, lambda payload: None)
assert reused
r.verify_receipt(root / 'outputs/sample/safety/completion/minced.json')
assert (Path(row['payload_root']) / 'result.txt').read_text() == 'verified first tool result'
assert row['bactologix_inheritance']['parent_run'] == 'parent'
# The loader receipt bytes are a dependency of the second cache. Relocation
# must preserve that dependency without executing either original callback.
for module, destination in [('rgi_database', 'native/rgi/database'), ('rgi', 'native/rgi/analysis')]:
 cache2, = list((root / ('outputs/sample/safety/results-cache/' + module)).glob('*/cache.json'))
 imported = json.loads(cache2.read_text())
 if module == 'rgi':
  assert imported['identity']['extra_inputs'] == {str(root / 'outputs/sample/safety/completion/rgi_database.json'): r.sha256(root / 'outputs/sample/safety/completion/rgi_database.json')}
 r._verify_execution_inputs(imported['identity'])
 _, reused2 = r.run_cached(module, imported['identity'], root / 'outputs/sample/safety' / destination,
  root / ('outputs/sample/safety/completion/' + module + '.json'), cache2.parent.parent, should_not_execute, lambda p: None)
 assert reused2
# A changed execution identity cannot hit the imported cache.
identity = dict(record['identity'], settings={'enabled': True, 'new_option': True})
row, reused = r.run_cached('minced', identity, Path(record['destination']), root / 'outputs/sample/safety/completion/minced.json', cache.parent.parent,
 lambda payload: (payload / 'result.txt').write_text('changed tool ran'), lambda payload: None)
assert not reused
'''
    subprocess.run([sys.executable, '-c', verify, str(new)], env={**env, 'PYTHONPATH': str(new / 'workflow/lib')}, check=True)
    assert all(Path(p).read_bytes() == data for p, data in originals.items())
    # Unticking a tool excludes its cache from the new branch.
    third = tmp_path / 'excluded'
    shutil.copytree(source / 'workflow', third / 'workflow')
    excluded = {**child, 'folder': str(third), 'output': str(third / 'outputs/sample'),
                'config': 'modules:\n  minced: {enabled: false}\n  rgi: {enabled: false}\n'}
    request.write_text(json.dumps({'parent': parent, 'child': excluded}))
    subprocess.run([sys.executable, str(helper), str(request)], check=True)
    assert json.loads((third / 'variant-imported.json').read_text()) == []


def test_micpscope_multi_output_checkpoint_reuses_properties(workspace, sample, tmp_path, monkeypatch):
    annotation = Path(sample['path']) / 'annotation'
    annotation.mkdir()
    (annotation / 'sample.faa').write_text('>software_fixture\nAAA\n')
    source = tmp_path / 'micp-source'
    source.mkdir()
    (source / 'config.yaml').write_text('{}')
    (source / 'MICPscope.smk').write_text('''
configfile: "config.yaml"
import shlex
OUT = config['output_dir'] + '/sample/micpscope/'
rule all:
    input: OUT + 'report.html'
    default_target: True
rule protein_properties:
    input: config['samples']
    output: OUT + 'results.json', OUT + 'proteins.tsv'
    shell: "echo fixture > {output[0]:q}; echo fixture > {output[1]:q}; echo properties >> " + shlex.quote(OUT + 'executed.txt')
rule report:
    input: OUT + 'results.json', OUT + 'proteins.tsv'
    output: OUT + 'report.html'
    shell: "cat {input:q} > {output:q}; echo report >> " + shlex.quote(OUT + 'executed.txt')
''')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['micpscope'], 'commit', commit)
    monkeypatch.setitem(variants.COMMITS, 'micpscope', {commit})
    workspace.put('installs', 'micp', {'id': 'micp', 'tool': 'micpscope', 'source': str(source), 'controller': sys.prefix, 'ready': True, 'version': {'commit': commit}})
    workspace.put('settings', 'active-micpscope', 'micp')
    run = create_run(workspace, 'micpscope', sample, '{}', 1, 1024)
    complete(workspace, run)
    child = branch(workspace, run)
    manifest = json.loads(Path(child['folder'], 'variant.json').read_text())
    assert len(manifest['checkpoints']) == 1
    assert len(manifest['checkpoints'][0]['outputs']) == 2
    complete(workspace, child)
    assert Path(child['output'], 'micpscope/executed.txt').read_text().splitlines() == ['report']


def test_scope_and_context_change_invalidation(workspace, parent):
    child = branch(workspace, parent, run_polypolish=True)
    assert not any(e['rule'] == 'final_copy' for e in json.loads(Path(child['folder'], 'variant.json').read_text())['checkpoints'])
    context = variants.shared_context(parent)
    # Installing an additional environment does not alter the captured ones.
    history = Path(parent['rule_cache']) / 'additional/conda-meta/history'
    history.parent.mkdir(parents=True)
    history.write_text('new tool environment\n')
    assert variants.shared_context(parent, context) == context
    # A fresh branch after shared environment changes is conservative.
    next_child = branch(workspace, parent, racon_rounds=2)
    assert next_child['variant']['checkpoints'] == 0
    assert 'shared_environment_or_database_changed_since_parent' in next_child['variant']['unknown_changes']


def test_stale_fork_lock_is_recoverable(workspace, parent):
    workspace.put('variant_lock', parent['id'], {'pid': 99999999, 'process_started': 0})
    with variants.freeze(workspace, parent['id']):
        assert workspace.get('variant_lock', parent['id']).get('path')
    assert workspace.get('variant_lock', parent['id']) is None


def test_post_completion_replacements_are_not_parent_checkpoints(workspace, parent):
    import os
    from datetime import datetime
    future = datetime.fromisoformat(parent['completed']).timestamp() + 60
    root = Path(parent['folder']) / 'workflow/.snakemake/metadata'
    for path in root.rglob('*'):
        if path.is_file():
            row = json.loads(path.read_text())
            if row.get('rule') == 'unicycler':
                row['endtime'] = future
                path.write_text(json.dumps(row))
    output = Path(parent['output'], 'assembly.txt')
    os.utime(output, (future, future))
    child = branch(workspace, parent, racon_rounds=2)
    manifest = json.loads(Path(child['folder'], 'variant.json').read_text())
    assert 'unicycler' not in {row['rule'] for row in manifest['checkpoints']}
