# Setup adviser

Open **New run → Settings → Setup adviser → Review setup** after choosing a pipeline and samples. Advice also appears on Review. It is available for new drafts; revisions retain their existing frozen-source editor.

The review uses the active pipeline installation, unsaved settings, selected registered samples and run compute limits. It checks current input file availability without reading sequence contents. A file being present and nonempty does not establish valid FASTQ records, paired reads, annotation consistency or sample identity.

## Source starting settings

Starting values come from a bounded read of the exact installed Git commit's configuration template. The repository must be clean and match its recorded supported commit. The adviser records the commit, template path and SHA256. Source template values are not necessarily the workflow's fallback for omitted settings; omitted current values remain unknown.

For exact reviewed HybAs versions, the review explains the configured input/polishing route. Racon rounds are source starting values, not inferred optimal counts. Medaka model choice requires the actual chemistry/basecaller information. Mixed read types receive separate explanations; shared polishing changes are disabled until samples with matching read types are reviewed together. Experimental PacBio routes remain explicit and are not treated as ONT.

BaSIS and GeneScope do not perform assembly polishing. Their review shows annotation requirements, selected source settings and relevant evidence limits. It does not recommend screening thresholds, infer function from similarity, or determine deployment safety.

## Applying a value

**Use source value** applies one displayed, applicable HybAs starting value to the current unsaved configuration. The server recomputes the review and rejects stale requests if inputs, selected source, settings or budgets changed. Values supplied by the browser cannot replace the server's source value. Manual settings unrelated to that choice are preserved.

Review does not create or execute a run. Application does not change workspace defaults, input files, saved runs or pipeline source. Creating a draft freezes the submitted settings through the existing run workflow; inspect that draft and complete its native dry-run before launch. Advice is not restored as fresh after reopening a browser tab.

## Limits

This first adviser does not measure coverage, quality, basecaller identity or assembly accuracy. It does not estimate a scientifically optimal setting, guarantee resource sufficiency, or establish performance from earlier results. File-stat freshness protects the review interaction; it is not a content identity claim. Existing draft creation and execution checks retain responsibility for content fingerprints and native validation.
