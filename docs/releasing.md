# 0.0.13 release: preparation, owner gates and publication record

## Preparation status

**Prepared for owner review; not directed, tagged or published.** `0.0.13` is the
proposed next plain numeric version; the owner selects the version. No owner
release direction, environment tag-rule change, hosted qualification request,
tag or protected upload approval is recorded for it. Published
`memoriesql==0.0.12` remains the current release.

This preparation packages merged main
`5290ad1194997e00f2a4ab86de8bff8738b2f59e` (PR #69 merged 2026-09-28). Its tree
`f825518cc452f0586f9cd5905409d42835e1ec77` is identical to the PR #69 head
`7a065f0342f231ab69f24974df39f6db7ddd57d0`, whose retained local evidence covers
587 installed fictional PostgreSQL 18.4 tests on each of Python 3.13.5 and 3.14.0,
byte-identical sdist-to-wheel rebuilds and fresh-process recovery proofs. That
evidence predates this metadata change and is development qualification, not
publication qualification. The release changes metadata, exact publication
controls, a new artifact inventory and guidance. Relative to published 0.0.12 it
carries these forward changes:

- The [assessed-relation lifecycle](verification/pr-03-lifecycle.md), schema 30
  and contract `memoriesql.assessed-relation-lifecycle.v1`: authenticated humans
  with current authority confirm, dispute or retract an authored or assessed
  relation assertion through append-only governed events, and
  `InspectBeadRelationsV3` reads its current or as-of state. Accepted beads,
  statements and assertions are never rewritten; agents and services cannot make
  lifecycle judgments.
- Internal agent-SQL mechanics, schemas 31–35: result-preparation journals, the
  assessed-relation SQL population, a restricted query bridge with owned
  settlement, atomic private result commitment and whole-closure authorization
  checks, with the new runtime dependency `sqlglot==30.19.0` (MIT). These add no
  available query, result, checkpoint or disclosure API; retrieval remains
  unqualified. See [agent SQL results](agent-sql-results-v1.md).
- [Exact source enrollment](exact-source-enrollment.md), schema 36 and contract
  `memoriesql.source-enrollment.v1`: an authenticated human with current
  workspace and source management/share capabilities enrolls one explicitly
  confirmed provider-neutral source, grants bounded read/write access subject to
  membership and pairing authority, or terminally revokes it with a receipt. It
  discovers, parses and captures nothing.
- The [public headless CLI](cli.md), contract `memoriesql.core-cli.v1`: `doctor`,
  `capabilities`, authorized `inspect`, `source` and `relations` reads and
  `sources enroll|grant|revoke` on the single `memoriesql` executable. It has no
  private product routing, source inventory, capture, worker or retrieval command.

Schema 33 changes database-wide privileges; see the
[upgrade caution](migrations.md). Consumers must qualify their actual database
roles on a disposable upgraded database before adopting this release.

It preserves the bytes of migrations 0001–0029 and all 64 published contract
payloads, adds migrations 0030–0036 and three contract records (67 in total), and
adds one runtime dependency. After publication, migrations 0030–0036 and the three
new payloads are immutable; later changes need forward migrations and new
contract versions.

## Exact release identity

| Setting | Proposed value |
| --- | --- |
| Distribution / version | `memoriesql` / `0.0.13` (owner selects) |
| Repository / ID | `JohnnyFiv3r/memoriesql` / `1357510758` |
| Only accepted new tag | `v0.0.13` |
| Workflow / environment | `.github/workflows/publish-pypi.yml` / `pypi` |
| Authoritative release inventory | `docs/verification/runtime-0.0.13-package-artifact-inventory.json` |

The existing Trusted Publisher for project `memoriesql`, repository
`JohnnyFiv3r/memoriesql`, workflow `publish-pypi.yml` and environment `pypi` needs
no change, and automation performs no PyPI sign-in or check. Advancing the `pypi`
environment tag rule to `v0.0.13` is an owner action. Immediately before tagging,
a read-only check must find no `v0.0.13` tag in this repository. Stop if the tag
is unavailable; never select another version or move a tag. Do not change
publisher configuration or access credential values.

## Owner gates, in order

1. Select the version and direct the release; record that direction in
   `AGENTS.md` in the release PR.
2. Merge only the reviewed release head after exact-head `package` CI and an
   independent comparison of its archives with the committed inventory.
3. Request the paid full hosted qualification at merged release main
   (`workflow_dispatch` with `expected_sha` and `owner_approval_ref`), then verify
   its `package`, `compatibility (3.13)` and `compatibility (3.14)` jobs and archives.
4. Advance the environment tag rule and create/push `v0.0.13` once at verified
   release main.
5. Approve the protected upload when the publishing run waits for it. Afterwards,
   independently verify the PyPI files, hashes, attestations, metadata and a fresh
   hash-pinned installation.

Automation never approves upload, bypasses a gate, uploads directly or changes
external settings. Earlier release directions and approvals are historical, not
authority for this release.

## Development qualification and historical verification

**Current CI policy:** routine pushes run only inexpensive package checks. Full
Python 3.13/3.14 installed database qualification remains mandatory locally;
hosted full qualification requires explicit owner approval. See
[CI cost policy](ci-policy.md). Skipped compatibility jobs are not qualification.

Development receipts bind full checkout SHA, filenames, sizes/hashes and
`unreleased-development`; they never authorize publication. Compatibility jobs
verify that receipt, rebuild a wheel from sdist, prove it byte-identical to the
reviewed wheel, and qualify the installed route with checkout access denied on
each supported interpreter. Development archives retaining 0.0.12 metadata are
not published 0.0.12 and cannot be renamed or reused as 0.0.13.

Published archives, tags, migrations and contract payloads remain immutable. The
repository keeps only the current release inventory; this preparation replaces
the 0.0.12 inventory with the 0.0.13 one. The tagged `v0.0.12` source and the
published archives remain the record of that release.

Fresh 0.0.13 wheel/sdist builds and an independent sdist-to-wheel reconstruction
must agree. Independently download final-head hosted archives and compare them
with the committed 0.0.13 release inventory and exact-head development receipt. A
changed downloaded receipt cannot override the committed release hashes.

## Existing fail-closed publication path

Only creation of exactly `v0.0.13` in this repository can trigger publication.
PRs, branch pushes, manual dispatch, wildcard and historical tags cannot upload.
The tag must target current main with genuine 0.0.13 metadata. Under the current
cost policy, the latest explicitly requested full package qualification on main
at that exact SHA must be successful, including actual successful `package`,
`compatibility (3.13)` and `compatibility (3.14)` jobs. A cheap main-push run,
skipped jobs or PR qualification alone is insufficient.

The verify job downloads only `memoriesql-python-<exact SHA>` from that selected
run and checks the two filenames, sizes and SHA-256 values against the committed
release inventory. Missing, extra, changed or symlinked archives fail closed.
Only those two verified archives reach the protected publish job. It uses pinned
actions and OIDC, runs no repository code and alone has `id-token: write`.
There is no publication-time build, rename, dependency resolution, token fallback
or `skip-existing`. Automation must not approve upload or change external settings.

1. Merge only the reviewed release head after exact-head CI and independent archive
   comparison. Obtain explicit approval for the full hosted qualification at
   actual merged-current-main, then verify its jobs and archives again. A green
   inexpensive main-push check cannot substitute for that qualification.
2. Recheck tag availability and GitHub environment protections. Stop on a
   mismatch or missing approval gate.
3. Create/push `v0.0.13` once at verified release main under the owner's
   direction. Observe release verification and provide the actual waiting
   publishing run, release SHA/tag and matched archive hashes. Do not invent a URL.
4. Stop for owner-only protected approval. After a separate owner action, actual
   PyPI files, hashes, metadata/provenance and fresh installs must be independently
   verified. Workflow success alone is not publication proof or private adoption authority.

## Candidate capabilities and caller composition

The candidate retains published schemas 1–29 and every 0.0.12 capability:
canonical runtime, evidence recovery/revisiting, local mentions, attributable
classification, stored-result inspection, hard-bounded model admission, declared
evidence scopes, explicit supervised managed dispatch, the run-reference binding,
canonical-apply refusal settlement, authored claims and relations and relation
assessment after acceptance. It adds the four forward changes listed above.
Relation-aware recall still needs qualified retrieval over the lifecycle
projection; enrollment and the CLI do not supply source discovery, capture,
workers or provider routes. These capabilities are prepared for 0.0.13;
publication is not a claim of real-provider quality, retrieval availability or
product readiness.

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

Python remains `>=3.13,<3.15`; dependencies are `psycopg[binary]==3.3.3`,
`pydantic==2.13.3`, `pydantic-ai-slim==2.27.0` and `sqlglot==30.19.0`, without
provider extras. Use the reviewed local 0.0.13 artifact before publication; only
after independently verified publication pin `memoriesql==0.0.13`. Existing
execution budgets and experimental API bounds are unchanged.

## Qualification and remaining gates

Release preparation uses focused release/control tests and fresh installed smoke
checks locally; unchanged database suites are not repeated locally. Final release-PR
CI/artifact checks and subsequently merged-main qualification remain mandatory.
Retain completed capability/lifecycle reviews; this release scope receives one
broad review and at most one focused rereview.

Public release precedes separately authorized private pinned consumption. Provider
composition, source discovery and capture adapters, relation-specialist binding,
relation-quality evaluation and live runs, public retrieval/results/checkpoints,
source fidelity/classification-abstention, total request/token/latency quality,
owner-data proof and CP-2 follow-ups remain gated. No private work, provider
activation/spending, deployment, production migration or checkpoint is authorized
by this release preparation or tag permission.

## 0.0.12 publication record

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
compose it separately. Its preparation record is retained in the tagged `v0.0.12`
source.

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
