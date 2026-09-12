# memoriesQL catalogs and provider-neutral runtime

`memoriesql` 0.0.5 is an experimental, pre-alpha distribution of governed contract
catalogs and provider-neutral runtime mechanics. It preserves the original 49
provider-neutral records and contains 53 records: 47 JSON Schema records, five
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
publication is independently verified, install with the exact `memoriesql==0.0.5`
pin. Before publication, use a reviewed local 0.0.5 artifact. The immutable
`0.0.1a1` remains catalog-only on its original Python versions: explicitly pin it
for older Python. An unpinned older-Python request may fail; `--pre` only makes that
historical catalog fallback eligible, never the incompatible runtime.

Published 0.0.4/schema-15 immutable accepted meaning and explicit version-2
corrections remain supported on their existing path. Corrections create distinct
beads with pinned supersession, not edits to accepted meaning. All migrations
0001–0018 and existing contract records are unchanged by 0.0.5 release preparation.
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
