# Core Memory engine record intake (development contract)

The `memoriesql.engine-intake.v1` contract and migrations 0055 and 0056 let
the Core Memory engine deliver its records into memoriesQL. This is the first
slice of the engine work package, a development candidate after migration 0054
and not a published capability in `memoriesql==0.0.12`. Migration 0056 makes
delivery require reading and pages reads by an opaque cursor, so a deliverer or
reader learns nothing about records, scopes or tenants it may not read.

The engine turns source events into deterministic chunks and assigns each one
a type outcome: `assigned` (a governed type), `unassigned` (`no_fit`,
`insufficient_evidence` or `ambiguous`) or `pending` (`timeout`,
`provider_error`, `capacity`, `unsupported_input` or `no_classifier`). A chunk
delivered as pending later gets exactly one `type_decision` record. memoriesQL
keeps every record exactly as delivered and authors nothing: no bead,
statement, render, run, rationale, confidence or semantic task is created.

## Why engine chunks are not source units

PR-01 source units carry source-specific structure: a transcript observation
unit must be a whole turn, a document unit a segment, and so on. An engine chunk
is a uniform piece of an event that can split a turn. Writing chunks as turns
would invent turn boundaries, so engine records keep their own tables. A later
slice can expose them to the agent SQL surface as their own relation family.

## Authority

- Each engine scope is one enrolled exact source:
  `source_system = 'core-memory'`, `object_kind = 'engine_scope'`, no
  installation id, and the engine scope as `external_object_id`. Enrollment,
  grants and revocation are the existing
  [source enrollment](exact-source-enrollment.md) operations, so each engine
  scope has its own explicit access scope.
- The engine authenticates as a paired service (`background_service`) whose
  pairing grant lists the `engine.records.deliver` and `source.read`
  capabilities and the engine scopes' access scopes, with a `read` and `write`
  source grant on each. `engine.records.deliver` is also granted to
  `personal_owner`; no other role holds it.
- Delivering to an engine scope needs everything reading it needs:
  `engine.records.deliver` with a `write` grant inside its window, and
  `source.read` with a `read` grant inside its window, on the scope's source.
  Deliver-only engines are not supported. Every outcome about records already
  held therefore goes to a caller who may read them.
- Reading needs `source.read` and `read` on the engine scope's source.

## Delivery

`accept_engine_records_v1(request)` takes
`{"contract_version": 1, "expected_schema_version": 55, "records": [...]}` with
1–256 records (16 MiB at most) and answers one outcome per record, in order:

| Outcome | Meaning |
|---|---|
| `accepted` | Stored under a new shared-ledger receipt (`engine_record.accept.v1`, keyed by the engine scope's source id and `record_key`). |
| `already_accepted` | The identical record is already held. |
| `conflict` | A different record holds the key. The stored one is kept, first writer wins, and returned as `stored`. |
| `rejected` | Final for this record, with a reason: malformed, an unknown record kind or contract, a type that is not a current governed bead type, a catalog other than `memoriesql.bead_types`, text that does not match its hash or ranges, chunks that do not reproduce their event, or a second final type for one chunk. |
| `retryable` | The engine scope is not enrolled, or this credential may not both deliver to it and read it. These cases are one answer with one reason, given before any record of the scope is consulted or locked, so it never shows whether a scope exists. Every later record in the batch is answered the same, unread, because the engine resends from there in order. |

Each accepted chunk is checked against its SHA-256 and its byte and character
ranges. When the last chunk of an event arrives, the chunks in ordinal order
must be contiguous and reproduce the event's content hash; the event is then
recorded complete. All chunks of an event must carry the same event object.
A type is accepted only when its key and revision name a current authorable
bead type and the record's type catalog is `memoriesql.bead_types`. A pending
type can be decided once, later; a final type is never replaced.

Writers to one engine scope take turns: before it reads a record, a batch locks
each of its scopes that the caller may deliver to and read, in source order,
and never any other scope, whether or not it exists. A scope's records
therefore commit in acceptance order, and a reader's `next` cursor never passes
a record that commits later. A batch that waits more than 500 ms for a scope
fails busy (`55P03`) with nothing written, and the engine resends it. No other
intake lock is taken, so a record that names another scope's record key or
event id is only compared with it and cannot hold that scope back.

Every accepted record writes one outbox event: `engine_chunk.accepted` or
`engine_type.decided`. No relay consumes them yet.

## Identity within an engine scope

Every record key, chunk id and event id is identified within its engine scope:
engine records, chunks, events, completions and type decisions are all keyed by
the scope's source as well. The engine derives these ids from event keys that
include the scope, so the same id in two scopes names two different things. A
writer allowed into one scope therefore cannot take another scope's keys
before that scope delivers them, and replays, conflicts, completions and late
types are judged within one scope only. Outbox payloads carry the scope's
`source_object_id`.

## Reads and vocabulary

`read_engine_records_v1` returns one engine scope's records in acceptance
order, paged by `after` and `limit` (at most 1,024). Each page's `next` is an
opaque cursor within that engine scope; pass it back as `after` to continue,
or omit `after` to start from the first record. It names the last record
returned within its scope and depends only on the scope's own records, so
other scopes' and tenants' deliveries never change a cursor or a page; no
global sequence is returned. A scope that is not enrolled or that the caller
may not read is `42501` whatever the cursor; a cursor that names no record of
a readable scope is `22023`. `engine_vocabulary_v1`
returns the current governed bead types as the engine's vocabulary
(`id = "memoriesql.bead_types"`, `types = [{key, rev, definition}]`); the engine
loads it rather than registering types of its own.

The typed requests and PostgreSQL adapter live in
`memoriesql.application.engine_intake` and
`memoriesql.infrastructure.postgres.engine_intake`.

## Not in this slice

Relationship records, governed erasure (tombstones and retractions), the outbox
relay to other consumers, and an agent SQL relation over engine chunks. A text
containing U+0000 cannot be stored in PostgreSQL; the engine's sink refuses such
a record before sending it.

Fictional fixtures: `tests/runtime/test_engine_intake.py` covers exact storage,
replay, first-writer-wins, the single late type, ungoverned and malformed
records, one retryable answer for every scope a caller may not both deliver to
and read (enrolled or not, without waiting on its lock), deliver-only and
read-only callers learning nothing about held records, authority, conflicts
with the stored record, immutability, concurrent chunks of one event, writers
taking turns in one scope, a batch that cannot hold back a scope it may not
write or whose ids it names, a scope that cannot take another scope's keys
first, cursors that other scopes' and tenants' deliveries and rollbacks never
change, records accepted before 0056 paged after it, and an impossible zone
offset rejected alone. `tests/unit/test_engine_intake_migration.py` pins the
grants, row-level security, immutability and 0056's single deliverability
predicate. Historical SQL 0001–0055 and records are unchanged.
