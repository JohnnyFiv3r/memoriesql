# PR-05 public-core internal result preparation

This public-core lane owns internal preparation, native membership witnesses and
atomic result construction. It is not the available query/result interface. The whole amended approval and matching
ea-3 are recorded in `../approvals/pr-05-settings-reconciliation.md`; both approved
normative blobs remain unchanged. M0030 and its canonical lifecycle projection are
an explicit owner-merged dependency, not reimplemented here.

M0034 is based on public main `cba4389ac937a17191aa84698b6be18b318dfd6b`,
including owner-merged #56 accepted head `5fb326ca886a84402ce86fa8ee95790d5e1476c9`.
PR-03 confirmed M0034 allocation, with no overlapping lifecycle migration.
M0031–M0033 and the canonical M0030 projector remain byte-identical dependencies.

The primary acceptance claim for M0034 is **atomic private persistence of native
bag-result values and complete witnesses for the qualified composition cut,
without repeating completed SELECT work**. The cut includes explicit projections,
typed filters, key and outer joins, nonrecursive CTE/derived-table composition and
`UNION ALL`. PostgreSQL computes values and membership in one restricted-reader
statement; Python binds emitted membership to exact frozen keys and shares DAG
nodes. Duplicate tuples/branches retain distinct saved ordinals. Outer nonmatches
and empty outputs retain the searched population, predicate and protected closure.
No Python SQL interpreter, original unchecked SQL, guessed contributor or empty
witness substitute is used. The admitted full private SELECT surface is unchanged.
Group/distinct, other sets, windows, correlated subqueries and recursion still
need their own witness-publication qualification; they cannot use this internal
commit path. This is a delivery milestone, not a reduction of the approved interface.

Typed ordered rows, original validated request/fingerprint, admitted program,
catalog/policy/independently checked privilege-profile pins, frame, coverage,
complete logical row population, exact canonical lifecycle bytes and evidence
bindings are sealed together. Canonical lifecycle records retain their original
numeric encoding in byte-preserving partitions, not a JSON float round-trip.
The result-json-v1 digest covers immutable schema/rows/query/frame/lineage/coverage
and witness hash; it excludes creation/delivery receipts and changing work state.
These private records are not yet the available wire response or admitted evidence.

Publication uses a fresh short trusted authority transaction while the original
source frame is held. Its repeatable-read snapshot cannot see a journal created
later by M0033, so publication independently checks that settled invocation and
the exact original issuer PID/start/virtual-transaction epoch. It also checks owner,
credential, query key/fingerprint, manifest and byte/digest pins. Sealing, result
identity and creation receipt commit atomically through M0031's single store;
there is no second body/allocation ledger. The acknowledgement follows commit.
The original deadline governs construction/publication; the next SQL statement's
timeout is set before dispatch. Publication records elapsed wall time since native
settlement separately. This is neither CPU/I/O measurement nor complete run work
accounting. A failed/uncertain write never grants ownership takeover or new SELECT.
Exact redelivery and authenticated private creation-receipt recovery require no
original snapshot or query rerun; they grant no result-byte disclosure. An unfinished
commit after the original source frame ends remains unavailable/pending.

Creation stores a fixed 30-day standalone deadline. No paging, standalone access,
expiry cleanup, saved-input/parent composition, checkpoint/named-save/restore hold,
whole retained-closure reauthorization or governed erasure API is available here.
Private owned receipt recovery is executor bookkeeping, never a model-facing
metadata route. Availability remains gated by all those surfaces, complete witness
qualification, actual host credential isolation, durable cumulative accounting,
physical storage qualification and useful W1–W7 fit. Existing M0031 parent holds
remain unchanged; M0034 does not claim to deliver refinement or expansion.

Seen fictional acceptance checks native bag multiplicity, both sides of outer
nonmatches, empty-filter population/predicate preservation, digest/frame/fingerprint
refusal, original-frame loss, deadline exhaustion, immutable bodies/receipt ACLs,
crash after sealing with no partial result, and restart/lost-response/concurrent
redelivery without native redispatch. Installed qualification and exact-head CI
belong on the PR. Earlier failed packaging/type/import/encoding fixtures remain
recorded; no retry erases a failure or establishes measured useful workload fit.

