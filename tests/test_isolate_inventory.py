"""An isolate export follows recorded identity, never a matching display label."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from bactologix import isolate_inventory as inventory
from bactologix.store import Store


def file(path, text='fixture'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def sample(store, sid='isolate-7', path=None):
    path = path or store.home.parent / sid
    path.mkdir(parents=True, exist_ok=True)
    row = {'id': sid, 'path': str(path), 'mode': 'hybrid', 'created': '2026-11-09T10:00:00Z'}
    store.put('samples', sid, row)
    return row


def run(store, rid, sample_row, tool='hybas', **extra):
    folder = store.home / 'runs' / rid
    output = folder / 'outputs' / sample_row['id']
    file(folder / 'config.yaml', '{}\n')
    file(output / 'result.txt')
    row = {'id': rid, 'sample': dict(sample_row), 'tool': tool, 'folder': str(folder), 'output': str(output),
           'config': '{}\n', 'inputs': [], 'commit': 'a' * 40, 'parent': None, **extra}
    store.put('runs', rid, row)
    return row


def put_job(store, run_id, kind='workflow', status='succeeded'):
    jid = store.submit(kind, {'run_id': run_id, 'action': 'run'})
    with store.connect() as db:
        db.execute("UPDATE jobs SET status=?,started='2026-11-09T10:00:00Z',finished='2026-11-09T10:01:00Z' WHERE id=?", (status, jid))
    file(store.log_path(jid), run_id + ' job log')
    return jid


def covers(doc, path):
    return [root for root in doc['roots'] if Path(path).is_relative_to(Path(root['source']))]


def test_linked_pipeline_runs_archive_shared_followup_and_same_name_exclusion(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    assembly = run(store, 'run-assembly', original)
    downstream = dict(original, path=assembly['output'])
    screening = run(store, 'run-screening', downstream, 'basis', parent=assembly['id'])
    gene_sample = dict(downstream, micp_handoff={'source_run': assembly['id'], 'source_commit': assembly['commit'], 'files': []})
    genes = run(store, 'run-genes', gene_sample, 'micpscope')
    variant = run(store, 'run-variant', original, parent=assembly['id'], variant={'parent': assembly['id']})
    followup = run(store, 'run-followup', downstream, 'basis', parent=screening['id'], run_type='followup', output=screening['output'])
    store.put('followup_output_owner', screening['id'], followup['id'])
    store.put('run_visibility', variant['id'], {'id': variant['id'], 'archived': True})
    other = dict(original, path=str(tmp_path / 'different-isolate'))
    run(store, 'run-unrelated', other)
    doc = inventory.build(store, original['id'])
    assert {r['id'] for r in doc['runs']} == {assembly['id'], screening['id'], genes['id'], variant['id'], followup['id']}
    assert {'source': assembly['id'], 'target': genes['id'], 'relation': 'handoff'} in doc['edges']
    assert any(r['kind'] == 'run_visibility' and r['body']['archived'] for r in doc['records'])
    assert len(covers(doc, screening['output'])) == 1
    assert not any('run-unrelated' in row['source'] for row in doc['roots'])


def test_metadata_reports_refresh_logs_and_external_inputs_are_complete_and_scoped(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    raw = file(tmp_path / 'elsewhere/reads.fastq')
    analysis = run(store, 'run-one', original, inputs=[{'path': str(raw), 'sha256': 'b' * 64}])
    first = put_job(store, analysis['id'])
    second = put_job(store, analysis['id'])
    unrelated = put_job(store, 'run-other')
    refresh = put_job(store, analysis['id'], 'report_refresh')
    edition = file(store.home / 'report-refreshes' / refresh / 'edition/report.html')
    file(edition.parent.parent / 'source-evidence.json')
    external = file(tmp_path / 'external/report.html')
    store.put('reports', 'external-own', {'id': 'external-own', 'run': analysis['id'], 'sample': original['id'], 'path': str(external)})
    store.put('reports', 'unlinked', {'id': 'unlinked', 'run': 'External report', 'sample': original['id'], 'path': str(tmp_path / 'private.html')})
    store.put('report_refreshes', refresh, {'id': refresh, 'run_id': analysis['id'], 'path': str(edition)})
    store.put('reports', refresh, {'id': refresh, 'run': analysis['id'], 'path': str(edition)})
    store.put('methods_evidence', analysis['id'], {'run_id': analysis['id'], 'job_id': second, 'evidence': {}})
    store.put('methods_attempt_evidence', first, {'run_id': analysis['id'], 'job_id': first, 'evidence': {}})
    store.put('methods_attempt_evidence', 'job-historical', {'run_id': analysis['id'], 'job_id': 'job-historical', 'evidence': {}})
    store.put('report_evidence', second, {'job_id': second, 'run': {'id': analysis['id']}, 'after': {}})
    store.put('result_observations', second, {'run_id': analysis['id'], 'after': {}})
    store.put('job_telemetry', second, {'id': second, 'steps': []})
    store.put('job_usage_history', second, {'id': second, 'samples': []})
    store.put('settings', 'secret', 'not part of sample export')
    store.put('job_telemetry', unrelated, {'id': unrelated, 'private': True})
    doc = inventory.build(store, original['id'])
    assert [job['id'] for job in doc['jobs']] == [refresh, second, first]
    assert {record['kind'] for record in doc['records']} >= {'methods_evidence', 'methods_attempt_evidence', 'report_evidence', 'result_observations', 'report_refreshes', 'job_telemetry', 'job_usage_history'}
    assert not any(row['id'] in {'secret', unrelated, 'unlinked'} for row in doc['records'])
    assert any(row['id'] == 'job-historical' for row in doc['records'])
    for path in (raw, external, edition, edition.parent.parent / 'source-evidence.json', store.log_path(first)):
        assert len(covers(doc, path)) == 1
    assert any('no explicit linked run' in message for message in doc['warnings'])
    assert str(tmp_path / 'private.html') not in json.dumps(doc)


def test_signature_is_stable_and_export_job_or_unrelated_metadata_does_not_change_it(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    analysis = run(store, 'run-one', original)
    before = inventory.build(store, original['id'])['signature']
    assert inventory.build(store, original['id'])['signature'] == before
    # Direct fixture inserts avoid any application-level export queue contract.
    with store.connect() as db:
        db.execute("INSERT INTO jobs(id,kind,payload,status,created) VALUES ('job-export','isolate_export',?,'running','now')",
                   (json.dumps({'run_id': analysis['id'], 'sample_id': original['id']}),))
    store._put('isolate_export_plans', 'one', {'sample_id': original['id']})
    store._put('isolate_export_receipts', 'one', {'complete': True})
    store._put('methods_evidence', 'run-other', {'run_id': 'run-other'})
    assert inventory.build(store, original['id'])['signature'] == before
    store._put('run_visibility', analysis['id'], {'id': analysis['id'], 'archived': True})
    assert inventory.build(store, original['id'])['signature'] != before


def test_missing_files_and_superseded_shared_followup_are_explicit(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    parent = run(store, 'run-parent', original, 'basis')
    old = run(store, 'run-old', original, 'basis', parent=parent['id'], run_type='followup', output=parent['output'])
    newer = run(store, 'run-new', original, 'basis', parent=parent['id'], run_type='followup', output=parent['output'])
    store.put('followup_output_owner', parent['id'], newer['id'])
    missing = tmp_path / 'missing.fastq'
    parent['inputs'] = [{'path': str(missing)}]
    store.put('runs', parent['id'], parent)
    jid = put_job(store, parent['id'])
    store.log_path(jid).unlink()
    doc = inventory.build(store, original['id'])
    assert str(missing) in doc['missing']
    assert str(store.log_path(jid)) in doc['missing']
    assert any(old['id'] in message and 'not retained' in message for message in doc['missing'])


def test_invalid_parent_and_unverified_handoff_do_not_pull_in_different_inputs(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    parent = run(store, 'run-parent', original)
    other = dict(original, path=str(tmp_path / 'another-isolate'))
    run(store, 'run-invalid-parent', other, parent=parent['id'])
    child = dict(original, path=parent['output'], micp_handoff={'source_run': parent['id'], 'source_commit': 'wrong'})
    run(store, 'run-invalid-handoff', child, 'micpscope')
    doc = inventory.build(store, original['id'])
    assert [row['id'] for row in doc['runs']] == [parent['id']]
    assert not doc['edges']
    assert any('Invalid or missing parent' in message for message in doc['warnings'])
    assert any('Unverified GeneScope' in message for message in doc['warnings'])


def test_recorded_aware_bundle_identity_links_derived_run_without_matching_name(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    parent = run(store, 'run-parent', original, aware={'bundle_sha256': 'c' * 64})
    bundle = Path(parent['output']) / 'reports/aware/manifest.json'
    read = file(bundle.parent / 'group/reads.fastq')
    file(bundle)
    derived = {'id': 'derived-1', 'path': str(bundle.parent), 'mode': 'ont_only',
               'manifest_source': {'path': str(bundle), 'sha256': 'c' * 64},
               'aware_provenance': {'bundle_path': str(bundle), 'bundle_sha256': 'c' * 64,
                                    'parent_sample': original['id'], 'group': 'group-1',
                                    'files': [{'path': str(read), 'sha256': 'd' * 64}]}}
    child = run(store, 'run-derived', derived)
    wrong = dict(derived, id='derived-2', aware_provenance={**derived['aware_provenance'], 'bundle_sha256': 'e' * 64})
    run(store, 'run-unverified', wrong)
    doc = inventory.build(store, original['id'])
    assert {row['id'] for row in doc['runs']} == {parent['id'], child['id']}
    assert {'source': parent['id'], 'target': child['id'], 'relation': 'aware_derived'} in doc['edges']
    assert any('run-unverified' in message and 'unverified' in message for message in doc['warnings'])
    # Selecting a derived isolate includes its source history but not siblings.
    store.put('samples', derived['id'], derived)
    sibling = dict(derived, id='derived-3')
    run(store, 'run-sibling', sibling)
    selected = inventory.build(store, derived['id'])
    assert {row['id'] for row in selected['runs']} == {parent['id'], child['id']}


def test_external_native_payload_store_is_isolate_namespaced(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    base = tmp_path / 'native-shared'
    analysis = run(store, 'run-basis', original, 'basis', config='storage:\n  native_results_directory: ' + str(base) + '\n')
    payload = file(base / hashlib.sha256(analysis['output'].encode()).hexdigest() / 'module/results.tsv')
    other = file(base / 'another-isolate/private.tsv')
    doc = inventory.build(store, original['id'])
    assert len(covers(doc, payload)) == 1
    assert not covers(doc, other)
    assert str(base) not in [row['source'] for row in doc['roots']]


def test_broad_workspace_roots_are_refused_and_software_metadata_is_filtered(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store, path=store.home)
    run(store, 'run-one', original, installation='installed-one')
    store.put('installs', 'installed-one', {'id': 'installed-one', 'tool': 'hybas', 'version': {'commit': 'a' * 40},
                                         'controller': str(store.home / 'controllers/one'), 'private_debug': 'omit'})
    doc = inventory.build(store, original['id'])
    assert str(store.home) not in [row['source'] for row in doc['roots']]
    assert str(store.home) in doc['missing']
    assert any('broad workspace' in message for message in doc['warnings'])
    assert 'private_debug' not in json.dumps(doc)
    with pytest.raises(ValueError, match='registered sample'):
        inventory.build(store, 'not-registered')


def test_items_and_jobs_use_one_consistent_database_snapshot(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    analysis = run(store, 'run-one', original)
    changed = json.dumps(dict(original, note='changed after snapshot'))
    job_payload = json.dumps({'run_id': analysis['id'], 'action': 'run'})
    real_loads, written = json.loads, []
    def concurrent_change(text, *args, **kwargs):
        if not written:
            written.append(True)
            with sqlite3.connect(store.home / 'workspace.sqlite3') as db:
                db.execute("UPDATE items SET body=? WHERE kind='samples' AND id=?", (changed, original['id']))
                db.execute("INSERT INTO jobs(id,kind,payload,status,created) VALUES ('job-later','workflow',?,'queued','later')", (job_payload,))
        return real_loads(text, *args, **kwargs)
    monkeypatch.setattr(inventory.json, 'loads', concurrent_change)
    doc = inventory.build(store, original['id'])
    assert 'note' not in doc['sample']
    assert not doc['jobs']
    assert inventory.build(store, original['id'])['sample']['note'] == 'changed after snapshot'


def test_corrupt_unrelated_sample_and_lineage_cycles_do_not_create_false_dag_edges(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store, sid='CON')
    first = run(store, 'run-first', original, parent='run-second')
    run(store, 'run-second', original, parent=first['id'])
    store.put('runs', 'run-bad', {'id': 'run-bad', 'sample': None, 'parent': first['id']})
    doc = inventory.build(store, original['id'])
    assert not doc['edges']
    assert any('Cyclic lineage' in message for message in doc['warnings'])
    assert 'run-bad' not in {row['id'] for row in doc['runs']}
    assert not any(root['relative'] == 'inputs/CON' for root in doc['roots'])


def test_manifest_registration_without_runs_includes_its_explicit_external_files(tmp_path):
    store = Store(tmp_path / 'workspace')
    original = sample(store)
    raw = file(tmp_path / 'separate/read.fastq')
    manifest = file(tmp_path / 'manifest.json')
    original.update(inputs=[{'path': str(raw)}], manifest_source={'path': str(manifest), 'sha256': 'f' * 64})
    store.put('samples', original['id'], original)
    doc = inventory.build(store, original['id'])
    assert not doc['runs']
    assert covers(doc, raw) and covers(doc, manifest)
