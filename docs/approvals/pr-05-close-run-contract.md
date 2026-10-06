# Closing a run: contract v1 (draft for the owner's approval)

A trusted-host operation beside `memoriesql.agent-sql-results.v1`. Draft by
the PR-05 lane, 2026-10-05, describing #76 as it will be once the admission
fix below lands. The owner decided to retain close_run, subject to this exact
contract and proof. This text is for the owner to approve the precise contract
and proof scope. It is not a blanket release authorization.

The proof establishes four things. Close_run:
1. rechecks the caller's authority and the run's ownership;
2. cannot close another principal's run;
3. handles crashes, repeated requests and concurrent work safely;
4. never equates closing a run with stopping provider work, successful
   completion or settled billing.

## Placement

- **Host only.** `PostgresAgentSqlResults.close_run(run_ref)` calls the database
  function `memoriesql.close_query_run_v1(run)`. Only the host's control role
  (`memoriesql_application`) may execute that function.
- **Not part of the agent protocol.** It is not an action kind of the packet. No
  agent request reaches it, and the packet's action kinds, reply envelope and
  catalog are unchanged.
- **Who calls it.** Host operators and host tooling (the trusted query host's
  admin command, and the `core-cli.v1` `broker close-run` command) call it for a
  run they hold.

## Authority, rechecked on every call

- **A fresh check each time.** Every call starts a new authorization context for
  the credential the host presents. That means a current, unexpired credential,
  an active principal and membership, and the current authority fence. Nothing
  is cached between calls.
- **The owner only.** A run may be closed only by its owner: the same tenant,
  workspace, principal and principal kind, user, on-behalf-of user and pairing
  grant that started it (`query_run_owner_v1`).
- **Others learn nothing.** Another principal, another workspace, another tenant,
  an unknown run or a malformed reference all get the same reply:
  `unavailable`. That reply discloses nothing about whether the run exists.

## Effect

Closing records the closure once, durably, with:
- `closed_at`;
- the closing principal, which is always the run's owner;
- the credential that principal presented.

That is attributable authority under the product plan's 01b §11. The record is
kept as long as the run's own records. From then on:
- the run no longer counts toward the workspace's two active runs;
- every new admission on the run is refused as `budget_exhausted` / `time`;
- a run cannot be reopened.

Closing is not any of these:
- **Stopping provider work.** Close is refused as `budget_exhausted` /
  `settlement` while any delivery or invocation of the run is unsettled. It never
  cancels, abandons or settles work. Stranded work is settled only by its owner,
  or by recovery after its owner is gone.
- **Completion.** No step, delivery or result changes state. Results stay
  readable and reusable exactly as before, until their own expiry.
- **Settled billing.** Nothing is refunded. Every charge stays in the
  workspace's rolling window. Budgets, cursors, the 30-day result expiry and the
  purge schedule are unchanged.

## Replies

| Case | Reply |
|---|---|
| Closed now, or already closed | `available`, with `closed.run_ref` and `closed.closed_at`. A repeat returns the same `closed_at`. |
| Unsettled work on the run | `budget_exhausted` / `settlement`. Nothing is written. |
| Not the owner, unknown or malformed run | `unavailable`. Nothing is written. |
| Lock or statement timeout | `budget_exhausted` / `time`. Nothing is written. |
| Expired run not yet purged | `available`. It records the closure, which changes nothing else. |
| Purged run | `unavailable`. |

## Concurrency and crashes

- **Serialized.** Starting a run, admission, close and recovery each take the
  workspace's query-access lock at session level, before their REPEATABLE READ
  snapshot (`acquire_query_access_lock_v1`). Each call's snapshot therefore
  contains everything the previous holder committed. The database function's
  own transaction-level lock on the same key is then re-entrant.
- **Why.** Before this, the snapshot was taken before the lock. A call that
  waited on the lock could not see what the holder had just committed. That
  breaks four guarantees:
  - a third active run against the cap of two;
  - a second admitted operation against the one executing operation;
  - a close beside a just-admitted delivery;
  - a second close that failed instead of returning the first close's reply.

  The first, third and fourth were reproduced on #76's code head `913be9d`. The
  admission case was masked there, because the executor refused the second
  operation later.
