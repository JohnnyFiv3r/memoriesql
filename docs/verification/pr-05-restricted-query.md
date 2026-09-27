# PR-05 restricted native invocation

Primary acceptance: trusted, transaction-bound execution of admitted SELECT over
the nine canonical assessed-relation populations, through a separately restricted
LOGIN, with independent cancellation initiation and owned settlement/recovery.
This is an internal dependency. The public SQL operation catalog remains at zero
available entries; returned native rows are not immutable public results.

## Approved dependency and ownership

Base main is `522a68a362456ad11f7f82a1a21ce5664aaefdfb`, the owner-merged #55
containing accepted head `8d89fdb30a9ae431c538377a8ff3f9d03d634215`. #46, #52 and
qualified PR-03 head `2bacc4c949b0e7ec6a0485eb2a9b849e821f968c` are ancestors.
PR-03 confirmed M0033 is free and that lifecycle storage, governance and projection
remain its responsibility. This slice consumes its projection; it adds no lifecycle
interpretation, observation-only replacement or private core.

The complete amended version-2 record is
[pr-05-settings-approval-candidate-v2.json](../approvals/pr-05-settings-approval-candidate-v2.json),
[durably approved by the owner](https://github.com/JohnnyFiv3r/memoriesql/pull/49#issuecomment-5851352880).
At `b3865f307b50bd94681b387ad0f3b16fbb20213a`, exact packet/dependency Git-blob
SHA-256 values are `78bbc04eaeffd0fd47b0576e270f25eaa30e07a90b42e53d27e64308dfd8269e`
and `69a0d985466c6610a3a2abc73bcef9242749ff9535cb709e32867c7e06414d2a`.
Content-free ea-3 matches the whole record, canonical record digest
`75d710cf604fff74977fca61042d0ead468647d1fc86a5e8de8da06b0f060b11`.
Both normative blobs, every earlier approval/custody record and SQL 0001–0032 are
unchanged. No unseen material or access path is accessed or received.

## Behavior and authority

`PostgresRestrictedQuery` admits the supplied composable SELECT with the existing
SQLGlot admission/emission path; original SQL is never sent to PostgreSQL. Genuine
join, UNION ALL, grouping and explicit ROWS window composition run natively. This
bridge accepts only relations actually prepared by M0032: other catalog relations
are explicitly unsupported here. No fixed query templates or empty fallback are
introduced. The approved full interface remains an integration obligation.

M0033 stages typed prepared rows in a private forced-RLS table. Nine fixed-schema
security-barrier views belong to a separate NOLOGIN owner with no bypass. Independent
preflight checks the reviewed effective privilege/function/view/policy manifest on
every invocation; a privileged LOGIN SET ROLE downward cannot qualify. PUBLIC
routine grants and database CREATE/TEMP are closed; existing PUBLIC routine access
is preserved only for trusted application/worker roles. Existing private lifecycle
helpers gain no new grants. Reader provisioning is caller-supplied and unavailable
as an agent operation. Seen development fixtures use explicit signatures, never
automatically approve the database's discovered permissions.

Scope is bound to the real LOGIN, backend PID/start and exact virtual transaction,
plus the live canonical issuer transaction/context, credential and deadline. No
agent-set GUC supplies authority. Capturing the virtual transaction after BEGIN/SET
but before the reader's first data snapshot permits committed staging to be visible
without a write XID. The RLS invocation check runs once per statement, avoiding a
live-statistics check for every row. Ambiguous active scope is refused. The trusted
bridge rechecks source authorization/context and both transaction epochs after
complete fetching; failures return no rows or column metadata, including aggregates.
The canonical authorization fence remains held through this handoff.

The caller must isolate reader credentials, factories and bookkeeping from the
agent in its actual deployed security principal. These fictional tests do not
qualify that host boundary. A raw PostgreSQL LOGIN can alter USERSET settings;
stock read-only catalog metadata is not hidden. Only the trusted bridge dispatches
the admitted SQL and performs fixed transaction settings. Parser admission alone,
a SELECT prefix, EXPLAIN or returned-row LIMIT is never the security boundary.

## Ownership, bounds and restart

A durable M0031 preparation owner receives at most one M0033 invocation. Fresh
workspace allocation serialization prevents overlapping unsettled invocations.
The journal and immutable staging population commit together before native SELECT.
PID/start/virtual-transaction binding prevents cancellation from targeting a reused
backend or later transaction. Cancellation does not assert rollback: unconfirmed
stop retains staging and the reservation, and blocks preparation seal/discard.
Settlement purges staging only after the identified transaction is absent.

A supervisor initiates owned cancellation independently of PostgreSQL's statement
timeout and the query thread. Fixed settings include pg_catalog-only search_path
(logical relations are schema-qualified), 4 MiB work_mem, 64 MiB
temp_file_limit, no parallel workers/JIT, 500 ms lock timeout and a remaining
statement timeout. Connection/factory delays and cancellation delivery are not
hard elapsed-time guarantees. The owned control statement has a 500 ms bound;
uncertainty remains pending rather than releasing ownership or reporting success.
Complete encoded output has an explicit 8 KiB–64 MiB admission ceiling. There is no
examined-row, CPU, I/O, RSS or physical-storage guarantee.

`recover` returns only content-free owned journal state, optionally requests owned
cancellation, and never reconnects a reader or reruns SQL. A replayed reservation
or stale original receipt cannot dispatch the invocation again. An uncertain stage
commit remains pending even when its response was lost. Unknown restart timing
retains the full duration reservation; it is not recorded as zero. Observed duration
is a component wall-time diagnostic, not the approved whole-operation cumulative
database-work ledger. Restored permissions, budget resets or recovered result rows
are not provided by this journal.

## Acceptance and qualification

| Case | Required observation |
| --- | --- |
| Native composition | Real restricted LOGIN runs canonical join → UNION ALL → group → ROWS window with multiplicity four; canonical records unchanged |
| Independent authority | SET/set_config, physical/catalog SQL and data-modifying CTE refused before dispatch; native private reads/writes, unsafe procedures, TEMP and elevation denied |
| Manifest/frame | Wrong LOGIN, procedure drift or a population borrowed from another source transaction refuses before staging |
| Disclosure epoch | Another backend or later transaction on the same LOGIN inherits no scope; trusted bridge refuses the whole outcome |
| Authority expires | Actual credential deadline passes after staging; aggregate rows and column metadata are both refused |
| Overflow/deadline | No partial output; independent cancellation starts even with native statement_timeout disabled; confirmed settlement purges staging |
| Explicit cancellation | Owned cancellation yields no output; wrong owner cannot cancel; repeated requests do not assert refund |
| Uncertain settlement | Live reader retains staging and full reservation; preparation discard blocked; recovery with unknown timing does not charge zero |
| Restart/lost response | Three fresh installed processes preserve original governance receipt replay and historical projection; original invocation journal recovers without SELECT redispatch |

Focused installed tests, the complete local installed Python 3.13/3.14 convergence
lane, all three fresh-process proofs, artifact equality and exact-head cheap CI
are required and recorded on the PR. Adversarial fanout proves safe exhaustion
only; it is not useful-workload fit. The successful composition fixture demonstrates
mechanics, not representative W1–W7 fit or semantic quality.

Remaining integrated delivery includes all admitted relation/claim capabilities,
complete positive/negative/set/window/recursive provenance, atomic reusable public
results and disclosure receipts, revocation/erasure, checkpoint/save/restore,
physical storage and cumulative accounting, and measured useful W1–W7 workloads.
The approved retention/cleanup policies and both EG-0001 conditions/thresholds stay
unchanged. No candidate default/fallback, PR-06 work, model/provider calls, owner
data, release/version/publication, Desktop consumption or owner checkpoint occurs.

## Preserved development failures

The complete Python 3.13 attempt at `9aa2ab2f3fdb0d6eb06683276d397e3d3adf3008`
retains 491 successful executions and one canonical recovery error out of 492;
all 13 restricted-query cases passed. Its unchanged receipt SHA-256 is
`ad0c36290f397afb52e5b5ad13121b4052802efbf9a00cac8a95bf199e97ffd4`.
The separate owner-authorized repair in #57 was accepted as public main
`4edc4eb4f057d2419031cd175409dba1b62d5af7`, containing qualified repair head
`11549c88cacfcd43bf043d68e31ec4babdcc583b`. This branch integrates that actual
merge without modifying its worker, regressions or verification note. No
restricted-query runtime, SQL, policy, deadline or authority boundary changes
as part of the integration. Fresh qualification of the integrated head and its
archives is recorded separately on #56; the failed attempt remains historical.
The completed broad and focused reviews of #56, and broad repair review of #57,
remain the bounded review record rather than restarting review of unchanged code.

The first focused launcher used isolated module invocation without copying a
discovery runner into its guarded test root, so two loader placeholders failed.
After correcting the launch, the first installed run preserved four failures and
one error: existing PUBLIC routine privileges broke manifest qualification, and
the fixture omitted the JSON extraction function. The forward migration now closes
PUBLIC grants while preserving only existing trusted-role access; the fictional
reader manifest explicitly grants extraction/equality functions. A subsequent
12-case run passed. The optimized scope/recovery run also passed all 12 cases;
the additional expiry case passed separately. Its first isolated name-based
launcher lacked the copied-root import path; that loader failure is retained too.
All three expanded restart processes passed. Final-head convergence is recorded
separately on the PR.
Original logs remain under ignored `build/pr05-restricted-evidence`; prior #52,
#54 and #55 failures are preserved.

The first complete installed profiles ran concurrently on one local host (six
shards). Both preserved older short-deadline cancellation/expiry failures and a
migration-negative fixture failure. M0033 introduces a role-defining boundary:
the positive-fixture migration helper commits segments and therefore must not be
used to test one production call's invalid-bound atomicity. That negative case
now calls the production runner directly and keeps its unchanged-history assertion.
Final qualification uses one interpreter profile at a time, retaining all three
shards and all original deadlines. No runtime, policy or historical SQL is changed
to excuse the timing failures; final results and original failed logs remain
separate on the PR.
