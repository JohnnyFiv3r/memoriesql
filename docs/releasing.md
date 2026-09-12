# Owner-controlled 0.0.5 release readiness

This PR prepares genuine `0.0.5` package metadata, exact `v0.0.5` repository release
controls, guidance and a committed candidate inventory. It authorizes no merge,
tag, upload, publication approval, external setting change or consumer deployment.
The PR head is not the eventual release SHA. Only the later exact current main
commit, its successful main-push CI and separately approved release actions can
establish that identity.

Baseline: `b9301105a5f6841654be7b9437afbd12959f81e8`, the merge of PR #13.
Reviewed head `1b9bf83deae265ae24a1565c8f933d2151a975d9` is ancestral and has an
identical tree. The preparation preserves runtime behavior, dependencies, SQL
0001–0018, all 53 existing contract records and generated catalogs. Only package
version metadata, release controls/tests, guidance and candidate provenance change.

Published `0.0.1a1`, `0.0.2`, `0.0.3` and `0.0.4` remain immutable. All five existing
artifact inventories under `docs/verification/` retain their original bytes,
including the reviewed immutable-observations candidate and
`runtime-package-artifact-inventory.json` (published 0.0.4). That historical filename
is no longer the active release-control input. The new
[runtime-0.0.5-package-artifact-inventory.json](verification/runtime-0.0.5-package-artifact-inventory.json)
is the sole committed 0.0.5 candidate inventory. No old archive is renamed or replaced.
Historical [0.0.4 readiness evidence](verification/release-readiness-0.0.4.md) remains
unchanged; its availability checks and pending actions describe that earlier preparation.

