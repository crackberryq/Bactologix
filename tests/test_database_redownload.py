"""Replacement preparation never deletes or selects existing database data."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from bactologix import database_redownload as redownload
from test_maintenance import FakeContext, installed
from test_resource_repair import stale_run


@pytest.fixture
def manual_database(installed, tmp_path):
    store, item = installed
    external = tmp_path / "manually-maintained/kraken-release-name-is-not-a-version"
    external.mkdir(parents=True)
    for name in ("hash.k2d", "opts.k2d", "taxo.k2d"):
        (external / name).write_bytes(b"preserve original user database\n")
    alias = store.home / "resources/manual-link"
    alias.symlink_to(external, target_is_directory=True)
    config = yaml.safe_dump({"db_root": str(alias.parent), "kraken_db_name": alias.name})
    store.put("configs", "hybas", config)
    store.put("database_locations", "hybas", {"kraken": {"path": str(alias)}})
    return store, item, external, alias, config


def reviewed(store, resource="kraken", **kwargs):
    plan = redownload.preview(store, "hybas", resource, **kwargs)
    return redownload.request(store, "hybas", resource, plan["signature"], plan["parent"],
                              choice="supported-source", **kwargs)


def complete_fixture(payload):
    def complete(argv, **kwargs):
        path = Path(payload["destination"]) / "standard"
        path.mkdir(exist_ok=True)
        for name in ("hash.k2d", "opts.k2d", "taxo.k2d"):
            (path / name).write_text("new software-only database fixture\n")
    return complete


def test_manual_database_preview_does_not_invent_version_or_touch_original(manual_database):
    store, item, external, alias, config = manual_database
    before = sorted(store.home.rglob("*"))
    plan = redownload.preview(store, "hybas", "kraken")
    assert plan["alternative"]["available"] and not plan["exact"]["available"]
    assert plan["current"]["path"] == str(external)
    assert plan["current"]["recorded_version"] is None
    assert plan["alternative"]["database_release"] is None
    assert plan["alternative"]["provider"] == "kraken2-build --standard"
    assert plan["alternative"]["commit"] == item["version"]["commit"]
    assert plan["alternative"]["pipeline_source"].startswith("https://github.com/")
    assert "may differ" in plan["notice"] and "folder name is not" in plan["exact"]["reason"]
    assert sorted(store.home.rglob("*")) == before and alias.is_symlink()
    assert store.get("configs", "hybas") == config and not store.jobs()


@pytest.mark.parametrize("choice", [None, "", "exact", "latest"])
def test_no_silent_switch_to_current_provider(manual_database, choice):
    store, _, external, alias, _ = manual_database
    plan = redownload.preview(store, "hybas", "kraken")
    with pytest.raises(ValueError, match="Explicitly choose"):
        redownload.request(store, "hybas", "kraken", plan["signature"], plan["parent"], choice=choice)
    assert alias.is_symlink() and len(list(external.iterdir())) == 3


def test_success_prepares_separately_records_limits_and_preserves_defaults_and_runs(manual_database):
    store, _, external, alias, config = manual_database
    frozen = {"id": "old-run", "tool": "hybas", "config": config, "status": "completed"}
    store.put("runs", frozen["id"], frozen)
    old = {p: p.read_bytes() for p in external.iterdir()}
    payload = reviewed(store)
    destination = Path(payload["destination"])
    assert not destination.exists() and destination.is_relative_to(store.home)
    assert destination.parent != alias.parent  # Whole configured db_root is protected.
    ctx = FakeContext(store, payload, "database_redownload")
    ctx.command = complete_fixture(payload)
    redownload.execute(ctx)
    row = store.get("database_downloads", payload["id"])
    assert row["status"] == "completed" and row["request"] == payload
    receipt_path = Path(row["replacement_receipt"]["path"])
    receipt = json.loads(receipt_path.read_text())
    assert row["replacement_receipt"]["sha256"] == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    assert receipt["status"] == "candidate-prepared" and not receipt["exact_replay"]
    assert receipt["database_release"] is None and receipt["native_evidence"] is None
    assert receipt["workflow_sha256"] == row["workflow_sha256"]
    assert receipt["config_sha256"] == row["config_sha256"]
    assert not receipt["selection_updated_by_this_job"] and not receipt["saved_runs_modified"]
    assert store.get("configs", "hybas") == config and store.get("runs", frozen["id"]) == frozen
    assert alias.is_symlink() and all(p.read_bytes() == content for p, content in old.items())


def test_failed_candidate_retained_and_original_preserved_on_retry(manual_database):
    store, _, external, alias, config = manual_database
    payload = reviewed(store)
    ctx = FakeContext(store, payload, "database_redownload")
    partial = Path(payload["destination"]) / "partial-download"
    def fail(*args, **kwargs):
        partial.write_bytes(b"retain attempted generated data")
        raise RuntimeError("fixture network failure")
    ctx.command = fail
    with pytest.raises(RuntimeError, match="network failure"):
        redownload.execute(ctx)
    assert partial.is_file() and store.get("database_downloads", payload["id"])["status"] == "failed"
    store.update_job(ctx.job["id"], status="failed")
    retry = FakeContext(store, payload, "database_redownload")
    retry.command = complete_fixture(payload)
    redownload.execute(retry)
    assert partial.is_file() and alias.is_symlink()
    assert store.get("configs", "hybas") == config and len(list(external.iterdir())) == 3


@pytest.mark.parametrize("changed", ["approval", "installation", "source", "selection", "request"])
def test_execution_rechecks_provider_review_before_any_download(manual_database, changed):
    store, item, _, _, config = manual_database
    payload = reviewed(store)
    assert redownload.maintenance.download_panel_capability(item, store)["available"]
    if changed == "approval":
        key = "hybas:" + item["version"]["commit"]
        review = store.get("release_approvals", key)
        review["test_run"] += "?changed=1"
        store.put("release_approvals", key, review)
    elif changed == "installation":
        item["rule_cache"] += "-changed"
        store.put("installs", item["id"], item)
    elif changed == "source":
        (Path(item["source"]) / "Hybas.smk").write_text("changed source")
    elif changed == "selection":
        store.put("configs", "hybas", config + "threads: 2\n")
    else:
        payload["memory_mb"] += 1
    ctx = FakeContext(store, payload, "database_redownload")
    with pytest.raises(ValueError, match="changed|differs"):
        redownload.execute(ctx)
    assert not ctx.calls and not Path(payload["destination"]).exists()


@pytest.mark.parametrize("kind", ["database_download", "database_redownload"])
@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_download_blocks_duplicate_before_creating_files(manual_database, kind, status):
    store, _, _, _, _ = manual_database
    payload = reviewed(store)
    jid = store.submit(kind, payload)
    store.update_job(jid, status=status)
    with pytest.raises(ValueError, match="already queued or running"):
        reviewed(store)
    assert not Path(payload["destination"]).exists()


@pytest.mark.parametrize("where", ["external", "symlink", "registered", "frozen"])
def test_parent_cannot_write_into_external_manual_or_frozen_database(manual_database, where):
    store, _, external, alias, _ = manual_database
    parent = external if where == "external" else alias
    if where in {"registered", "frozen"}:
        parent = store.home / "resources/previous-database"
        parent.mkdir()
        if where == "registered":
            store.put("database_locations", "basis", {"vfdb": {"path": str(parent)}})
        else:
            store.put("runs", "frozen", {"tool": "basis", "config": yaml.safe_dump({
                "modules": {"vfdb": {"database": str(parent / "old.dmnd")}}})})
    plan = redownload.preview(store, "hybas", "kraken")
    with pytest.raises(ValueError, match="workspace|symbolic|outside existing"):
        redownload.request(store, "hybas", "kraken", plan["signature"], str(parent), choice="supported-source")
    assert alias.is_symlink() and len(list(external.iterdir())) == 3


def test_symlink_retarget_between_review_and_execution_is_rejected(manual_database):
    store, _, external, _, _ = manual_database
    store.put("configs", "hybas", yaml.safe_dump({"db_root": str(external.parent), "kraken_db_name": external.name}))
    parent = store.home / "resources/replacement-root"
    parent.mkdir()
    plan = redownload.preview(store, "hybas", "kraken")
    payload = redownload.request(store, "hybas", "kraken", plan["signature"], str(parent), choice="supported-source")
    parent.rmdir()
    parent.symlink_to(external, target_is_directory=True)
    ctx = FakeContext(store, payload, "database_redownload")
    with pytest.raises(ValueError, match="symbolic"):
        redownload.execute(ctx)
    assert not ctx.calls and len(list(external.iterdir())) == 3


def test_native_receipt_is_bound_as_evidence_without_inventing_release(manual_database):
    store, _, _, _, _ = manual_database
    payload = reviewed(store)
    ctx = FakeContext(store, payload, "database_redownload")
    native = Path(payload["destination"]) / "standard/provenance.json"
    def complete(argv, **kwargs):
        complete_fixture(payload)(argv, **kwargs)
        native.write_text('{"status":"completed","version":"provider-recorded"}\n')
    ctx.command = complete
    redownload.execute(ctx)
    row = store.get("database_downloads", payload["id"])
    receipt = json.loads(Path(row["replacement_receipt"]["path"]).read_text())
    assert receipt["native_evidence"]["sha256"] == hashlib.sha256(native.read_bytes()).hexdigest()
    assert "not independently rehashed" in receipt["native_evidence"]["status"]
    assert receipt["database_release"] is None and not receipt["exact_replay"]


def test_escaping_native_receipt_fails_without_deleting_candidate(manual_database):
    store, _, external, _, _ = manual_database
    payload = reviewed(store)
    ctx = FakeContext(store, payload, "database_redownload")
    def complete(argv, **kwargs):
        complete_fixture(payload)(argv, **kwargs)
        (Path(payload["destination"]) / "standard/provenance.json").symlink_to(external / "hash.k2d")
    ctx.command = complete
    with pytest.raises(ValueError, match="provenance escapes"):
        redownload.execute(ctx)
    row = store.get("database_downloads", payload["id"])
    assert row["status"] == "failed" and "replacement_receipt" not in row
    assert (Path(payload["destination"]) / "standard/hash.k2d").is_file()


def test_external_result_link_cannot_be_published_as_new_candidate(manual_database):
    store, _, external, _, _ = manual_database
    payload = reviewed(store)
    ctx = FakeContext(store, payload, "database_redownload")
    def complete(argv, **kwargs):
        (Path(payload["destination"]) / "standard").symlink_to(external, target_is_directory=True)
    ctx.command = complete
    with pytest.raises(ValueError, match="escapes the isolated"):
        redownload.execute(ctx)
    row = store.get("database_downloads", payload["id"])
    assert row["status"] == "failed" and "replacement_receipt" not in row
    assert len(list(external.iterdir())) == 3


def test_busco_lineage_is_reviewed_as_name_not_release(manual_database):
    store, _, _, _, _ = manual_database
    plan = redownload.preview(store, "hybas", "busco", lineage="fungi_odb10")
    assert plan["alternative"]["lineage"] == "fungi_odb10"
    assert plan["alternative"]["database_release"] is None
    with pytest.raises(ValueError, match="changed"):
        redownload.request(store, "hybas", "busco", plan["signature"], plan["parent"],
                           choice="supported-source", lineage="bacteria_odb10")


@pytest.mark.parametrize("external_companion", [False, True])
def test_basis_native_receipt_and_companions_stay_together(stale_run, external_companion):
    store, parent = stale_run
    store.put("configs", "basis", parent["config"])
    plan = redownload.preview(store, "basis", "vfdb")
    payload = redownload.request(store, "basis", "vfdb", plan["signature"], plan["parent"], choice="supported-source")
    ctx = FakeContext(store, payload, "database_redownload")
    root = Path(payload["destination"]) / "vfdb"
    reference = store.home / "manual-reference.faa" if external_companion else root / "vfdb_setA.faa"
    def complete(argv, **kwargs):
        root.mkdir()
        database = root / "vfdb_setA.dmnd"
        database.write_text("index fixture")
        reference.write_text(">fixture\nMKK\n")
        (root / "provenance.json").write_text(json.dumps({"status": "completed", "result": {
            "module_updates": {"vfdb": {"database": str(database), "reference_fasta": str(reference),
                                        "database_version": "provider fixture identity"}}}}))
    ctx.command = complete
    if external_companion:
        with pytest.raises(ValueError, match="companion paths escape"):
            redownload.execute(ctx)
        assert store.get("database_downloads", payload["id"])["status"] == "failed"
    else:
        redownload.execute(ctx)
        row = store.get("database_downloads", payload["id"])
        assert row["result"]["updates"]["modules.vfdb.reference_fasta"] == str(reference)
        receipt = json.loads(Path(row["replacement_receipt"]["path"]).read_text())
        assert receipt["native_evidence"] and not receipt["exact_replay"]
    assert reference.is_file() and store.get("runs", parent["id"]) == parent
    assert store.get("configs", "basis") == parent["config"]


@pytest.mark.parametrize("owner", ["defaults", "frozen", "registration", "reference_package", "database_build"])
@pytest.mark.parametrize("field", ["db_proteins", "database_manifest", "tree_review_registry", "placement_cache_dir", "future_provider_field"])
def test_nonpicker_and_future_database_dependencies_are_protected(manual_database, owner, field):
    store, _, external, _, _ = manual_database
    # Keep the general resources folder available so this test exercises the
    # additional dependency, not the broad db_root protection.
    store.put("configs", "hybas", yaml.safe_dump({"db_root": str(external.parent), "kraken_db_name": external.name}))
    protected = store.home / "resources/manual-companions"
    protected.mkdir()
    config = {"sample_module_overrides": {"isolate": {"pathogenfinder2": {field: str(protected)}}}}
    if owner == "defaults":
        store.put("configs", "basis", yaml.safe_dump(config))
    elif owner == "frozen":
        store.put("runs", "old", {"tool": "basis", "config": yaml.safe_dump(config)})
    elif owner == "registration":
        store.put("database_locations", "basis", {"custom": {"fields": config}})
    elif owner == "reference_package":
        store.put("reference_packages", "manual", {field: str(protected)})
    else:
        store.put("database_builds", "build", {"destination": str(protected), "status": "completed"})
    plan = redownload.preview(store, "hybas", "kraken")
    assert plan["parent"] == str(store.home / "resources")
    with pytest.raises(ValueError, match="outside existing"):
        redownload.request(store, "hybas", "kraken", plan["signature"], str(protected), choice="supported-source")
    assert not list(protected.iterdir())


def test_resource_directory_container_and_missing_fasta_parent_are_protected(manual_database):
    store, _, external, _, _ = manual_database
    store.put("configs", "hybas", yaml.safe_dump({"db_root": str(external.parent), "kraken_db_name": external.name}))
    protected = store.home / "resources/manual-basis"
    protected.mkdir()
    for config in ({"resources": {"directory": str(protected)}},
                   {"modules": {"pathogenfinder2": {"db_proteins": str(protected / "missing.fa")}}}):
        store.put("configs", "basis", yaml.safe_dump(config))
        plan = redownload.preview(store, "hybas", "kraken")
        with pytest.raises(ValueError, match="outside existing"):
            redownload.request(store, "hybas", "kraken", plan["signature"], str(protected), choice="supported-source")
    assert not list(protected.iterdir())
