# Bounded transcript-fold recovery (schema 19, included in the 0.0.6 candidate)

A fresh explicitly authorized service can discover acknowledged fold outcomes
from a source ID, inspect original stored facts, and read their exact retained
raw derivation. It needs no external transcript, remembered revision/file key,
or original fold command. Recovery establishes neither source completeness nor
producer qualification, and creates no semantic artifacts. It is not an author
source-revisiting tool and does not extend the executor.

The new record is `memoriesql.transcript-fold.recovery.v1`. Python request and
response models live in `memoriesql.application.fold_recovery` and the caller-
composed adapter is `memoriesql.infrastructure.postgres.fold_recovery.PostgresFoldRecovery`.
One SQL entry point, `memoriesql.recover_transcript_fold_v1(jsonb)`, dispatches
four operations. PostgreSQL remains the evidence authority.

| Operation | Input | Returned evidence and continuation |
| --- | --- | --- |
| `discover` / `DiscoverFoldOutcomes` | Source ID; optional continuation or `after` key; limit 1–32 | Outcome summaries with durable `(source_object_id, fold_receipt_id, outcome_ordinal)` keys; fixed watermark; `continuation` and `resume_after` |
| `inspect` / `InspectFoldOutcome` | Exact outcome key | Revision/file identity, connector/profile/format context, stored outcome facts, receipt provenance, normalized-envelope digest/byte length, lineage count |
| `lineage` / `PageFoldLineage` | Key; ordinal; limit 1–16 | Original ordered raw receipt IDs, receipt and slice boundaries/digests; next ordinal |
| `read` / `ReadFoldEvidence` | Key; exact envelope or raw lineage ordinal; expected digest; byte offset; 1–32,768 bytes | Hex-encoded exact bytes, page digest, whole selected-evidence digest/length and next byte offset |

`inspect` preserves the unknown span's `topology_status=unknown` and retained-
range storage declaration, or the policy disposition code and scope reason.
For exact turns, subordinate reads return the stored canonical envelope **bytes**;
there is no parse/re-serialize step. Its normalized digest is distinct from the
raw source digest. Raw reads follow immutable lineage, validate the original raw
receipt's complete chunk sequence and hash, then validate the selected slice.
Reader pages and storage chunks are transport boundaries, never semantic units.
A Unicode code point may cross byte pages: concatenate bytes before decoding.

An incomplete physical tail outside acknowledged outcomes remains in raw
storage. Recovery does not advance fold/capture progress or infer EOF from a
terminal discovery page. Completed independent outcomes remain recoverable;
absence of another acknowledged outcome is not proof of source completeness.
The API does not expose unacknowledged tail bytes as a completed outcome.

## Discovery semantics and migration

The old inbox omits required context and detail and orders by offsets that repeat
across revisions/files. The old raw window reader requires the missing revision
and file identifiers. Schema 19 adds an indexed `recovery_position` to the
**existing** fold receipt table; no archive, receipt ledger, or scheduler is added.

The one-time migration backfills each source by receipt UUID order. This is a
stable enumeration of existing receipts, not inferred source chronology. Original
receipt fields and shared receipt payloads remain unchanged. A before-insert
trigger serializes subsequent receipt inserts with a per-source advisory lock,
held through transaction commit/rollback. It allocates the next indexed position
inside that lock. A later position cannot become visible while an earlier insert
is still pending. The existing fold command and replay payload stay unchanged.
Stronger-isolation concurrent writers may receive a transaction/uniqueness
conflict and must follow their existing retry policy; no committed receipt is
silently skipped. The migration requires the runner's exclusive DDL window and
may scan/backfill existing receipts once; routine recovery does not do that work.

First discovery fixes the highest committed receipt as its watermark. Each page
continues strictly after the previous outcome, within that watermark. Serializing
and replaying a continuation after restart returns the same immutable selection;
replaying a page intentionally repeats that page. Follow the returned continuation
to enumerate once. At terminal completion, start a **new** discovery using
`after=resume_after` to include later commits. Empty sources return no watermark
or resume key. Offsets are not global identities, and changes of revision/file
remain separate receipts. A cursor with an absent anchor/watermark, impossible
ordinal, reversed watermark, or another source fails explicitly. Cursors are seek
positions, not signatures proving earlier inspection; a caller may intentionally
seek to a known valid key. Every seek is newly authorized.

