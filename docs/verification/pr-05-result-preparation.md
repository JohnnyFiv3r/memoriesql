# PR-05 private durable result preparation

This slice owns private preparation bytes and a durable operation journal. It is
not the available query/result interface. The whole amended approval and matching
ea-3 are recorded in `../approvals/pr-05-settings-reconciliation.md`; both approved
normative blobs remain unchanged. M0030 and its canonical lifecycle projection are
an explicit owner-merged dependency, not reimplemented here.

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
the private discard is neither governed erasure nor query cancellation settlement.
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
database acceptance. The failure was not considered a passing test or runtime
capacity evidence. Existing historical failures and all SQL 0001–0030 bytes remain.

Remaining delivery includes admitted SELECT execution, complete compositional
witness generation, atomically available immutable results, paging/hydration,
current whole-closure authorization and revocation/erasure, explicit contexts and
checkpoint/save/restore holds, concurrency/settlement/work/storage and measured
useful W1–W7 fit. No observation-only substitute or lifecycle duplication, model/
provider call, holdout access, default candidate/fallback, owner data, CLI expansion,
production checkpoint, deployment, merge, version selection, release or Desktop
consumption. Mechanical preparation proof is not semantic-quality certification.
