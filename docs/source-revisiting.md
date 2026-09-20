# Author-controlled source revisiting (schema 20, included in the 0.0.6 candidate)

The unreleased [declared-evidence-scope extension](declared-evidence-scopes.md)
adds an explicit non-native scope route while preserving this historical contract.

An explicitly activated revision-3 complete-unit author can return to exact earlier
source evidence during the same attempt, including after forward coverage ends.
One author selects typed navigation actions; there is no navigator, reviewer,
second scheduler, external transcript reader or new semantic authority.

## Explicit transition and caller composition

`memoriesql.source-revisiting.v1` adds `ActivateSourceRevisiting` (activation
version 2), `RevisitingExecutionInput` (task revision 3), `SourceAuthorStep`,
`ReadSourceEvidence`, `SourceDelivery` (delivery version 2) and
`ApplySourceRevisiting` (canonical command version 4). The canonical output model
and hash remain the existing initial-observation contract. Corrections and
reauthoring are not supported by this transition.

`PostgresCompleteInput.activate_revisiting` accepts an untouched schema-17
binding, an explicitly reviewed revision-3 dispatch policy and zero to four
optional context pins. It uses the original binding lock, task row lock, queue,
shared idempotency receipts and atomic transfer. Current owner cancellation
wins over activation; natural duplicates bind every successful submitted key.
A changed context/policy under an acknowledged key conflicts. Existing revision-2
executions cannot be silently switched: their inputs, receipts and execution
links stay intact. A conflicting existing activation fails explicitly.

The existing dispatch-policy table gains an immutable
`execution_contract_revision` field. Historical policies default to revision 2
and do not qualify the new boundary. Tests explicitly insert fictional revision-3
policies. An installation administrator must separately assess and provision
production trust; an ID or evidence digest is not certification. No policy,
provider, credential or automatic task activation is installed here.

Caller composition selects `load_source_revisiting_task_registry`, one matching
leaf-agent binding, the existing worker/executor and an explicitly supplied
`PostgresEvidenceExposureRecorder`. The worker composes the new typed access
through its existing owned database calls. The ordinary author receives no
credential, SQL execution facility or filesystem access. Only the already
characterized fictional model classes are admitted; real-model admission remains
a separate public prerequisite.

## What the author receives and controls

Forward delivery prefers the complete target when it fits a version-2 window:
65,536 normalized characters and 131,072 canonical JSON bytes. This combines
storage fragments without redefining their source component identity. Otherwise
it supplies bounded windows through the same reader-v2 batches. Unicode escaping
and metadata count against the byte bound. A 40,000-character, three-part fixture
now arrives together; the preserved revision-2 path still uses three interactions.

Each stateless interaction contains the immutable task input, exact delivery,
forward-delivery status and up to 4,096 characters of disposable navigation notes.
The registered caller-supplied agent instructions remain the semantic policy.
The control description specifies operations and units, not semantic importance,
retention criteria or a competing authoring checklist. Notes are never evidence,
exposure proof or canonical memory. Earlier model histories are not repeatedly
hydrated or carried into every request.

The typed output actions are:

- `next`: request the next target window. After the final window it supplies no
  additional evidence; it does not force a result.
- `read`: select a pinned package/inventory, part ordinal and bounded interval.
- `continue`: another bounded author step, without additional source delivery.
- `finish`: return the existing typed initial annotation and target evidence ref.
- `incomplete`: record explicit unsuccessful authorship, without semantic output.

A read chooses normalized characters (up to 16,384) or raw bytes (up to 32,768)
within exactly one declared lineage range. Offsets are relative to that selected
representation. Responses include the unchanged inventory/native facts and raw
lineage, exact content or hex bytes, page digest and next offset. Raw bytes may
split a Unicode code point; join byte pages before decoding. Normalization may
have transformed the source: part-level derivation is not an invented mapping
between individual normalized characters and raw bytes.

Package IDs, inventory digests and ordinals are reference addresses, not grants.
Only the target and immutable `authorized_context` allowlist are selectable.
Context must be sealed, in the same workspace/access scope and currently readable;
its source schema version is pinned. Optional context does not add another
mandatory transcript or qualify another source unit. Context never becomes a new
bead or task in this path. Source-native identity, uncertainty, topology and
parent/event facts remain those already pinned by materialization.

## Trusted delivery, coverage and authorization

