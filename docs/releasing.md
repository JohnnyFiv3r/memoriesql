# Prepared 0.0.10 release and owner gates

This preparation packages accepted main
`f6f649eed5c5908f21a1bf2f4e365582e51ae6e6` (PR #31 and PR #32 merged 2026-09-22; the runtime is
identical to `e165fae46b62a61f42dcf05c90d5f191eb000068`), whose runtime's exact-main package run is [run 35754179853](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/35754179853) (successful: package, contract, export and both installed-route
compatibility jobs passed). The release changes metadata, exact publication controls, a new artifact
inventory, focused verification/provenance and guidance. Relative to published
0.0.9 it carries two owner-authorized forward runtime changes: the executor binds
authored statements to its own run reference before hashing (single-run trees),
and the worker settles data-error canonical-apply refusals as invalid output. It
preserves all 26 SQL migrations, 61 contract payloads/catalogs, dependencies,
authorization and accounting.

On 2026-09-22 the owner authorized preparing 0.0.10 and creating/pushing
`v0.0.10` once at subsequently verified current release main, after merging the
qualified release PR; that authorization is recorded in `AGENTS.md` beside the
runtime-change authorization it follows. That merge SHA, not the earlier accepted main or PR head, is the release
identity. Protected upload approval remains exclusively the owner's action. Stop
when the actual publishing workflow waits for that approval; never approve,
bypass or upload directly. The 0.0.9 authorization is historical, not authority
for this release.

## Exact release identity

| Setting | Prepared value |
| --- | --- |
| Distribution / version | `memoriesql` / `0.0.10` |
| Repository / ID | `JohnnyFiv3r/memoriesql` / `1357510758` |
| Only accepted new tag | `v0.0.10` |
| Workflow / environment | `.github/workflows/publish-pypi.yml` / `pypi` |
| Authoritative release inventory | `docs/verification/runtime-0.0.10-package-artifact-inventory.json` |

On 2026-09-22 the owner updated the environment tag rule for `v0.0.10`; the
existing Trusted Publisher for project `memoriesql`, repository
`JohnnyFiv3r/memoriesql`, workflow `publish-pypi.yml` and environment `pypi`
needs no change, and automation performs no PyPI sign-in. Immediately before
tagging, read-only checks must find no `v0.0.10` tag and no published 0.0.10.
Stop if version/tag is unavailable; never select another version or move a tag.
Do not change publisher configuration or access credential values.

## Development qualification and historical verification

Development receipts bind full checkout SHA, filenames, sizes/hashes and
`unreleased-development`; they never authorize publication. Compatibility jobs
verify that receipt, rebuild a wheel from sdist, prove it byte-identical to the
reviewed wheel, and qualify the installed route with checkout access denied on
each supported interpreter. Development archives retaining 0.0.9 metadata are
not published 0.0.9 and cannot be renamed or reused as 0.0.10.

Published archives, tags, migrations and contract payloads remain immutable. After
the `v0.0.10` tag the owner retired historical inventories, baseline fixtures and
the frozen-source reproduction from the repository; the tagged source and the
published archives are the record.

Fresh 0.0.10 wheel/sdist builds and an independent sdist-to-wheel reconstruction
must agree. Independently download final-head hosted archives and compare them
with the committed 0.0.10 release inventory and exact-head development receipt. A
changed downloaded receipt cannot override the committed release hashes.

## Existing fail-closed publication path

Only creation of exactly `v0.0.10` in this repository can trigger publication.
PRs, branch pushes, manual dispatch, wildcard and historical tags cannot upload.
The tag must target current main with genuine 0.0.10 metadata; the latest package
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
3. Create/push `v0.0.10` once at verified release main under the explicit owner
   authorization. Observe release verification and provide the actual waiting
   publishing run, release SHA/tag and matched archive hashes. Do not invent a URL.
4. Stop for owner-only protected approval. After a separate owner action, actual
   PyPI files, hashes, metadata/provenance and fresh installs must be independently
   verified. Workflow success alone is not publication proof or private adoption authority.

## Candidate capabilities and caller composition

The candidate retains published schemas 1–26 and every 0.0.9 capability:
canonical runtime, evidence recovery/revisiting, local mentions, attributable
classification, stored-result inspection, hard-bounded model admission, declared
evidence scopes and explicit supervised managed dispatch. It adds the
[run-reference binding and canonical-apply refusal settlement](runtime.md#run-references-and-canonical-apply-refusals):
a statement's `model_run_ref` is host provenance the executor now binds itself for
single-run trees, and a data-error refusal from canonical apply settles the attempt
as `invalid_output` (`worker.canonical_apply_refused`) instead of leaving it running.
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
Use the reviewed local 0.0.10 artifact before publication; only after independently
verified publication pin `memoriesql==0.0.10`. Existing execution budgets and
experimental API bounds are unchanged.

## Qualification and remaining gates

Release preparation uses focused release/control tests and fresh installed smoke
checks locally; unchanged database suites are not repeated locally. Final release-PR
CI/artifact checks and subsequently merged-main qualification remain mandatory.
Retain completed capability/lifecycle reviews; this release scope receives one
broad review and at most one focused rereview.

Public release precedes separately authorized private pinned consumption. Provider
composition, source fidelity/classification-abstention, total request/token/latency
quality, Q's human inspection path, owner-data proof and CP-2 remain gated. No
private work, provider activation/spending, deployment, production migration or
checkpoint is authorized by this release preparation or tag permission.
