# memoriesQL catalogs and provider-neutral runtime

`memoriesql` 0.0.8 is an experimental, pre-alpha distribution of governed contract
catalogs and provider-neutral runtime mechanics. It preserves the original 49
provider-neutral records and contains 59 records: 53 JSON Schema records, five
Python-surface records and one synthetic reference connector. Five other catalog
kinds remain explicit empty `not_implemented` documents. Twelve experimental catalog
Python/CLI APIs provide listing and exact lookup.

Public core owns canonical PostgreSQL migrations, authorization, capture/range/fold,
canonical transactions, the durable queue, worker, executor and accounting. Desktop
composition, live acquisition, real-provider bindings, recall and a running product
service are not included. Catalog reads use package resources without database
imports or external access.

```console
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

The 0.0.8 candidate packages the already merged public prerequisites:

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

## Evidence packages and complete-input opt-in

Schema 16 retains immutable evidence packages, exact inventories and raw/fold
lineage for source-native units, with bounded authorized reads. Storage completeness
is distinct from producer-attested source completeness; neither proves author exposure.

Schema 17 atomically creates/replays a qualified unit, initial thin bead, exact
sealed-package task binding, outbox effects and shared receipts without authoring
meaning. Pending or unresolved packages do not qualify. Original bindings remain
unavailable for execution, preserving their inputs, pins and receipts.

Schema 18 adds explicit successor activation, authorized reader v2, trusted
attempt-bound evidence exposure and fenced initial semantic application through
the same queue/worker/executor/sink. Reader pages are transport boundaries, not
semantic units or instructions to make one model call per page. An owner cancellation
that wins activation prevents a successor. Every successful operation key, including
natural duplicates, is recorded in the shared ledger and cannot be reused for a
changed request; valid replay preserves the same successor and package pin.

Current source and producer/dispatch authority are rechecked during hydration,
dispatch after waits and canonical application. Full required-input exposure is
mandatory for successful application. This establishes mechanical supply at a
trusted execution boundary, not comprehension, correctness, remote retention or
independent source completeness. An arbitrary qualification string or digest does
not certify a producer or attestor. Rolling-note quality and execution ceilings
remain experimental. Over-budget or unavailable input remains retained with its
thin bead and a truthful outcome; no silent truncation or placeholder meaning is
allowed. Complete-input correction/reauthoring is not delivered.

Installing/upgrading does not migrate a database, provision production trust,
activate tasks, configure a provider or start a worker. Callers must explicitly
choose an authorized migration and provide credential/workspace, source qualification,
producer/dispatch and worker-claim policies, task/module and agent/model registries,
worker composition and a separately trusted exposure recorder. Activation is a
separate operation; schema-17 bindings cannot fall through to legacy shortened
input. Production trust provisioning and real-provider interpretation remain
separate, deferred work. Qualification here uses fictional `TestModel`/`FunctionModel`
callbacks rather than real providers.

## Compatibility and cleanup

The package requires Python `>=3.13,<3.15`; Python 3.13/3.14 have installed wheel and
sdist coverage. Dependencies remain exactly `psycopg[binary]==3.3.3`,
`pydantic==2.13.3` and `pydantic-ai-slim==2.27.0`, without provider extras. Once
publication is independently verified, install with the exact `memoriesql==0.0.8`
pin. Before publication, use a reviewed local 0.0.8 artifact. The immutable
`0.0.1a1` remains catalog-only on its original Python versions: explicitly pin it
for older Python. An unpinned older-Python request may fail; `--pre` only makes that
historical catalog fallback eligible, never the incompatible runtime.

Published 0.0.4/schema-15 immutable accepted meaning and explicit version-2
corrections remain supported on their existing path. Corrections create distinct
beads with pinned supersession, not edits to accepted meaning. All migrations
0001–0024 and all 59 existing contract records are unchanged by 0.0.8 release preparation.
Historical receipts remain subject to current authorization.

Retain a worker reporting `cleanup_pending`, its event loop and owned cleanup;
observe `wait_for_cleanup()` before treating the cycle as settled. Foreground
cancellation is not proof of termination, rollback or reconciled usage.

All 0.x APIs have no general compatibility guarantee. Published contract ID/version
payloads are immutable: breaking changes use a new contract version, and an uploaded
distribution is corrected through a new distribution version. Publication, product
adoption and database deployment require separate authorization. PR-02O/CP-2 remain
incomplete; the six historical Desktop queue failures remain unresolved evidence.
See the [public release and caller guide](https://github.com/JohnnyFiv3r/memoriesql/blob/main/docs/releasing.md).

Source uses Apache-2.0; that license does not grant rights to memoriesQL trademarks.

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
