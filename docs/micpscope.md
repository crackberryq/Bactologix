# MICP Scope in Bactologix

Use Install & update → MICP Scope → First-time install. The first installation downloads the pinned source, creates or reuses the required Snakemake runner, and prepares the rule environments. It remains marked incomplete until the environment checks finish. Retry resumes this path after failure. Normal updates preserve caches and do not automatically install all environments.

The same first-install path applies to HybAs and BaSIS. Database setup remains a separate step; a successful installation is not proof that a run is ready.

Register an input folder containing `annotation/<sample_id>.faa`. Optionally include `annotation/<sample_id>.evidence.csv` with the MICP Scope evidence schema. Create a MICP Scope run, validate it, check/prepare its environments and execute. Read-mode selection does not change MICP Scope analysis. Find its report in Results & downloads. Existing runs retain their frozen versions and inputs.

MICP Scope currently characterizes proteins and records evidence. It does not annotate genomes or infer operating pH/temperature.
