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

## What `waited` covers

`waited` is the time spent inside the bracketed statements below, and nothing
else.
- Every key named here is `hashtextextended(tenant_id || …, 0)`.
- "The fence" is the tenant `:semantic_outcome_authority:` key. Authorization
  takes it shared through `evidence_package_authorize`.
- "Capability rows" are `memoriesql.role_capabilities` rows of the caller's
  role, locked `FOR SHARE OF cap`.
- Only the first authorization in a call can wait for the fence or a capability
  row. Later ones reuse what the transaction already holds, so their brackets
  measure only their lookups.

| Function | Bracketed statements, and what each can wait for |
|---|---|
| `write_evidence_package_v1` | the first authorization, in the create or the append/seal branch (the fence); the `:evidence-operation:` key; the `:evidence-occurrence:` key with the authorization recheck after it; the package row `FOR UPDATE` (append/seal); the final authorization recheck |
| `read_evidence_package_v1` | both authorizations (the fence; the second is a recheck) |
| `recover_transcript_fold_v1` | the first authorization (the fence); the `source.raw.read` capability row; the final authorization recheck |
| `materialize_logical_unit_v2`, `materialize_declared_scope_v1` | the first `source_stable_authorize`: the fence, and through `revisiting_source_authorize` up to three capability rows (`source.raw.read`, `memory.capture`, `memory.maintain`); the `:materialization-source:` key; the `:materialize-operation:`, `:logical-event:` and `:logical-occurrence:` keys with the authorization recheck after them; the final authorization recheck |
| `inspect_stored_bead_v1` | every `revisiting_source_authorize` call (the first can wait for the fence and up to three capability rows; the rest are rechecks) |

**Not covered, so still counted as work:**
- every read, write, hash and validation outside those brackets;
- the replay path's authorization, which returns without a deadline check;
- in the materializations, `enqueue_semantic_task` and the waits inside it
  (its `:semantic_task.enqueue:` key, its shared `:semantic_module:` key and
  its receipt row).

The authorization lookups inside a bracket are excluded with its wait. They
are a few indexed reads per call.

## Adapter bounds: work plus bounded lock waits

The Python adapters' wall-clock statement timeout used to be 2 s (2.5 s for
inspection), and it counted lock waits too, so contention still ended calls the
database would finish. Each adapter now takes its bound from one table,
`infrastructure/postgres/work_deadlines.py`:

> statement timeout = 2 s of work + (lock waits the function can meet × its
> 500 ms lock timeout)

A later authorization in the same call reuses the fence and rows its
transaction already holds, so it adds no wait.

| Function | Fence | Role-capability rows | Advisory locks | Other rows | Statement timeout |
|---|---|---|---|---|---|
| `write_evidence_package_v1` | 1 | 0 | 2 (operation, occurrence) | 1 (package) | 4.0 s |
| `read_evidence_package_v1` | 1 | 0 | 0 | 0 | 2.5 s |
| `recover_transcript_fold_v1` | 1 | 1 | 0 | 0 | 3.0 s |
| `materialize_logical_unit_v2` | 1 | 3 (via source revisiting) | 4 (source, operation, event, occurrence) | 0 | 6.0 s |
| `materialize_declared_scope_v1` | 1 | 3 (via source revisiting) | 4 | 0 | 6.0 s |
| `inspect_stored_bead_v1` | 1 | 3 (via source revisiting) | 0 | 0 | 4.0 s |

**What changes for callers.** The documented "operation 2 s" bound becomes
**2 s of work plus bounded lock waits**, per function, as in the table. The
in-database work deadline stays 2 s. Other queries through the same adapters
keep their earlier bound.

**Role-capability rows.** No product path writes them today. They are counted
anyway, so the bound holds whatever writes them later.

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
  - After it the same append succeeds, as standalone SQL and through the
    evidence adapter, whose 4 s bound leaves room for both waits.
- **`tests/unit/test_work_deadlines.py`** (database-free) pins each adapter
  bound. It re-derives each function's lock waits from its installed text and
  checks the lock timeout and deadline constants against it, so changing a lock,
  a lock timeout or the deadline forces a review there.
  - Each wait stays under the lock timeout and the work under 2 s, so the
    margins do not depend on machine speed.
