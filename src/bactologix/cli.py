from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from . import __version__
from .store import Store, workspace


def main():
    parser = argparse.ArgumentParser(description="Bactologix — local HybAs and BaSIS workspace")
    parser.add_argument("command", nargs="?", choices=["serve", "doctor", "smoke-test", "jobs"], default="serve")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home", help="Workspace directory (default: platform user data directory)")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--host", default="127.0.0.1", help="Listen address; use your WSL private IP to bypass localhost forwarding")
    args = parser.parse_args()
    home = workspace(args.home)
    os.environ["BACTOLOGIX_HOME"] = str(home)
    store = Store(home)
    from .engine import doctor, enqueue
    if args.command == "doctor":
        print(json.dumps(doctor(store), indent=2))
    elif args.command == "jobs":
        print(json.dumps(store.jobs(), indent=2))
    elif args.command == "smoke-test":
        print(enqueue(store, "smoke", {}))
    else:
        argv = [sys.executable, "-m", "streamlit", "run", str(Path(__file__).with_name("app.py")),
                "--server.address", args.host, "--server.port", str(args.port),
                "--server.headless", "true",
                "--server.fileWatcherType", "none",
                "--browser.gatherUsageStats", "false"]
        if args.host not in {"0.0.0.0", "::"}:
            argv += ["--browser.serverAddress", args.host]
        raise SystemExit(subprocess.call(argv))


if __name__ == "__main__":
    main()
