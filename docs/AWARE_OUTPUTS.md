# HybAs 10: one configurable AWARE module

This feature is available in the HybAs 10 development integration and requires the compatible, reviewed v2 execution adapter. The stable pipeline is not changed or activated by this update.

In **New run → Kraken & AWARE**, select:

- **Run through assembly** or **Stop after AWARE**.
- **Pause after identification** when the target should be reviewed before filtering.
- **Preserve additional read groups** for optional provisional species/shared/unclassified outputs.
- **Statistical evidence** for assignment-support intervals and resampling stability.

Regular AWARE selection and cleaning remain the defaults. The extra controls do not replace its original functionality. Both short-read and long-read inputs are supported; PacBio chemistry remains explicit.

After an AWARE endpoint succeeds, the run says **AWARE complete**. Open **AWARE outputs** to inspect group names, retained units, actual assembly routes and unsupported combinations. Select eligible groups and choose **Register selected samples**. Use New run to create separate drafts for the registered samples, with their own settings. A group with only Illumina reads is registered as Illumina-only even if its parent was hybrid.

**Samples → Import AWARE bundle** accepts `reports/aware/manifest.json` after the entire bundle has been copied into any local folder. Checksums and the embedded library manifests determine the inputs; the app does not guess from directory names. Paths escaping the bundle, symlinks, missing files and changed contents are rejected. Derived records retain the original parent sample, context, group and filtering history. Original parent paths in historical provenance are not required to import a copied bundle.

Derived runs skip Kraken by default to avoid repeated filtering. Enable **Reclassify derived inputs** explicitly to use the chosen Kraken settings again. Preserved species labels are provisional, not verified isolate/strain reconstructions. Shared and unclassified groups remain available for inspection/export. The primary output can overlap species groups; these are not independent biological replicates.

**Continue to assembly** validates the same run's saved source and AWARE bundle, creates a checksummed continuation receipt, and performs a new native dry-run/environment check before assembly. It preserves the original YAML and reuses verified upstream outputs. Install missing environments through the existing controls, then retry continuation. Completion of assembly remains separate from completion of AWARE.

Statistical intervals describe observed Kraken assignment support under sampling assumptions. Selection frequency is a deterministic stratified bootstrap of original assignments passed to the unchanged election rule. Neither statistic establishes identification correctness or removes database bias. Statistical evidence does not change the cleaning decision.

## Stage-aware Methods and testing package

Methods exports verify the saved identification or AWARE receipt. Identification-only and AWARE-only exports omit downstream method claims; completed assembly exports retain their available execution records. Reproducibility ZIPs include the verified stage evidence. The output picker shows the parent sample, child sample IDs and actual retained routes.

Use `scripts/package_development_pair.py` to build an application wheel and both source archives from explicit Git commits. The generated `testing-pair.json` and `SHA256SUMS` identify the paired test build. Install into an isolated virtual environment and separate Bactologix home. A package is not an execution approval: the official prerelease compatibility registry remains the gate for managed runs on a new HybAs commit.