## Authority and operational bounds

The adapter requires a caller-supplied credential hash and workspace, and owns a
short idle-connection READ COMMITTED transaction. It sets the existing application
role, begins a current authorization context, and calls the new function. The
function reuses `evidence_package_authorize(source, false)`: current
`source.raw.read`, source resource/scope policy, role/capability and delegation
intersection, existing authorization audit, and the existing shared authority
mutation fence. It rechecks after the fence wait and before return. A shared lock on the exact
role-capability row (after the tenant authority fence) serializes role-policy
UPDATE/DELETE through transaction completion. An additional return-time
`clock_timestamp()` expiry check covers the context/credential/pairing deadline
and currently applicable explicit-scope access grants; the published helpers
retain their statement-clock behavior. Multiple valid grants remain alternatives,
including non-expiring access grants. A scope/principal index supports the
existing access-grant lookup. The fence
orders relevant revocation against delivery; revocation after a completed read
cannot retract bytes already received. Continuations confer no authority.

Service identity remains service identity. Neither the migration nor adapter
grants any capability or impersonates a human. Tests explicitly configure a
fictional service role policy and pairing grant. Production policy and
provisioning remain caller/owner decisions. Published human-only raw readers and
the metadata inbox retain their original distinct policies and behavior.

| Bound | Reason / bounded work |
| --- | --- |
| Discovery 32 outcomes | Indexed source/position seek, at most 34 receipt candidates and 33 returned/lookahead outcomes; each outcome query uses the receipt/ordinal primary key |
| Point detail | One receipt and outcome lookup; at most 1,024 stored lineage rows counted (existing ordinal constraint); exact envelope metadata lookup |
| Lineage 16 ranges | Primary-key seek with one lookahead and at most one predecessor lookup; gaps or terminal coverage shortfalls fail |
| Bytes 32 KiB | Hex plus metadata fits the 128 KiB response cap; any valid stored envelope/lineage slice is inspectable through continuation |
| Raw validation | One original receipt, at most 256 existing chunks and 256 KiB accumulated bytes per read, plus the selected slice; no history hydration |
| Envelope validation | At most the existing 1 MiB stored envelope per read, bounded independently of history; hash and slice without semantic reinterpretation |
| Request 8 KiB; response 128 KiB | Explicit rejection rather than silent truncation |
| Lock 500 ms; operation 2 s | Adapter per-statement timeout, SQL lock timeout and final recovery-body elapsed-time check; standalone SQL callers must also set a statement timeout for interruption rather than only deadline rejection |

Each adapter operation executes six SQL statements plus transaction control:
isolation, two timeouts, application role, context, recovery. Those are database
operations, not provider interactions. This slice makes **zero provider calls**.
The load test exercises a deep seek through 4,097 receipts and a 64-part Unicode
unit; measured results are in the verification note. Operational limits do not
qualify a source unit and must not drive semantic splitting.

Malformed/stale selection raises a request error; missing/corrupt retained evidence
raises `fold_recovery_unavailable` (`P0002`); absent/revoked authority fails closed;
lock/work/response limits raise explicit database errors. There is no truncated
success result or fallback to filesystem/provider access. SQL failures roll back
that operation's transaction and audit work. Callers may retry a bounded operation
with current credentials; recovery owns no maintenance or retry machinery.

## Compatibility and dependent work

Migrations 0001–0018 and all previously published record bytes remain unchanged.
Schema 19, two inventoried runtime modules and one generated record are additive.
The 0.0.6 candidate packages this already merged substrate without changing its
SQL, runtime or record bytes. The earlier schema-19 development inventory and all
published inventories stay immutable; the new versioned 0.0.6 inventory controls
release readiness. See [the release guide](releasing.md).

This delivers only recovery substrate. Author-controlled source revisiting is supplied by
the separate schema-20 slice, also included in the 0.0.6 candidate. A later authorized release precedes product consumption;
producer normalization/qualification, private composition, owner proof, P/Q,
real-model admission, provider policy and UI remain deferred. Mechanical recovery
and exposure do not establish model comprehension or independent completeness.
Rolling-note quality and execution ceilings remain experimental. PR-02O and CP-2
remain incomplete; existing checkpoint closure and unresolved historical queue
evidence are not changed by this public recovery work.
