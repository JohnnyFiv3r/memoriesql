# Canonical logical-unit materialization

Schema 17 adds `memoriesql.logical-unit-materialization.v1`. A qualified, sealed
[evidence package](evidence-packages.md) can create or replay one genuine
source-native unit, its initial thin bead, an exact-package authoring task,
outbox effects and shared receipts in one transaction. It authors no meaning.
This schema-17 path is included in the 0.0.5 candidate. Schema 18 adds a separate
[explicit successor activation](complete-input-execution.md); it never reinterprets
these original unavailable bindings. PR-02O and CP-2 remain incomplete.

## Trust and qualification

The authenticated principal must be the package's recorded producer and retain
current `source.raw.read` and `memory.capture` authority over its source. The
source schema revision must match the command. A sealed inventory must have
`qualified_native_unit`, physically complete records, complete normalized input,
producer-attested source completeness and no unresolved coverage. Sealing an
incomplete tail only preserves that evidence; it never makes the tail eligible.

An installation administrator must separately provision an active, unexpired
`evidence_producer_policies` row scoped to the tenant, workspace, access scope,
source object, producer principal, qualification reference and normalization
policy version. It records the approving principal and the SHA-256 of reviewed
qualification evidence. There are no production rows, application write grants,
self-certification command or implicit fallback. Identity and approval fields
are immutable; revocation is one way. Replacement requires a new policy ID.
Policy changes share the existing authority mutation fence.

This is an explicit **trusted producer boundary**, not independent source proof.
The administrator must assess the producer's handling of genuine boundaries,
occurrence keys, native topology, physically incomplete tails and coverage of all
required evidence. A matching string or artifact digest is not certification.
The core checks the scoped trust decision and the sealed storage inventory; it
cannot determine from normalized text whether the producer omitted a source
record. Neither a receipt nor a fully materialized declared inventory claims
independent source completeness. No production producer is qualified here.

## Identity and event accounting

`MaterializeLogicalUnit` carries a sealed package ID and inventory hash, trust
policy ID, expected source schema revision, genuine event declaration, optional
already-resolved canonical parent, and operation idempotency key.

The package's existing occurrence hash is the durable identity: source object,
source revision and stable occurrence key. Package revision, normalization
version, acquisition windows, parser retries, part IDs and read-page sizes do
not enter occurrence identity. The trusted producer must use the same occurrence
key for the same occurrence across those changes. Producer-assigned keys are
explicitly distinguished from native identifiers; core does not infer occurrence
identity from equal text. Events similarly use source object, source revision
and a stable genuine event key, independently of transport batches.

The first successful binding is immutable. A later package for that occurrence
returns `already_exists` with both its submitted package ID and the **original**
sealed package pin, bead and task. It never silently replaces queued evidence or
mints another initial bead. Changes to asserted native identity/topology,
canonical parent or event declaration conflict. A changed representation is not
an observation correction; later reauthoring would require an explicit future
contract and must preserve accepted-bead correction lineage.

New canonical `source_events` and `source_units` carry
`materialization_version = 1`. Units have `unit_kind = logical_unit`, no inline
content, and the complete inventory hash and opaque package reference. Their
structure preserves the original native facts, including session, branch,
participant, time precision and source order. Event metadata preserves its native
facts and labels its hash as an event-declaration hash, not a whole-source content
hash. Unknown actor, native ID or order remains null. Legacy typed-detail rows
are not fabricated to satisfy requirements for facts the source did not provide.

A supplied canonical parent must already exist in the same event and scope;
known native parent identities must agree. If only a native parent ID is known,
it remains present with `parent_resolution = native_identity_only`; no guessed
canonical parent is created. Missing native parent facts remain `unknown`.
This slice does not retroactively resolve parent pointers.

One command materializes one genuine unit. A complete unit need not wait for an
incomplete sibling. `source_event_materializations` maintains an atomic count;
`InspectLogicalEvent` returns current bounded, point-indexed accounting:

| Declared total | Current count | State |
| --- | --- | --- |
| Unknown | Any materialized units | `total_unknown` |
| Known, larger than current | Current units | `partial` |
| Known, equal to current | Declared units | `declared_inventory_materialized` |

Declared totals are positive 32-bit counts, not request-sized arrays. They never
cause enumeration or semantic splitting. Twelve actual units can commit as
1/12 through 12/12 without changing the eight-unit v1/v2 commands. The declaration
is immutable; learning an initially unknown total requires a later explicit
transition. Receipt progress is labeled `event_progress_at_commit`; replay keeps
that historical snapshot. Current inspection does not rewrite receipts.

