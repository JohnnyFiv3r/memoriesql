# Complete retained input execution

Schema 18 adds an explicit opt-in route from a qualified source-native thin bead
to accepted initial meaning. It reuses the existing durable queue, worker,
PydanticAI executor, model accounting, canonical sink, authorization and cleanup
ownership. Public PR-02O and CP-2 remain incomplete. No provider, producer or
production dispatch identity is provisioned by this migration.

## Forward transition and identity

`memoriesql.complete-input-execution.v1` defines `ActivateCompleteInput`, the
revision-2 `memory.semantic.author-complete-unit` task, execution windows and the
version-3 canonical apply command. A caller must explicitly activate the original
schema-17 binding with a reviewed dispatch policy. Installing schema 18 does not
activate existing tasks. The source producer must retain current raw-read,
capture and task-maintenance authority.

Activation atomically records a `complete_input_executions` link, closes the old
unavailable task with `complete_input.execution_transferred`, and enqueues one
successor in the existing queue with `rerun_of_task_id` naming that original task.
The original input, unavailable binding-time snapshot, package pin, materialization
receipt and enqueue receipt remain byte-for-byte intact. The unique original-task
link prevents duplicate activation. Every successful submitted operation key is
durably bound to its exact request in the existing shared idempotency ledger,
including natural duplicates. A fresh key for an existing activation receives its
own receipt ID and `replayed: true`, retaining the original successor, enqueue
receipt and package pin. Equal-key/equal-request replay is stable; a changed request
under an acknowledged key conflicts. The original activation receipt and execution
link are never rewritten. A conflicting policy also fails. There is no second
scheduler or semantic authority.

Activation takes the existing authority fence, operation-key lock, binding-key
lock, then the original task's row lock in that order. It reloads current task
state under that row lock and rechecks authority after the waits. An owner
cancellation that wins prevents a new transfer, with no successor, receipt or
outbox effects from the rejected activation. A prior unrelated cancellation is
not an execution transfer. Once activation has completed, its valid receipt and
natural replays remain available even if the successor is later cancelled,
subject to current authorization. Receipt acknowledgement and successor creation
retain the same transaction and rollback boundary.

The new task carries the same seal, entire inventory, producer policy and canonical
IDs, plus the exact source declaration, event declaration, native facts, parent
identity/resolution and current source revision snapshot. Occurrence deduplication
still belongs to the original materialization, independently of packaging and read
sizes. Source parts and execution windows never create additional units or beads.
Unknown topology stays unknown. Pending siblings and event totals do not gate a
qualified unit. Physically incomplete or unresolved packages cannot be activated
because they cannot acquire a qualifying materialization binding.

## Trusted exposure, not self-certification

A separately provisioned `complete_input_dispatch_policies` row identifies a
reviewed service principal allowed to attest dispatch for one source/scope. Its
identity and approval evidence are immutable; revocation is one-way. No application
writes or seed policies exist. An administrator must actually assess the adapter's
request boundary and credential isolation. An arbitrary policy ID, qualification
string or digest is not certification. Production review/provisioning is deferred.

Trusted composition supplies `PostgresEvidenceExposureRecorder` with that separate
attestor credential. The credential is never a task field, prompt or model tool.
The ordinary worker, producer, model and reader cannot grant themselves this
identity. Database administrators and the approved attestor implementation are in
the trust boundary; this does not defend against a malicious database administrator
or dishonest approved attestor. Deployment must isolate that credential from
untrusted model callbacks and other worker extensions.

At each existing `_DispatchGuardedModel.request` boundary, the executor inspects the
actual outgoing user message and requires equality with its exact validated evidence
window. After durable request intent, current authorization and cancellation are
checked again, including after the model-concurrency wait. Only a successful model
return with durable usage disposition can acquire exposure. The recorder validates
the task/attempt/generation, original package/seal/inventory, every inventory entry,
raw/fold lineage and exact Unicode interval against retained content. Ordered,
contiguous intervals must start at zero and finish every preceding part. The
request payload hash binds the record to the existing immutable request intent.
A repeated identical recording is harmless; conflicting request replay, interval
replay, omission, substitution and cross-attempt use fail.

Reads, cache population, available tools and `used_evidence_refs` do not create
coverage. The tables have forced RLS and no application table grants. Success
requires complete required-input coverage in the current attempt. A process crash
before recording leaves no proof; retry must expose the evidence again. A late
record cannot revive an expired/cancelled attempt. Provider billing may therefore
repeat across retries, as in the existing at-least-once queue contract.

