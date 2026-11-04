"""Software-only coordinated update contracts; no real downloads or installs."""
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest
from bactologix import __version__, update_all, restart
from bactologix.engine import Context
from bactologix.store import Store
from bactologix.ui_backend import dispatch


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path)
    jid = store.submit('app_update', {'update_all': True, 'release': {'version': __version__}})
    ctx = Context(store, store.job(jid))
    store.put('settings', 'all-update', {'job': jid, 'status': 'running',
        'components': {'application': {'status': 'current'}}})
    yield ctx
    ctx.log.close()


def release(commit='a'*40, date='2026-11-04', kind='release', ref='v2'):
    return {'commit': commit, 'publishedAt': date, 'kind': kind, 'ref': ref}


def setup(monkeypatch, ctx, *, failure=None):
    monkeypatch.setattr(update_all, 'PIPELINES', {'hybas': {'name': 'HybAs'}, 'basis': {'name': 'BaSIS'}})
    monkeypatch.setattr(update_all.release_contract, 'refresh', lambda *a: {'versions': [release(), release('b'*40, '2026-11-05', 'branch')]})
    monkeypatch.setattr(update_all, 'supported', lambda *a: True)
    installed = []
    def install(c):
        tool = c.job['payload']['tool']; installed.append(tool)
        key = tool + '-' + 'a'*40
        c.store.put('installs', key, {'id': key, 'tool': tool, 'ready': True})
        c.store.put('settings', 'active-' + tool, key)
    monkeypatch.setattr(update_all, 'install', install)
    monkeypatch.setattr(update_all.maintenance, 'recipes', lambda r: [{'id': 'test'}])
    monkeypatch.setattr(update_all.maintenance, 'environment_request', lambda s,k,ids,action: {'tool': k.split('-')[0], 'action': action})
    def environments(c):
        if c.job['payload']['tool'] == failure:
            raise RuntimeError('synthetic environment failure')
    monkeypatch.setattr(update_all.maintenance, 'execute_environments', environments)
    return installed


def test_all_pipelines_include_missing_installations_preserve_runs(context, monkeypatch):
    ctx = context; installed = setup(monkeypatch, ctx)
    ctx.store.put('runs', 'frozen', {'source': 'old', 'config': 'preserve'})
    update_all.pipeline_updates(ctx)
    assert installed == ['hybas', 'basis']
    assert ctx.store.get('settings', 'all-update')['status'] == 'complete'
    assert ctx.store.get('runs', 'frozen') == {'source': 'old', 'config': 'preserve'}
    assert ctx.job['payload']['update_all'] is True


def test_partial_failure_keeps_previous_active_and_continues(context, monkeypatch):
    ctx = context; installed = setup(monkeypatch, ctx, failure='hybas')
    ctx.store.put('settings', 'active-hybas', 'previous')
    with pytest.raises(RuntimeError, match='hybas'):
        update_all.pipeline_updates(ctx)
    assert installed == ['hybas', 'basis']
    assert ctx.store.get('settings', 'active-hybas') == 'previous'
    state = ctx.store.get('settings', 'all-update')
    assert state['status'] == 'partial'
    assert state['components']['basis']['status'] == 'ready'
    assert state['components']['hybas']['error'] == 'synthetic environment failure'


def test_newest_unreviewed_release_never_silently_falls_back(context, monkeypatch):
    ctx = context; installed = setup(monkeypatch, ctx)
    monkeypatch.setattr(update_all, 'supported', lambda *a: False)
    with pytest.raises(RuntimeError): update_all.pipeline_updates(ctx)
    assert not installed
    assert 'compatible reviewed runner' in ctx.store.get('settings', 'all-update')['components']['basis']['error']


def test_network_failure_is_not_already_current(context, monkeypatch):
    ctx = context; setup(monkeypatch, ctx)
    monkeypatch.setattr(update_all.release_contract, 'refresh', Mock(side_effect=OSError('offline')))
    with pytest.raises(RuntimeError): update_all.pipeline_updates(ctx)
    assert ctx.store.get('settings', 'all-update')['status'] == 'partial'


