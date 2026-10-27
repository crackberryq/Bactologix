import json
import pytest
from bactologix.store import Store
from bactologix.support import build_snapshot, redact, MAX_LOG_BYTES
from bactologix.ui_backend import dispatch


def test_default_excludes_sensitive_data(tmp_path):
    s = Store(tmp_path)
    j = s.submit('install', {'password': 'payload-secret'})
    s.update_job(j, error='private error')
    s.log_path(j).write_text('secret log')
    before = s.job(j)
    out = json.loads(build_snapshot(s, [j]))
    assert 'error' not in out['jobs'][0] and 'log_excerpt' not in out['jobs'][0]
    assert 'payload-secret' not in json.dumps(out)
    assert s.job(j) == before


def test_redaction_and_tail(tmp_path):
    s = Store(tmp_path)
    s.put('samples','PATIENT42',{'id':'PATIENT42'})
    j = s.submit('smoke', {})
    s.log_path(j).write_text('x' * MAX_LOG_BYTES + '\nPATIENT42 /home/alice/private/file password=secret\nhttps://user:pass@example.com?q=secret\nalice@example.com\nERROR: missing executable')
    out = json.loads(build_snapshot(s,[j],True))['jobs'][0]
    assert out['log_truncated']
    for secret in ['PATIENT42','alice','password=secret','user:pass','q=secret']:
        assert secret not in out['log_excerpt']
    assert 'missing executable' in out['log_excerpt']


def test_symlink_refused(tmp_path):
    s = Store(tmp_path/'workspace')
    j=s.submit('smoke',{})
    outside=tmp_path/'private';outside.write_text('outside-secret')
    s.log_path(j).symlink_to(outside)
    out=json.loads(build_snapshot(s,[j],True))
    assert out['jobs'][0]['log_unavailable']
    assert 'outside-secret' not in json.dumps(out)


def test_directory_symlink_refused(tmp_path):
    s=Store(tmp_path/'workspace');j=s.submit('smoke',{})
    (s.home/'logs').rmdir()
    outside=tmp_path/'outside';outside.mkdir()
    (outside/(j+'.log')).write_text('outside-secret')
    (s.home/'logs').symlink_to(outside, target_is_directory=True)
    assert json.loads(build_snapshot(s,[j],True))['jobs'][0]['log_unavailable']


def test_validation_and_readonly_dispatch(tmp_path, monkeypatch):
    s=Store(tmp_path)
    for ids in ['job-fake', ['x']*6, [1], ['missing']]:
        with pytest.raises(ValueError):build_snapshot(s,ids)
    with pytest.raises(ValueError):build_snapshot(s,[], 'true')
    monkeypatch.setattr('bactologix.ui_backend.updates.assert_current',lambda s:pytest.fail('read-only export blocked'))
    result=dispatch(s,'support_preview',{})
    assert result['keep_modal'] and json.loads(result['support_preview'])['schema']=='bactologix-support/1'


def test_credentials():
    text='Authorization: Bearer secret\napi_key: abc\n-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----\nC:\\Users\\alice\\secret.txt'
    out=redact(text)
    assert all(s not in out for s in ['Bearer','abc','private','alice'])