This proves mechanical supply to the trusted registered model request boundary,
with a returned interaction. It does not prove remote retention, model attention,
comprehension, correctness or independent source completeness. Qualification of a
real transport's mapping from this boundary to its provider request remains part
of deferred provider integration. Fictional FunctionModel tests are not real
provider qualification.

## Complete inspection with bounded working context

The reader fetches bounded batches; the executor independently assembles ordered
windows of at most 16,384 source characters and 131,072 canonical JSON bytes. The
same root run receives each exact interval once. Intermediate typed working notes
are bounded to 4,096 characters and remain ephemeral. Each interaction gets those
notes and the next exact window, without earlier message history or repeated whole
inventory hydration. The last window requests one typed final annotation for the
one genuine source unit. Working notes are model-authored memory, not replacement
source evidence or exposure proof. They may lose meaning; complete mechanical
exposure deliberately does not claim comprehension.

There are no per-reader-page model calls, mandatory reviewer calls or maintenance
jobs. The source-controlled execution ceiling permits eight interactions, 131,072
source characters, 300 seconds, 524,288 aggregate input tokens and 32,768 output
tokens. This covers eight bounded inspection windows with bounded working notes;
it is a new execution ceiling, not an increase to a published payload meaning or
approval of a production model/cost. JSON escaping and metadata can require additional windows and
therefore exhaust the request budget earlier. Caller runtime ceilings may be
narrower. The existing accounting and model-profile limits continue to apply.
The neutral standard-effort profile requires explicit caller composition; no
frontier model or production provider is inherited or selected here.

Rolling-note quality and these execution ceilings remain experimental. This
activation repair makes no authoring-strategy change; real-provider qualification
must assess semantic retention and practical budgets separately from mechanical
exposure.

A unit larger than the execution budget remains retained with its thin bead and
an explicit `budget_exhausted` attempt. The 16-MiB storage ceiling is not a promise
that every retained package can run under this execution budget. Missing trusted
dispatch composition reports `unavailable`. Revocation pauses work; incomplete or
forged exposure cannot succeed. No truncation, summary substitution at hydration,
consent widening or placeholder canonical meaning occurs.

## Reader version 2 and measured tradeoff

`memoriesql.evidence-package-reader.v2` reads 1–8 complete retained storage parts
through the existing `(tenant, package, ordinal)` index. Each part remains at most
64 KiB UTF-8, so an operation reads at most 512 KiB text plus 64 KiB inventory
metadata. Worst-case JSON escaping fits the 4-MiB response bound. Short owned
transactions require READ COMMITTED, a two-second statement limit and a 500-ms
lock limit. Every operation rechecks current raw-read authority after the existing
authority fence and before return. The worker additionally checks its live attempt
and producer/dispatch policies on both sides of the read.

The schema-16 reader, storage ceilings and record bytes are unchanged. The new
operation removes measured per-1,024-character transaction amplification without
raising storage limits. It neither assembles a whole session nor changes genuine
unit boundaries. Reader calls and model interactions are measured separately in
[fictional throughput evidence](verification/complete-input-execution.md).

## Canonical apply and cleanup

The existing canonical sink dispatches `ApplyCompleteInput` into an explicit
version-3 branch of `apply_semantic_annotations`. It retains the existing atomic
statement/evidence/render/receipt/outbox/settlement implementation. New checks
require the exact single initial bead and full attempt-bound exposure; database
triggers also reject direct queue success and legacy/correction bypasses. Current
source authority, producer and dispatch policies are checked at hydration,
dispatch, exposure recording and canonical application, including after relevant
waits. Receipt replay rechecks current policy without rewriting historical receipts.
No transaction spans model/network work. The existing accepted-bead seal and
immutable correction lineage remain authoritative. Reauthoring or corrections
using complete-input units need their own explicit contract; legacy correction
commands cannot bypass this initial-input boundary.

The worker retains ownership through model drain, exposure writes, accounting and
settlement. A started attestor database write finishes before cancellation escapes.
`cleanup_pending` still quarantines that worker; observation does not cancel owned
cleanup. Settlement failure is visible, renewal ends only after owned work finishes,
and the existing expiry/reaper protocol recovers. A foreground cancellation is not
proof of rollback or reconciled usage.

Migrations 0001–0017 and all prior contract record bytes are preserved. Legacy
v1/v2 authoring payloads, registry identities, receipts and accepted meaning are
unchanged. Schema 18 and its contracts must merge and be released in public core
before Desktop consumes them. No future release version or publication control is
selected. Product composition, real-provider interpretation, production trust
qualification, owner-data runs, UI, CP execution and PR-02P/Q remain deferred.
