# HybAs to MICP Scope

Update Bactologix and install the current MICP Scope release through Install & update. Select your completed reference database in MICP Scope resources. Open a completed HybAs run and choose **Create MICP Scope draft**. The new draft uses its Prokka protein FASTA and GFF plus the final assembly, with independent output folders. Review and run it through the usual run controls.

The handoff requires nonempty Prokka FAA/GFF and final assembly outputs. If Prokka was skipped, finish annotation before handing off. Draft creation does not launch an analysis or modify the source run. The source run ID, workflow commit, input file hashes and database checksum identity are recorded. Changed inputs or database contents invalidate execution and require a new draft. Cross-pipeline provenance is separate from result-variant inheritance.

MICP Scope installs a dedicated reference-search environment through the normal environment preparation controls. Existing completed database packages remain usable without rebuilding. Existing frozen analysis runs keep their original workflow; create a new draft to use the search capability.

Reference matches are similarity candidates with scores and linked literature, not evidence of measured activity, complete operons, or optimal temperature/pH.
