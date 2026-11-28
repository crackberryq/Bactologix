"""Generate the development trajectory report from a completed HybAs execution."""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

SOURCE = "b13075706781873483fe418a9f41fb7935e56e88"


def generate(home, run_id, threads):
    if not re.fullmatch(r"run-[0-9a-f]+", run_id) or threads < 1:
        raise ValueError("Supply a run ID and a positive thread count")
    source = home / "sources" / ("hybas-" + SOURCE)
    script = source / "workflow/scripts/polishing_trajectory.py"
    from . import dev_catalog
    registry = json.loads(dev_catalog.BUNDLED.read_text())
    row = next(r for r in registry["builds"] if r["commit"] == SOURCE)
    for name in ("workflow/scripts/polishing_trajectory.py", "workflow/envs/qc.yml",
                 "workflow/lib/resource_identity.py","workflow/lib/workflow_support.py"):
        path = source / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["files_sha256"][name]:
            raise ValueError("Install the checked HybAs 10.0.0a8 source before generating this report")
    prefix_root = home / "envs/hybas"
    spec = (source/"workflow/envs/qc.yml").read_bytes()
    prefixes = [p for p in sorted(prefix_root.glob("*")) if (p/"bin/dnadiff").is_file()
                and (p/"bin/busco").is_file() and (p/"conda-meta/history").is_file()
                and p.with_suffix(".env_setup_done").is_file()
                and p.with_suffix(".yaml").is_file() and p.with_suffix(".yaml").read_bytes() == spec]
    if not prefixes:
        raise ValueError("Install the HybAs 10.0.0a8 environments in Pipelines & databases first")
    conda = home/"tools/miniforge/bin/conda"
    runner = [str(conda),"run","--no-capture-output","-p",str(prefixes[0]),"python"]
    receipts = list((home/"runs"/run_id/"outputs").glob("*/metadata/polish_execution.json"))
    if not receipts:
        raise ValueError("No retained polishing execution record for this run")
    for receipt in receipts:
        iso = receipt.parents[1]
        reports = iso/"reports"
        dest = reports/"polishing_trajectory"
        if (dest/"index.html").is_file():
            print(dest/"index.html")
            continue
        if dest.exists():
            raise ValueError(f"Output already exists without a completed report: {dest}")
        identity = json.loads((reports/"busco/resource_identity.json").read_text())
        lineage = identity["directory"]
        subprocess.run(runner+[str(source/"workflow/lib/resource_identity.py"),"--path",lineage,
                               "--expected",identity["sha256"]],check=True)
        work = Path(tempfile.mkdtemp(prefix="trajectory-attempt-",dir=reports))
        print(f"Measuring retained stages in {work}",flush=True)
        subprocess.run(runner+[str(script),"--out",str(work),"--threads",str(threads),
                               "--receipt",str(receipt),"--lineage",lineage],check=True)
        (work/"retrospective.json").write_text(json.dumps(dict(source_commit=SOURCE,
            original_receipt=str(receipt),lineage_sha256=identity["sha256"],environment=str(prefixes[0])),indent=2))
        work.rename(dest)
        print(dest/"index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--threads",type=int,default=4)
    parser.add_argument("--home",type=Path,default=Path.home()/".local/share/bactologix-dev")
    args = parser.parse_args()
    generate(args.home,args.run_id,args.threads)
