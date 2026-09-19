# Development qualification and historical release verification

Development CI qualifies unreleased archives against the checked-out commit and
its current runtime, migration and contract inventories. The retained
`python-package-artifacts.json` includes the exact commit, archive sizes/hashes
and `unreleased-development` designation. Compatibility jobs re-inspect downloaded
archives against that checkout and compare the receipt, then independently rebuild
the sdist wheel and run both installed routes with checkout access denied on
Python 3.13/3.14. Retaining 0.0.7 package metadata does not make these development
archives the published 0.0.7 release, and does not select a future release version.

Published migrations 0001–0021, contract payloads and historical inventories remain
byte-frozen on development branches. The complete 0.0.7 runtime/metadata baseline
is checked separately against immutable published commit
`1e611c2a426684a6ede479e06e21a123a964a818`, not later development source. The frozen
fixture and published 0.0.7 artifact inventory are themselves hash-pinned in tests.
No release check is replaced by a development receipt.

Publication remains fail-closed: `verify_release.py artifacts` accepts only the
committed, separately approved release inventory, never the downloaded development
receipt. The existing exact tag/version, exact-main successful CI, protected owner
approval, and no-build upload controls are unchanged. New release preparation,
version selection, merge, tag and publication require separate authorization.
The following section is the retained historical 0.0.7 preparation record; its
past approvals and availability observations are not current authorization.

## Historical owner-controlled 0.0.7 release readiness

This release prepares genuine `0.0.7` metadata, exact `v0.0.7` repository controls,
guidance and fresh deterministic wheel/sdist inventories. The owner separately
authorized preparation, its narrowly scoped merge and tag creation after all gates
pass. Protected upload approval remains exclusively an owner action; automation
must stop at the waiting publish job and never approve it or change protections.
The preparation PR head is not automatically the release SHA.

Baseline: `d82f5baf3eff92ac68cdbcda922415a33f2cb0c1`, the merge of PR #18.
Approved reviewed head `4dcbc08cd60a518be0dd102f02643024ec080e87` is ancestral.
This preparation changes no runtime behavior, dependencies, SQL 0001–0021,
56 existing contract record bytes, generated catalogs, Python support, execution
ceilings or provider admission. A frozen baseline fixture verifies these boundaries.

A live check on 2026-09-15 found PyPI 0.0.7 absent (HTTP 404) and no `v0.0.7`
repository tag. This is not a reservation: recheck before tagging. If either becomes
unavailable, stop; never select another version or move a tag. All historical release
and development inventories, published archives and tags remain immutable.
The existing GitHub `pypi` environment was verified read-only with exactly tag
`v0.0.7`, required owner `JohnnyFiv3r` approval and administrator bypass disabled.
Authenticated current PyPI publisher identity must also be verified before tagging;
historical provenance is not a substitute.

## Exact release identity and controls

| Setting | Value |
| --- | --- |
| PyPI project | `memoriesql` |
| GitHub repository | `JohnnyFiv3r/memoriesql` (ID `1357510758`) |
| Workflow | `.github/workflows/publish-pypi.yml` |
| Protected environment | `pypi` |
| Only accepted new tag | `v0.0.7` |
| Genuine distribution version | `0.0.7` |
| Committed candidate inventory | `docs/verification/runtime-0.0.7-package-artifact-inventory.json` |

The existing release process is retained:

- Only creation of exactly `v0.0.7` can trigger publication. Historical/wildcard
  tags, manual dispatch, PRs and branch pushes cannot upload.
- The tag commit must equal then-current main, metadata must equal 0.0.7, and the
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
Published 0.0.6 adds schemas 19–20, retained in this release:

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
0.0.7 candidate. After separately verified publication, pin `memoriesql==0.0.7`.
Older Python can explicitly use immutable catalog-only `0.0.1a1`, never as a runtime.

## Verification evidence

