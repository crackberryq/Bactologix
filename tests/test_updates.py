import copy
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import time
from zipfile import ZipFile

import pytest

from bactologix import __version__, updates
from bactologix.catalog import PIPELINES, supported, versions
from bactologix.engine import Context, controller_key, prepare_controller
from bactologix.runtime import environment_lock
from bactologix.store import Store
from bactologix.ui_backend import dispatch, installation_status, snapshot


def release(version="99.0.0a1", content=b"wheel"):
    name = f"bactologix-{version}-py3-none-any.whl"
    return {"id": 77, "tag_name": "v" + version, "prerelease": True, "draft": False,
            "assets": [{"id": 88, "name": name, "state": "uploaded", "size": len(content),
                        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                        "browser_download_url": f"https://github.com/{updates.REPO}/releases/download/v{version}/{name}"}]}


def wheel_bytes(version="99.0.0a1", python=">=3.11"):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as wheel:
        wheel.writestr(f"bactologix-{version}.dist-info/METADATA",
                       f"Metadata-Version: 2.4\nName: bactologix\nVersion: {version}\nRequires-Python: {python}\n")
    return stream.getvalue()


def test_compatible_snapshots_survive_moving_branch_and_preserve_prior_commit(tmp_path):
    store = Store(tmp_path)
    cached = {"live": True, "checkedAt": "test", "versions": [dict(id="branch:snakemake-standalone", kind="branch", commit="0"*40)]}
    store.put("catalog", "basis", cached)
    rows = versions(store, "basis")["versions"]
    assert rows[0]["commit"] == "1d2d96b266e98d956254d1a518ff5b437ab7a10a"
    assert supported("basis", "f66dfedd46da05ae79bf769e7e6fada91270d4d1")
    assert supported("basis", "d2d2e7806df4d2f586dc4a11d5c1f980deb06857")
    assert not supported("basis", "0"*40)
    assert store.get("catalog", "basis") == cached


def test_downloaded_basis_can_finish_setup_without_forcing_repair(tmp_path, monkeypatch):
    store = Store(tmp_path)
    item = dict(id="downloaded", tool="basis", ready=False, controller=None,
                version={"ref": "snakemake-standalone", "commit": PIPELINES["basis"]["commit"]})
    store.put("installs", item["id"], item)
    row = installation_status(item, [])
    assert row["setup_state"] == "Ready to set up" and row["supported"]
    captured = []
    monkeypatch.setattr("bactologix.ui_backend.enqueue", lambda *args: captured.append(args) or "job-test")
    dispatch(store, "setup_install", {"id": item["id"]})
    assert captured[0][1] == "install" and captured[0][2]["repair"] is False
    item["version"]["commit"] = "0"*40
    store.put("installs", item["id"], item)
    assert installation_status(item, [])["setup_state"] == "Version not supported"
    with pytest.raises(ValueError, match="not supported"):
        dispatch(store, "setup_install", {"id": item["id"]})
    assert len(captured) == 1


def test_finish_setup_reuses_existing_environment(tmp_path, monkeypatch):
    store = Store(tmp_path / "workspace")
    source = tmp_path / "source"; source.mkdir()
    content = b"dependencies: [python=3.11, snakemake=9.6.2]\n"
    (source / "environment.yml").write_bytes(content)
    prefix = store.home / "controllers" / ("basis-" + controller_key("basis", content))
    prefix.mkdir(); (prefix / "preserved").write_text("existing environment")
    item = dict(id="downloaded", tool="basis", source=str(source), ready=False, controller=None)
    jid = store.submit("install", {})
    ctx = Context(store, store.job(jid))
    monkeypatch.setattr("bactologix.engine.find_conda", lambda s: "/conda")
    checks = []
    monkeypatch.setattr("bactologix.engine.check_controller", lambda *args: checks.append(args[2]))
    monkeypatch.setattr(ctx, "command", lambda *args: pytest.fail("Environment creation must not occur"))
    try:
        prepare_controller(ctx, item)
    finally:
        ctx.log.close()
    assert checks == [prefix] and item["ready"]
    assert store.get("settings", "active-basis") == item["id"]
    assert (prefix / "preserved").read_text() == "existing environment"


@pytest.mark.parametrize("bad", ["url", "digest", "size", "draft"])
def test_release_rejects_unexpected_or_unverifiable_asset(bad):
    row = release()
    if bad == "url": row["assets"][0]["browser_download_url"] = "https://example.org/evil.whl"
    if bad == "digest": row["assets"][0]["digest"] = None
    if bad == "size": row["assets"][0]["size"] = updates.MAX_WHEEL_BYTES + 1
    if bad == "draft": row["draft"] = True
    result = updates.release_record(row)
    assert result is None or not result["available"]


