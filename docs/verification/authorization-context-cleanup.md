# Authorization context cleanup that never waits (schema 40)

**Primary claim: starting an authorization context no longer waits on, or fails
because of, another session's cleanup of stale context rows.** Migration 0040
replaces one statement of `begin_authorization_context`. Every credential,
membership and pairing check, every refusal and the returned context are
unchanged.

## The defect

`begin_authorization_context` (migration 0006) removed every stale context row
with one DELETE: the caller's own backend's rows, expired rows and rows of
ended backends. The row locks of that DELETE last until the caller's transaction
ends. Long holders include a relation read frame and a query's source frame,
which stays open for the whole operation. The stale row only has to appear once:
every closed connection leaves one.

- **A wait, or a lock timeout (SQLSTATE 55P03).** While a long transaction is
  removing a stale row, every other session's context start waits on that row.
  A caller that set the adapters' 500 ms lock timeout fails with `canceling
  statement due to lock timeout`, in context `while deleting tuple … in relation
  "authorization_contexts"`. A caller that set none (the worker, model
  accounting, source ranges, transcript folds) waits as long as the holder's
  transaction stays open.
- **A backend waiting on its own row.** The same happens to a backend whose own
  earlier row has expired while another session is removing it.
- **A serialization failure (SQLSTATE 40001).** Under REPEATABLE READ, the DELETE
  fails with `could not serialize access due to concurrent delete` when another
  session removed a stale row after the transaction's snapshot. The relation
  read frame starts its context in such a transaction.

None of these says anything about the caller's authority.

## The change

A context row is usable only by the backend and transaction that began it:
`current_authorization_context` and `current_context_scope_time_authorized`
both require the caller's backend and transaction identifiers. So:

- **This transaction's earlier context is still removed at once.** A second
  context start in one transaction revokes the first, and naming the first
  context again resumes nothing. Those rows were written by the caller's own
  transaction, so no other session can see or lock them.
- **Every other removable row is garbage** and is collected without waiting. A
  private helper, `collect_authorization_contexts_v1`, locks the rows it can with
  `FOR UPDATE SKIP LOCKED` and deletes exactly those, so a row another session
  is already removing is skipped. It keeps migration 0006's three reasons a row
  is removable. It is granted to nobody and runs only inside the kernel function.
- **A backend's rows from its earlier transactions are collected, not deleted
  inline.** Another session may already be removing one that has expired.
- **Under snapshot isolation the collection is an attempt.** There, locking a
  row that another session removed after the snapshot fails even with SKIP
  LOCKED. The kernel function undoes that attempt in a subtransaction and starts
  the context anyway; a later start collects what is left. Read-committed
  callers need no subtransaction, because they simply skip such a row.

So every caller attempts collection, whatever isolation level its session uses,
and no caller waits or fails because of it. Under snapshot isolation an attempt
that meets a row removed after its snapshot is undone and collects nothing; a
later start collects what is left.

The kernel function is migration 0006's text with that one statement replaced,
generated and diffed. `CREATE OR REPLACE` keeps its owner, grants and comment,
and the statement restates the same signature, SECURITY DEFINER setting and
search path. The refusals stay in this one function, where the worker
recognizes a refused credential by its origin.

## Source authority commands report a busy database distinctly

`PostgresSourceEnrollment` raises `SourceAuthorityBusy` when a lock timeout
(55P03) or a cancelled statement (57014: a statement timeout or a cancel
request) stops `enroll`, `grant` or `revoke`. Nothing was written, and the
request UUID makes the identical retry idempotent. The psycopg error is always
its cause, so a caller can still read the SQLSTATE. The CLI reports `failed`
with reason `source_authority_busy` (exit status 3), where it used to report the
general `source_authority_failed`.

## Not changed here

**Authority commands still wait for running reads.** Enrollment, grants and
revocations take the tenant's authority fence exclusively (migration 0036, and
the triggers migration 0008 puts on the authority tables). A relation read holds
the same fence shared for its whole frame (migration 0030), and so does a query
for up to its 30 s operation bound. An authority command issued during such a
read still waits 500 ms and is then refused, now as `source_authority_busy`.
This is the fence working as designed: a read and an authority change never
overlap. Whether authority commands should wait longer for reads to finish is a
separate decision.

A replay of an already recorded enrollment writes nothing. It could take the
fence shared, as reads do, leaving only a first enrollment to take it
exclusively; a host could then confirm its enrollment while reads run. That is
a change to migration 0036's function and is not made here.

Other adapters keep their existing handling of a lock timeout; only the source
authority commands are changed. With the cleanup fixed, none of them meets this
cause of one.

## Acceptance

Installed tests (`test_authorization_context_cleanup`) run each race on schema
39 first and then on schema 40, in one fictional database:

| Sequence | Schema 39 | Schema 40 |
| --- | --- | --- |
| An open transaction is removing an ended backend's row; another session starts a context with a 500 ms lock timeout | `LockNotAvailable`, in context `while deleting tuple … "authorization_contexts"` | Starts at once; the skipped row is removed when the holder commits |
| An open transaction is removing a backend's own expired row; that backend starts a context | The same `LockNotAvailable` | Starts at once |
| A REPEATABLE READ transaction takes its snapshot; another session collects a stale row and commits; the first starts a context | `SerializationFailure` | Starts; the attempt is undone and collects nothing |

On schema 40 they also show:

- a snapshot-isolation start with nothing removed behind its snapshot collects
  like any other;
- a SERIALIZABLE start takes the same branch as REPEATABLE READ: its attempt is
  undone, and the context starts and commits;
- a second context in one transaction revokes the first, under READ COMMITTED
  and REPEATABLE READ, and naming the first again resumes nothing;
- a start collects an ended backend's row, its own backend's earlier rows and an
  expired row of a live backend, and keeps a live backend's unexpired row;
- the kernel function's owner, grants, security setting, search path, signature,
  volatility, strictness, cost and comment are identical across the upgrade; the
  collector runs with its caller's rights and neither product role nor PUBLIC can
  call it; the worker still recognizes a refused credential as the kernel's own
  refusal;
- enrollment, grant and revocation under a held read fence each raise
  `SourceAuthorityBusy` with the psycopg error as its cause, and write nothing;
  the identical enrollment and revocation then succeed, and replay.

A database-free test holds migration 0040 to migration 0006's kernel text with
only the stale-row DELETE replaced, and to adding one ungranted helper and
nothing else: no grant, alter, comment or drop. CLI tests cover the
`source_authority_busy` reason for all three commands. A held read fence trips
the lock timeout long before the statement timeout, so the installed test
produces only 55P03; a database-free test (`test_source_enrollment_busy`) raises
both 55P03 and 57014 in each of the three commands and checks that each becomes
`SourceAuthorityBusy` with that error as its cause.

Migrations 0001–0039 keep their bytes. The executor's installed tests now run on
schema 40.

## Custody

**Implementation linkage: ea-6-link-1 → `104d83f`.** The independent custodian
recorded it at 2026-10-06T01:43:07Z. It is stored byte-identical in
`../approvals/pr-05-custody-ea-6-link-1.json` (16,704 bytes, SHA-256
`1277e530…`).

- **Its determination.** No authority or evidence-policy amendment is
  identified. The custody-bound implementation still changes: migration 0040
  replaces the context cleanup mechanics, and source-authority commands gain a
  distinct busy outcome. So the record supplies a narrow implementation link
  under ea-6, keeping ea-5 and ea-4 as ancestry. No new semantic reconciliation
  or replacement freeze is required.
- **What it binds.** It binds this head `104d83f`, carrying #78 at `9f3aa77`.
  It binds migration 0040 `09ce9ead…` and migration 0039 `ee846ac7…`
  (unchanged), the migration inventory, the CLI, the source-authority adapter,
  this document and the retained public-results document, all at `104d83f`.
  Its previous linkage is ea-5-link-2 → `9f3aa77`.
- **Its boundaries.** Authority commands keep the exclusive tenant fence while
  reads hold it shared, and the 500 ms source-authority wait is not increased.
  `source_authority_busy` is a failure reason, never a successful enrollment,
  grant or revocation. An identical retry is permitted only once the earlier
  transaction is conclusively settled; it adds no evaluation attempt and resets
  no budget.
- **Semantics only.** It is not runtime, security, workload or semantic-quality
  certification, and not release readiness. It claims no combined head with #79.
