"""Exercise the real Streamlit trigger protocol and its backend boundary."""
import base64
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.proto.WidgetStates_pb2 import WidgetStates
from streamlit.components.v2.bidi_component.main import _make_trigger_id
from bactologix import __file__ as PACKAGE_FILE
from bactologix.store import Store
from bactologix.ui_backend import dispatch, handle_request, snapshot

APP = Path(PACKAGE_FILE).with_name("app.py")


def send(app, kind, payload=None, request_id="request-1"):
    state = WidgetStates()
    event = state.widgets.add()
    event.id = _make_trigger_id(app.get("bidi_component")[0].proto.id, "events")
    event.json_trigger_value = json.dumps({"event": "action", "value": {"id": request_id, "kind": kind, "payload": payload or {}}})
    app._run(state, timeout=20)
    assert not app.exception, [e.value for e in app.exception]
    return app.session_state["ui-response"]


def test_html_component_mount_and_config_trigger(tmp_path, monkeypatch):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(tmp_path))
    app = AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception
    component = app.get("bidi_component")[0].proto
    assert 'class="app-shell"' in component.html_content
    assert "Pick up where you left off." in component.js_content
    result = send(app, "save_config", {"tool": "hybas", "text": "custom: {retain: [1, true]}\nthreads: 2\n"})
    assert result["ok"]
    assert "retain" in Store(tmp_path).get("configs", "hybas")
    result = send(app, "edit_setting", {"tool": "hybas", "text": Store(tmp_path).get("configs", "hybas"), "field": "threads", "value": "6"}, "request-2")
    assert result["ok"] and "threads: 6" in result["config"]["text"]
    assert "retain" in result["config"]["text"]


def test_rerun_does_not_duplicate_job(tmp_path, monkeypatch):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(tmp_path))
    queued = []
    monkeypatch.setattr("bactologix.ui_backend.enqueue", lambda s, k, p: queued.append(k) or "job-test")
    app = AppTest.from_file(str(APP)).run(timeout=20)
    assert send(app, "smoke")["ok"]
    assert send(app, "smoke")["ok"]
    app.run()
    assert queued == ["smoke"]


def test_failed_action_is_returned_to_component(tmp_path, monkeypatch):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(tmp_path))
    app = AppTest.from_file(str(APP)).run(timeout=20)
    result = send(app, "add_sample", {"id": "test", "path": str(tmp_path / "missing"), "mode": "hybrid"})
    assert result["ok"] is False and result["message"]
    assert not Store(tmp_path).all("samples")


def test_reports_are_data_and_registered_only(tmp_path):
    store = Store(tmp_path)
    source = tmp_path / "example.html"
    content = b'<h1>Report test</h1><script>parent.document.title="bad"</script>'
    source.write_bytes(content)
    dispatch(store, "register_report", {"path": str(source), "name": "Report test"})
    report = snapshot(store)["reports"][0]
    result = dispatch(store, "open_report", {"id": report["id"]})
    assert base64.b64decode(result["report"]["base64"]) == content
    with pytest.raises(ValueError, match="no longer exists"):
        dispatch(store, "open_report", {"id": str(source)})
    js = (APP.parent / "ui/workspace.js").read_text()
    assert "f.setAttribute('sandbox','allow-scripts allow-downloads allow-modals')" in js
    assert "allow-same-origin" not in js
    assert 'e(r.path)' in js


def test_install_selection_is_resolved_on_server(tmp_path, monkeypatch):
    store = Store(tmp_path)
    catalog = snapshot(store)["catalogs"]["basis"]["versions"]
    selected = next(v for v in catalog if v["supported"])
    captured = []
    monkeypatch.setattr("bactologix.ui_backend.enqueue", lambda s,k,p: captured.append(p) or "job-install")
    with pytest.raises(ValueError, match="Version changed"):
        dispatch(store, "install", {"tool": "basis", "version_id": selected["id"], "commit": "0" * 40})
    dispatch(store, "install", {"tool": "basis", "version_id": selected["id"], "commit": selected["commit"], "version": {"commit": "injected"}})
    assert captured[0]["version"]["commit"] == selected["commit"]


