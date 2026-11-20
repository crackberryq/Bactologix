# Run with a disk-swap allowance

Bactologix separates physical RAM from an optional disk-swap allowance. A native
workflow reservation can exceed the physical RAM budget even when Linux has
enough active swap. A dry-run rejection of that reservation is not an observed
out-of-memory failure.

## Save a pipeline default

Open **Pipelines & databases → Compute & CUDA**, then select the pipeline under
**Pipeline compute defaults**. For HybAs, select **Calculate swap for each new
HybAs draft** and choose **Save compute defaults**. Bactologix checks the
selected database and current capacity when creating each draft. It records a
numeric allowance in the draft, including zero when Kraken is off or fits in
RAM. A capacity check that fails gives the reason.

To use the same numeric allowance for future runs, leave automatic calculation
off, select **Allow disk swap by default**, enter the amount, and save. You can
also choose automatic or manual sizing for an individual new HybAs draft
under **Customize compute resources**.

The initial default is off. CPU/RAM power presets preserve an explicitly saved
swap allowance and continue to calculate physical-RAM budgets separately. Saving
these settings does not create swap or reserve memory. Available capacity is
checked again when a run is validated or executed.

## Override the allowance for a run

Open a new run or choose **Edit settings** on a saved draft, then expand
**Customize compute resources**. In HybAs, choose **Calculate and fill swap
allowance**. Bactologix measures the selected Kraken index and takes the higher
of its estimated full-load requirement and any explicit Kraken reservation.
It subtracts the RAM HybAs can use and fills the additional allowance in whole
MiB (MB in this form). When RAM covers the requirement, the allowance is zero.
If the measured capacity cannot support the proposal, review the reported
shortfall. You can also enter a manual allowance.

For example, 113,868 MB required and 42,102 MB effective RAM gives a 71,766 MB
swap allowance. With 72,702 MB usable swap after reserve, that leaves 936 MB of
usable swap outside the allowance. The combined memory and filesystem checks
must also pass.

Review and save the draft, then select **Validate run**. An editable draft keeps
its run ID. If an existing run needs a revision, save and validate that revision.

You can change the inherited allowance or turn it off for an individual run
without changing the pipeline default. Existing saved runs retain their policy,
and a revision inherits its parent's allowance until it is changed, even if the
pipeline default has since changed. An allowance is a scheduling choice, not a request to create a swap
file, a hard operating-system limit, or a measurement of actual memory use.

## What is checked

The inventory reads Linux-visible free RAM and free swap, including reported
container restrictions. WSL swap is separate from the Windows pagefile. The
checks account for cgroup v2 swap limits and cgroup v1 combined RAM-and-swap
limits. Unknown capacity cannot authorize a swap-backed launch.

Validate and Run refresh these checks when queued and again in the worker, so a
previous validation cannot substitute for current available capacity. Headroom
is retained for the system. Other processes can consume memory after a check;
the check cannot guarantee completion or prevent all out-of-memory conditions.

Disk paging can make a run substantially slower. Keep the existing single
concurrent Kraken job limit. The extra allowance increases the workflow's total
scheduling budget; it does not automatically increase SPAdes, assembly,
polishing, or other tool-specific memory settings. Presets remain based on
physical RAM. This control does not switch Kraken to memory-mapping or change
the selected reference database.

## Reproducibility

The allowance is saved in the frozen run configuration as
`bactologix_compute.swap_mb`; zero or an absent key disables it. The launch log
records the RAM budget, swap allowance and total scheduler budget. Methods
describes those saved choices separately from measured resource use. Older
runs do not gain a retrospective claim that swap was used.

The native HybAs reservation check remains in place. Bactologix supplies the
explicitly chosen combined scheduler budget to the existing workflow, so the
analysis parameters do not need to change. HybAs Dev requires the reviewed
10.0.0a4 source or a later source with the same approved swap interface. Earlier
Dev sources cap scheduling at physical RAM; update the pipeline and create a
new run instead of editing the frozen workflow in an old run.

HybAs Dev keeps its existing physical-RAM resolution, including the 80% ceiling
when total RAM is Auto. It adds the explicit allowance only after that
resolution. The budget shown by Bactologix is therefore the requested maximum;
the pipeline may use a smaller effective scheduler budget. Its resolved-settings
receipt and native log record the effective RAM, swap and scheduling amounts.
Tool-specific automatic limits are resolved from the RAM portion alone.

References: [Microsoft WSL memory and swap settings](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)
and [Linux cgroup v2 memory controls](https://docs.kernel.org/admin-guide/cgroup-v2.html).
