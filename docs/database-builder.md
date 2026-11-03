# Database Builder

Open Bactologix → Install & update → Guided setup → MICP Scope → Databases.
Load a YAML/JSON reference specification, check its inputs, choose CPU/RAM,
and click **Build database**. Bactologix snapshots the specification, local
FASTA files and workflow source, then queues `DatabaseBuilder.smk`. Build logs
are available in Jobs and beside the registered database. Failed builds never
become selectable completed databases. Start a new build to retry; old build
folders are retained. Building explicitly permits preparation of the dedicated
Conda environment if needed. First-time pipeline setup also installs it.

Stages: validate → retrieve/import → DIAMOND index → package → checksum verification.
Each stage records logs and Snakemake benchmarks. Outputs live in a unique
Bactologix-managed `resources/micpscope/reference-…/database` directory.
Choose **Select for future drafts** to save its path; existing runs are unchanged.
The current MICP protein report does not yet search these references. Database
construction and downstream search integration are separate capabilities.

## Local input example

```yaml
schema_version: "1.0"
name: my-reference
version: "0.1.0"
proteins:
  - id: reference_001
    path: /absolute/path/to/proteins.faa
    record: exact_fasta_record_id
    evidence: user_supplied_annotation
    references: [publication-or-source-id]
    license: specify-source-license
regulatory: []
annotations: []
modules: []
references: []
```

The UI requires local paths on the machine running Bactologix (Windows paths
are translated by its WSL path adapter). FASTA IDs must be exact and unique.
For direct Snakemake use, relative paths resolve against the specification.
Each protein source selects exactly one FASTA record. Full-length identical
sequences retain their separate reference identities; no automatic deduplication
or merging of evidence occurs.

For a UniProt download replace `path` and `record` with `accession`, integer
`taxon_id`, integer `sequence_version`, `sequence_sha256`, and
`curation_status: approved`. Retain id, evidence, references and license.
The SHA256 covers the uppercase sequence, without FASTA headers or whitespace.
Only canonical accession downloads are supported; no arbitrary URL execution.
All records are explicit user selections. Build success does not certify
functional annotation, literature claims, licenses or suitability.

Regulatory entries use a local DNA FASTA plus `record`, `accession_version`,
`feature_type` (promoter, binding_site, upstream_region or regulatory_rna),
`start`, `end`, `strand` and `sequence_sha256`. Coordinates are 1-based inclusive;
negative-strand extraction is reverse-complemented before checksum comparison.
Only ACGTN reference DNA is currently accepted. Origin-crossing circular regions
must be represented separately; they are not silently wrapped. This imports
supplied regulatory evidence; it does not predict or infer promoters.

Annotations, modules and references are carried as user-supplied metadata, not
converted automatically into validated detection rules. Unresolved literature
entries should remain in the separate curation catalog until sequences and
coordinates are selected.

The database contains proteins.faa, regulatory.fna, proteins.dmnd, DIAMOND version,
reference manifest, sequence provenance, raw downloaded JSON when applicable,
checksums.json, database.zip and an external completed.json receipt. ZIP content
and file checksums are verified before registration. Raw input paths are recorded
for traceability; consider this when sharing the archive.

Direct use:

```sh
snakemake -s DatabaseBuilder.smk --use-conda --cores 2 --config reference_spec=/path/references.yaml build_dir=/path/new-build threads=2 memory_mb=2048
```
