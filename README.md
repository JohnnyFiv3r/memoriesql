![memoriesQL — observe, process, store, recall](assets/trademarks/memoriesql-readme-banner.png)

# memoriesQL catalogs and provider-neutral runtime

This public repository preserves the original 49 provider-neutral contract records
and provides 53 records through eight generated catalogs and 12 experimental
catalog Python/CLI APIs. Public `memoriesql` owns the canonical PostgreSQL schema,
authorization, capture/range/fold mechanics, queue, worker, executor and accounting.
It is not the memoriesQL desktop product, a running background service, a recall
engine or a provider integration. Catalog reads do not access external services.

Published `0.0.2` introduced the canonical migration/runtime substrate; `0.0.3`
retains cleanup ownership independently of a blocked heartbeat. Published `0.0.4`
adds schema 15: immutable accepted observations and explicit version-2
initial-authoring/correction commands. Corrections create distinct beads with pinned
supersession. Those releases, tags and inventories remain immutable.

## Prepared 0.0.5 scope

The 0.0.5 release-readiness candidate packages the already merged schemas 16–18:

- **Schema 16:** immutable evidence packages retain exact inventory and raw/fold
  lineage for one source-native unit. Bounded authorized reads do not create meaning.
- **Schema 17:** a qualified sealed package atomically materializes its stable unit,
  initial thin bead, exact-package task binding, outbox effects and shared receipts.
  Original tasks remain explicitly unavailable, with immutable binding-time snapshots.
- **Schema 18:** explicit activation creates one revision-2 successor in the existing
  queue. Authorized reader v2 supplies bounded batches independently of model
  interaction sizes. Trusted attempt-bound exposure and current authorization are
  required before fenced initial semantic application through the existing sink.

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
python3.13 -m pip install ./memoriesql-0.0.5-py3-none-any.whl
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

After separately authorized and verified publication, pin `memoriesql==0.0.5`.
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

This PR prepares exact `v0.0.5` repository controls and candidate hashes. It does not
publish, tag, approve upload or change external publisher/environment settings.
The PR head is not the eventual release SHA: later current-main push CI and owner
authorization must establish that SHA. See [release controls](docs/releasing.md).
PR-02O and CP-2 remain incomplete. The six historical Desktop queue-test failures
remain unresolved evidence; this preparation does not rerun or label them fixed.

## Licensing and contribution

Source and designated materials use [Apache License 2.0](LICENSE). The memoriesQL
name and banner remain subject to [trademark guidance](TRADEMARKS.md). Contributions
use the [Developer Certificate of Origin](CONTRIBUTING.md), not a contributor
license agreement.
