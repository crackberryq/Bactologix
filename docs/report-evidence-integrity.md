# Saved report identity and assessment coverage

Report refresh compares saved sample identifiers with the selected Bactologix
run. Every BaSIS sample/record and saved validation/decision sample key must
agree. HybAs isolate and GeneScope sample identifiers are checked separately.
Available native input, source and configuration hashes are compared to the
corresponding saved baselines. An explicit `bactologix` identity block may carry
the application run, sample, attempt, source commit and configuration/workflow
hashes. BaSIS's own native `run_id` is a different namespace.

Workflow attempts now capture content checksums of report evidence before and
after execution. A receipt binds the observed checksum to the application run,
source/configuration/workflow identity, input baselines and reviewed AWARE
decision hashes. Evidence created or changed during that attempt may be labelled
as matching the observed attempt. This records association with an execution,
not proof of the native producing process or scientific correctness.

A successful no-op run cannot assign its source to unchanged legacy evidence.
Older completion-only Methods snapshots do not establish a producing attempt.
Missing provenance stays visibly unverified; missing native software versions
stay not recorded. Native identity conflicts, changed evidence that no longer
matches recorded attempts, and source/configuration conflicts block refresh.
An older matching edition may still be rendered for diagnosis after a failed
rerun, but its observed attempt and the latest failed attempt remain separate.

Refresh previews, standalone HTML and registered editions preserve:

- Latest computational attempt and its status.
- Evidence attribution, observed attempt and that attempt's status.
- Recorded assessment coverage and its limitations.
- Successful rendering as a separate operation.

The saved evidence, renderer, execution status and identity are rechecked before
and after rendering. Changes require a new preview. Original reports and JSON
remain unchanged.

Run and result cards display execution separately from assessment coverage.
BaSIS's explicit requested-scope record can be shown as reported complete, but
missing, failed or contradictory requested-module records remain incomplete.
Missing/mismatched evidence or a failed later attempt has unverified coverage.
HybAs and GeneScope have no reviewed aggregate completeness contract here, so
successful processing never supplies a new scientific-quality or safety label.

These checks repair evidence handling. They do not replace native-tool,
independent analytical, or user-interpretation validation before beta release.
