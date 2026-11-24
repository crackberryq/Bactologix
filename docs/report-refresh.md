# Refresh an existing report

Open a saved run → Results & downloads → **Regenerate report**.
The preview checks the saved evidence and identifies the original analysis version
and the selected ready installed pipeline renderer. To use newer presentation code,
update that pipeline and select its installed version in **Install & update** first.

**Create refreshed report** queues a report-only job. The job captures the original
JSON, renders a new HTML edition, verifies that the evidence and renderer stayed
unchanged, and registers the edition under **Saved report editions & registered reports**.
Original HTML, JSON, configurations and analysis outputs are retained.

Each edition lives under `report-refreshes/<job-id>/` in the Bactologix workspace.
Its `provenance.json` contains source and renderer hashes, original analysis and
renderer versions, timestamp and report checksum. Use **Open report folder** to
access this folder and its evidence snapshot; the HTML also shows edition provenance.

GeneScope supports saved `micpscope/results.json` schema 1.3. BaSIS supports
`safety/evidence.json` schema `basis-report-1`, and owned follow-up runs support
`safety/deepsafe-report/evidence.json`. The selected installation must include the
corresponding renderer. HybAs supports `reports/hybas/results.json` schema
`hybas-report-1`, with `metadata/run_summary.json` as a legacy fallback. Select
HybAs 9.0.1 AWARE or newer to render these saved results. Legacy summaries lack
some detailed decisions and annotation counts; these are marked unavailable.
MultiQC remains accessible as a detailed tool report.

A refresh can update layout, charts and reporting summaries based on saved data.
It does not apply a new reference database or analysis threshold, predict features,
generate trees, install environments, or run a Snakemake workflow. Missing source
artifacts can leave panels unavailable. The report shows its saved evidence scope;
refreshing does not make an incomplete analysis complete.

For a full BaSIS run, use **Regenerate report** on the safety report to link
the saved DeepSafe review outcomes. The edition includes a follow-up page when
the sample, configuration, and screening source hashes match. A different
follow-up that owns the output has its own regeneration button. To refresh
only the DeepSafe page, use the button next to **Follow-up report**.

With a HybAs renderer that supports the edit-density link, regenerating
**Polishing trajectory** also renders the validation page from retained
measurements when available. The link opens the edit-density section for
the same edition. **HybAs research report** regenerates the research page
and its available trajectory and validation companions together.
