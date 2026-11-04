# GeneScope and application databases

Update Bactologix to 0.1.0a68 and select GeneScope 0.1.0a9 in Install & update. Existing MICP Scope installations, settings, databases and frozen runs remain registered under the same internal pipeline identity. No folders or records are destructively renamed.

Database Builder starts with the MICP application specification. Edit it or supply another YAML/JSON application specification. Schema 2.0 uses `application.id`, `application.name`, `application.version` and `application.description`. Database labels and report titles derive from these fields, and packages use `<id>-<version>.zip`. The build record remains unique, so repeated builds do not overwrite earlier databases.

Choose a completed database for future drafts. GeneScope uses the same analysis workflow for different application packages. Schema 1.0 packages remain readable under their original names; schema 2.0 requires the updated engine. Existing frozen runs continue using their recorded workflow and database identity. Create a new draft to use GeneScope's application-driven interpretation.

For compatibility, the repository and persisted technical identifiers retain `micpscope`; public UI labels are GeneScope. MICP is the bundled application, not the pipeline name. Functional groups and supplied evidence do not by themselves establish activity, complete operons or regulatory function.
