# Independent Bactologix development channel

Bactologix Dev is a separate installation for development of the application, HybAs, BaSIS, GeneScope and future engineering applications. It does not activate development code in the regular installation.

## Ownership and storage

The dedicated `bactologix-dev` entry point requires a Dev-owned application environment. The installer creates a separate Conda distribution, application prefix and workspace. Workspace markers reject stable/Dev cross-use, nested workspaces and adoption of old unmarked databases. Dev ignores `BACTOLOGIX_HOME`, ambient Conda discovery and external cache/controller paths. Its configured database/model and native-result paths must resolve inside its own workspace. Common Conda, pip, XDG, model and temporary caches are redirected into that workspace. Symlink escapes are rejected at storage boundaries.

The stable entry point rejects a Dev-marked application environment. Stable's existing workspace remains usable; its first use with this version records stable ownership. Windows bootstrap names, shortcuts, control files and browser profiles are separate. Dev uses ports 8601–8620 by default. Updates and process shutdown use each installation's existing environment/workspace/session identity.

Input files can be reused as sources; generated data and tool environments stay separate. These are application-level protections against accidental sharing, not an OS sandbox against deliberately modified code. Operating system resources and host-level Conda bookkeeping may still be shared. No scientific result is validated merely by passing software checks.

## Development branches and exact builds

Each repository has a `development` branch. The application development branch starts from its HybAs 10 integration; HybAs starts from its tested experimental implementation. BaSIS and GeneScope start from their reviewed baseline commits. Maintenance corrections can be applied to both release lines while HybAs 9 remains the regular near-beta pipeline and HybAs 10 remains experimental. Each app retains its own pipeline catalog and workspace.

`compatibility/development-v1.json` is the official development catalog. The app bundles the same document as `data/development-v1.json` so the first installation can work before a catalog refresh. Dev refresh reads only the official application repository's development branch. It does not list arbitrary branches or fall back to stable releases. Each designated build records an immutable commit, entry points, runner versions, file hashes and software-check evidence. Old build contracts remain available for saved runs. Immutable contract changes are rejected; display preferences may change.

A development branch push is not an automatic pipeline upgrade. After appropriate checks, add a new exact build to both catalog copies. Keep prior entries. Users select/install the new snapshot explicitly; existing runs retain their frozen source/configuration. Adding an entirely new pipeline also requires its execution adapter, configuration/report integration and tests, not just a registry row.

Application update feeds are separated by PEP 440 development version: Dev accepts `.devN` builds; the regular app excludes them while retaining its existing alpha/prerelease choices. A wheel is installed only into the running application environment after identity/checksum validation. The initial Dev installer bundles its exact wheel and checksum manifest. Public Dev releases use `dev-v<version>` tags and `Bactologix-Dev-<version>.whl` download assets, so older regular updaters and installers cannot select them. The Dev updater verifies the downloaded bytes and restores the canonical wheel filename locally before installation. An older Dev build that does not recognize this release format must use the new Dev setup bundle once. Windows setup updates the separate Dev app environment; Linux/WSL users run `python3 install_bactologix.py --dev --install-conda --upgrade-app` from the extracted bundle. Existing Dev workspaces and runs are preserved.

## Build and test

Build the wheel with `python -m build`. Build the self-contained Windows installer using `scripts/build_development_bundle.py`; its archive contains the Dev launchers, installer, wheel, checksum manifest and instructions. Source code and provenance are distributed alongside it in the development package. Install in a normal non-root Ubuntu account.

Test channel markers, cross-channel updates, symlink escape rejection, dedicated Conda discovery, frozen catalogs, resource path checks, Windows payload copying, application package identity and actual side-by-side startup. CI performs Windows/bootstrap and browser checks. A fresh real Windows/WSL installation and representative pipeline datasets remain part of workstation/analytical qualification.

## Deferred beta work

Attributed user/device provenance and administrator approval/revocation remain a separate beta-launch design. No whitelist, tracking or remote deletion is implemented here. Future environmental engineering databases, including remediation research, can be developed and tested within GeneScope; measured proteomics may need a distinct adapter/pipeline.
