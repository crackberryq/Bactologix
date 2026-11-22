import json
import os
import shutil
from pathlib import Path

import pytest
from bactologix import trajectory_backfill as backfill


def test_completed_run_backfill(tmp_path, monkeypatch):
    source = os.environ.get("BACTOLOGIX_HYBAS_V2_SOURCE")
    if not source:
        pytest.skip("Requires matching pipeline source")
    dest = tmp_path/"sources"/("hybas-"+backfill.SOURCE)
    for name in ("workflow/scripts/polishing_trajectory.py","workflow/envs/qc.yml",
                 "workflow/lib/resource_identity.py","workflow/lib/workflow_support.py"):
        (dest/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(Path(source)/name,dest/name)
    prefix = tmp_path/"envs/hybas/test"
    for name in ("bin/busco","bin/dnadiff","conda-meta/history"):
        (prefix/name).parent.mkdir(parents=True,exist_ok=True)
        (prefix/name).touch()
    prefix.with_suffix(".env_setup_done").touch()
    shutil.copyfile(dest/"workflow/envs/qc.yml",prefix.with_suffix(".yaml"))
    iso = tmp_path/"runs/run-abc/outputs/isolate1"
    report = iso/"reports/busco/resource_identity.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(dict(directory="/lineage",sha256="expected")))
    receipt = iso/"metadata/polish_execution.json"
    receipt.parent.mkdir()
    receipt.write_text("{}")
    calls = []
    def run(cmd,check):
        calls.append(cmd)
        if "--out" in cmd:
            (Path(cmd[cmd.index("--out")+1])/"index.html").write_text("complete")
    monkeypatch.setattr(backfill.subprocess,"run",run)
    backfill.generate(tmp_path,"run-abc",2)
    backfill.generate(tmp_path,"run-abc",2)
    assert len(calls)==2
    assert (iso/"reports/polishing_trajectory/retrospective.json").is_file()
    assert receipt.read_text()=="{}"
