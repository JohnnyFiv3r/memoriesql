# 0.0.12 release: preparation, owner gates and publication record

## Publication record

Under the owner's direction, `v0.0.12` was pushed at release main
`61bd53366ea653aeed1ac13b3df38e63f4b0cb91` on 2026-09-25 (13:37Z), after the
exact-main package run [36140624584](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/36140624584)
succeeded. The publishing run
[36142106238](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/36142106238)
verified the two archived artifacts against the committed inventory and, after
the owner's protected approval, uploaded them to PyPI at 13:48Z. Independent
verification afterwards, using public unauthenticated reads only: the PyPI wheel
(`bfc00c11…418ce`, 720,620 bytes) and sdist (`dd8f11dc…efbd3`, 643,655 bytes) are
byte-identical to the archived CI artifacts and match the committed inventory;
each carries a trusted-publishing attestation for `JohnnyFiv3r/memoriesql`,
`publish-pypi.yml` and environment `pypi`; PyPI metadata reports version 0.0.12,
`>=3.13,<3.15` and the three pinned dependencies, and neither file is yanked; a
fresh hash-pinned installation from the PyPI index reports version 0.0.12, 64
contracts, 29 migrations and intact `RECORD` hashes, and passes the
package-isolation and release-installation proofs. `memoriesql==0.0.12` may now be
pinned. Publication is not private adoption: consumers still pin, qualify and
compose it separately. The preparation record below is retained as written.

