# Prepared 0.0.9 release and owner gates

This preparation packages accepted main
`aff4270a549f7618d99db3735020ccb2785f32f1`, qualified by
[exact-main run 35516596470](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35516596470).
All four installed Python 3.13/3.14 wheel/sdist routes passed 313 tests. The release
changes metadata, exact publication controls, a new artifact inventory, focused
verification/provenance and guidance. It preserves runtime behavior, all 26 SQL
migrations, 61 contract payloads/catalogs, dependencies, authorization and accounting.

The owner authorized preparing 0.0.9, merging its exact qualified release PR and
creating/pushing `v0.0.9` once at subsequently verified current release main.
That merge SHA, not the earlier accepted main or PR head, is the release identity.
Protected upload approval remains exclusively the owner's action. Stop when the
actual publishing workflow waits for that approval; never approve, bypass or upload
directly. The previous 0.0.8 authorization is historical, not authority for this release.

## Exact release identity

| Setting | Prepared value |
| --- | --- |
| Distribution / version | `memoriesql` / `0.0.9` |
| Repository / ID | `JohnnyFiv3r/memoriesql` / `1357510758` |
| Only accepted new tag | `v0.0.9` |
| Workflow / environment | `.github/workflows/publish-pypi.yml` / `pypi` |
| Authoritative release inventory | `docs/verification/runtime-0.0.9-package-artifact-inventory.json` |

On 2026-09-20, read-only checks found no `v0.0.9` tag and PyPI returned 404 for 0.0.9.
After the owner's tag-rule update, the live GitHub environment permits exactly
`v0.0.9`, requires `JohnnyFiv3r` approval and disables administrator bypass.
These observations do not reserve a version or replace checks immediately before
tagging. Stop if version/tag is unavailable; never select another version or move a tag.

The current authenticated PyPI publishing page redirected to sign-in. Before
tagging, verify the existing Trusted Publisher for project `memoriesql`, repository
`JohnnyFiv3r/memoriesql`, workflow `publish-pypi.yml` and environment `pypi` in an
owner-authorized authenticated session. Historical provenance does not establish
current configuration. Do not change publisher configuration or access credential values.

## Development qualification and historical verification

Development receipts bind full checkout SHA, filenames, sizes/hashes and
`unreleased-development`; they never authorize publication. Compatibility jobs
verify that receipt, rebuild a wheel from sdist and qualify both installed routes
with checkout access denied. Previous development archives retaining 0.0.8 metadata
are not published 0.0.8 and cannot be renamed or reused as 0.0.9.

Published archives, tags, inventories, migrations and contract payloads remain
immutable. Frozen 0.0.7 source is reproduced at
`1e611c2a426684a6ede479e06e21a123a964a818` with its historical metadata. The 0.0.8
baseline remains unchanged. The new 0.0.9 integrity fixture binds the accepted
implementation before release-only edits; exact runtime reproduction is scoped to
this release preparation, not a permanent restriction on future development.
Historical release guidance remains available at the immutable
[0.0.8 source](https://github.com/JohnnyFiv3r/memoriesql/blob/v0.0.8/docs/releasing.md).

Fresh 0.0.9 wheel/sdist builds and an independent sdist-to-wheel reconstruction must
agree. Independently download final-head hosted archives and compare them with the
committed 0.0.9 release inventory and exact-head development receipt. A changed
downloaded receipt cannot override the committed release hashes.

## Existing fail-closed publication path

Only creation of exactly `v0.0.9` in this repository can trigger publication. PRs,
branch pushes, manual dispatch, wildcard and historical tags cannot upload. The tag
must target current main with genuine 0.0.9 metadata; the latest package main-push
run at that exact SHA must be successful. PR qualification alone is insufficient.

The verify job downloads only `memoriesql-python-<exact SHA>` from that selected
run and checks the two filenames, sizes and SHA-256 values against the committed
release inventory. Missing, extra, changed or symlinked archives fail closed.
Only those two verified archives reach the protected publish job. It uses pinned
actions and OIDC, runs no repository code and alone has `id-token: write`.
There is no publication-time build, rename, dependency resolution, token fallback
or `skip-existing`. Automation must not approve upload or change external settings.

1. Merge only the reviewed release head after exact-head CI and independent archive
   comparison. Verify actual merged-current-main latest push CI and archives again.
2. Recheck version/tag availability, current authenticated PyPI publisher identity
   and GitHub environment protections. Stop on a mismatch or missing approval gate.
3. Create/push `v0.0.9` once at verified release main under the explicit owner
   authorization. Observe release verification and provide the actual waiting
   publishing run, release SHA/tag and matched archive hashes. Do not invent a URL.
4. Stop for owner-only protected approval. After a separate owner action, actual
   PyPI files, hashes, metadata/provenance and fresh installs must be independently
   verified. Workflow success alone is not publication proof or private adoption authority.

## Candidate capabilities and caller composition

The combined candidate retains published schemas 1–24: canonical runtime,
evidence recovery/revisiting, local mentions, attributable classification,
stored-result inspection and hard-bounded model admission. It adds the already
merged schema 25 [declared evidence scopes](declared-evidence-scopes.md), schema 26
[supervised managed dispatch](supervised-dispatch.md), and the terminal-settlement
repair. Local completion-with-error is distinct from successful durable event
persistence; failed child writes no longer strand dependent cleanup. Successful
ordering, observable errors, started-write ownership and late accounting remain intact.
These are prepared candidate capabilities, not a claim of publication or real-provider quality.

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
Use the reviewed local 0.0.9 artifact before publication; only after independently
verified publication pin `memoriesql==0.0.9`. The catalog-only `0.0.1a1` is never a
runtime fallback. Existing execution budgets and experimental API bounds are unchanged.

## Qualification and remaining gates

Release preparation uses focused release/control tests and fresh installed smoke
checks locally; unchanged database suites are not repeated locally. Final release-PR
CI/artifact checks and subsequently merged-main qualification remain mandatory.
Retain completed capability/lifecycle reviews; this release scope receives one
broad review and at most one focused rereview.

Public release precedes separately authorized private pinned consumption. Provider
composition, source fidelity/classification-abstention, total request/token/latency
quality, Q's human inspection path, owner-data proof and CP-2 remain gated. The six
historical unexplained Desktop queue failures remain unresolved evidence. No private
work, provider activation/spending, deployment, production migration or checkpoint
is authorized by this release preparation or tag permission.
