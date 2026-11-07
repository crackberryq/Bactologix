"""Application boundary checks; upstream biological tools are not executed."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from bactologix.catalog import PIPELINES
from bactologix.followup import create_followup
from bactologix.runs import create_run, command, reports, template, tree_digest, validate_launch
from bactologix.store import Store
from bactologix.ui_backend import dispatch, snapshot
from test_run_integrity import perform
from bactologix import variants
from bactologix.followup import source_options
from test_release_contract import review


@pytest.fixture
def screening(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('BaSIS.smk', 'Followup.smk'):
        (source / name).write_text('rule all:\n    input: []\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setitem(PIPELINES['basis'], 'commit', commit)
    monkeypatch.setitem(PIPELINES['basis'], 'followup_commits', [commit])
    installed = {'id': 'fixture', 'tool': 'basis', 'ready': True, 'controller': '/fixture/controller',
                 'source': str(source), 'version': {'commit': commit, 'ref': 'fixture'}}
    store.put('installs', 'fixture', installed)
    store.put('settings', 'active-basis', 'fixture')
    folder = tmp_path / 'sample'
    (folder / 'annotation').mkdir(parents=True)
    for ext in ('fna', 'faa', 'gff'):
        (folder / 'annotation' / ('sample.' + ext)).write_text('Software-only placeholder\n')
    sample = {'id': 'sample', 'path': str(folder), 'mode': 'illumina_only'}
    parent = create_run(store, 'basis', sample, template('basis'))
    parent.update(completed='fixture-completed', dry_run_ok=True)
    store.put('runs', parent['id'], parent)
    safety = Path(parent['output']) / 'safety'
    for rel in ('metadata/sample.json', 'metadata/config.json', 'metadata/validation.json',
                'metadata/setup.ready.json', 'metadata/report-navigation.json', 'evidence/minced.json'):
        path = safety / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n')
    (safety / 'report.html').write_text('<p>Software screening fixture</p>')
    (safety / 'evidence.json').write_text(json.dumps({'run': {'evidence_sources': [{'path': str(safety / 'evidence/minced.json')} ]}}))
    monkeypatch.setattr('bactologix.engine.find_conda', lambda s: '/fixture/conda')
    return store, parent


def test_followup_requires_explicit_review_and_opt_in(screening):
    store, parent = screening
    with pytest.raises(ValueError, match='Review'):
        create_followup(store, parent['id'], 'deep: {enabled: true}')
    with pytest.raises(ValueError, match='Explicitly enable'):
        create_followup(store, parent['id'], 'deep: {enabled: false}', True)
    with pytest.raises(ValueError, match='only deep'):
        create_followup(store, parent['id'], 'deep: {enabled: true}\nisolate: /other', True)
    with pytest.raises(ValueError, match='receipt is missing'):
        create_followup(store, parent['id'], 'deep: {enabled: true}\nresource_receipts: [/missing-fixture]', True)
    assert len(store.all('runs')) == 1


def test_followup_uses_frozen_saved_screening_without_recreating_analysis(screening):
    store, parent = screening
    safety = Path(parent['output']) / 'safety'
    before = {str(p): p.read_bytes() for p in safety.rglob('*') if p.is_file()}
    child = create_followup(store, parent['id'], 'deep: {enabled: true}', True)
    assert {str(p): p.read_bytes() for p in safety.rglob('*') if p.is_file()} == before
    assert child['output'] == parent['output'] and child['parent'] == parent['id']
    assert not child['dry_run_ok'] and 'completed' not in child
    cfg = yaml.safe_load(child['config'])
    assert cfg['isolate'] == parent['output'] and 'samples' not in cfg
    argv = command(store, child, 'dry-run')
    assert Path(argv[argv.index('--snakefile') + 1]).name == 'Followup.smk'
    assert '--dry-run' in argv
    validate_launch(store, child['id'], 'dry-run')
    (safety / 'evidence/minced.json').write_text('{"changed":true}')
    with pytest.raises(ValueError, match='screening prerequisite changed'):
        validate_launch(store, child['id'], 'dry-run')


@pytest.fixture(params=[False, True], ids=['native-cache', 'saved-result-wrapper'])
def screening_variant(screening, request):
    store, parent = screening
    folder = Path(parent['folder'])
    manifest = {'schema': 1, 'parent': 'earlier-screening', 'checkpoints': [],
                'inputs': {row['path']: variants.sha(row['path']) for row in parent['inputs']},
                'input_metadata': {row['path']: variants.metadata(row['path']) for row in parent['inputs']}}
    variants.write(folder / 'variant.json', manifest)
    parent['variant'] = {'schema': 1, 'parent': 'earlier-screening', 'checkpoints': 0,
                         'changed': [], 'unknown_changes': [], 'mode': 'native_cache', 'items': []}
    parent['variant_manifest_sha256'] = variants.sha(folder / 'variant.json')
    if request.param:
        wrapper = folder / 'workflow/BactologixVariant.smk'
        wrapper.write_text('raise RuntimeError("Screening wrapper must not run during follow-up")\n')
        restore = folder / 'restore_variant.py'
        restore.write_text('raise RuntimeError("Screening restore must not run during follow-up")\n')
        parent['variant_entry'] = wrapper.name
        parent['variant_restore_sha256'] = variants.sha(restore)
        parent['workflow_sha256'] = tree_digest(folder / 'workflow')
    store.put('runs', parent['id'], parent)
    return store, parent


def test_followup_from_completed_variant_is_an_independent_followup(screening_variant):
    store, parent = screening_variant
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in Path(parent['folder']).rglob('*') if p.is_file() and not p.is_symlink()}
    saved_parent = store.get('runs', parent['id'])
    child = create_followup(store, parent['id'], 'deep: {enabled: true}', True)
    validate_launch(store, child['id'], 'dry-run')
    argv = command(store, child, 'dry-run')
    assert Path(argv[argv.index('--snakefile') + 1]).name == 'Followup.smk'
    assert child['parent'] == parent['id'] and child['output'] == parent['output']
    assert not any(key in child for key in ('variant', 'variant_entry', 'variant_manifest_sha256',
                                           'variant_restore_sha256'))
    assert child['followup_sources'] and not child['dry_run_ok']
    assert store.get('runs', parent['id']) == saved_parent
    assert {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in Path(parent['folder']).rglob('*') if p.is_file() and not p.is_symlink()} == before
    evidence = Path(parent['output']) / 'safety/evidence/minced.json'
    evidence.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='screening prerequisite changed'):
        validate_launch(store, child['id'], 'dry-run')


def test_followup_checks_parent_variant_content_before_creating_child(screening_variant):
    store, parent = screening_variant
    path = Path(parent['inputs'][0]['path'])
    original, stat = path.read_bytes(), path.stat()
    path.write_bytes(b'X' + original[1:])
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match='variant input changed'):
        create_followup(store, parent['id'], 'deep: {enabled: true}', True)
    assert len(store.all('runs')) == 1
    assert [p.name for p in (store.home / 'runs').iterdir()] == [parent['id']]


def test_only_latest_followup_attempt_displays_shared_report(screening):
    store, parent = screening
    first = create_followup(store, parent['id'], 'deep: {enabled: true}', True)
    second = create_followup(store, parent['id'], 'deep: {enabled: true}', True)
    for run in (first, second):
        perform(store, run, 'dry-run')
    perform(store, first, 'run')
    report = Path(parent['output']) / 'safety/deepsafe-report/report.html'
    report.parent.mkdir()
    report.write_text('<p>Software-only follow-up report</p>')
    assert [r['run'] for r in reports(store) if r['name'] == 'Follow-up report'] == [first['id']]
    perform(store, second, 'run', fail=True)
    displayed = [r for r in reports(store) if r['name'] == 'Follow-up report']
    assert len(displayed) == 1 and displayed[0]['run'] == second['id']
    assert displayed[0]['run_status'] == 'failed'


def test_ui_followup_available_only_for_completed_current_screening(screening):
    store, parent = screening
    assert snapshot(store)['runs'][0]['can_followup']
    dispatch(store, 'create_followup', {'id': parent['id'], 'text': 'deep: {enabled: true}', 'reviewed': True})
    child = store.all('runs')[0]
    assert child['run_type'] == 'followup'
    assert not snapshot(store)['runs'][0]['can_followup']
    with pytest.raises(ValueError, match='new reviewed follow-up'):
        dispatch(store, 'revise_run', {'id': child['id'], 'text': child['submitted']})
    jid = store.submit('workflow', {'run_id': parent['id'], 'action': 'run'})
    assert not next(r for r in snapshot(store)['runs'] if r['id'] == parent['id'])['can_followup']
    with pytest.raises(ValueError, match='Complete screening'):
        create_followup(store, parent['id'], 'deep: {enabled: true}', True)


def test_bundled_screening_default_does_not_override_saved_preferences(tmp_path):
    store = Store(tmp_path)
    assert yaml.safe_load(template('basis'))['deep']['enabled'] is False
    store.put('configs', 'basis', 'deep: {enabled: true}\n')
    assert yaml.safe_load(snapshot(store)['configs']['basis'])['deep']['enabled'] is True


def reviewed_installation(store, parent, source):
    """An independently reviewed future source; never add it to the app allowlist."""
    source.mkdir()
    files = {'BaSIS.smk': 'rule all:\n    input: []\n',
             'Followup.smk': '# New reviewed follow-up\nrule all:\n    input: []\n',
             'environment.yml': 'dependencies: [python=3.11, snakemake=9.6.2]\n'}
    contract = review()
    for name, value in files.items():
        (source / name).write_text(value)
        contract['files_sha256'][name] = hashlib.sha256(value.encode()).hexdigest()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                    'commit', '-qm', 'reviewed future source'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    contract.update(commit=commit, followup={'interface': 1, 'entry_point': 'Followup.smk',
        'screening_schema': 'basis-snakemake-run-v1', 'parent_commits': [parent['commit']],
        'scopes': ['amrfinder-review', 'all-supported']})
    store.put('release_approvals', 'basis:' + commit, contract)
    installed = {'id': 'reviewed-new', 'tool': 'basis', 'ready': True, 'controller': '/new/controller',
                 'source': str(source), 'rule_cache': str(store.home / 'envs/new-cache'),
                 'version': {'commit': commit, 'ref': 'v99.0.0'}}
    store.put('installs', installed['id'], installed)
    return installed, contract


def test_explicit_new_source_preserves_screening_and_records_transition(screening, tmp_path):
    from bactologix.methods import build
    store, parent = screening
    installed, contract = reviewed_installation(store, parent, tmp_path / 'reviewed source')
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in Path(parent['folder']).rglob('*')
              if p.is_file() and not p.is_symlink()}
    assert [r['id'] for r in source_options(store, parent)] == ['screening', installed['id']]
    child = create_followup(store, parent['id'], 'deep: {enabled: true, maximum_candidates: 5}',
                            True, installed['id'], 'amrfinder-review')
    assert child['commit'] == installed['version']['commit'] != parent['commit']
    assert child['installation'] == installed['id']
    assert child['controller'] == installed['controller'] and child['rule_cache'] == installed['rule_cache']
    transition = child['followup_transition']
    assert transition['screening_commit'] == parent['commit']
    assert transition['release_contract'] == contract and transition['scope'] == 'amrfinder-review'
    assert yaml.safe_load(child['config'])['deep']['aware'] == {'enabled': True, 'scope': 'amrfinder-review'}
    assert len(store.jobs()) == 0
    validate_launch(store, child['id'], 'dry-run')
    assert {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in Path(parent['folder']).rglob('*')
            if p.is_file() and not p.is_symlink()} == before
    assert store.get('runs', parent['id']) == parent
    doc = build(store, child)
    assert doc['followup_transition'] == transition
    assert all(value in '\n'.join(doc['paragraphs']) for value in (parent['commit'], child['commit'], 'amrfinder-review'))
    (Path(child['folder']) / 'followup-transition.json').write_text('{}')
    with pytest.raises(ValueError, match='transition changed'):
        validate_launch(store, child['id'], 'dry-run')


@pytest.mark.parametrize('failure', ['unreviewed', 'wrong-parent', 'incomplete', 'modified', 'wrong-commit'])
def test_invalid_new_sources_fail_without_drafts(screening, tmp_path, failure):
    store, parent = screening
    installed, contract = reviewed_installation(store, parent, tmp_path / 'source new')
    if failure == 'unreviewed':
        contract.pop('followup')
    elif failure == 'wrong-parent':
        contract['followup']['parent_commits'] = []
    elif failure == 'incomplete':
        installed.update(setup_mode='first_install', setup_completed=False)
    elif failure == 'modified':
        (Path(installed['source']) / 'Followup.smk').write_text('changed')
    else:
        installed['version']['commit'] = 'e' * 40
    store.put('release_approvals', 'basis:' + contract['commit'], contract)
    store.put('installs', installed['id'], installed)
    with pytest.raises(ValueError, match='reviewed|changed'):
        create_followup(store, parent['id'], 'deep: {enabled: true}', True, installed['id'], 'amrfinder-review')
    assert len(store.all('runs')) == 1


def test_new_scope_cannot_silently_run_on_old_source(screening, tmp_path):
    store, parent = screening
    with pytest.raises(ValueError, match='does not support'):
        create_followup(store, parent['id'], 'deep: {enabled: true}', True, scope='amrfinder-review')
    installed, _ = reviewed_installation(store, parent, tmp_path / 'new')
    for text in ('deep: {enabled: true, aware: {enabled: false}}',
                 'deep: {enabled: true, aware: {scope: all-supported}}'):
        with pytest.raises(ValueError, match='Enable AWARE|conflicts'):
            create_followup(store, parent['id'], text, True, installed['id'], 'amrfinder-review')
    original = create_followup(store, parent['id'], 'deep: {enabled: true}', True)
    assert original['commit'] == parent['commit']
    assert original['followup_transition']['selection'] == 'screening'
