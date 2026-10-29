# Owned-transaction retry after lock contention

## The defect

Kernel functions and their Python adapters wait at most 500 ms for a lock.
Authority changes take the tenant's outcome-authority fence exclusively, and
query read frames and settlements hold it shared. So an owner command waits
behind every in-flight frame, and reads queued behind that waiting writer time
out too. Each such timeout (SQLSTATE 55P03) failed the whole operation, and
nothing retried it, although the transaction had rolled back and written
nothing.

## The change

`memoriesql.infrastructure.postgres.transient_retry.run_owned` re-runs a short
transaction that its caller owns, when lock contention alone stopped it:

| Error | Re-run? |
|---|---|
| 55P03 lock timeout | yes |
| 40P01 deadlock | yes |
| 40001 raised by PostgreSQL ("could not serialize access …") | yes |
| 40001 raised by a kernel function (a stale revision, for example) | no: it is a real conflict |
| 57014 statement timeout, and every other error | no |

- **Bounds.** At most four attempts. The backoff doubles from 50 ms to 200 ms,
  keeping at least half of each wait and filling the rest with jitter.
- **Total wait.** Lock waiting stays within about 2.4 s.
- **Mapping.** After the last attempt the original error is raised unchanged,
  so every caller maps it exactly as before: `source_authority_busy`,
  `budget_exhausted` and so on.

**Re-run** (each is one `with transaction()` block turned into a closure):
- source enrollment, grant and revocation;
- local client pairing and revocation;
- personal-local initialization;
- the assessed-relation lifecycle command;
- relation lifecycle and relation-assessment commands, and the relation
  delivery attestation;
- stored-bead inspection;
- fold recovery;
- complete-input calls;
- evidence-package and materialization calls;
- engine record intake, reads and vocabulary;
- the lock-taking transaction of the relation read frame, for inspection reads
  only (`read_relation_projection`).

**Not re-run** (each is listed, with its reason, in
`tests/unit/test_transient_retry.py`):
- **Agent-SQL operations** (`agent_sql_results`, `restricted_query`, the query
  host's `paired_agent`). They carry an admitted deadline and their own outcome
  mapping, so their retry belongs to the agent-SQL admission change. The read
  frame keeps `retry` off for them.
- **The operator's migration runner** (10 s lock timeout).
- **The read frame's snapshot transaction**, which runs the caller's body.

**Session locks.** The read frame takes its fence and query-access lock at
session level, and a session lock outlives the rolled-back transaction that took
it. So a failed attempt releases whatever it had taken before it runs again or
raises. Each release switches role before its unlock, as every transaction does.

**Role switch.** Each re-run closure keeps its adapter's static `SET LOCAL`
prologue, so the role switch still comes before any function on every attempt.
`tests/unit/test_role_switch_prologues.py` covers every closure.

A re-run repeats only database statements, from a rolled-back transaction. No
request, receipt, refusal shape or authority check changes.

## Tests

- **`tests/unit/test_transient_retry.py`** (database-free) covers:
  - the classification;
  - the attempt bound;
  - backoff and jitter limits;
  - that a final error is the same object;
  - that a non-transient error is raised at once;
  - a source scan that fails if any owned transaction setting a lock timeout is
    neither re-run nor listed as exempt.
- **`tests/runtime/test_transient_retry.py`** uses a lock holder in another
  session, released from the backoff pause. It shows:
  - an enrollment that outlasts a brief shared fence holder;
  - four bounded attempts, then `source_authority_busy` with nothing written;
  - a stale pairing revision that fails at once;
  - a read frame that releases its session fence between attempts, holds
    exactly its two locks once open, and none after;
  - a frame without `retry` that still fails at once.
