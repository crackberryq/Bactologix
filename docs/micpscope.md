# GeneScope setup

GeneScope is hosted at [crackberryq/Bactomics_GeneScope](https://github.com/crackberryq/Bactomics_GeneScope). Bactologix 0.1.0a69 recommends the tested `genescope-v0.1.0a10` release. Historical GeneScope and MICP Scope snapshots remain supported.

Use **Install & update → GeneScope** to install the pipeline. In **Databases & models → GeneScope**, build the bundled MICP application specification or supply your own, then select a completed database. Create a draft from a completed HybAs run, or register a folder containing `annotation/<sample_id>.faa`, with optional GFF, assembly and evidence sidecars. Validate, prepare missing environments and run through the normal controls.

Database names and report titles come from the selected application package. MICP is the bundled application rather than the pipeline name. See [migration and compatibility](genescope-migration.md).

The persisted `micpscope` tool ID, output paths, historical tags and `MICPscope.smk` alias remain compatible. Existing frozen runs and original checkout remotes are not rewritten. New installations and CI use the canonical repository; cached release links are normalized for display and old reviewed approvals remain valid.
