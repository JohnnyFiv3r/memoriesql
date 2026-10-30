# Pairing revision fence order (migration 0048)

## The defect

A pairing-grant revocation and an outcome settlement for a task that grant
originated could deadlock (SQLSTATE 40P01). PostgreSQL then aborted one of
them.

- `revise_pairing_grant` (migration 0006) locks the grant's `pairing_grants`
  row `FOR UPDATE`. It then inserts the revision, whose
  `pairing_grant_revisions_outcome_fence` trigger (migration 0008) waits for
  the tenant's outcome-authority fence exclusively.
- `record_semantic_task_outcome` (migration 0008) holds that fence shared
  through its final authorization check and every durable write. Its last
  write updates the task row, which `append_semantic_task_event` already
  updated in the same transaction. PostgreSQL therefore rechecks the task's
  foreign keys, including `semantic_tasks_origin_pairing_fk`, and takes
  `FOR KEY SHARE` on the origin grant's row: the row the revocation holds.

Each waited for the other. Expired-task recovery takes the same fence and
updates each task twice in the same way, so by its source it could meet a
revocation the same way. This test does not race that path separately.

The cycle is as old as migration 0008, but a stale context row could hide it.
Before migration 0040, every context start deleted stale context rows and
waited on any row another transaction was deleting. Sometimes a settlement's
context start had already removed such a row, for example one left by a closed
connection. The revocation's context start then waited for the whole
settlement before it locked the grant. Migration 0040 made that cleanup skip
locked rows, so the revocation reached the grant row first. Desktop's
`test_outcome_authority_revocation_serializes_with_settlement` passes against
the 0.0.12 schema and deadlocks against schema 46.

## The change

Migration 0048 restates `revise_pairing_grant` with one added statement. Once
it has read the caller's context, it takes the outcome-authority fence
exclusively, before it locks the grant's row. `revoke_exact_source_v1`
(migration 0036) already takes the fence first.
- The trigger still takes the same fence at the insert, and the second lock in
  the same transaction is a no-op.
- A revocation still has only the fence's two serial positions: before a
  settlement's final authorization check, or after the settlement's queue
  transition commits. A settlement never completes under revoked authority.
- Terminal revocation (migration 0042), the stale-revision conflict (40001),
  every refusal and the returned revision are unchanged.
- The authorization check now runs after the fence wait. So it sees every
  authority change committed before it, where it previously ran before that
  wait. As in 0036, a caller that is then refused has held the tenant fence
  until its transaction ends.

Migrations 0001–0047 keep their bytes.

## Tests

- `test_pairing_revision_fence_order.test_revocation_waits_for_a_settlement_without_a_deadlock`
  races both operations in one fictional database:
  - a test-only trigger holds a settlement inside its writes, after it has
    taken the fence shared;
  - a revocation of the task's origin grant then starts.
  - At schema 47, before the fix:
    - while the revocation waits for the fence, it holds the grant's row (a
      `FOR KEY SHARE NOWAIT` probe fails);
    - once released, the settlement waits for the revocation's transaction,
      and the two block each other (`pg_blocking_pids`, `pg_locks`).
  - At schema 48:
    - the revocation waits for the fence without the row;
    - the settlement succeeds, and the revocation then records revision 2.
- `test_pairing_revision_fence_order.test_a_settlement_after_a_revocation_never_succeeds`:
  a settlement that waits for an uncommitted revocation's fence does not
  succeed once the revocation commits.
- `test_pairing_revision_fence_order_migration` (database-free) checks that
  0048:
  - is 0006's function plus the fence statement before the row lock;
  - takes the trigger's own fence key exclusively;
  - changes nothing else.
