# 0.0.11 release: preparation, owner gates and publication record

## Publication record

Under the owner's authorization, `v0.0.11` was pushed at release main
`91e29c92ab61c3366c78f7f9fa9437d619771380` on 2026-09-23 (20:33Z), after the
exact-main package run [35915656747](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35915656747)
succeeded. The publishing run
[35916803725](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35916803725)
verified the two archived artifacts against the committed inventory and, after
the owner's protected approval, uploaded them to PyPI at 20:44Z. Independent
verification afterwards: the PyPI wheel (`9a3ec138…03ec7`, 633,122 bytes) and
sdist (`80ac82ba…0eae2`, 563,555 bytes) are byte-identical to the archived CI
artifacts and match the committed inventory; each carries a trusted-publishing
attestation for `JohnnyFiv3r/memoriesql`, `publish-pypi.yml` and environment
`pypi`; PyPI metadata reports version 0.0.11, `>=3.13,<3.15` and the three pinned
dependencies; a fresh installation from the PyPI index reports version 0.0.11, 63
contracts, 27 migrations and intact `RECORD` hashes. `memoriesql==0.0.11` may now
be pinned. The preparation record below is retained as written.

This preparation packages merged main
`6b3ae977cfa499d35096fe244f5c6d6e30727d0e` (PR #37 merged 2026-09-23), whose
exact-main package run is [run 35912761818](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35912761818)
(package and both installed-route compatibility jobs). The release changes
metadata, exact publication controls, a new artifact inventory and guidance.
Relative to published 0.0.10 it carries one owner-authorized forward capability,
[authored claims and relations](authored-claims-and-relations.md): forward-only
schema 27 and author-complete-unit revision 6. It preserves the bytes of
migrations 0001–0026, all 61 published contract payloads, dependencies,
authorization and accounting, and adds migration 0027 and two contract records
(63 in total).

On 2026-09-23 the owner authorized preparing 0.0.11, merging its exact qualified
release PR and creating/pushing `v0.0.11` once at subsequently verified current
release main; that authorization is recorded in `AGENTS.md`. That merge SHA, not
the earlier merged main or PR head, is the release identity. Protected upload
approval remains exclusively the owner's action. Stop when the actual publishing
workflow waits for that approval; never approve, bypass or upload directly. The
0.0.10 authorization is historical, not authority for this release.

## Exact release identity

| Setting | Prepared value |
| --- | --- |
| Distribution / version | `memoriesql` / `0.0.11` |
| Repository / ID | `JohnnyFiv3r/memoriesql` / `1357510758` |
| Only accepted new tag | `v0.0.11` |
| Workflow / environment | `.github/workflows/publish-pypi.yml` / `pypi` |
| Authoritative release inventory | `docs/verification/runtime-0.0.11-package-artifact-inventory.json` |

On 2026-09-23 the owner advanced the environment tag rule to `v0.0.11`; the
existing Trusted Publisher for project `memoriesql`, repository
`JohnnyFiv3r/memoriesql`, workflow `publish-pypi.yml` and environment `pypi`
needs no change, and automation performs no PyPI sign-in. Immediately before
tagging, read-only checks must find no `v0.0.11` tag and no published 0.0.11.
Stop if version/tag is unavailable; never select another version or move a tag.
Do not change publisher configuration or access credential values.

## Development qualification and historical verification

Development receipts bind full checkout SHA, filenames, sizes/hashes and
`unreleased-development`; they never authorize publication. Compatibility jobs
verify that receipt, rebuild a wheel from sdist, prove it byte-identical to the
reviewed wheel, and qualify the installed route with checkout access denied on
each supported interpreter. Development archives retaining 0.0.10 metadata are
not published 0.0.10 and cannot be renamed or reused as 0.0.11.

Published archives, tags, migrations and contract payloads remain immutable. After
the `v0.0.10` tag the owner retired historical inventories, baseline fixtures and
the frozen-source reproduction from the repository; the tagged source and the
published archives are the record. This preparation likewise replaces the 0.0.10
inventory with the 0.0.11 one.

Fresh 0.0.11 wheel/sdist builds and an independent sdist-to-wheel reconstruction
must agree. Independently download final-head hosted archives and compare them
with the committed 0.0.11 release inventory and exact-head development receipt. A
changed downloaded receipt cannot override the committed release hashes.

## Existing fail-closed publication path

Only creation of exactly `v0.0.11` in this repository can trigger publication.
PRs, branch pushes, manual dispatch, wildcard and historical tags cannot upload.
The tag must target current main with genuine 0.0.11 metadata; the latest package
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
2. Recheck version/tag availability and GitHub environment protections. Stop on a
   mismatch or missing approval gate.
3. Create/push `v0.0.11` once at verified release main under the explicit owner
   authorization. Observe release verification and provide the actual waiting
   publishing run, release SHA/tag and matched archive hashes. Do not invent a URL.
4. Stop for owner-only protected approval. After a separate owner action, actual
   PyPI files, hashes, metadata/provenance and fresh installs must be independently
   verified. Workflow success alone is not publication proof or private adoption authority.

## Candidate capabilities and caller composition

The candidate retains published schemas 1–26 and every 0.0.10 capability:
canonical runtime, evidence recovery/revisiting, local mentions, attributable
classification, stored-result inspection, hard-bounded model admission, declared
evidence scopes, explicit supervised managed dispatch, the run-reference binding
and canonical-apply refusal settlement. It adds
[authored claims and relations](authored-claims-and-relations.md): the primary
author of one complete unit may optionally propose tracked claims, evidence-backed
relations to explicitly pinned candidate beads, lifecycle judgments about those
candidates' claims and an assessment of every pinned candidate, in the same
acceptance as its statements. Claim and relation states derive from append-only
authored and governed history as of a known time; derivation roots never count a
transformation as independent corroboration. These capabilities are published in
0.0.11; publication is not a claim of real-provider quality.

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
Use the reviewed local 0.0.11 artifact before publication; only after independently
verified publication pin `memoriesql==0.0.11`. Existing execution budgets and
experimental API bounds are unchanged.

## Qualification and remaining gates

Release preparation uses focused release/control tests and fresh installed smoke
checks locally; unchanged database suites are not repeated locally. Final release-PR
CI/artifact checks and subsequently merged-main qualification remain mandatory.
Retain completed capability/lifecycle reviews; this release scope receives one
broad review and at most one focused rereview.

Public release precedes separately authorized private pinned consumption. Provider
composition, source fidelity/classification-abstention, total request/token/latency
quality, Q's human inspection path, owner-data proof and CP-2 follow-ups remain
gated. No private work, provider activation/spending, deployment, production
migration or checkpoint is authorized by this release preparation or tag permission.

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
