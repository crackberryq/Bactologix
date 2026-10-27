from __future__ import annotations
import argparse
import json
import os
from . import __version__
from .store import Store, workspace


def main():
    parser = argparse.ArgumentParser(description="Bactologix — local HybAs and BaSIS workspace")
    parser.add_argument("command", nargs="?", choices=["serve", "doctor", "smoke-test", "jobs"], default="serve")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home", help="Workspace directory (default: platform user data directory)")
    parser.add_argument("--port", type=int, help="Listen port (default: first available port from 8501)")
    parser.add_argument("--host", help="Listen address (default: local loopback, or the current WSL address)")
    parser.add_argument("--no-browser", action="store_true", help="Start the app without opening a browser")
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
        from .launcher import serve
        try:
            code = serve(home, args.host, args.port, browser=not args.no_browser)
        except (OSError, ValueError) as exc:
            parser.exit(1, f"Bactologix could not start: {exc}\n")
        raise SystemExit(code)


if __name__ == "__main__":
    main()