@pytest.mark.parametrize('newer', [False, True])
def test_fresh_process_after_app_update_and_same_version_retry(context, monkeypatch, newer):
    ctx = context; ctx.lock_fds = (41, 42)
    version = '99.0.0' if newer else __version__
    monkeypatch.setattr(update_all, 'latest_application', lambda s: {'version': version})
    installer = Mock(); monkeypatch.setattr(update_all.updates, 'install_update', installer)
    calls = []
    def child(argv, **kw):
        calls.append((argv,kw))
        state=ctx.store.get('settings','all-update');state['status']='complete';ctx.store.put('settings','all-update',state)
    monkeypatch.setattr(update_all.subprocess, 'run', child)
    update_all.execute(ctx)
    assert installer.call_count == int(newer)
    assert calls[0][0][:3] == [sys.executable, '-m', 'bactologix.update_all']
    assert calls[0][1]['pass_fds'] == (41,42)
    assert ctx.job['payload']['update_all']


def test_failed_wheel_never_runs_pipeline_helper(context, monkeypatch):
    ctx = context
    monkeypatch.setattr(update_all, 'latest_application', lambda s: {'version':'99.0.0'})
    monkeypatch.setattr(update_all.updates, 'install_update', Mock(side_effect=ValueError('bad wheel')))
    child=Mock();monkeypatch.setattr(update_all.subprocess,'run',child)
    with pytest.raises(ValueError): update_all.execute(ctx)
    child.assert_not_called()
    assert ctx.store.get('settings','all-update')['components']['application']['status']=='failed'


def test_latest_application_does_not_skip_unavailable_newest(context, monkeypatch):
    monkeypatch.setattr(update_all.updates, 'fetch_releases', lambda: {'releases':[
        {'version':'99.0.0','available':False}, {'version':'98.0.0','available':True}]})
    with pytest.raises(ValueError, match='latest'): update_all.latest_application(context.store)


def test_verified_app_restarts_after_partial_pipeline_failure(context):
    ctx=context;item=restart.register(ctx.store,'http://127.0.0.1:8501')
    ctx.store.update_job(ctx.job['id'],status='failed')
    state={'job':ctx.job['id'],'status':'installed','version':__version__, 'environment':sys.prefix,
           'launcher_id':item['id'],'pip_started_at':time.time()}
    ctx.store.put('settings','app-update',state)
    partial=ctx.store.get('settings','all-update');partial.update(status='partial',components={'application':{'status':'updated'}})
    ctx.store.put('settings','all-update',partial)
    assert restart.pending(ctx.store,item)==state
    state['status']='failed';ctx.store.put('settings','app-update',state)
    assert restart.pending(ctx.store,item) is None


def test_dispatch_uses_server_release_not_browser_payload(tmp_path, monkeypatch):
    store=Store(tmp_path);selected={'version':__version__,'available':True}
    monkeypatch.setattr(update_all,'latest_application',lambda s:selected)
    monkeypatch.setattr('bactologix.updates.assert_current',lambda s:None)
    monkeypatch.setattr('bactologix.updates.status',lambda s:{'active_job':None})
    enqueue=Mock(return_value='job-test');monkeypatch.setattr('bactologix.ui_backend.enqueue',enqueue)
    result=dispatch(store,'update_all',{'release':{'version':'malicious'},'tools':[]})
    assert result['navigate']=='jobs'
    assert enqueue.call_args.args[2]['update_all'] is True
    assert enqueue.call_args.args[2]['release']==selected


def test_reviewed_newer_snapshot_not_downgraded_to_historical_tag(context, monkeypatch):
    ctx=context;installed=setup(monkeypatch,ctx)
    monkeypatch.setattr(update_all,'PIPELINES',{'hybas':{'name':'HybAs','preferred':'HybAs_v9.0.0-AWARE','commit':'a'*40}})
    monkeypatch.setattr(update_all.release_contract,'refresh',lambda *a:{'versions':[release('b'*40,ref='HybAs-AWARE-V8.8.0')]})
    monkeypatch.setattr(update_all,'supported',lambda t,c,s:c=='a'*40)
    update_all.pipeline_updates(ctx)
    assert installed==['hybas']
    assert ctx.store.get('settings','all-update')['components']['hybas']['commit']=='a'*40
