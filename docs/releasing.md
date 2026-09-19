# Prepared 0.0.8 release and owner gates

This preparation packages the already merged public main
`8dfa5a2ef6bb4ef52b62e4405ee8a26ccfa36e6b`, which passed
[exact-main installed qualification](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35462360085).
It changes release metadata, repository publication controls, artifact inventory,
verification and guidance. It changes no runtime behavior, SQL 0001–0024,
59 contract payloads/catalogs, dependencies, execution ceilings or provider policy.

The owner has explicitly authorized preparation, the qualified release-PR merge,
and creation/push of `v0.0.8` at verified release main after every required gate.
Protected upload approval remains exclusively the owner's action. Stop when the
real publishing workflow waits for that approval; never approve it or upload directly.
There is no publishing-run approval URL until a real workflow exists.
The PR head is not automatically the release SHA.

## Exact release identity

| Setting | Prepared value |
| --- | --- |
| Distribution / version | `memoriesql` / `0.0.8` |
| Repository / ID | `JohnnyFiv3r/memoriesql` / `1357510758` |
| Only accepted new tag | `v0.0.8` |
| Workflow / environment | `.github/workflows/publish-pypi.yml` / `pypi` |
| Authoritative release inventory | `docs/verification/runtime-0.0.8-package-artifact-inventory.json` |

A read-only preflight on 2026-09-19 found no `v0.0.8` tag and PyPI returned 404 for
version 0.0.8. The existing GitHub environment allowed exactly tag `v0.0.8`, required
owner `JohnnyFiv3r` approval, and disabled administrator bypass. No settings were
changed. These observations are not reservations or future authorization: recheck
before any authorized tag creation. If version/tag becomes unavailable, stop;
never choose another version or move a tag.

The owner reports PyPI configuration ready. The authenticated publishing page
redirected to login during this preflight, so current Trusted Publisher identity
has **not** been independently verified. Before tagging, verify the PyPI project,
repository owner/name, `publish-pypi.yml` and environment `pypi` in an authenticated
session. Historical provenance and the owner report do not replace that check.

## Development qualification and historical verification

Development CI still qualifies archives against their exact checkout and records
full commit, sizes/hashes and `unreleased-development` in the retained receipt.
That receipt never becomes publication authority. Compatibility jobs re-inspect
it and independently rebuild the sdist wheel, then test both installed routes with
checkout access denied on Python 3.13/3.14. Candidate metadata alone does not
establish publication, including genuine 0.0.8 candidate metadata.

Published archives, tags, historical inventories, migrations and contract payloads
remain immutable. The complete published 0.0.7 source is independently reproduced
at `1e611c2a426684a6ede479e06e21a123a964a818`, with its own metadata version rather
than the current release identity. Its frozen fixture and inventory remain
hash-pinned. The new 0.0.8 baseline records accepted main and preserves all existing
historical payloads; runtime reproduction is release-scoped, never a permanent
freeze on future development source. No 0.0.7 development archive is renamed or
reused as 0.0.8.

