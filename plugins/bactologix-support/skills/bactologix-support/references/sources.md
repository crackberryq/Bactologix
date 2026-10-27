# Official sources

- Bactologix: https://github.com/crackberryq/Bactologix
- HybAs: https://github.com/crackberryq/Bactomics_HybAs
- BaSIS: https://github.com/crackberryq/Bactomics_BaSIS

Use each repository's README, version-specific configuration documentation, CHANGELOG, and release notes. Resolve tags/branches to commits when comparing versions. Do not assume a default branch contains the user's installed release. For a frozen run, inspect documentation at its recorded commit. Confirm an actual published release before recommending an update; development branches and PRs are not releases.

The bundled `bactologix-readme.md` is copied from Bactologix commit 87cbc53838d8d8b320ebc70f116c5a3bb4e4a4f3, retrieved 2026-10-27, describing 0.1.0a18. It predates the diagnostic export added alongside this plugin. Consult it for that version only. Upstream documentation is MIT licensed; the license is included in this directory.

The plugin has no bundled HybAs or BaSIS scientific parameter defaults. Retrieve those from the user's matching pipeline commit rather than guessing or applying values from another release.
