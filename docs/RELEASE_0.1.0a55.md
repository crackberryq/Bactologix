# Bactologix 0.1.0a55 — Alpha

**Status: unreleased development alpha.** Planned tag: `v0.1.0a55`.
Implementation and current CI are in [PR #40](https://github.com/crackberryq/Bactologix/pull/40).
The Windows desktop setup and session-lifecycle changes from 0.1.0a54 are retained.

## Changes

Import and preview HybAs library manifests from Samples. Preserve platform,
HiFi/CLR chemistry, preparation, orientation and read groups. Freeze declarations
per run and verify input size/mtime plus frozen manifest bytes before resume.
Existing runs and original reads remain unchanged.

Require a reviewed `hybas-v2` exact-commit compatibility contract. Fresh supported
workspaces default to Auto for the supported controls; saved defaults and manual
overrides remain in effect. Discover the separate SPAdes, NxTrim and Flye recipes
and the integrated research report. The existing installer honors the approved
source's pinned Python and Snakemake requirements.

New release names follow `Bactologix <version> — Alpha/Beta/Release candidate`,
with `v<version>` tags and one versioned release document. Historical tags remain
unchanged. The application remains distributed as wheel/source and Windows setup
artifacts, with their existing digest-verification gates.

## Update and test

No 0.1.0a55 release is published by this draft. Once published, use Update
Bactologix → Refresh releases → v0.1.0a55, then restart the application.
Keep existing workflow installations and outputs; this update does not activate
an unpublished HybAs workflow. A separately published and approved HybAs alpha
is required for manifest execution.

See [hybas-manifests.md](hybas-manifests.md) for import, preparation and resume
behavior. Validate a new draft before installing its tools or executing it.

## Verification and release scope

Required CI covers Python 3.11/3.12, legacy adapters, native Snakemake 9.27.1
manifest DAGs, browser interactions, environment deployment, Windows desktop
behavior and package construction. Current results and immutable tested commits
are recorded on PR #40; published notes add the exact release commit.

Local checks passed 604 tests with 57 environment-dependent skips, plus three
native DAG checks using HybAs commit `8c7bdd792259b5e540727a2283c8300b893b66c3`.
These are software checks. Input identities use size/mtime, not content hashes;
workflow provenance may add content hashes. No new scientific accuracy or
physical workstation qualification is implied. HybAs reference-data qualification
and activation in the compatibility registry remain separate release work.