A read-only check on 2026-09-12 found versions through 0.0.4 in
[PyPI release history](https://pypi.org/pypi/memoriesql/json); the
[0.0.5 endpoint](https://pypi.org/pypi/memoriesql/0.0.5/json) returned HTTP 404.
No `v0.0.5` repository tag existed. This is not a reservation. Recheck immediately
before release; if unavailable, stop for owner direction rather than choosing a
new version or suffix. The 0.x line remains experimental.

## Exact release identity and controls

| Setting | Value |
| --- | --- |
| PyPI project | `memoriesql` |
| GitHub repository | `JohnnyFiv3r/memoriesql` (ID `1357510758`) |
| Workflow | `.github/workflows/publish-pypi.yml` |
| Protected environment | `pypi` |
| Only accepted new tag | `v0.0.5` |
| Genuine distribution version | `0.0.5` |
| Committed candidate inventory | `docs/verification/runtime-0.0.5-package-artifact-inventory.json` |

- Only creation of the exact tag can trigger publication. Historical/wildcard tags,
  manual dispatch, pull requests and branch pushes cannot trigger upload.
- The tag commit must equal current main, metadata must be exactly 0.0.5, and the
  latest exact-head `python-package.yml` **main push** run must have completed
  successfully. A PR run or an earlier green main run cannot authorize the merge.
- Download `memoriesql-python-<exact SHA>` only from that selected run ID. Compare
  both filenames, version, sizes and SHA-256 values to the committed candidate
  inventory. The downloaded inventory cannot override committed hashes. Missing,
  extra, altered or symlinked distributions fail closed.
- Candidate PR/main package CI also checks that committed inventory, genuine
  metadata, deterministic rebuilds and installed ownership/compatibility. An
  independent hosted/local comparison is required in addition to the CI assertion.
- The verify job stages only those two archives. The protected publish job uses
  pinned actions and OIDC, executes no repository code and alone has `id-token: write`.
  There is no publication-time build, dependency resolution, compatibility rerun,
  token fallback or `skip-existing` behavior.
- The `pypi` environment must retain owner approval and disabled administrator
  bypass. Automation cannot approve its own upload. The existing self-review
  allowance permits the sole owner to approve a release they initiated.

The existing Trusted Publisher must retain this repository, workflow filename and
`pypi` environment. Repository guidance or historical public provenance does not
verify current authenticated PyPI publisher configuration. The owner must verify
that configuration before separately authorized publication. This PR neither
registers a publisher nor changes GitHub environment settings.

## Caller composition and explicit opt-in

Python support remains `>=3.13,<3.15`, qualified on 3.13/3.14. Dependencies remain
`psycopg[binary]==3.3.3`, `pydantic==2.13.3` and `pydantic-ai-slim==2.27.0` without
provider extras. After verified publication, consumers must pin `memoriesql==0.0.5`.
Before publication, use the hash-matched reviewed local candidate. Catalog-only
`0.0.1a1` remains an explicit older-Python option, never a runtime installation.

Schema 16 retains exact evidence inventories and raw/fold lineage. Schema 17
materializes qualified source-native units and thin beads with durable tasks pinned
to the sealed package; it creates no meaning. Schema 18 supplies an explicit
revision-2 successor activation, authorized reader v2, trusted exposure and fenced
initial application. Legacy schema-15 observation/correction commands remain on
their existing path. All historical receipts and accepted-bead immutability remain
subject to current authorization.

Installing/upgrading the Python package does not migrate a database, provision
production trust, activate tasks, configure a provider or start a worker. An approved
schema migration also does not activate existing tasks or seed production
producer/dispatch trust or worker-claim policy. Operational callers must separately:

1. Plan and authorize an explicit forward database migration using the actual
   expected schema version. Backups/recovery and deployment approval remain separate.
2. Supply credential/workspace context, current source authority and a reviewed
   producer qualification policy. Producer-attested completeness, a qualification
   string or a digest is not independent certification. Pending/incomplete siblings
   remain visible while genuinely complete units may qualify independently.
3. Deliberately activate an exact schema-17 binding with `PostgresCompleteInput`
   and `ActivateCompleteInput`. The original input, unavailable availability
   snapshot, package pin and receipts remain immutable. No task silently resumes
   or falls through to legacy shortened-input authoring.
4. Explicitly compose `load_complete_input_task_registry`, the caller's
   `BuiltInModuleRegistry`, compatible agent/model-profile registries and
   `IntegratedSemanticWorker`, with the applicable worker-claim policy. Supply a
   `PostgresEvidenceExposureRecorder` using a separately trusted, policy-bound
   attestor identity. Production policy provisioning and provider interpretation
   remain deferred; this distribution qualifies only fictional model callbacks.
5. Keep the existing worker, executor and event loop alive through owned cleanup.
   A `cleanup_pending` receipt is not rollback or reconciled usage; observe
   `wait_for_cleanup()` and preserve uncertain settlement outcomes.

Authorized reader batches and execution windows are separate from genuine units
and from provider-call granularity. Exact retained input, ordering, uncertainty,
identity and lineage are preserved. Current authority and producer/dispatch policy
are checked during hydration, dispatch after waits, exposure recording and canonical
apply. Complete required-input exposure is mandatory before successful initial
application; reads, caches or model claims cannot supply proof. Mechanical exposure
proves supply to a trusted dispatch boundary, not comprehension, correctness,
remote retention or independent source completeness. Rolling-note quality and
execution ceilings remain experimental. Oversized/unavailable input keeps its raw
evidence and thin bead with an explicit outcome. Complete-input correction and
reauthoring are not delivered.

PR #13 already repaired both activation defects: current row-locked state after
keyed waits prevents transfer when owner cancellation wins, and every successful
natural-duplicate key obtains a request-bound shared-ledger receipt while retaining
the original successor/pin. The completed activation review is not reopened here.
See [execution](complete-input-execution.md), [compatibility](architecture/compatibility.md),
[migration operations](migrations.md) and [cleanup ownership](runtime.md#cancellation-cleanup-ownership).

## Remaining separately authorized release actions

1. Review and merge this readiness PR after final-head CI, artifact comparison and
   review closure. It is left open and unmerged by this task. Neither its head nor
   its base is designated as the eventual release SHA.
2. Separately authorize the exact-tag GitHub environment allowance change. Read-only
   API inspection on 2026-09-12 found `pypi` restricted to `v0.0.4`, required reviewer
   `JohnnyFiv3r`, administrator bypass disabled and self-review allowed. A future
   owner action must permit only `v0.0.5`, retaining those protections and rejecting
   branches/wildcards. This preparation has not changed them.
3. Recheck PyPI 0.0.5 availability, authenticated Trusted Publisher identity and
   environment protections. Establish the then-current merged main SHA, its latest
   successful main-push CI and retained matching artifacts. Independently compare
   the archives to the committed inventory. Stop on drift, missing/expired artifacts
   or unavailable version; never fall back to an older head or rebuild at upload.
4. Separately authorize publication and create `v0.0.5` once at that verified main
   SHA. Never move, delete, recreate or retarget an existing published tag.
5. The owner inspects the selected run, SHA and hashes and separately approves the
   waiting `pypi` upload job. Readiness review does not supply that approval.
6. After upload, verify PyPI filenames/hashes, Trusted Publisher provenance and
   fresh exact-version installs on Python 3.13/3.14. CI is not publication proof.

Public release must precede separately authorized Desktop adoption. Provider calls,
owner data, production provisioning, deployment and checkpoint execution remain
outside this preparation. PR-02O/CP-2 remain incomplete. Preserve the six historical
Desktop queue failures as unresolved evidence; no new tracker or rerun is warranted
by these metadata/control changes.