M0031 reserves one private ownership identity for an authenticated `(run,step)`
and request fingerprint before execution. Exact retry returns that same journal
identity; different semantic inputs conflict. No query dispatch or ownership
takeover exists. A restarted executor must recover/settle the owned operation,
never interpret a replayed reservation as permission to execute the query again.

Sealing atomically commits canonical content, witness and protected-dependency
partitions, their domain-separated private artifact hash, exact ordered parent
pins/holds and the private mutation receipt. Partition lengths enter the private
hash. This hash is deliberately separate from the approved public result digest;
no wire digest, result identity, creation time or disclosure deadline is selected
by this preparation layer. Parents are immutable and stored once. There is no
ancestry-depth cap. Malformed or noncanonical Python inputs are rejected before
the trusted database write. The SQL routine independently checks bytes/hash,
ownership, parent pins, authentication, shared authority fence and snapshot.

All four tables have forced RLS and no application/public table grants. Only the
three private write functions are granted to the trusted application role; no
read function, agent catalog entry, raw bytes, rows, counts or metadata disclosure
is added. This is a trusted executor dependency, not an agent capability. The
actual restricted query login still requires independent privilege qualification.
The canonical lifecycle/shared-frame API supplies authentication fencing; this
does not certify complete dependency authorization or provenance construction.

Workspace allocation serializes reservation, sealing, parent holds and discard.
A changed allocator row makes a stale repeatable-read snapshot serialize-fail
instead of counting pre-wait allocations. Recover the same private command in a
new fenced snapshot; there is no automatic query rerun. Reservation targets use
the approved 64 MiB/result, 128 MiB/run and 512 MiB/workspace ceilings. Shared parents
are retained once. Discard refunds body retention, preserves cumulative new
allocation and charges the remaining journal. A held parent cannot be discarded;
the internal discard is neither governed erasure nor query cancellation settlement.
Discarded keys refuse every reservation retry, including original inputs: cleanup
removes the protected fingerprint, so no tombstone is reported as an exact-input
replay or fresh execution grant. Repeating the owned discard remains idempotent.
It cannot be used to refund work while any remote execution is unconfirmed.

The current preparation charge is encoded partition bytes plus a minimum 8 KiB
control-state charge and 512 bytes per new parent hold. This is an internal minimum
charge, **not a qualified physical allocation profile**. It cannot support an
available result or a measured-physical-bytes claim. Actual physical overhead,
owned growth/WAL, cumulative database/transport accounting, abandoned preparation
cleanup and confirmed cancellation settlement remain integrated-executor
qualification obligations. No operational capacity guarantee is made here.

Seen fictional acceptance covers exact bytes/partition identities, duplicate-key
and alternate encoding refusal, durable restart/lost-response identity, immutable
parents and >20 continuations, wrong owners/tenants/pins, table/function ACLs,
authority-frame bypass, overflow without publication, crash injection between
body/holds/receipt, retained cumulative charges and simultaneous/stale-snapshot
allocation. Installed wheel/sdist and exact-head CI receipts belong on the PR.

The first strict-type check caught a test fixture accidentally shadowing
`unittest.TestCase.run`; the fixture field was renamed to `run_ref` before any
database acceptance. Installed fixture failures and corrections are retained in
`pr-05-preparation-failures.txt`: password-redacted reconnects, the immutable-change
error class, repeating the one-time bootstrap and a LIKE placeholder. The fixtures
were repaired without changing canonical behavior. No failure was considered
passing acceptance or runtime capacity evidence. Existing historical failures and
all SQL 0001–0030 bytes remain.

Remaining integrated delivery includes the remaining logical populations and
witness shapes, atomically available immutable results, paging/hydration,
current whole-closure authorization and revocation/erasure, explicit contexts and
checkpoint/save/restore holds, concurrency/settlement/work/storage and measured
useful W1–W7 fit. No observation-only substitute or lifecycle duplication, model/
provider call, holdout access, default candidate/fallback, owner data, CLI expansion,
production checkpoint, deployment, merge, version selection, release or Desktop
consumption. Mechanical preparation proof is not semantic-quality certification.
