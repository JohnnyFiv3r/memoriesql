# Owner-controlled 0.0.6 release readiness

This slice prepares genuine `0.0.6` metadata, exact `v0.0.6` repository release
controls, guidance and fresh wheel/sdist inventories. It authorizes no merge,
tag, upload, publication approval, external setting change or consumer deployment.
The PR head is not the eventual release SHA. Only a later current-main commit,
its successful main-push CI and separate owner authorization establish that identity.

Baseline: `10bd13494434b64de3ff57a6e2e2667a0664c26f`, the merge of PR #16.
Reviewed head `b39dc99a887a717727c80e42f69443030e651449` is ancestral.
This preparation changes no runtime behavior, dependencies, SQL 0001–0020,
55 contract record bytes, generated catalogs, execution ceilings or provider admission.

A live PyPI check on 2026-09-15 found releases `0.0.1a1`, `0.0.2`, `0.0.3`,
`0.0.4` and `0.0.5`; the 0.0.6 JSON endpoint returned HTTP 404. No `v0.0.6`
repository tag existed. This is not a reservation. Recheck before publication;
if unavailable, stop for owner direction rather than choose another version.
All published artifacts/tags and the eight existing historical release/development
inventories remain immutable. Prior readiness history remains available in Git.

## Exact release identity and controls

| Setting | Value |
| --- | --- |
| PyPI project | `memoriesql` |
| GitHub repository | `JohnnyFiv3r/memoriesql` (ID `1357510758`) |
| Workflow | `.github/workflows/publish-pypi.yml` |
| Protected environment | `pypi` |
| Only accepted new tag | `v0.0.6` |
| Genuine distribution version | `0.0.6` |
| Committed candidate inventory | `docs/verification/runtime-0.0.6-package-artifact-inventory.json` |

The existing release process is retained:

- Only creation of exactly `v0.0.6` can trigger publication. Historical/wildcard
  tags, manual dispatch, PRs and branch pushes cannot upload.
- The tag commit must equal then-current main, metadata must equal 0.0.6, and the
  latest exact-head `python-package.yml` **main push** run must have passed.
  A PR run or earlier green main run cannot authorize release.
- Download `memoriesql-python-<exact SHA>` only from that selected run ID. Compare
  filenames, embedded versions, sizes and SHA-256 values with the committed
  inventory. Missing, extra, altered or symlinked archives fail closed. A downloaded
  inventory cannot override committed hashes; never rebuild or rename at upload.
- PR/main package CI checks genuine metadata, deterministic builds, the active
  committed inventory, installed compatibility and namespace/resource ownership.
  Independently download and compare the retained archives as a separate check.
- The verify job stages only the two matched archives. The protected publish job
  uses pinned actions and OIDC, runs no repository code and alone has
  `id-token: write`. There is no publication-time build, dependency resolution,
  token fallback or `skip-existing` behavior.
- The `pypi` environment must allow the exact authorized tag, require owner approval
  and disable administrator bypass. Existing self-review allowance may support
  the sole owner approving their own release; automation cannot approve upload.

No external setting is changed here. Before publication, verify current GitHub
protections and authenticated PyPI Trusted Publisher configuration for this exact
repository, `publish-pypi.yml` and environment `pypi`. Historical provenance is not
current configuration proof. Any required setting change needs separate authorization.

## Caller composition and explicit opt-in

Published 0.0.5 supplies schemas 16–18: exact evidence packages, qualified stable
unit/thin-bead materialization and unavailable task binding, then explicit
revision-2 activation, reader v2, trusted exposure and fenced initial application.
The 0.0.6 candidate includes the already merged schemas 19–20:

- **19:** authorized retained-fold recovery through bounded discovery, outcome,
  lineage and exact-byte operations. Stored topology, uncertainty and pending tails
  remain truthful. Recovery creates no producer qualification or semantic work.
- **20:** explicit `ActivateSourceRevisiting` can activate an untouched schema-17
  binding into a revision-3 task. The single author can revisit earlier exact
  normalized/raw evidence from its pinned target and optional authorized context,
  including after forward coverage finishes. Existing revision-2 activations cannot
  silently switch. No correction/reauthoring is added.

Installing/upgrading does not migrate a database, provision production trust,
activate tasks, start a worker or configure a provider. Callers must separately:

1. Authorize an explicit forward migration with the actual expected schema version.
   Backup/recovery, deployment and database upgrade remain separate owner actions.
2. Supply credentials/workspace and current source authority. Recovery requires
   an explicitly scoped service grant. Producer qualification and dispatch trust
   require actual approved policies; arbitrary strings/digests are not certification.
