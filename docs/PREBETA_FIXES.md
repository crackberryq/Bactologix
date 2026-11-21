# Pre-beta correction status

The 8 November 2026 review remains the baseline. These corrections are being
qualified in the independent development channel. Existing stable installations
and saved source snapshots are not migrated by this update.

| Review item | Development correction |
| --- | --- |
| A1 | Refresh rejects wrong-sample or conflicting available identity; content observations bind evidence to an execution attempt without inventing provenance for unchanged legacy files. |
| A2 | Latest analysis execution, evidence attempt, assessment coverage and rendering completion remain separate in saved records, exported HTML and UI. |
| A3 | Run and result cards show assessment coverage separately from computational completion. Missing or stale coverage is unverified. |
| A4 | New drafts retain streaming SHA-256 input fingerprints and verify them before launch or reuse. Old records remain explicitly metadata-only. Quick UI checks do not hash large reads. |
| H1–H5 | HybAs separates Medaka attempts, preserves read model metadata, records effective model identity, separates processing status from analytical suitability, fixes same-lineage rivalry and fingerprints BUSCO resources. |
| B1–B4 | BaSIS validates annotation coverage and export consistency, displays input limitations and rejects contradictory native coordinates before normalization. |
| GS1–GS4 | GeneScope validates category vocabulary, separates conflicting/ambiguous evidence, validates coordinate bounds with explicit linkage limitations and tracks rendering dependencies. |

The corrected MICP starter specification is **1.0.1**. Build a new database from
that specification for new GeneScope runs. Existing 1.0.0 database packages and
their checksums must not be edited in place. Old reports remain evidence from
their recorded analysis; refreshing presentation does not rerun a search.

## Qualification still required

- A5: exact-version Methods prose must be reviewed against native execution
  receipts for the final supported beta routes. Unknown defaults remain omitted.
- Run actual pinned analysis tools across the declared supported route matrix,
  including interruption, unchanged rerun and changed-input rerun.
- Benchmark analytical accuracy on independent, appropriate reference data;
  software regression tests do not establish sensitivity or specificity.
- Perform clean Windows/WSL and Linux installation/recovery tests and verify
  interpretation with intended engineer users.
- Freeze the tested app, pipeline and database combination before beta promotion.

Details: [application evidence](report-evidence-integrity.md),
[HybAs development](https://github.com/crackberryq/Bactomics_HybAs/tree/development),
[BaSIS development](https://github.com/crackberryq/Bactomics_BaSIS/tree/development),
[GeneScope development](https://github.com/crackberryq/Bactomics_GeneScope/tree/development).
