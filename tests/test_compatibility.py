"""Exact version support and saved bindings are distinct from runtime readiness."""
import copy
import importlib.metadata
import json
import subprocess

import pytest

from bactologix import compatibility as c
from bactologix.catalog import PIPELINES
from bactologix.store import Store, now
from bactologix.ui_backend import dispatch


def installation(store, iid='install-one', tool='hybas', commit=None, **extra):
    item = {'id': iid, 'tool': tool, 'version': {'commit': commit or PIPELINES[tool]['commit'], 'ref': 'moving-branch'},
            'source': '/not-read/source', 'controller': '/not-probed/runner-' + iid, 'ready': True,
            'checked': '2026-01-02T03:04:05+00:00',
            'controller_requirements': {'python': '3.11', 'snakemake': '9.27.1'}, **extra}
    store.put('installs', iid, item)
    return item


def run(store, item, rid='run-one', **extra):
    value = {'id': rid, 'label': 'saved run', 'tool': item['tool'], 'commit': item['version']['commit'],
             'installation': item['id'], 'controller': item['controller'], 'dry_run_ok': True,
             'validated_controller': item['controller'], 'validated': '2026-01-03T00:00:00+00:00', **extra}
    store.put('runs', rid, value)
    return value


def test_active_selection_does_not_change_earlier_supported_run(tmp_path):
    store = Store(tmp_path)
    old = installation(store, 'old', commit=PIPELINES['hybas']['previous_commits'][0])
    new = installation(store, 'new')
    saved = run(store, old)
    store.put('settings', 'active-hybas', new['id'])
    result = c.panel(store)
    r = result['runs'][0]
    assert r['support'] == 'earlier_supported' and r['binding'] == 'recorded_match'
    assert r['validation'] == 'same_runner_path' and not r['needs_review']
    assert not r['uses_active_selection'] and r['active_installation'] == 'new'
    assert store.get('runs', saved['id']) == saved


@pytest.mark.parametrize('mutation,binding,validation', [
    ('missing', 'missing_installation', 'binding_unavailable'),
    ('commit', 'source_mismatch', 'binding_unavailable'),
    ('tool', 'source_mismatch', 'binding_unavailable'),
    ('not_ready', 'setup_needed', 'binding_unavailable'),
    ('no_runner', 'setup_needed', 'binding_unavailable'),
    ('runner', 'recorded_match', 'runner_changed'),
])
def test_binding_mismatches_are_explicit(tmp_path, mutation, binding, validation):
    store = Store(tmp_path)
    item = installation(store)
    run(store, item)
    if mutation == 'missing':
        with store.connect() as db: db.execute("DELETE FROM items WHERE kind='installs'")
    else:
        if mutation == 'commit': item['version']['commit'] = 'f' * 40
        elif mutation == 'tool': item['tool'] = 'basis'
        elif mutation == 'not_ready': item['ready'] = False
        elif mutation == 'no_runner': item['controller'] = None
        else: item['controller'] = '/replacement/runner'
        store.put('installs', item['id'], item)
    r = c.panel(store)['runs'][0]
    assert r['binding'] == binding and r['validation'] == validation and r['needs_review']


def test_revalidated_replacement_and_missing_legacy_validation(tmp_path):
    store = Store(tmp_path)
    item = installation(store)
    old_path = item['controller']
    item['controller'] = '/replacement'; store.put('installs', item['id'], item)
    run(store, item, controller=old_path)
    r = c.panel(store)['runs'][0]
    assert r['runner_changed_since_draft'] and r['validation'] == 'same_runner_path'
    assert not r['needs_review']
    run(store, item, dry_run_ok=False)
    assert c.panel(store)['runs'][0]['validation'] == 'not_recorded'
    legacy = run(store, item); legacy.pop('validated_controller'); legacy.pop('validated')
    store.put('runs', legacy['id'], legacy)
    r = c.panel(store)['runs'][0]
    assert r['validation'] == 'same_runner_path' and r['validated_at'] is None