def test_release_selection_uses_cached_server_asset(tmp_path, monkeypatch):
    store = Store(tmp_path)
    row = updates.release_record(release())
    store.put("app_catalog", "releases", {"releases": [row]})
    captured = []
    monkeypatch.setattr("bactologix.ui_backend.enqueue", lambda *args: captured.append(args) or "job-update")
    dispatch(store, "update_app", {"release_id": row["id"], "version": row["version"], "wheel_url": "https://example.org/evil"})
    assert captured[0][2]["release"] == row
    with pytest.raises(ValueError, match="Refresh"):
        dispatch(store, "update_app", {"release_id": row["id"], "version": "9.9.9"})


def test_update_queue_and_cancel_guards(tmp_path):
    store = Store(tmp_path)
    normal = store.submit("smoke", {})
    with pytest.raises(ValueError, match="Finish"):
        store.submit("app_update", {})
    store.cancel(normal)
    update = store.submit("app_update", {})
    with pytest.raises(ValueError, match="queued or running"):
        store.submit("smoke", {})
    with pytest.raises(ValueError, match="queued or running"):
        store.submit("app_update", {})
    assert store.claim()["id"] == update
    with pytest.raises(ValueError, match="cannot be cancelled"):
        store.cancel(update)
    assert store.job(update)["cancel"] == 0


def test_update_status_uses_cached_ui_while_package_files_are_replaced(tmp_path, monkeypatch):
    store = Store(tmp_path)
    initial = snapshot(store)
    jid = store.submit("app_update", {})
    monkeypatch.setattr("bactologix.ui_backend.configuration_fields", lambda *a: pytest.fail("Package data read during update"))
    current = snapshot(store)
    assert current["configs"] == initial["configs"]
    assert current["app_update"]["active_job"] == jid
    assert current["jobs"][0]["id"] == jid


def test_environment_update_lock_excludes_other_workers():
    with environment_lock():
        with pytest.raises(RuntimeError, match="Another Bactologix"):
            with environment_lock(exclusive=True): pass
    with environment_lock(exclusive=True):
        with pytest.raises(RuntimeError, match="Another Bactologix"):
            with environment_lock(): pass


def test_download_checks_checksum_and_python_before_install(tmp_path, monkeypatch):
    content = wheel_bytes()
    record = updates.release_record(release(content=content))
    def response(data):
        result = io.BytesIO(data); result.url = record["wheel_url"]; return result
    monkeypatch.setattr(updates, "urlopen", lambda *a, **k: response(content))
    assert updates.download_wheel(record, tmp_path).read_bytes() == content
    monkeypatch.setattr(updates, "urlopen", lambda *a, **k: response(b"x"*len(content)))
    with pytest.raises(ValueError, match="SHA-256"):
        updates.download_wheel(record, tmp_path)
    assert not (tmp_path / record["wheel_name"]).exists()
    path = tmp_path / "wrong-python.whl"; path.write_bytes(wheel_bytes(python=">=99"))
    with pytest.raises(ValueError, match="needs Python"):
        updates.validate_wheel(path, record["version"])


def test_changed_release_is_rejected_before_pip(tmp_path, monkeypatch):
    store = Store(tmp_path)
    raw = release(); selected = updates.release_record(raw)
    raw["assets"][0]["digest"] = "sha256:" + "0"*64
    monkeypatch.setattr(updates, "api", lambda path: raw)
    jid = store.submit("app_update", {"release": selected}); ctx = Context(store, store.job(jid))
    try:
        with pytest.raises(ValueError, match="asset changed"):
            updates.install_update(ctx)
    finally: ctx.log.close()
    assert store.get("settings", "app-update") is None


@pytest.mark.parametrize("fail", [False, True])
def test_pip_targets_current_environment_and_requires_restart(tmp_path, monkeypatch, fail):
    store = Store(tmp_path)
    raw = release(); selected = updates.release_record(raw)
    monkeypatch.setattr(updates, "api", lambda path: raw)
    wheel = tmp_path / selected["wheel_name"]; wheel.write_bytes(b"verified fixture")
    monkeypatch.setattr(updates, "download_wheel", lambda *args: wheel)
    calls = []
    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        if fail: raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(updates.subprocess, "run", execute)
    jid = store.submit("app_update", {"release": selected}); ctx = Context(store, store.job(jid))
    try:
        if fail:
            with pytest.raises(subprocess.CalledProcessError): updates.install_update(ctx)
        else: updates.install_update(ctx)
    finally: ctx.log.close()
    assert calls[0][0][:4] == [sys.executable, "-m", "pip", "--isolated"]
    assert calls[0][0][-1] == str(wheel)
    assert "conda" not in calls[0][0] and "--no-user" in calls[0][0]
    state = store.get("settings", "app-update")
    assert state["status"] == ("failed" if fail else "installed")
    assert updates.status(store)["restart_required"]
    with pytest.raises(ValueError, match="Restart"):
        dispatch(store, "save_conda", {"path": "/conda"})
    assert dispatch(store, "refresh", {}) == {}
    monkeypatch.setattr(updates, "PROCESS_STARTED", time.time()+1)
    assert not updates.status(store)["restart_required"]
