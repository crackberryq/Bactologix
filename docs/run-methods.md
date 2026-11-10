# Run-specific Methods and data flow

Open a saved run and select **Methods & citation**, then **Generate Methods** or
**Refresh Methods**. The view explains this run's tools, purpose, inputs, outputs,
versions and evidence. The combined pipelines are experimental: scientific
validation for their intended application has not been established. Published
evidence about an individual tool does not validate the assembled workflow.

## Snakemake versions

New executions record Snakemake and controller Python versions inside the actual
controller invocation. Later environment checks do not replace that identity.
Attributable failed or interrupted observations identify their controller without
implying successful analysis. An older run without such evidence remains **Not
recorded**; updating the application cannot reconstruct its historical version.

## Diagram evidence

For new runs, bounded Snakemake events identify rule names, declared input/output
paths, and reported outcomes. The observer does not read sequence contents,
capture shell commands, or monitor physical byte transfers. Rule completion does
not establish execution of every optional executable inside that rule. Source
descriptions explain tool roles only when they match the saved workflow.

Each attempt has its own trace. An empty trace means no rule events were recorded;
it does not borrow earlier steps or prove that no analytical work occurred.
Missing, unsupported, ambiguous or capped events are labelled as partial coverage.
Observation does not change workflow settings, tools or scheduling decisions.

For older runs without attributable traces, supported unchanged sources provide
a **configured dependency diagram** based on saved settings. These arrows show
the configured route. Native metadata can label reported execution, reuse or
skips, but cannot establish observed file transfers. Unknown or modified sources
receive an explicitly unresolved overview rather than an invented tool chain.

Read the caption and evidence labels. The diagram is a reproducibility aid,
not scientific validation or a safety assessment.

## Downloads

- **Diagram SVG:** scalable figure for documents and printing.
- **Diagram JSON:** nodes, dependencies, identity and evidence labels.
- **Printable HTML:** Methods, diagram, tool explanations and reproducibility
  supplement; print to PDF from a browser.
- **Reproducibility ZIP:** includes `methods-flow.svg`, `methods-flow.json`,
  Methods text/Markdown/HTML, frozen settings, provenance and available citations.

Review unresolved versions, database identities and citations before publication.
Exports can contain sample identifiers and local paths. Refreshing Methods does
not execute the analysis or install software.