Unchanged-runtime evidence is retained from PR #18's approved head and
[exact-head installed qualification](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/34993383566):
208 tests per Python 3.13/3.14 wheel/sdist lane. This release compares all runtime,
SQL, contracts and historical inventories against merged baseline bytes, runs focused
release/control checks and verifies fresh genuine wheel and independently rebuilt
sdist-wheel installations. No local database suite is repeated for version metadata.
The existing hosted matrix qualifies the final preparation head and then the actual
merged-main commit. Independently download both selected runs' retained archives and
compare filenames, embedded versions, sizes and hashes to the committed inventory.
Local qualification produced genuine 0.0.7 wheel/sdist archives. Two independent
builds match byte-for-byte, and a wheel rebuilt from the normalized sdist also
matches. Fresh Python 3.13.5 and 3.14 installations pass CLI/version, all 21 packaged
migration hashes, 56 contracts, source-stable API and 44-module ownership checks
from both artifact routes, without database/provider access. Focused release/catalog
checks passed after updating one stale assertion to the newly active release
inventory. Ruff, strict typing, provenance/boundary checks and strict archive checks
pass. No unchanged local database suite was repeated. The PR/evidence lane records
exact heads, hosted CI, independent archive comparisons and review closure.

<a id="separate-owner-actions"></a>

## Authorized release sequence and protected owner approval

1. Open a signed-off, non-draft preparation PR and subscribe. Complete one broad
   review and at most one focused rereview, resolving actionable findings. Merge
   only after final-head CI and independent archive comparison pass.
2. Fetch then-current main and establish its exact SHA. Wait for the latest
   successful `python-package.yml` main-push run at that SHA, then independently
   verify its retained archives against the committed 0.0.7 inventory. A PR run
   does not qualify. Stop on unexpected main changes, missing artifacts or mismatch.
3. Recheck version/tag availability, authenticated publisher identity and exact
   environment protections. Never weaken or recreate settings to proceed.
4. Create and push `v0.0.7` exactly once at verified main. Do not rebuild at upload.
   Verify the publishing workflow's verification job succeeds and `publish` waits
   on the protected `pypi` environment.
5. Give the owner the actual publishing run, release SHA/tag, exact-main CI and
   archive filenames/sizes/hashes. Stop for their protected upload approval;
   preparation review and tag authorization do not authorize automation to approve.
6. After owner approval, follow the publishing job, independently download both
   PyPI files and verify hashes, metadata and Trusted Publisher provenance including
   release SHA. Perform fresh exact-version Python 3.13/3.14 installations and check
   CLI/imports, packaged contracts/migrations and source-stable materialization APIs.
   Green workflow status alone is not publication proof. Inspect actual PyPI state
   before any retry of a partial failure; never overwrite, retag or use skip-existing.

Public release precedes separately authorized Desktop adoption. PR-02O/CP-2 remain
incomplete pending private composition and owner proof. The existing CP-2 checklist
remains the progress authority: no new tracker, P/Q advancement or N/N3/CP-1 reopening.
The six historical unexplained Desktop queue failures remain unresolved. Providers,
owner data, production provisioning, deployment, UI and checkpoints remain deferred.

## Schema 21: explicit qualified source-stable identity

`MaterializeSourceStableUnit` and
`PostgresLogicalUnitMaterialization.materialize_source_stable` provide an explicit
version-2 materialization path under an administrator-approved immutable producer
identity namespace. Qualified event/occurrence keys are scoped to tenant, source
and namespace; native IDs are not assumed globally unique, and equal content is
not identity proof. Unknown or unapproved identity cannot opt in.

The same qualified occurrence across retained revisions and package representations
retains one initial event/unit/bead/task binding, its original inputs, exact evidence
pin and first receipts. Successful fresh operation keys use the shared receipt
ledger. Each package's raw/fold lineage still names its actual retained revisions;
repackaging never switches an existing task to newer evidence. Contradictory native
facts, event declarations, parent identity or ordered content fail explicitly.
Adjacent storage fragments may vary; component interleaving remains significant.

Existing canonical events prohibit source opt-in, returning an explicit unsupported
transition. Raw/fold evidence alone does not prohibit opt-in. After opt-in, legacy
capture/materialization cannot establish a second identity mode on that source.
Revision-sensitive v1 behavior remains available elsewhere. No existing binding is
adopted, no equivalence is inferred, and no historical duplicate repair or
complete-input correction/reauthoring is added.

Installation does not migrate a database, provision trust, activate tasks or configure
a provider. Producer qualification and identity-policy approval are separate from
mechanical consistency checks. Existing explicit activation, authorized readers,
trusted exposure and canonical-apply fences continue to use the original exact pin.
