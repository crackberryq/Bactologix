# Generated output preservation

Native tool results, intermediate stages, logs and failed attempts are engineering
research records. The current workflows preserve these records by default.

**Results & downloads → All tool outputs** lists every file type one folder at a
time. **Open folder** opens the selected location. Choose **Run files & tool
history** to reach workflow-owned `retained/` folders alongside the sample output.
Earlier Bactologix snapshots are available from the same browser. Listings are
live observations; they do not prove a tool ran or that a result is scientifically
complete. Missing measurements are not reconstructed or inferred.

Current, explicitly reviewed pipeline versions archive a rule's existing output,
logs, benchmarks and declared native sidecars before Snakemake can remove them.
Renaming into unique retained folders avoids duplicating every unchanged large
file. Failure/retry outputs remain available. Failed preservation stops replacement
rather than discarding data. Archives record original paths and the preserving
operation without inventing prior success.

Older frozen workflows receive an independent, SHA256-verified copy of existing
generated files in `output-history/<attempt>/outputs` before execution. This may
need substantial additional space. Unknown external links, changed source files,
copy errors, cancellation or insufficient storage stop the workflow. Partial
copies remain available. Registered original inputs remain recorded links, not
independent historical copies of raw inputs. An old frozen workflow still uses
its old tool cleanup behavior inside a new invocation: install the updated
pipeline and create a new run for native retention.

**Export isolate** copies linked run directories, output histories and native
files with destination verification. Source files remain. A local copy does not
confirm a SharePoint upload. Use **Storage** for deliberate, reviewed removal
after transfer. Research outputs are never automatically deleted to free space.

Tool-supported retention flags are enabled where available. HybAs 9.0.6-AWARE
and 10.0.0a3 additionally preserve reviewed BUSCO Python-managed analysis artifacts
and logs before deletion or replacement. Dev 10.0.0a3 also adds this capture for
Flye. BUSCO per-job stdout/stderr is saved separately for successful and failed
attempts. Visible `native-retained/` directories hold these files and their
receipts. Use **Run files & tool history** to inspect the retained files alongside
each run's native outputs.

This capture does not include every private scratch file: native SAMtools and
other compiled programs can delete their own spill files internally. Streams,
kernel scratch and in-memory states are not recorded. Pipeline documentation
identifies the reviewed versions, captured artifacts and remaining limits.
Updating cannot recover files deleted by older versions. Preserving stage files
does not itself compute per-stage quality or annotation measurements. Use the
[validation baseline procedure](validation-baseline.md) to test retention against
your expected artifact list and preserve the actual run identities.
