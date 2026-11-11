# Regular and development testing

Use two separate installations for this qualification round. These are alpha
testing targets; passing software checks does not promote them to a validated
beta or establish analytical accuracy.

| Component | Regular testing installation | Experimental installation |
| --- | --- | --- |
| Bactologix | 0.1.0a88; `bactologix` launcher | 0.1.0a88.dev1; `bactologix-dev` launcher |
| HybAs | 9.0.3; v9 workflow | HybAs 10 development; experimental input modes and AWARE+ |
| BaSIS | 0.4.0a6 | 0.4.0a6; independent Dev installation |
| GeneScope | 0.1.0a16 with MICP database 1.0.1 | 0.1.0a16; independent Dev installation and MICP 1.0.1 database copy |
| Default workspace on Linux/WSL | `~/.local/share/bactologix` | `~/.local/share/bactologix-dev` |
| Application environment | Existing `bactomics` environment, or legacy `bactomix` | `~/.local/share/bactologix-dev-install/app` |

The regular updater and installer reject development releases, including a Dev
release incorrectly marked as stable on GitHub. The Dev installer uses its own
Conda installation, application environment, workspace, controllers and tool
environments. Open each channel with its own launcher. Keep registered runs,
pipeline installations, database copies and generated outputs in the owning
workspace; do not point both channels at one output directory or share writable
environment prefixes. Original input files can be selected separately when
appropriate; their identity is checked before execution.

Record the application version, pipeline commit, database identity, input hashes,
configuration and observed result for each test. Compare the two runs by those
records, not just by their sample names. A failed or unassessed step must remain
visible even when report rendering succeeds. Retain both original reports and
their logs when reporting a discrepancy.

The regular application's CI retains the older pinned adapter checks and adds
an independent three-pipeline matrix for the exact near-beta candidates. That
matrix exercises the app-to-pipeline contract and saved-evidence report
rendering, and must pass before publishing the application release. It does
not replace native-tool accuracy benchmarks, fresh-machine installation tests
or scientific review of the supported scope.
