# PR-03 exact lifecycle approval

The owner explicitly approved implementation in Codex task
`01a0deee-ad9c-7d62-877c-88d39fb7e265` on 2026-09-26.

- Repository: `JohnnyFiv3r/memoriesql` (public core).
- Approved packet: PR #46, commit `2baffc688291dbfdeccaa46f7ca05f6e9ef0e12c`.
- Approved blob: `docs/relation-assessment.md`.
- Git blob object: `cf9eab25b3e5446f2eefbc03b9360a3530877474`.
- SHA-256: `69a0d985466c6610a3a2abc73bcef9242749ff9535cb709e32867c7e06414d2a`.
- Durable approval reference: https://github.com/JohnnyFiv3r/memoriesql/pull/46#issuecomment-5849423968.

Defaults L1–L5 are approved: human-only governance under current endpoint-write
and dependency/evidence-read authority; serial disputes with evidence-backed
confirmation closing prior disputes at the compared head; immediate recorded-time
operation, terminal withdrawal and no confirmation across corrected pins;
explicit indeterminate/unsupported roots and null counts; disputed/pending
accepted assertions retaining cycle reservations until withdrawal/replacement.

The exact Git blob hash was verified before implementation. Fresh `origin/main`
was `e8cfa0df3c1f8109c199a8126c0556624bc421b6`, an ancestor of the approved head.
PR #46 was open and unmerged. This implementation is stacked on that approved
head and depends on it; it does not merge the dependency. Approved document bytes
remain unchanged, including their historical proposal-status wording.

PR-05 confirmed allocation: migration 0030 belongs exclusively to PR-03 lifecycle;
PR-05 consumes the shared interface and reserves 0031+ for retrieval/results.
PR-03 implementation does not depend on the unseen holdout. The PR-05 holdout gate
remains separate. Approval here authorizes no merge, release, version selection,
publication, provider calls, owner-data access, deployment or checkpoint execution.
