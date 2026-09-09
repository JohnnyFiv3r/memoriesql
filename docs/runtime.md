# Experimental provider-neutral runtime

The unreleased `0.0.2` adds the explicitly inventoried neutral runtime to the
migration substrate. Python 3.13 is the support floor; 3.13 and 3.14 are the
initial qualification matrix (`>=3.13,<3.15`). Install an exact reviewed runtime
wheel. An older Python resolver may select the immutable catalog-only `0.0.1a1`;
that is not a runtime installation. No release is authorized by this PR.

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

Release recommendation: qualify this correction before the staged runtime is
published. `0.0.2` remains unreleased in this repository; this change grants no
publication authority. Never replace published artifacts, including `0.0.1a1`;
if the runtime version is published before this repair lands, use a new version.

## Historical N2 cancellation limitation

At the N2 baseline, a cancellation-resistant `FunctionModel` callback could outlive the worker's first
cancellation grace interval. The extracted worker cancelled a second time and then
waited without a second timeout while retaining its cleanup lease. Accordingly,
N2 does not claim hard wall-clock termination for such callbacks. This behavior
is inherited unchanged from the approved worker source (SHA-256
`a6b990a6a74461f34e7e757a3e6f7426020f8cef8df4cdbaad2a95f2fcd10398`).
Broad review identified it at PR #4. The separate correction above preserves ownership of unfinished work rather than
claiming it has terminated. The historical extraction review remains closed.