The existing guarded model boundary checks the actual outgoing message against
its validated `SourceDelivery`. It durably records request intent and reauthorizes
after the model-concurrency wait, immediately before invoking the characterized
model. A successful returned interaction with durable usage disposition can then
be attested using a separate qualified credential. Reads, prefetch, available
operations and the model's own assertions cannot attest delivery.

The new recorder validates the current attempt/generation, exact package and
inventory, normalized intervals or retained raw bytes, request hash, successful
usage and current attestor policy. It reuses the existing exposure table and
per-request dispatch receipts. Each actual delivery gets a receipt and accounting
intent; only uncovered normalized target intervals are added to unique exposure.
Raw reads, optional context and repeated delivery cannot inflate required
coverage. Canonical application and queue-success guards require the union to
cover every original target part, without gaps, in the current attempt.

Every hydration, cached/window dispatch, attestation and canonical apply checks
current authority. Existing authority locks plus ordered role-capability row
locks serialize relevant policy changes; clock-aware checks reject expiry after
waits. Used optional context is reauthorized through the rest of the attempt;
unused optional neighbors do not become mandatory input. Cancellation, lease
loss and stale attempts cannot regain acceptance through replay. A new attempt
must establish its own exposure. No database transaction spans model work.

Mechanical delivery proves supply at the trusted registered request boundary
with a returned interaction. It does not prove comprehension, semantic quality,
independent source completeness or a real transport's behavior. Production
attestor isolation, producer qualification and provider transport qualification
remain external, separately authorized responsibilities.

## Operational limits and truthful failures

The existing target ceiling remains 131,072 normalized characters. Activation
rejects a larger sealed target before transfer or new receipt/outbox effects,
preserving its original unavailable task, raw evidence and thin bead. The typed
input and current hydration/dispatch/apply authorizer enforce the same ceiling.
The remaining limits are 300
seconds, 524,288 aggregate input tokens and 32,768 output tokens. Revision 3
allows 12 interactions: the former eight-window qualification envelope plus
four bounded author-selected follow-through steps. The separate delivery ceiling
is 262,144 units, counting normalized characters and raw bytes each time supplied;
it bounds rereading without enlarging the mandatory target. These are experimental
ceilings, not production quality/cost approvals. Caller/model ceilings may be
narrower; reaching a ceiling never means the author understood the source.

Reader-v2 batching remains independent of model interactions. Typed reads use
indexed part selection; normalized text remains within a retained 64-KiB part.
Raw verification reads at most 256 chunks and one 256-KiB declared receipt range,
never adjacent bytes outside that range. Typed-read JSON is capped at 256 KiB
(including worst-case Unicode escaping); delivery adds at most 2 KiB of pin
metadata. Owned reader transactions enforce READ COMMITTED, a two-second statement
timeout and a 500-ms lock timeout. The JSON window bound remains unchanged while
its new character bound permits practical complete-context delivery.

Missing/revoked evidence, stale pins, scope escapes, omitted exposure, exhausted
budgets and cancellation have explicit unsuccessful outcomes. Raw evidence and
the thin bead remain retained. No shortened successful input, synthetic annotation
or implicit consent expansion is allowed. The existing worker retains ownership
through accounting, attestor writes, settlement and cleanup; cancellation does not
abandon a started database write or release a quarantined worker early.

## Compatibility and delivery boundary

Migrations 0001–0019 and all 54 earlier record files are byte-for-byte unchanged.
Migration 0020 adds fields to existing execution/policy/dispatch tables and forward
operations/branches in existing admission, authorization and canonical apply.
There is no competing receipt ledger or evidence store. Version-1/2 behavior,
receipts and accepted meaning retain their historical contracts.

The 0.0.6 candidate packages this already merged substrate without runtime, SQL,
contract, ceiling or provider-admission changes. Historical development and published
inventories remain immutable; the new versioned 0.0.6 inventory governs readiness.
See [historical qualification](verification/source-revisiting.md) and
[release controls](releasing.md).

This delivers only the public source-revisiting substrate. A separately authorized
release must precede Desktop consumption. PR-02O/CP-2 remain incomplete pending
release, private composition and owner proof. No producer normalization or
materialization changes, authoring-policy integration, P/Q, entities, UI, provider
calls, owner data, deployment or checkpoint execution are included. Rolling-note
quality and execution ceilings remain experimental. The six historical unexplained
Desktop queue failures remain unresolved; existing N/N3/CP-1 closure stays closed.
