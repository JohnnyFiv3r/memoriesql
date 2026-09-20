![memoriesQL — observe, process, store, recall](assets/trademarks/memoriesql-readme-banner.png)

Unreleased development: [explicit declared evidence scopes](docs/declared-evidence-scopes.md)
add non-native scope qualification on the existing canonical path. This change is
not included in published 0.0.8; release and downstream consumption remain gated.

# memoriesQL catalogs and provider-neutral runtime

This public repository preserves the original 49 provider-neutral contract records
and provides 59 records through eight generated catalogs and 12 experimental
catalog Python/CLI APIs. Public `memoriesql` owns the canonical PostgreSQL schema,
authorization, capture/range/fold mechanics, queue, worker, executor and accounting.
It is not the memoriesQL desktop product, a running background service, a recall
engine or a provider integration. Catalog reads do not access external services.

Published `0.0.2` introduced the canonical migration/runtime substrate; `0.0.3`
retains cleanup ownership independently of a blocked heartbeat. Published `0.0.4`
adds schema 15: immutable accepted observations and explicit version-2
initial-authoring/correction commands. Corrections create distinct beads with pinned
supersession. Those releases, tags and inventories remain immutable.

## Prepared 0.0.8 scope

Published 0.0.7 includes schemas 16–21. The 0.0.8 candidate packages the already merged public prerequisites:

- **Bounded model admission:** exact caller-supplied model/profile/target qualification,
  pre-dispatch request/token reservation and attributable usage through the existing
  executor. No production transport or provider entitlement is supplied.
- **Schema 22:** atomic authored local mentions, including explicit authored-empty
  and unresolved/ambiguous local state, without inventing global entity resolution.
- **Schema 23:** a bounded evidence-backed classification packet, registered
  vocabulary revisions, explicit abstention/disagreement and one attributable
  accepted contribution. Mechanical acceptance does not prove semantic quality.
- **Schema 24:** currently authorized stored bead/result and exact-evidence reads;
  legacy, missing, hidden and authored-empty remain distinct. Reads do not run inference.
- **Accounting cancellation repair:** already-started database writes and late usage
  remain owned through repeated cancellation; callers still retain the worker/event
  loop until cleanup finishes.

The candidate preserves the earlier paths:

- **Schema 16:** immutable evidence packages retain exact inventory and raw/fold
  lineage for one source-native unit. Bounded authorized reads do not create meaning.
- **Schema 17:** a qualified sealed package atomically materializes its stable unit,
  initial thin bead, exact-package task binding, outbox effects and shared receipts.
  Original tasks remain explicitly unavailable, with immutable binding-time snapshots.
- **Schema 18:** explicit activation creates one revision-2 successor in the existing
  queue. Authorized reader v2 supplies bounded batches independently of model
  interaction sizes. Trusted attempt-bound exposure and current authorization are
  required before fenced initial semantic application through the existing sink.

- **Schema 19:** authorized recovery of retained fold outcomes and raw derivation.
- **Schema 20:** explicit author-controlled source revisiting with trusted delivery
  and unique mandatory exposure; details below.

An owner cancellation that wins activation's waits prevents successor creation.
Every successful activation key, including a natural duplicate, is bound to its
request in the shared ledger; replay preserves the original successor and package
pin. These are the completed PR #13 repairs, not new runtime changes in this PR.

Exposure establishes exact mechanical supply at a trusted dispatch boundary, not
model comprehension, correctness or independent source completeness. Producer
attestation and arbitrary qualification strings are not independent certification.
Rolling-note quality and execution ceilings remain experimental; excess input stays
retained with a thin bead and explicit failure. Complete-input correction and
reauthoring are not delivered. See [evidence packages](docs/evidence-packages.md),
[materialization](docs/logical-unit-materialization.md) and
[complete-input execution](docs/complete-input-execution.md).

## Installation and caller opt-in

Python `>=3.13,<3.15` is required; 3.13 and 3.14 are qualified by fictional installed
acceptance. Use a reviewed local candidate until publication is separately verified:

