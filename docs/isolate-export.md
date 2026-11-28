# Isolate locations, export and analysis history

Open **Samples → Analysis & history** to see the registered input folder and the
output folder of every analysis linked by saved Bactologix records. Registering a
folder does not move its files. Ordinary run outputs live below
`<workspace>/runs/<run-id>/outputs/<sample-id>/`; logs, report editions and saved
metadata may live elsewhere. A copy of the input folder alone is therefore not
a complete analysis archive.

## Copy to USB, another disk or a synced folder

1. Finish or cancel queued/running work in this workspace.
2. Choose **Samples → Export isolate**, then enter an existing destination folder
   accessible to the computer running Bactologix. Under WSL, a Windows drive is
   normally addressed through `/mnt/<drive>/...`. A USB drive must already be
   mounted and writable.
3. Select the destination type, including **SharePoint-synced folder** when
   applicable, and preview the copy. Review linked analyses, bytes, missing files,
   warnings and the destination. Editing the destination requires another preview.
4. Choose **Copy & verify**. Bactologix creates a new, uniquely named package;
   it does not overwrite an existing package or remove original files.
5. Review the receipt in Activity or the isolate export history. Open `index.html`
   inside the package to browse its reports, Methods, history and file manifest.

The worker streams files directly to the destination, computes SHA256 hashes and
reads the copied files back to verify them. It checks for changed source files,
changed records, destination replacement and insufficient space. Other mutations
and new work in the same workspace wait until the export finishes or is cancelled.
Independent programs can still modify files; a detected change fails the copy.

An interrupted, cancelled or failed attempt can leave a partial destination
folder. Its original files remain intact. Review the receipt and use a new preview
for a new attempt. `EXPORT-COMPLETE.json` is written only after local verification
succeeds. A **verified local copy** is distinct from **complete historical scope**:
an export can correctly copy everything available while reporting unavailable
historical files or evidence. Saved input checksums are compared during copying;
a mismatch or an unavailable historical checksum remains explicit. Unavailable
Methods documents also make the final historical scope partial.

The SharePoint option writes to a local synced folder. It does not sign in to
Microsoft, upload through Microsoft Graph, or verify a cloud copy. Check that the
sync client has completed and the destination copy is available before using
the separate Storage cleanup controls. This feature never deletes source data
automatically, including after a successful export.

## Package contents and limits

The package gathers the registered isolate inputs and analyses connected by
explicit saved identity and lineage: HybAs runs, downstream BaSIS and GeneScope
analyses, variants, follow-ups and archived runs. It includes available output
files, frozen settings, logs, report editions, relevant saved records, Methods
exports and history documents. A matching sample name alone does not establish
a relationship. Unregistered analyses and already deleted files cannot be found
or reconstructed automatically.

Repeated staged links to the same input are copied once. `manifest.json` and the
HTML index map their original locations to the copied file. Unsafe external
links, cycles, special filesystem objects and Windows-incompatible filename
collisions block copying. The package contains materialized files rather than
links back to the original computer.

Software environments and reference database contents are not bundled. Their
available identities and versions are retained in the records. This is a portable
evidence package, not an automatically relocated runnable workspace. Original
reports and configurations retain their contents; absolute links inside a native
report may still point to the original machine. Use the package index and path
map to locate the corresponding exported files.

Records, labels, logs and configuration files can contain local paths and other
research information. Review the package before sharing it. The export excludes
unrelated workspace records rather than copying the complete workspace database.

## Analysis-history diagram

The sample dossier shows linked runs and recorded execution attempts. Download
the diagram and evidence as SVG, PDF, JSON or HTML. The export package includes
the same formats. Use the vector formats as a starting figure for a publication
and review the associated JSON and Methods before describing the work.

The diagram distinguishes recorded attempts, recorded stage evidence and planned
or unknown history. A successful dry run is not an executed analysis. Failure,
cancellation and interruption remain visible; the presence of a later result
file does not turn an earlier failed attempt into a success. New completion
evidence is retained per attempt so a later rerun does not replace that record.

Older runs may lack stage evidence, and shared follow-up output may have replaced
earlier files. These gaps remain explicit. The graph cannot infer an exact
historical tool execution or parameter value from a pipeline name or current
output alone. It is an evidence view of the available records, not a certification
of scientific validity or a reconstruction of unrecorded history.
