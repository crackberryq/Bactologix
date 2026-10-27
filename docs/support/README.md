# Bactologix Support

Free software support for Bactologix, HybAs, and BaSIS, by **Jimi Goldstein · Human Link**.

Bactologix Support is a skills-only ChatGPT/Codex plugin. It helps with installation, configuration, and troubleshooting using official documentation matched to the user's software version, plus error excerpts or diagnostic snapshots the user chooses to attach.

The plugin is free, has no purchases, and is intended for all countries supported by the host platform. ChatGPT account eligibility, availability, and any host subscription charges are separate. The public submission draft was uploaded on 27 October 2026, but the portal currently blocks new skills-only submissions. It has not been submitted for review or published in the directory.

## What it does

- Explains documented installation and configuration steps.
- Helps diagnose software errors using reviewed logs and version information.
- Distinguishes recorded job status from current machine state.
- Suggests verification steps and preserves existing runs and environments.

It has no developer-hosted backend, live connection to a computer, automatic uploads, or tools that run pipelines or change settings. It does not certify scientific results or provide clinical or regulatory determinations.

## Getting help

Ask, for example, “Help me install Bactologix” or “Explain this error for my installed version.” Include the Bactologix version and pipeline commit when available. In releases with **Get support**, generate a preview, review/edit it, download the diagnostic snapshot, and attach it in the conversation. Older releases can use a short reviewed error excerpt instead.

Logs can contain confidential information. Redaction is best effort; review attachments before sharing. Do not submit credentials, patient identifiers, or raw sequencing files for routine software support.

[Support and contact](SUPPORT.md) · [Privacy notice](PRIVACY.md) · [Terms of use](TERMS.md)

[Project source](https://github.com/crackberryq/Bactologix) · [HybAs](https://github.com/crackberryq/Bactomics_HybAs) · [BaSIS](https://github.com/crackberryq/Bactomics_BaSIS)

## Lab testing while directory publication is blocked

Download the [plugin ZIP](../../plugins/releases/bactologix-support-0.1.1.zip). In ChatGPT, use Plugin Creator and attach the ZIP with the request: “Create a private plugin from this Bactologix Support package.” Each tester gets their own private copy; this is not a shared public-directory installation. Availability depends on their account's access to Plugin Creator. No API key or live machine connection is part of this package.