The 0.0.8 wheel and sdist must be freshly and deterministically built. Two builds
and an independent sdist-to-wheel reconstruction must agree. Independently download
exact-head hosted archives and compare them against the committed 0.0.8 release
inventory as well as the development receipt. A modified downloaded receipt cannot
override committed release hashes. Historical 0.0.7 release guidance remains in
[its immutable published source](https://github.com/JohnnyFiv3r/memoriesql/blob/v0.0.7/docs/releasing.md).

## Existing fail-closed publication path

Only creation of exactly `v0.0.8` can trigger the prepared publication workflow.
PRs, branch pushes, manual dispatch, wildcard and historical tags cannot upload.
The tag must target actual current main with genuine 0.0.8 metadata, and the latest
package **main-push** run at that exact SHA must be successful. An earlier green
run or a PR run cannot authorize publication.

The verify job downloads only `memoriesql-python-<exact SHA>` from that selected
run. It checks the exact two filenames, sizes and SHA-256 values against the
committed 0.0.8 inventory; missing, extra, changed or symlinked archives fail closed.
It stages only those two verified archives. The protected publish job uses pinned
actions and OIDC, runs no repository code, and alone has `id-token: write`.
There is no publication-time build, rename, dependency resolution, token fallback
or `skip-existing`. Automation must not approve protected upload or change settings.

Under the explicit owner merge/tag authorization:

1. Merge only the reviewed release head after exact-head CI and independent archive
   checks. Verify actual merged-main CI and archives again; the merge SHA is the
   release candidate identity, not the earlier PR SHA.
2. Recheck version/tag availability, authenticated PyPI publisher identity and exact
   environment protections. Stop on a mismatch rather than weakening controls.
3. Create/push `v0.0.8` once at that verified main only if explicitly authorized.
   Observe verification and provide the owner the actual waiting publishing run,
   release SHA/tag and matched artifact hashes. Do not invent a workflow URL.
4. Stop for owner-only protected approval. After that separate owner action, verify
   actual PyPI files, hashes, metadata and Trusted Publisher provenance, then fresh
   Python 3.13/3.14 installs. Workflow success alone is not publication proof.

## Candidate capabilities and caller composition

The candidate retains schemas 15–21 and packages merged public prerequisites:
bounded real-model admission, schema-22 atomic local mentions, schema-23
attributable bead classification and schema-24 currently authorized stored-result
and exact-evidence inspection. It also retains the accounting cancellation repair.
See [admission](real-model-admission.md), [mentions](local-entity-mentions.md),
[classification](bead-classification.md) and [stored inspection](stored-bead-inspection.md).

<a id="caller-composition-and-explicit-opt-in"></a>

Installing/upgrading does not migrate a database, provision trust, activate tasks,
start a worker or configure a provider. Consumers separately authorize forward
migration and supply current credentials/workspace, source qualification,
producer/dispatch/claim policies, registered vocabulary, task/module/agent/model
composition and the trusted exposure recorder. Existing bindings and their exact
pins do not silently upgrade. See [migration operations](migrations.md).

An admitted model needs an independently qualified, exactly bound transport that
performs at most one inference per bounded request, enforces input/generated-token
ceilings before dispatch, and has no unapproved retry/fallback. Public admission
and reservations are not a supplied provider transport or live-quality proof.
Q reads are inference-free and recheck current authorization on every request;
legacy, hidden, absent and authored-empty states remain distinct.

Retain the worker, event loop and owned cleanup through `wait_for_cleanup()`.
Foreground cancellation and `cleanup_pending` do not establish rollback,
termination or reconciled usage. Started writes and late usage remain owned;
uncertain settlement outcomes stay explicit.

Python stays `>=3.13,<3.15`; dependencies remain `psycopg[binary]==3.3.3`,
`pydantic==2.13.3` and `pydantic-ai-slim==2.27.0`, without provider extras.
Before publication use a hash-matched reviewed local 0.0.8 artifact; only after
independently verified publication pin `memoriesql==0.0.8`. The immutable catalog-only
`0.0.1a1` is never a runtime fallback. Current bounds remain experimental and unchanged:
131,072 target characters; 65,536-character/131,072-JSON-byte windows; 12 interactions;
262,144 repeated-delivery units; 300 seconds; 524,288 input/32,768 output tokens.

## Qualification and remaining gates

Accepted main passed 264 tests on each installed Python 3.13/3.14 wheel/sdist route,
including P/Q authorization/accounting regressions and fresh-process recovery.
This release delta uses focused release/control tests and fresh installed archive
smoke checks locally; it does not repeat unchanged local database suites. Final
release-PR CI and independent inventory comparison are required, then exact merged-main
qualification after a separately authorized merge. Reviews of unchanged runtime
slices are retained; release preparation gets one broad review and at most one
focused rereview.

Public release precedes separately authorized private pinned consumption. P's real
provider composition, hard transport bounds, source fidelity/classification-abstention
and request/token/latency quality proof, Q's private human inspection path, owner-data
proof and CP-2 remain incomplete. The six historical unexplained Desktop queue
failures remain unresolved evidence. No private work, provider activation/spending,
OpenRouter, deployment, production migration or checkpoint is authorized here.
