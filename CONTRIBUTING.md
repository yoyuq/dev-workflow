# Contributing

Report bugs with the workflow version, OS, Python version, harness and CLI version, the smallest reproducible plan, expected/actual behavior, and relevant redacted errors. State whether a worker was simulated or a real model. Do not upload credentials, private project files, viewer grants, or full runtime databases.

For changes, open a focused branch and describe the problem, resulting behavior, and meaningful validation. Run the Python and dashboard checks in the root README. Add behavioral regression coverage when changing execution, review, recovery, requirements, or language contracts.

## Package integrity

The installable Skill is `dev-workflow/`. The repository-level documentation, CI, and additional notices are outside that directory so the published 2.12.2 package remains byte-for-byte identical to its verified snapshot.

`dev-workflow/package-manifest.json` hashes every package file except itself. If you change the Skill, deliberately update the manifest's file hashes and version, the version in `SKILL.md`, and affected version assertions. Run `workflow_portable.py doctor` and the full relevant checks before publishing a new version. Do not silently edit a frozen package or existing run's pinned Skill.

Keep `references/modules/**` and `module-index.json` preserved for this snapshot. Third-party guide updates must be an explicit snapshot upgrade with upstream sources, license review, and regenerated per-file digests. Runtime adaptation belongs in the adapters/routing layer. Do not remove existing copyright notices or claim an unknown upstream commit.

Run state, preferences, logs, credentials, and model output belong outside the Skill and public commits. Examples must be synthetic and must not present simulated work as real model or project acceptance evidence.

Original contributions use the root MIT license. Third-party files retain the licenses listed in NOTICE.md.
