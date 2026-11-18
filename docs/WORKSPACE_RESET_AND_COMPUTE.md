# Workspace reset and compute guidance

## Developer reset

In **Workspace tools → Reset workspace** at the top right, review the current workspace path and select Preview reset. The dialog is available independently of Storage loading. The preview lists exact deletion paths, file sizes, retained paths and blockers. Type `RESET THIS WORKSPACE` and acknowledge the reviewed permanent deletion to enqueue it.

Database deletion is off by default. Selecting it requires a fresh preview and the additional phrase `DELETE MANAGED DATABASES`. Only positively recorded app-created, eligible database destinations inside this workspace can be removed. Linked or ambiguous database locations are retained, including manually selected locations inside the workspace. External databases are never selected. Bundled resources inside an environment are removed with that environment unless a registered dependency protects them.

The application, bootstrap Conda, original sample files/registrations, terminal job history and deletion receipts remain. Shared, unverified and model caches may remain. This is a reset of the reviewed managed data, not an operating-system uninstall or a promise that every workspace byte will disappear. Known related workspaces and process identities are checked; unregistered dependencies or undiscovered workspaces cannot be inferred. WSL virtual disk files may not shrink immediately after deletion.

An interrupted deletion leaves a receipt in Storage and blocks new mutations until recovery. Do not manually remove that receipt. Preview changes invalidate confirmation. Reset is not allowed while queued/running work or uncertain native ownership remains.

## Database download again

Use **Download again** on a configured database card. Review the provider and confirm **Prepare a new snapshot**. Exact replay is not offered when the original provider release, URL and checksum cannot be reproduced by the checked pipeline contract. The replacement may differ.

The job prepares a separate workspace destination and retains partial outputs on failure. Its old database, selected path and frozen runs stay unchanged. Select the completed replacement and save locations explicitly after checking it. This operation does not delete the original database or change existing results. Preparation can require substantial disk space; the workflow low-disk pause is not a guarantee for database preparation jobs.

## Swap recommendation

The compute panel and run override panel offer **Calculate swap recommendation**. Kraken guidance uses its three core files and the reviewed working-memory estimate, the proposed physical-RAM budget, active usable swap and required filesystem capacity (including WSL host backing/swap drives when applicable). A calculated additional allowance is not a new swap file.

**Use recommendation** fills the form only. Changed inputs invalidate the recommendation; saving defaults, validating and launching remain explicit actions with fresh checks. Missing measurements, insufficient active swap or inadequate storage headroom prevent applying a proposal. Disk paging may be much slower than RAM. Bactologix does not edit `.wslconfig`, allocate swap or restart WSL.

## HybAs development direction

HybAs 9 remains an immutable reference; new native features target HybAs 10. A matching legacy hybrid/AWARE/Tripolish route preserves many existing commands, but identical output requires matching actual inputs, database contents, tool builds, model weights and execution behavior. A version label or shared setting name alone does not prove reproducibility. No AFB02 cross-version sequence-equivalence result is claimed.

For sources exposing the checked version-2 Medaka contract, new drafts offer the pinned documented default after explicit model choice and exact metadata matching. Old saved configurations with an omitted policy stay strict. Fallback is recorded in outputs and Methods and does not establish accuracy for a particular read set. Mixed/incomplete metadata, invalid inputs and model/download/execution failures do not trigger silent model substitution.
