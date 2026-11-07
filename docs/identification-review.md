# Identification review

The application option is `kraken_review_before_filtering` (boolean, default
false). It requires the native `app/identification-review.json` interface 1 on an
otherwise compatible, immutable HybAs source. Existing unsupported sources reject
the option before a run directory is created.

Phase one uses the normal native command and configuration. HybAs's default
target stops at `reports/identification/review.json` and `report.html`. Only a
successful workflow execution records `awaiting_review`; file presence does not
mark a run ready or completed.

A preview is read-only. A saved choice uses a compare-and-swap token over the run
identity, evidence receipt and prior draft choice. Confirming saves a separate
immutable JSON decision under `identification-decisions/`, retaining the original
YAML. Active jobs, archives, stale windows and previous finalization reject writes.
The UI queues a `continue` workflow job, which performs native validation and an
environment inventory before execution. Missing tools require explicit installation.

Native continuation receives the decision path and its SHA-256 via `--config`.
HybAs verifies the report checksum, sample/config/source/database/input identity
and merged-read/classification evidence hashes. The target choice changes only
the existing decision/filtering stage; normal rerun triggers remain enabled.

Each workflow attempt keeps the normal resource-use receipt. Methods distinguish
original saved settings from the finalized effective target policy. Results keep
the early identification report alongside the final assembly report.

One sample per native reviewed run is supported; Bactologix batches remain
independent per-sample runs. This feature is separate from AWARE+ reconstruction.
