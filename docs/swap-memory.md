# Run with a disk-swap allowance

Bactologix separates physical RAM from an optional disk-swap allowance. A native
workflow reservation can exceed the physical RAM budget even when Linux has
enough active swap. A dry-run rejection of that reservation is not an observed
out-of-memory failure.

## Choose the allowance for a run

Open a new run or edit a saved run, then expand **Customize compute resources**.
Keep the physical RAM budget appropriate for the RAM available inside Linux or
WSL. Enable the swap option and enter an additional allowance in MB. Review the
total scheduler budget before saving. Editing a saved run creates a revision;
the original configuration and outputs remain unchanged. Validate the revision
before execution.

For example, a 28,672 MB RAM budget plus a 98,304 MB swap allowance gives a
126,976 MB scheduler budget. This exceeds a 113,868 MB Kraken reservation. The
example requires enough currently available RAM and swap, including the system
reserves checked at launch; it is not a universal recommended allocation.

Swap is disabled for new runs unless explicitly selected. Existing saved runs
retain their policy, and a revision inherits its parent's allowance until it is
changed. An allowance is a scheduling choice, not a request to create a swap
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