```console
python3.13 -m pip install ./memoriesql-0.0.8-py3-none-any.whl
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

After separately authorized and verified publication, pin `memoriesql==0.0.8`.
Older Python can explicitly pin the immutable catalog-only `0.0.1a1`; it is not a
runtime fallback. The dependencies remain `psycopg[binary]==3.3.3`,
`pydantic==2.13.3` and `pydantic-ai-slim==2.27.0`, without provider extras.

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

Installing/upgrading the package does not migrate a database, provision production
trust, activate tasks, start a worker or configure a provider. An authorized consumer
must explicitly choose a forward migration and supply credentials/workspace,
source/producer qualification, dispatch and worker-claim policies, task/module and
agent/model registries, the worker and separately trusted exposure recorder. Schema
18 activation is a separate command; it never silently resumes schema-17 bindings
or routes them through legacy preview input. Production provider integration and
trust provisioning remain deferred. Current executor qualification uses fictional
`TestModel`/`FunctionModel` callbacks, not real provider calls.

Worker integrations must retain a worker reporting `cleanup_pending`, its event
loop and owned cleanup, and observe `wait_for_cleanup()`. Cancellation does not
prove termination, rollback or reconciled usage. See [migration operations](docs/migrations.md),
[caller composition](docs/releasing.md#caller-composition-and-explicit-opt-in) and
[cleanup ownership](docs/runtime.md#cancellation-cleanup-ownership).

## Authority, compatibility and release status

[`contracts/public-registry.json`](contracts/public-registry.json) is the sole export
and generation authority; unlisted files are denied. All APIs remain experimental
throughout 0.x, with no general compatibility guarantee. A published contract ID
and version payload is immutable. Breaking payload changes require a new contract
version; correcting an uploaded distribution requires a new distribution version.
See [compatibility](docs/architecture/compatibility.md) and [repository ownership](docs/architecture/repository-boundary.md).

This preparation records exact `v0.0.8` controls and fresh candidate hashes.
The owner has authorized preparation and, after the required checks, merge and
exact tag creation. Protected upload approval remains owner-only. The PR head is not automatically the release SHA; successful exact-main
push CI and independent archive comparison must establish that identity. See [release controls](docs/releasing.md).
Private P/Q composition and proof, PR-02O and CP-2 remain incomplete. The six historical Desktop queue-test failures
remain unresolved evidence; this preparation does not rerun or label them fixed.

## Licensing and contribution

Source and designated materials use [Apache License 2.0](LICENSE). The memoriesQL
name and banner remain subject to [trademark guidance](TRADEMARKS.md). Contributions
use the [Developer Certificate of Origin](CONTRIBUTING.md), not a contributor
license agreement.

## Recovery and revisiting retained from 0.0.6

Schema 19 supplies explicitly authorized retained-fold recovery through bounded
window discovery, outcome, lineage and exact-byte operations. It recovers stored
facts, including unknown topology and pending tails, without qualifying producers,
inventing units or creating semantic work. Recovery requires explicit scoped service
access; installation creates no grant.

Schema 20 adds explicit `ActivateSourceRevisiting` activation of an untouched
schema-17 binding into a revision-3 task. The single author can revisit exact
normalized or raw evidence from the pinned target and explicitly supplied optional
context, including after forward coverage completes. Existing revision-2 activations
cannot silently switch. Compose `load_source_revisiting_task_registry`, the existing
worker/executor and a separately qualified revision-3 dispatch attestor. Production
trust provisioning and actual provider qualification remain deferred; the public
bounded-admission interface is included in this candidate.

Complete-context delivery is preferred when it fits the existing 65,536-character /
131,072-JSON-byte window; otherwise delivery and typed rereads stay bounded. The
131,072-character target, 12 interactions and 262,144 repeated-delivery-unit ceilings
are unchanged and experimental. Actual trusted dispatch establishes exposure;
reads, notes and repeated delivery cannot inflate unique mandatory target coverage.
Current authorization, lease/cancellation fences, immutable accepted meaning and
cleanup ownership remain required. Mechanical exposure does not prove comprehension
or source completeness. Complete-input correction/reauthoring is not delivered.

See [retained-fold recovery](https://github.com/JohnnyFiv3r/memoriesql/blob/main/docs/transcript-fold-recovery.md)
and [source revisiting](https://github.com/JohnnyFiv3r/memoriesql/blob/main/docs/source-revisiting.md).

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
