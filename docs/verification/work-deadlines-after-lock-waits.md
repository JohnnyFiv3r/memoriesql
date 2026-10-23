# Work deadlines exclude lock waits (provisional migration 0051)

## The defect

Six kernel functions bound their own work with a 2 s deadline measured from
the moment they start:
- `write_evidence_package_v1` and `read_evidence_package_v1` (57014
  `evidence_work_timeout`);
- `recover_transcript_fold_v1` (57014 `fold_recovery_work_timeout`);
- `materialize_logical_unit_v2` and `materialize_declared_scope_v1` (57014
  `materialization_operation_expired`);
- `inspect_stored_bead_v1` (54000 `stored_bead_budget`).

Inside that window each also waits for locks:
- the tenant outcome-authority fence that its authorization takes shared;
- keyed advisory locks;
- a package row or role-capability row lock.

Any wait may last up to the 500 ms lock timeout, so contention alone could
spend the budget before any work began. For materialization this is five
waits before its first check.

## The change

The migration restates each function as its latest definition with timing
statements only:
- every wait point is bracketed by `lock_at:=clock_timestamp()` and
  `waited:=waited+(clock_timestamp()-lock_at)`;
- every 2 s check subtracts `waited`.

**Unchanged:** the 2 s deadline itself, every lock, authorization check,
refusal and response. Work before and between the locks, such as hashing a
package's content, still counts.

**Waits that still count:**
- waits inside other callees, such as `enqueue_semantic_task`'s module lock;
- time spent inside authorization calls, which is bracketed as a wait and so
  excluded. Authorization is bounded lookup work.

Relation inspection (`relation_inspection_frame_v1`) needs no change. Its read
frame takes the fence and query-access locks in an earlier transaction, so its
statement starts after them.

The number is provisional; the coordinator assigns the final one (0053 or
later). Forward-only; earlier migrations keep their bytes.

## Limit: adapter statement timeouts

The Python adapters set their own wall-clock `statement_timeout`:
- 2 s for evidence, fold recovery and materialization (`OPERATION_TIMEOUT_MS`);
- 2.5 s for stored-bead inspection.

That timeout still counts lock waits. So this migration removes the false
trip for standalone SQL callers and lets inspection absorb up to 0.5 s of
waits. Through the 2 s adapters, waits plus work above 2 s still end in a
statement timeout (57014). Giving those adapters headroom for waits raises
their documented wall-clock bound; that is pending a decision and is not in
this change.

## Tests

- **`tests/unit/test_work_deadline_migration.py`** (database-free) checks that:
  - each function, with the timing statements removed, is byte for byte its
    previous definition;
  - every bracket encloses a lock or authorization call;
  - every 2 s check subtracts `waited`;
  - the number of checks is unchanged;
  - the migration replaces these six functions and nothing else.
- **`tests/runtime/test_work_deadlines.py`** sets up an evidence append whose
  authorization and operation lock each wait 0.3 s, with a test-only trigger
  that sleeps 1.6 s while it works.
  - Before the fix the call ends with `evidence_work_timeout`.
  - After it the same append succeeds.
  - Each wait stays under the lock timeout and the work under 2 s, so the
    margins do not depend on machine speed.
