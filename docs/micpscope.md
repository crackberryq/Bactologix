# MICP Scope in Bactologix

Use Install & update → MICP Scope → First-time install. The first installation downloads the pinned source, creates or reuses the required Snakemake runner, and prepares the rule environments. It remains marked incomplete until the environment checks finish. Retry resumes this path after failure. Normal updates preserve caches and do not automatically install all environments.

The same first-install path applies to HybAs and BaSIS. Database setup remains a separate step; a successful installation is not proof that a run is ready.

Register an input folder containing `annotation/<sample_id>.faa`. Optionally include `annotation/<sample_id>.evidence.csv` with the MICP Scope evidence schema. Create a MICP Scope run, validate it, check/prepare its environments and execute. Read-mode selection does not change MICP Scope analysis. Find its report in Results & downloads. Existing runs retain their frozen versions and inputs.

MICP Scope currently characterizes proteins and records evidence. It does not annotate genomes or infer operating pH/temperature.


## Named releases and gene/function reports

Bactologix 0.1.0a49 recommends `micpscope-v0.1.0a4`, pinned to its tested commit. The older supported snapshots remain available. `main` is a development branch, not the release name.

New reports group supplied gene/protein annotations into urease, maturation, inorganic carbon conversion, metabolism, biofilm matrix and sporulation context. Expand records for function, descriptors, evidence level and references. Search works offline. The packaged JSON catalog is a focused 20-entry starter review with coverage gaps recorded. Header mapping is not sequence-confirmed function detection; unannotated locus IDs remain unresolved.

Aliphatic index and theoretical pI are not converted into unsupported temperature/pH optima. Source-linked measured observations can still be imported through `annotation/<sample_id>.evidence.csv`. Existing frozen reports are unchanged.
