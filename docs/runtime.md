# Experimental provider-neutral runtime

Published `0.0.2` introduced the explicitly inventoried neutral runtime to the
migration substrate. Python 3.13 is the support floor; 3.13 and 3.14 are the
initial qualification matrix (`>=3.13,<3.15`). Install an exact reviewed runtime
wheel. Older Python requires an explicit `memoriesql==0.0.1a1` pin or `--pre`
for the immutable catalog-only fallback; an unpinned request may fail.
The historical catalog package is not a runtime installation. No release is authorized by this PR.

The runtime includes authorization, historical canonical writes and receipts,
neutral capture/range/fold ports, semantic task resolution, PostgreSQL queue and
worker fencing, bounded synthetic execution and accounting. The executor still
accepts only `TestModel` and `FunctionModel`. It preserves conductor support,
authoring limits, task hashes, transaction boundaries and cancellation settlement.
It does not assemble or start a product service.

`contracts/runtime-inventory.json` enumerates every added runtime source and its
hash. Copy entries must equal their approved source content hash. The one task
loader seam takes a caller-supplied `BuiltInModuleRegistry`; canonical task,
agent, contract, effort and evidence-policy values remain unchanged. The internal
static module mechanics retain composition, dependency, placement, integrity and
reserved-identity checks. They are not a supported plugin API or a public product
module catalog. The capture initializer imports no acquisition or adoption code.

The source inventory and sanitized provenance are default-deny. Wheel and sdist
inspection checks exact member sets and runtime hashes as well as all fourteen
unchanged migration resources. CI rebuilds from the public sdist independently,
installs both wheels in clean environments, records transitive dependency artifact
hashes for each supported Python, and runs fictional acceptance with checkout
filesystem access denied. Catalog checks retain all 49 payloads and twelve APIs.

N2 fixtures are authored in the public repository: a fictional orchard, generated
identifiers, in-memory byte ports and synthetic models. No acquisition is needed.
The cancellation contract deliberately retains outcome-settlement authorization
after cancellation, while preventing hydration and canonical apply; this lets
late usage remain accountable. Existing within-bead semantics remain historical
behavior, not the future immutable correction contract.

N3 namespace migration and released-core consumption, immutable correction,
retained-evidence authoring handoff, real providers, provider discovery, host
filesystem helpers, harness adoption, product composition, recall, CP execution,
and publication remain separate work. Public `memoriesql` alone is the future
core namespace. The N2 extraction merged in PR #4; later runtime corrections use separate review lanes.

Base prerequisite: public PR #3 merged the reviewed head
`ad692e66ac064cec2e37cd1b1a3b4e93d548a663` as
`ab99b42b1836deed1a1d8137e55b3b85205fa007`. The latter was exact public
`origin/main` when this isolated N2 branch was created.

## Cancellation cleanup ownership

The worker now bounds foreground cancellation waiting with
`SemanticWorkerConfig.cancellation_return_timeout_seconds` (default five seconds,
finite and nonnegative). This starts on caller cancellation or entry into execution
cleanup; it does not change the task budget, executor grace, or immutable deadline.
With a responsive event loop, a caller receives `CancelledError`, or a
`cleanup_pending` cycle receipt for internally requested cancellation/deadline
cleanup. Pending receipts name the attempt when known and claim no durable result.

The owning worker keeps its existing cycle alive, acquires cleanup retention before
normal outcome persistence as well as cancellation drains, retains it through settlement, and refuses another claim while cleanup remains pending.
`cleanup_pending` reports whether cleanup is still active.
`await worker.wait_for_cleanup()` observes its eventual receipt or error; cancelling
that observer does not cancel cleanup. Successful cancellation settlement returns
a cancelled task receipt to the observer. A failed cleanup remains observable and
blocks subsequent claims on that instance. Caller cancellation during an already-started
normal outcome write still propagates after the write finishes; that cancellation
is not proof that the transaction rolled back. Do not replace the instance until the
old work and lease disposition are understood. There is no second scheduler.

The caller and the owner have separate lifetimes: cancelling an asyncio task is a
request, not proof that a callback, database thread, or external work terminated.
The owned executor drain may remain pending indefinitely. Keep the event loop alive;
loop/process shutdown is not a durable cleanup handoff and can still wait for
cancellation-resistant work. This repair does not promise immediate durable
settlement, continued throughput, or hard termination of arbitrary callbacks.

Started accounting writes finish before cancellation propagates. Late responses
remain accountable against their original durable intent, but cannot authorize
semantic success or another dispatch. Late usage persistence failure is surfaced by
the cleanup observer even if the executor discarded its output. A task cancellation
receipt alone is not proof of complete usage reconciliation. No reported usage is
invented for an intent whose model dispatch was prevented.