- **What callers see.**
  - A run is never closed beside unsettled work.
  - Nothing is admitted on a run after its `closed_at`.
  - The two-active-run cap and the one-executing-operation rule hold under
    concurrent calls.
  - A concurrent close that gets the lock returns the first close's reply.
  - One that cannot get the lock within its 500 ms wait is refused and writes
    nothing.
- **Atomic.** A close is one transaction. A crash before it commits leaves the
  run open. Retrying after a lost reply returns the same `closed_at`.

## Proof: installed tests that must pass at the exact head

0. Attribution. After a close, the closure names the run's owner principal and
   the presenting credential, with `closed_at` equal to the reply's.
1. Ownership is rechecked.
   - Another principal in the same workspace gets `unavailable` and writes
     nothing, and the owner can still use the run.
   - A run of another workspace, and of another tenant, gets a reply
     byte-identical to an unknown run's.
2. Authority is rechecked. After a paired owner's pairing is revoked, close is
   `unavailable` and writes nothing. A revoked or expired credential or
   membership fails the same fresh authorization context.
3. A crash before commit: the host dies after the close statement runs and
   before it commits. The run stays open and usable, and a later close
   succeeds.
4. Repeats. A retry after a lost reply returns the same `closed_at`. When two
   closes race, the one holding the lock closes, and nothing fails after a
   close.
5. Concurrency with admission. A close and an admission run concurrently. No
   run is ever closed beside unsettled work.
6. Concurrent starts and admissions, which the admission fix covers:
   - concurrent starts never make a third active run;
   - concurrent admissions never admit two operations.
7. Not stopping work. Close is refused while a delivery or invocation is
   unsettled, live or crashed. After recovery settles it, close succeeds, and the
   work's charge and receipts are unchanged.
8. Not completion or billing. After close:
   - the run's steps, results, charges and cursors are byte-identical;
   - the run's results are still reusable from a new run;
   - the rolling-window charges count exactly as before.
9. An expired run may be closed, and nothing else changes; this is tested. A
   purged run's rows are gone, so a close reads as an unknown run
   (`unavailable`). That holds by construction and has no separate test.

Tests 0–3, 5, 6, 8 and the expired case of 9 are new.
- Tests 4 and 7 extend existing ones:
  - `test_owner_close_frees_slot_without_refund`;
  - `test_close_refuses_while_run_work_is_unsettled`;
  - `test_host_death_mid_query_keeps_capacity_until_reader_ends`.
- #73's host acceptance already drives close end to end at the host boundary: a
  crashed owner's delivery refuses close; recovery, then close; a query after
  close; an unknown run. Its focused checks rerun on the final #76.

## Custody

A link request for #76's head binds:
- this contract;
- migration 0038, changed in place (it is unreleased) for two things only: the
  closure's attribution columns, and `acquire_query_access_lock_v1`;
- `relation_projection.py` and `agent_sql_results.py` with the admission fix;
- the proof tests.

The qualification is integration candidate 9's, through its head map.

## Related fixes in other PRs

- #73's operator registry may drift from the database when a close reply is
  lost. #73 adds a test that it re-reads the run.
- #77 forgets a closed run, so a stored run that was closed is not reused.
- The `--run-ref` usage in the close-run doc matches the parser.

## Note on this version

- **The admission fix changed mechanism.** An earlier draft, the one the owner
  saw, fixed the admission race with READ COMMITTED. That does not work: every
  call here runs in the relation read frame, which requires REPEATABLE READ
  (`relation_read_frame_authorized_v1`, migration 0030) and refuses anything
  else. Every start was refused in a development run.
- **This version** keeps REPEATABLE READ and takes the workspace's query-access
  lock before the snapshot instead. It mirrors the existing relation read
  fence, which is also a session-level lock taken before the snapshot.
- **Reproduced first.** All four races were reproduced on `913be9d` before the
  fix, and pass after it: run cap, admission, close beside work, double close.
- **The admission race was first masked** in a synchronous reproduction. There,
  the executor refused the second operation later, so the reply was already
  right, though the second delivery had been admitted. Run concurrently on its
  own thread, it reproduces: two operations admitted against a cap of one.