3. Deliberately choose activation and the matching registry. For revisiting, use
   `PostgresCompleteInput.activate_revisiting`, `ActivateSourceRevisiting` and
   `load_source_revisiting_task_registry`, with explicit optional context pins.
   Original inputs, unavailable snapshots, pins and receipts stay immutable.
4. Compose the existing module/agent/model registries and worker with its claim
   policy and a separately trusted `PostgresEvidenceExposureRecorder`. Revision-2
   dispatch policies cannot certify revision-3 execution. Production trust and
   real-model admission remain deferred; only fictional callbacks are qualified.
5. Retain the worker, event loop and owned cleanup through `wait_for_cleanup()`.
   `cleanup_pending` and foreground cancellation do not prove rollback or reconciled
   usage. Preserve uncertain settlement outcomes.

The existing limits remain unchanged: target 131,072 normalized characters;
revisiting windows up to 65,536 characters/131,072 JSON bytes; 12 interactions;
262,144 repeated-delivery units; 300 seconds; 524,288 input and 32,768 output tokens.
Whole-context delivery is preferred when it fits; otherwise bounded delivery and
typed rereads keep storage pages separate from model interactions and genuine units.

Actual trusted dispatch establishes exposure; a read, cache, note or model claim
cannot. Repeated delivery is charged independently of unique required target
coverage. Current authorization, producer/dispatch policy, attempt/lease/cancellation
and canonical-apply fences remain required. Mechanical exposure does not establish
comprehension, correctness or independent source completeness. Rolling-note quality
and execution ceilings remain experimental. Oversized/unavailable input remains
retained with its thin bead and explicit unsuccessful outcome.

The completed PR #13 cancellation/idempotency repairs and PR #16 target-ceiling
repair are preserved. This metadata slice does not reopen their implementation
review. See [recovery](transcript-fold-recovery.md), [revisiting](source-revisiting.md),
[compatibility](architecture/compatibility.md) and [migration operations](migrations.md).

Python remains `>=3.13,<3.15`; installed qualification covers 3.13/3.14. Dependencies
remain `psycopg[binary]==3.3.3`, `pydantic==2.13.3` and `pydantic-ai-slim==2.27.0`,
without provider extras. Before publication, use a hash-matched reviewed local
0.0.6 candidate. After separately verified publication, pin `memoriesql==0.0.6`.
Older Python can explicitly use immutable catalog-only `0.0.1a1`, never as a runtime.

## Verification evidence

Unchanged-runtime evidence is retained from PR #16's reviewed head and
[exact-head installed qualification](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/34907957277):
191 cases in each Python 3.13/3.14 wheel/sdist lane. This readiness slice compares
all runtime/SQL/contract bytes with its merged baseline, runs focused release/control
checks, and verifies fresh wheel and independently rebuilt sdist-wheel installs
for metadata, contracts and namespace/resource ownership. No local database suite
is repeated solely for version metadata. The existing hosted matrix still qualifies
the final readiness head. The PR records its exact CI and independent retained-archive
comparison with the committed 0.0.6 inventory.

<a id="separate-owner-actions"></a>

## Remaining separately authorized release actions

1. Review and merge this readiness PR after final-head CI, independent archive
   comparison and review closure. It remains unmerged here; no release SHA is selected.
2. Recheck version/tag availability, authenticated publisher configuration and
   environment protections. Separately authorize any necessary exact-tag setting
   change; preserve owner approval and disabled administrator bypass.
3. Establish then-current merged main, its latest successful main-push CI and its
   retained matching archives. Independently compare with the committed inventory.
   Stop on drift, missing/expired artifacts or unavailable 0.0.6; no fallback/rebuild.
4. Separately authorize publication and create `v0.0.6` once at that verified main
   commit. Never move, delete or retarget an existing published tag.
5. The owner inspects the run, SHA and hashes and separately approves the waiting
   `pypi` upload job. Readiness review does not supply upload approval.
6. Verify PyPI filenames/hashes, publisher provenance and fresh exact-version
   installations after publication. CI is not publication proof.

Public release precedes separately authorized Desktop adoption. PR-02O/CP-2 remain
incomplete pending private composition and owner proof. The existing CP-2 checklist
remains the progress authority: no new tracker, P/Q advancement or N/N3/CP-1 reopening.
The six historical unexplained Desktop queue failures remain unresolved. Providers,
owner data, production provisioning, deployment, UI and checkpoints remain deferred.