Settlement failure or an indeterminate started write never produces a settled
foreground receipt. Retention stays alive while that operation remains owned; once
work is finished and settlement has failed, renewal stops and the existing database
expiry/reaper protocol supplies recovery. Recovery may record lease expiry rather
than the original cancellation reason. Authorization, generation fences and
immutable request intents are unchanged; unresolved usage stays uncertain.

No schema migration or preview-payload change is needed. The runtime receipt enum
and cleanup observation methods are experimental additions. Python support, all
fourteen SQL resources, the 49 preview payloads and twelve preview APIs are unchanged.
This correction is public-authored development after N2, not another extraction.
Changed inventory entries use `public-forward`, retain the original approved source
hash, name the public base commit and base hash, and carry a new current content
hash. Unchanged copies still require byte equality; all inventories still deny
unlisted members. No private source, history or provenance is added.

Published `0.0.1a1`, `0.0.2` and `0.0.3` remain immutable. The following
cleanup history describes the released repair; publication of any later version
requires separate authority. See the
[release controls](releasing.md).

## Historical N2 cancellation limitation

At the N2 baseline, a cancellation-resistant `FunctionModel` callback could outlive the worker's first
cancellation grace interval. The extracted worker cancelled a second time and then
waited without a second timeout while retaining its cleanup lease. Accordingly,
N2 does not claim hard wall-clock termination for such callbacks. This behavior
is inherited unchanged from the approved worker source (SHA-256
`a6b990a6a74461f34e7e757a3e6f7426020f8cef8df4cdbaad2a95f2fcd10398`).
Broad review identified it at PR #4. The separate correction above preserves ownership of unfinished work rather than
claiming it has terminated. The historical extraction review remains closed.

## Cleanup-lease handoff in 0.0.3

In `0.0.2`, cleanup awaited a cancelled ordinary heartbeat before retaining its
lease. Cancellation of that heartbeat still owns its started database call, so
a blocked call could prevent control-only retention while execution remained
unfinished. The permanent fictional PostgreSQL regression fails on installed
`0.0.2`: one expired attempt is reaped while zero is required.

The `0.0.3` change cancels the heartbeat and establishes retention independently.
The existing execution teardown still awaits that heartbeat and every started
call, with cleanup renewal alive, before settlement and release of ownership.
No second worker, scheduler, detached operation, schema, or lifecycle contract
is introduced. Existing SQL makes late ordinary heartbeat writes monotonic
across the task, attempt and concurrency slot; a late heartbeat cannot clamp
a retained lease back to the immutable execution deadline.

Independent retention requires the database to service the control operation.
Transient failures preserve the unavailable signal and retry; total unavailability
can still lead to expiry. An expired or lost generation cannot be revived.
The worker remains quarantined while its old work is owned, and observers must
continue to handle uncertain settlement and late accounting.

`test_cleanup_lease_handoff.py` uses only public fictional fixtures, installed
migrations, and disposable PostgreSQL. It gates I/O before database arrival and
uses a short persisted fixture expiry, without modifying production lease bounds.
It covers expiry/reaping, late ordinary completion, initial/renewal failure, total
control outage, and lost generations. `test_cancellation_drain.py` also holds an
ordinary heartbeat through executor completion, repeated caller cancellation,
late usage and settlement, checking quarantine and eventual complete drainage.
Existing success, authorization, settlement-failure and late-accounting coverage
remains part of installed acceptance.

## Immutable observation commands published in 0.0.4

Schema 15 adds explicit initial authorship and distinct-bead corrections. The
legacy registry remains unchanged; consumers opt into `load_observation_task_registry`
and its two revision-2 tasks. The canonical sink dispatches typed results through
the same worker, cancellation/quarantine, accounting and fenced settlement path.
No executor or scheduler is replaced. See [commands, caller migration and acceptance](immutable-observations.md).

## Unreleased evidence-package reader

`memoriesql.application.evidence_packages` and
`memoriesql.infrastructure.postgres.evidence_packages` add the explicitly inventoried
version-1 storage/reader path. `PostgresEvidencePackages` owns short transactions;
credential/workspace composition remains trusted caller input. Its create/append/seal
commands and inspect/inventory/read operations reuse canonical raw evidence,
authorization and receipts. They do not integrate the semantic executor. See
[evidence packages](evidence-packages.md) for qualified-producer responsibilities,
operation bounds, overflow behavior and the later trusted exposure/apply boundary.

## Unreleased complete-input execution

Schema 18 explicitly transfers unavailable schema-17 bindings into revision-2
complete-input tasks through the existing queue. Its reader, trusted exposure,
canonical apply and cleanup boundaries are described in
[the forward execution contract](complete-input-execution.md). This does not
activate a provider or provision production trust, and PR-02O/CP-2 remain open.
