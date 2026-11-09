"""Storage selection regressions independent of a locally installed browser."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='Node is required for UI helper checks')
SOURCE = Path(__file__).parents[1] / 'src/bactologix/ui/workspace.js'


def evaluate(state, expression):
    source = SOURCE.read_text()
    storage = source[source.index('const storageCategories='):source.index('function resultFileState(')]
    script = '''
const fs=require('fs'); const U=JSON.parse(fs.readFileSync(0,'utf8'));
const val=(id,fallback='')=>U.drafts?.[id]??fallback;
const S={jobs:[],app_update:{}};
const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(action,label)=>`<button data-action="${action}">${e(label)}</button>`;
const icon=()=>''; const badge=x=>e(x);
const heading=(k,t,d)=>`<h1>${e(t)}</h1><p>${e(d)}</p>`;
const notice=(t,d)=>`<p>${e(t)} ${d}</p>`;
const field=()=>''; const select=()=>'';
''' + storage + '\nconsole.log(JSON.stringify(' + expression + '));'
    result = subprocess.run([NODE, '-e', script], input=json.dumps(state), text=True,
                            capture_output=True, check=True)
    return json.loads(result.stdout)


def candidate(kind, identifier, **kwargs):
    return {'kind': kind, 'id': identifier, 'label': identifier, 'paths': [], 'blocked': [], **kwargs}


def test_select_visible_respects_search_category_and_protection():
    items = [candidate('run', 'old-report', paths=['/runs/old']),
             candidate('run', 'current-report', blocked=['Used by another run']),
             candidate('sample_files', 'old-input'),
             candidate('sample_registration', 'old-input')]
    state = {'storageCleanup': {'candidates': items},
             'drafts': {'cleanup-category': 'runs', 'cleanup-search': '/runs/old'},
             'cleanupSelected': ['sample_files:old-input']}
    result = evaluate(state, '{visible:cleanupVisible().map(cleanupKey), selectable:cleanupSelectable().map(cleanupKey), selected:cleanupSelection().map(cleanupKey)}')
    assert result == {'visible': ['run:old-report'], 'selectable': ['run:old-report'],
                      'selected': ['sample_files:old-input']}
    state['drafts'] = {'cleanup-category': 'samples'}
    assert evaluate(state, 'cleanupSelectable().map(cleanupKey)') == ['sample_files:old-input']


def test_dependent_environment_requires_its_installation_selection():
    source = candidate('installation', 'source', paths=['/pipeline'])
    env = candidate('environment', 'env', blocked=['Installation still uses this environment'],
                    conditional_blocked=True, depends_on=[{'kind': 'installation', 'id': 'source'}])
    state = {'storageCleanup': {'candidates': [env, source]},
             'drafts': {'cleanup-category': 'pipelines', 'cleanup-search': 'env'}}
    assert evaluate(state, 'cleanupSelectable().map(cleanupKey)') == []
    state['cleanupSelected'] = ['installation:source']
    assert evaluate(state, 'cleanupSelectable().map(cleanupKey)') == ['environment:env']


def test_unknown_sizes_and_dates_are_not_reported_as_measured_zero():
    state = {'storageCleanup': {'candidates': [candidate('run', 'unknown'),
             candidate('run', 'new', logical_bytes=1024, created_at='2026-11-09T09:00:00Z'),
             candidate('run', 'old', logical_bytes=2048, created_at='2025-01-01T00:00:00Z')]},
             'drafts': {'cleanup-sort': 'oldest'}, 'cleanupSelected': ['run:unknown', 'run:new']}
    assert evaluate(state, 'cleanupVisible().map(c=>c.id)') == ['old', 'new', 'unknown']
    assert evaluate(state, 'cleanupEstimate()') == '1 KiB + unmeasured'


def test_capacity_is_per_filesystem_before_cleanup_and_paths_remain_inspectable():
    counters = dict.fromkeys(['entries','links','special','mounts','inaccessible','depth','hardlinks'], 0)
    scan = {'created_at': '2026-11-09T09:00:00Z', 'elapsed_s': 1, 'include_external': False,
            'logical_bytes': 1024, 'allocated_bytes': 4096, 'allocation_known': True,
            'counters': counters, 'locations': [], 'limits_reached': [], 'active_jobs_at_start': [],
            'devices': [{'device': 'A', 'path': '/workspace', 'total_bytes': 10240, 'available_bytes': 2560},
                        {'device': 'B', 'path': '/external', 'total_bytes': 20480, 'available_bytes': 10240}]}
    state = {'storage': {'last_scan': scan, 'locations': [], 'limits': {'seconds': 30, 'entries': 500, 'depth': 20}},
             'storageCleanup': {'candidates': [candidate('run', 'test', paths=['/full/path/to/run'])]}}
    markup = evaluate(state, 'storagePage()')
    assert markup.index('storage-capacity') < markup.index('storage-cleanup')
    assert 'width:75.00%' in markup and 'width:50.00%' in markup
    assert '/full/path/to/run' in markup
    assert '2.5 KiB' in markup and '10 KiB' in markup
    assert '2026-11-09T09:00:00Z' in markup