This preparation packages merged main
`95b160709bda3dc794f139ac761772e70d681aeb` (PR #42 merged 2026-09-25), whose
exact-main package run is [run 36138162249](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/36138162249)
(package and both installed-route compatibility jobs). The release changes
metadata, exact publication controls, a new artifact inventory and guidance.
Relative to published 0.0.11 it carries two owner-authorized forward capabilities:
the canonical-apply refusal reason (schema 28) and
[relation assessment after acceptance](relation-assessment.md) (schema 29, task
`memory.semantic.assess-relations` revision 1). It preserves the bytes of
migrations 0001–0027, all 63 published contract payloads, dependencies,
authorization and accounting, and adds migrations 0028 and 0029 and one contract
record (64 in total).

On 2026-09-25 the owner directed the 0.0.12 release to its protected upload
approval: preparing it, merging its exact qualified release PR and
creating/pushing `v0.0.12` once at subsequently verified current release main;
that direction is recorded in `AGENTS.md`. That merge SHA, not the earlier merged
main or PR head, is the release identity. Protected upload approval remains
exclusively the owner's action. Stop when the actual publishing workflow waits for
that approval; never approve, bypass or upload directly. The 0.0.11 authorization
is historical, not authority for this release.

## Exact release identity

| Setting | Prepared value |
| --- | --- |
| Distribution / version | `memoriesql` / `0.0.12` |
| Repository / ID | `JohnnyFiv3r/memoriesql` / `1357510758` |
| Only accepted new tag | `v0.0.12` |
| Workflow / environment | `.github/workflows/publish-pypi.yml` / `pypi` |
| Authoritative release inventory | `docs/verification/runtime-0.0.12-package-artifact-inventory.json` |

On 2026-09-25 the owner advanced the environment tag rule to `v0.0.12` and stated
that the PyPI configuration is already set: the existing Trusted Publisher for
project `memoriesql`, repository `JohnnyFiv3r/memoriesql`, workflow
`publish-pypi.yml` and environment `pypi` needs no change, and automation performs
no PyPI sign-in or check. Immediately before tagging, a read-only check must find
no `v0.0.12` tag in this repository. Stop if the tag is unavailable; never select
another version or move a tag. Do not change publisher configuration or access
credential values.

## Development qualification and historical verification

Development receipts bind full checkout SHA, filenames, sizes/hashes and
`unreleased-development`; they never authorize publication. Compatibility jobs
verify that receipt, rebuild a wheel from sdist, prove it byte-identical to the
reviewed wheel, and qualify the installed route with checkout access denied on
each supported interpreter. Development archives retaining 0.0.11 metadata are
not published 0.0.11 and cannot be renamed or reused as 0.0.12.

Published archives, tags, migrations and contract payloads remain immutable. After
the `v0.0.10` tag the owner retired historical inventories, baseline fixtures and
the frozen-source reproduction from the repository; the tagged source and the
published archives are the record. This preparation likewise replaces the 0.0.11
inventory with the 0.0.12 one.

Fresh 0.0.12 wheel/sdist builds and an independent sdist-to-wheel reconstruction
must agree. Independently download final-head hosted archives and compare them
with the committed 0.0.12 release inventory and exact-head development receipt. A
changed downloaded receipt cannot override the committed release hashes.

## Existing fail-closed publication path

Only creation of exactly `v0.0.12` in this repository can trigger publication.
PRs, branch pushes, manual dispatch, wildcard and historical tags cannot upload.
The tag must target current main with genuine 0.0.12 metadata; the latest package
main-push run at that exact SHA must be successful. PR qualification alone is
insufficient.

The verify job downloads only `memoriesql-python-<exact SHA>` from that selected
run and checks the two filenames, sizes and SHA-256 values against the committed
release inventory. Missing, extra, changed or symlinked archives fail closed.
Only those two verified archives reach the protected publish job. It uses pinned
actions and OIDC, runs no repository code and alone has `id-token: write`.
There is no publication-time build, rename, dependency resolution, token fallback
or `skip-existing`. Automation must not approve upload or change external settings.

1. Merge only the reviewed release head after exact-head CI and independent archive
   comparison. Verify actual merged-current-main latest push CI and archives again.
2. Recheck tag availability and GitHub environment protections. Stop on a
   mismatch or missing approval gate.
3. Create/push `v0.0.12` once at verified release main under the owner's
   direction. Observe release verification and provide the actual waiting
   publishing run, release SHA/tag and matched archive hashes. Do not invent a URL.
4. Stop for owner-only protected approval. After a separate owner action, actual
   PyPI files, hashes, metadata/provenance and fresh installs must be independently
   verified. Workflow success alone is not publication proof or private adoption authority.

## Candidate capabilities and caller composition

The candidate retains published schemas 1–27 and every 0.0.11 capability:
canonical runtime, evidence recovery/revisiting, local mentions, attributable
classification, stored-result inspection, hard-bounded model admission, declared
evidence scopes, explicit supervised managed dispatch, the run-reference binding,
canonical-apply refusal settlement and authored claims and relations. It adds two
forward capabilities:

- Schema 28 keeps why canonical apply refused an attempt's output, as a bounded
  diagnostic record written before the attempt settles
  ([runtime](runtime.md#run-references-and-canonical-apply-refusals)).
- [Relation assessment after acceptance](relation-assessment.md), schema 29: a
  separate, explicitly activated task pins explicitly supplied accepted beads and
  exact relation-type revisions; an author proposes relations between their
  existing statements and covers every pinned pair; and code accepts a proposal
  only when a provider-neutral specialist finds that exact assertion consistent.
  Disagreement stays unaccepted. The task never changes a bead or vetoes
  acceptance, reconsideration is explicit and linked, and each task runs one
  attempt with no automatic retry. Revision 1 is an explicitly incomplete
  mechanical substrate: assessed assertions have no governed confirm, dispute or
  retract, and append-only dispute and retraction must exist before assessed
  relations are used durably in live memory or qualified for recall.

These capabilities are published in 0.0.12; publication is not a claim of
real-provider quality.

<a id="caller-composition-and-explicit-opt-in"></a>

Installing/upgrading does not migrate a database, provision trust, activate tasks,
start a worker or configure a provider. Consumers separately authorize forward
migration and supply credentials/workspace, source qualification, producer/dispatch/
claim policies, vocabulary, task/module/agent/model composition and trusted exposure.
Bindings and exact pins do not silently upgrade. See [migration operations](migrations.md).

Hard-bounded model admission remains the default: one inference per bounded request,
pre-dispatch token ceilings and no unapproved retry/fallback. Explicit supervised
managed dispatch instead needs its own exact approval, durable allowances and
truthful reported/estimated/unavailable accounting; it does not invent hidden
request counts or hard token/cash bounds. Neither supplies an adapter or live-call
permission. Q reads remain inference-free and recheck current authorization;
legacy, hidden, absent and authored-empty states remain distinct.

Retain the worker/event loop through `wait_for_cleanup()`. Foreground cancellation
and `cleanup_pending` do not establish rollback, remote termination or reconciled
usage. Started writes and late usage remain owned; uncertain settlement stays explicit.

Python remains `>=3.13,<3.15`; dependencies remain `psycopg[binary]==3.3.3`,
`pydantic==2.13.3` and `pydantic-ai-slim==2.27.0`, without provider extras.
Use the reviewed local 0.0.12 artifact before publication; only after independently
verified publication pin `memoriesql==0.0.12`. Existing execution budgets and
experimental API bounds are unchanged.

## Qualification and remaining gates

Release preparation uses focused release/control tests and fresh installed smoke
checks locally; unchanged database suites are not repeated locally. Final release-PR
CI/artifact checks and subsequently merged-main qualification remain mandatory.
Retain completed capability/lifecycle reviews; this release scope receives one
broad review and at most one focused rereview.

Public release precedes separately authorized private pinned consumption. Provider
composition, relation-specialist binding, relation-quality evaluation and live
runs, source fidelity/classification-abstention, total request/token/latency
quality, Q's human inspection path, owner-data proof and CP-2 follow-ups remain
gated. No private work, provider activation/spending, deployment, production
migration or checkpoint is authorized by this release preparation or tag permission.

## 0.0.11 publication record

Under the owner's authorization, `v0.0.11` was pushed at release main
`91e29c92ab61c3366c78f7f9fa9437d619771380` on 2026-09-23 (20:33Z), after the
exact-main package run [35915656747](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35915656747)
succeeded. The publishing run
[35916803725](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35916803725)
verified the two archived artifacts against the committed inventory and, after
the owner's protected approval, uploaded them to PyPI at 20:44Z. Independent
verification afterwards: the PyPI wheel (`9a3ec138…03ec7`, 633,122 bytes) and
sdist (`80ac82ba…0eae2`, 563,555 bytes) are byte-identical to the archived CI
artifacts and match the then-committed inventory; each carries a trusted-publishing
attestation for `JohnnyFiv3r/memoriesql`, `publish-pypi.yml` and environment
`pypi`; PyPI metadata reports version 0.0.11, `>=3.13,<3.15` and the three pinned
dependencies; a fresh installation from the PyPI index reports version 0.0.11, 63
contracts, 27 migrations and intact `RECORD` hashes. `memoriesql==0.0.11` may be
pinned.

## 0.0.10 publication record

The owner created and pushed `v0.0.10` at release main
`33ece99bc473685839ef53e14b71a950b9adb88c` on 2026-09-22 (22:17Z), after the
exact-main package run [35790235188](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35790235188)
succeeded. The publishing run
[35791541401](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35791541401)
verified the two archived artifacts against the committed inventory and, after
the owner's protected approval, uploaded them to PyPI at 22:23Z. Independent
verification afterwards: the PyPI wheel (`2c6f40db…6b3f8`, 548,152 bytes) and
sdist (`0d6189cd…30f76`, 488,793 bytes) are byte-identical to the archived CI
artifacts and matched the then-committed inventory; PyPI metadata reports version
0.0.10, `>=3.13,<3.15` and the three pinned dependencies; a fresh installation
from PyPI reports version 0.0.10, 61 contracts, 26 migrations and intact
`RECORD` hashes.