def test_moving_branch_labels_never_grant_support_and_catalogue_is_dated(tmp_path):
    store = Store(tmp_path)
    item = installation(store, commit='f' * 40)
    run(store, item)
    store.put('settings', 'active-hybas', 'missing-selection')
    store.put('catalog', 'hybas', {'live': True, 'checkedAt': '2025-01-01', 'versions': [
        {'id': 'branch:moving', 'kind': 'branch', 'ref': PIPELINES['hybas']['preferred'], 'commit': 'f' * 40}]})
    d = c.panel(store)
    p = next(p for p in d['pipelines'] if p['tool'] == 'hybas')
    assert p['catalog_checked_at'] == '2025-01-01' and p['catalog_origin'] == 'Saved GitHub response'
    assert p['unreviewed_catalog_refs'] == 1 and not p['active_registered']
    assert p['commits'][0]['commit'] == PIPELINES['hybas']['commit']
    assert d['installations'][0]['support'] == d['runs'][0]['support'] == 'unreviewed'
    assert d['runs'][0]['needs_review']


def test_recorded_checks_missing_requirements_and_failed_execution_are_separate(tmp_path):
    store = Store(tmp_path)
    item = installation(store, controller_requirements={})
    saved = run(store, item)
    check = store.submit('check', {'installation': item['id']})
    store.update_job(check, status='failed', finished=now())
    execution = store.submit('workflow', {'run_id': saved['id'], 'action': 'run'})
    store.update_job(execution, status='failed')
    store.put('run_visibility', saved['id'], {'id': saved['id'], 'archived': True})
    d = c.panel(store)
    i, r = d['installations'][0], d['runs'][0]
    assert i['requirements'] == {'python': None, 'snakemake': None}
    assert i['checked_at'] == item['checked'] and i['latest_check']['status'] == 'failed'
    assert r['archived'] and r['latest_execution']['status'] == 'failed'
    assert r['binding'] == 'recorded_match'  # Stored flags are shown, never silently rewritten.
    assert 'stored records, not fresh package probes' in d['notice']


def test_overview_does_not_probe_fetch_queue_or_rewrite(tmp_path, monkeypatch):
    store = Store(tmp_path)
    item = installation(store)
    saved = run(store, item)
    before = copy.deepcopy((store.all('installs'), store.all('runs')))
    def forbidden(*args, **kwargs): raise AssertionError('unexpected external operation')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr('bactologix.catalog.api', forbidden)
    monkeypatch.setattr('bactologix.ui_backend.enqueue', forbidden)
    d = dispatch(store, 'inspect_compatibility', {})['compatibility']
    response = dispatch(store, 'download_compatibility', {})
    exported = json.loads(response['download']['text'])
    assert exported == response['compatibility']
    assert exported['runs'] == d['runs'] and exported['installations'] == d['installations']
    assert (store.all('installs'), store.all('runs')) == before and not store.jobs()
    assert d['runs'][0]['id'] == saved['id']


def test_missing_application_metadata_and_runtime_requirement_are_explicit(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(c.updates, 'status', lambda _: {'restart_required': False, 'active_job': None})
    def missing(_): raise importlib.metadata.PackageNotFoundError('fixture')
    monkeypatch.setattr(c.importlib.metadata, 'metadata', missing)
    app = c.panel(store)['app']
    assert app['installed_version'] is None and app['python_matches'] is None
    monkeypatch.setattr(c.importlib.metadata, 'metadata', lambda _: {'Version': '99.0', 'Requires-Python': '>=99'})
    assert c.panel(store)['app']['python_matches'] is False
    assert c.panel(store)['app']['restart_required']
    monkeypatch.setattr(c.importlib.metadata, 'metadata', lambda _: {'Version': '99.0', 'Requires-Python': 'invalid'})
    assert c.panel(store)['app']['python_matches'] is None


def test_unknown_and_missing_commit_are_not_supported():
    assert c.source_state('unknown', 'f' * 40) == 'unknown_pipeline'
    assert c.source_state('hybas', None) == 'missing_commit'