## Atomic receipt and task binding

`logical_unit_materializations` links the occurrence to its event, unit, initial
bead, package, policy, materialization receipt and existing queue task. This is a
canonical identity/binding table, not another scheduler or receipt ledger.
`logical_unit.materialize.v1` uses the existing shared idempotency receipts.
The existing `enqueue_semantic_task` creates its own shared enqueue receipt and
outbox event in the same transaction. The unit outbox event is
`source_unit.materialized`; partial events never emit a legacy whole-event
acceptance claim. Deferred constraints require the canonical binding and exact
queue input together. A failure at any point rolls everything back. Equal-key
retries replay the original receipt; changed requests under that key conflict.
Concurrent calls serialize by authority, operation key, event and occurrence.

The new `memory.semantic.author-complete-unit` revision-1 input contains canonical
IDs and one package-level manifest reference. Its pin includes package ID, seal
receipt ID, inventory SHA-256, required part/character/UTF-8 counts, and
`coverage = entire_sealed_inventory`. This binds every inventory part. It contains
no text preview, summary, provider budget or selected-page evidence substitute.
The binding hash is admission metadata, **not** an executable task definition or
agent registry. Published author-observations v1/v2 inputs and hashes remain intact.

## Original bindings remain unavailable

The existing queue stores these tasks as `policy_paused` with
`complete_input_executor_unavailable` and zero attempts. Database guards reject
resume, claim-state changes, attempts and success for these original tasks.
Schema 18 supplies complete-input execution through an explicit successor, not
by removing these guards or changing the original task input. Standalone enqueue cannot commit
without the exact canonical binding. A second guard rejects semantic versions
for these source units, including attempts to route them through legacy authoring
or correction. There is no placeholder semantic output and no second scheduler.
Availability in immutable input and receipts is the binding-time snapshot; the
queue guards enforce the original execution policy. The schema-18 successor
requires complete trusted exposure before accepting a semantic result from the
same sealed pin; installation/upgrading does not activate it.
Cancellation and terminal failure remain operational options; neither authors
meaning. Module admission, current authority and shared queue fences still apply.

The adapter explicitly selects read-committed isolation in its owned transaction.
Both SQL entry points reject stronger snapshot isolation, so a policy revocation
committed while waiting cannot be hidden by an inherited repeatable-read snapshot.
Materialization requires current authorization before work, after keyed waits and
before return; it rechecks policy expiry. Historical receipt replay also requires
current authorization and a currently valid submitted producer policy. Reads
continue through the schema-16 bounded authorized evidence reader. That reader
rechecks current source authority on each operation; it does not prove model
exposure. The schema-18 executor rechecks source/producer authorization during hydration
and canonical apply, with trusted exposure to all required input mandatory before
semantic acceptance. Mechanical exposure does not prove comprehension or independent
source completeness. Complete-input correction/reauthoring remains deferred.

## Bounds, compatibility and later acceptance

Commands/responses are bounded to 16,384 JSON bytes and event metadata to 8,192
bytes. Adapters own a short transaction with a two-second statement timeout and
500 ms lock timeout; the write function also checks its elapsed deadline. Writes
inspect one sealed package's stored totals and indexed identities, without
hydrating content or scanning an event. Inventory storage and reader limits from
schema 16 are unchanged. Oversized evidence remains explicit pending work if it
cannot be represented under the published package contract; it is not truncated,
split into semantic units or made acceptable by raising limits here.

Evidence-reader pages are transport operations, independent of provider-call
granularity. Later execution acceptance must qualify reader throughput across
large, Unicode and many-part complete units, account for authorization overhead,
and demonstrate complete inspection without per-page model calls or an enormous
single prompt. This slice neither runs nor claims that qualification.

Migrations 0001–0016 and all published contract record bytes are preserved. New
nullable identity fields and partial-event cardinality apply only to the marked
schema-17 path. Legacy required fields, typed details, eight-unit/4,096-character
and 4,096-JSON-byte input meanings, historical receipts, accepted semantics and
correction lineage retain their existing rules. No release number or publication
control changes here.

Public contracts, neutral runtime, canonical SQL and ledger changes belong in
`JohnnyFiv3r/memoriesql`. Provider interpretation, production qualification evidence
and product composition belong in `JohnnyFiv3r/memoriesql-desktop`. This core change
must merge and be released before a product consumes it; no private workaround.
The complete-input executor, production trust decisions, provider activation,
product adoption and end-to-end capture proof remain separate acceptance work.
