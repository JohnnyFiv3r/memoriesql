# Contributor guidance

This repository is the staged public boundary for the memoriesQL open core. Keep changes provider-neutral, deterministic, content-blind, and default-deny.

- `contracts/public-registry.json` is the sole authority for generated catalogs.
- Never discover export candidates by scanning another repository.
- Do not add desktop UI, installers, update channels, product module manifests, provider-specific discovery or parsing, private evidence, owner data, or credentials.
- The approved migration substrate is limited to the explicit migration inventory, runner, schema inspection and resource closure. Preserve SQL 0001–0020 byte-for-byte. The public-authored schema-19 transition adds only bounded, explicitly authorized fold recovery; see `docs/transcript-fold-recovery.md`. The public-authored schema-20 transition adds only explicit author-controlled source revisiting with trusted delivery and unique mandatory coverage; see `docs/source-revisiting.md`. The runtime cut is limited to `contracts/runtime-inventory.json`: neutral runtime and internal static mechanics, with caller-supplied composition. The public-authored schema-15 transition adds only the inventoried immutable-observation commands, task definitions and adapter dispatch. The public-authored schema-16 transition adds only version-1 complete-input evidence package storage and bounded authorized reads; see `docs/evidence-packages.md`. The public-authored schema-17 transition adds only qualified logical-unit materialization and exact sealed-package binding in the existing queue, with execution explicitly unavailable; see `docs/logical-unit-materialization.md`. The public-authored schema-18 transition adds explicit complete-input activation, bounded evidence-reader v2, trusted dispatch exposure and fenced initial application through the existing executor; see `docs/complete-input-execution.md`. No production provider binding or trust policy is added. No supported plugin platform is included.
- Generated catalogs must be refreshed with `python scripts/generate_catalogs.py` and verified with `--check`.
- Treat all 0.x APIs as experimental. Preserve an already published contract ID and version payload; model breaking changes as a new contract version.
- Every contribution must carry a Developer Certificate of Origin sign-off.
- Open requested PRs in non-draft state, subscribe to their comments, and address, answer, and resolve actionable review threads. Require CI on the final PR commit. For this bootstrap, request one broad review and at most one focused rereview; do not merge, publish, or change repository visibility without owner authorization.

The owner-authorized schema-21 slice adds only explicit source-stable logical
materialization, reviewed producer identity scope and compatibility fences. See
`docs/logical-unit-materialization.md`. Preserve SQL 0001–0020 and all earlier
record bytes. That implementation slice authorized no provisioning or release. The separately
owner-authorized 0.0.7 release follows `docs/releasing.md`, preserves all 21 SQL
files and existing contracts/runtime, and stops for owner-only protected upload approval.

The owner-authorized PR-02P development slice permits explicit provider-neutral
bounded real-model admission and schema-22 atomic authored local mentions. These
are unreleased forward changes, not replacement 0.0.7 release artifacts. Preserve
the published SQL/contract bytes and the existing publication gates. Version
selection, merge, tag, release and private consumption remain separately gated.

The owner-authorized forward schema-23 slice adds provider-neutral typed bead
classification and one attributable accepted contribution. P owns canonical
writes and fixtures; Q owns authorized stored-result reads. Preserve historical
migrations 0001–0022 and records; no provider-specific integration or release is
part of this public slice. See `docs/bead-classification.md`.

The owner-authorized forward schema-27 slice (PR-03, authorized 2026-09-23) adds
optional authored claims, relations to explicitly pinned candidates, candidate
coverage, append-only claim/relation lifecycle history, governed relation vocabulary
and a bounded relations read. Preserve migrations 0001–0026 and earlier records. No
specialist relation judgment, inferred edges, agent recall change, provider binding or
release is part of this slice. See `docs/authored-claims-and-relations.md`.

The Q-owned forward schema-24 slice permits bounded, currently authorized stored
bead/result and evidence inspection. Preserve migrations 0001–0023 and earlier
records. No inference, canonical writes, provider binding or private consumption
is introduced. See `docs/stored-bead-inspection.md`.

The owner-authorized 0.0.8 preparation starts from merged main
`8dfa5a2ef6bb4ef52b62e4405ee8a26ccfa36e6b`. Only release metadata, exact repository
publication controls, fresh release inventory, focused verification/provenance and
truthful guidance may change. Preserve runtime behavior, all SQL 0001–0024,
59 contracts, dependencies, ceilings and provider policy. The owner has also explicitly authorized merging the reviewed, qualified release
PR and creating/pushing v0.0.8 once at verified release main after all required
checks. Stop at the protected approval gate; never approve upload. Publication
protected approval and private consumption remain separate.

On 2026-09-22 the owner separately authorized one forward runtime change on top of
released 0.0.9, for resiliency before the next supervised live proof: the executor
binds authored statements to its own run reference before hashing, and the worker
settles data-error canonical-apply refusals as invalid output (PR #31). It targets
the next release, changes no migration, contract payload or dependency, and does
not alter the published 0.0.9 archives. The 0.0.9 preparation paragraph below is
historical.

On 2026-09-22 the owner also authorized the 0.0.10 release: preparing it from
accepted main `f6f649eed5c5908f21a1bf2f4e365582e51ae6e6` (after PR #32), merging its exact
qualified release PR, and creating/pushing `v0.0.10` once at subsequently
verified current release main. The owner updated the environment tag rule for
`v0.0.10`; the existing PyPI Trusted Publisher needs no change and automation
performs no PyPI sign-in. Protected upload approval remains the owner's action;
stop when the publishing workflow waits for it. See `docs/releasing.md`.

On 2026-09-22, after `v0.0.10` was tagged, the owner decided that with no external
forks or consumers the repository owes no backwards compatibility until that
changes, and authorized retiring the historical release bookkeeping: historical
package and candidate inventories, release baseline/integrity fixtures and the
`verify_release.py source` reproduction, the export-provenance manifests and
per-file runtime provenance hashes, historical migration-prefix tests and `0.0.1a1`
references. The current release inventory, the byte-exact migration inventory,
the default-deny runtime closure and the publication gates remain.

The owner-authorized 0.0.9 preparation starts from merged main
`aff4270a549f7618d99db3735020ccb2785f32f1`. Preserve runtime behavior, all 26
migrations, 61 contracts, dependencies and historical release inventories. Release
metadata, exact publication controls, a new immutable inventory, focused tests and
truthful guidance may change. The owner authorizes the qualified release-PR merge
and one v0.0.9 tag push at subsequently verified release main, after current
publisher/protection checks. Stop at actual owner-only protected publish approval;
never approve or bypass upload. This does not authorize private adoption, provider
calls, migration, deployment or checkpoint execution.