def test_unknown_command_is_rejected(tmp_path):
    result = handle_request(Store(tmp_path), {"id": "bad", "kind": "shell", "payload": {"cmd": "invalid"}}, {})
    assert not result["ok"] and "Unknown" in result["message"]


def test_folder_picker_registers_real_sample_through_component(tmp_path, monkeypatch):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(tmp_path / "workspace"))
    sample = tmp_path / "sample folder"; (sample / "raw/nanopore").mkdir(parents=True)
    app = AppTest.from_file(str(APP)).run(timeout=20)
    result = send(app, "browse_folders", {"path": str(sample)})
    assert result["ok"] and result["browser"]["layout"]["raw/nanopore"]
    assert result["keep_modal"]
    result = send(app, "add_sample", {"id": "sample", "path": result["browser"]["path"], "mode": "ont_only"}, "register")
    assert result["ok"]
    assert Store(tmp_path / "workspace").get("samples", "sample")["path"] == str(sample)


def test_app_update_release_selection_through_component(tmp_path, monkeypatch):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(tmp_path / "workspace"))
    release = {"id": 17, "version": "99.0.0a1", "available": True, "wheel_url": "server-selected"}
    monkeypatch.setattr("bactologix.updates.fetch_releases", lambda: {"releases": [release]})
    captured = []
    monkeypatch.setattr("bactologix.ui_backend.enqueue", lambda *args: captured.append(args) or "job-update")
    app = AppTest.from_file(str(APP)).run(timeout=20)
    assert "Update Bactologix" in app.get("bidi_component")[0].proto.html_content
    assert send(app, "refresh_app_releases")["ok"]
    result = send(app, "update_app", {"release_id": 17, "version": "99.0.0a1"}, "select-update")
    assert result["ok"] and result["navigate"] == "jobs"
    assert captured[0][1:] == ("app_update", {"release": release, "launcher_id": None})
    send(app, "update_app", {"release_id": 17, "version": "99.0.0a1"}, "select-update")
    assert len(captured) == 1


def test_database_scan_browse_and_save_through_real_component(tmp_path, monkeypatch):
    from bactologix import databases
    monkeypatch.setenv('BACTOLOGIX_HOME', str(tmp_path / 'workspace'))
    root = tmp_path / 'databases'
    db = root / 'my-kraken'
    db.mkdir(parents=True)
    for name in ['hash.k2d', 'opts.k2d', 'taxo.k2d']:
        (db / name).write_text('software fixture')
    monkeypatch.setattr(databases, '_roots', lambda *args: [root])
    app = AppTest.from_file(str(APP)).run(timeout=20)
    scanned = send(app, 'scan_databases', {'tool': 'hybas'}, 'scan-resource')
    assert scanned['ok']
    choice = scanned['database_scan']['candidates']['kraken'][0]
    assert choice['preferred'] and choice['path'] == str(db)
    browsed = send(app, 'browse_folders', {'path': str(root)}, 'browse-resource')
    assert browsed['browser']['folders'][0]['path'] == str(db)
    checked = send(app, 'check_database', {'tool': 'hybas', 'id': 'kraken', 'path': str(db)}, 'check-resource')
    assert checked['ok'] and checked['database_check']['status'] == 'Layout recognized'
    saved = send(app, 'save_databases', {'tool': 'hybas', 'changes': {'kraken': choice['path']}, 'revision': scanned['database_scan']['revision']}, 'save-resource')
    assert saved['ok'] and saved['database_saved'] == 'hybas'
    import yaml
    config = yaml.safe_load(Store(tmp_path / 'workspace').get('configs', 'hybas'))
    assert (Path(config['db_root']) / config['kraken_db_name']).resolve() == db
