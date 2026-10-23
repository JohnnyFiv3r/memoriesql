# Design note: narrowing the tenant outcome-authority fence

Status: **proposal, not implemented.** The bounded retry
(`docs/verification/owned-transaction-retry.md`) is the only code change that
comes with this note. Every option below that changes when a revocation takes
effect is marked as an owner decision.

## What the fence is

There is one advisory key per tenant: `hashtextextended(tenant_id || ':semantic_outcome_authority:', 0)`.

- **Exclusive.** Every insert, update or delete of an authority row takes the
  key exclusively, through `fence_semantic_outcome_authority_mutation`
  (migration 0008, plus 0017, 0018, 0026 and 0029). The authority tables are:
  - credentials, users, principals;
  - workspaces, memberships, access scopes;
  - access grants and pairing grants (each with revisions), protected
    resources;
  - producer, dispatch, qualification and relation-assessment policies.

  `enroll_exact_source_v1`, `grant_exact_source_v1`, `revoke_exact_source_v1`
  (0036) and `revise_pairing_grant` (0048) also take it exclusively before any
  row.
- **Shared, for one transaction.**
  - Outcome settlement, recovery and failure recording, through
    `lock_semantic_task_outcome_authority`.
  - `evidence_package_authorize`, behind every evidence, inspection, fold and
    materialization read.
  - The relation lifecycle and assessment commands.
  - Semantic apply.
- **Shared, for a whole session.** `acquire_relation_read_fence_v1` (0030) holds
  it at session level for an entire read or query frame, up to the operation's
  30 s.

**What it guarantees.** Every authority change has only two serial positions
relative to each shared holder: before that holder's final authorization
check, or after the holder commits. Three consequences:
- no settlement completes under authority that a revocation has removed;
- no read is answered from a half-applied change;
- a revocation that has returned has no read left in flight under the old
  authority.

## Why it stalls

PostgreSQL grants a lock in queue order. So the stall has three steps:
1. An owner write requests the key exclusively and waits for every shared
   holder in the tenant, including long query frames.
2. While it waits, every new shared request queues behind it. Settlements,
   inspections and new query frames all stop.
3. Each waiter gives up at its 500 ms lock timeout.

One grant change therefore stalls every query in the tenant. A personal-local
installation has one tenant with one workspace, so scoping by workspace alone
would not help it.

## Lock-order rules any change must keep

These come from the revocation/settlement deadlock (0048) and its follow-up
audit (2026-10-23).

1. **Fence before authority rows.** A function that locks an authority row and
   takes the fence takes the fence first. That is 0036 and 0048's order.
   - Two places still lock a row first: `revise_access_grant`, and an UPDATE
     through a fenced trigger.
   - Today these are safe only because nothing that holds the fence then
     requests those rows.
2. **No upgrade.** A holder of the shared key never later requests it
   exclusively.
3. **Keys in a fixed order.** If the fence is split into several keys, every
   transaction takes them in one global order. Each key is taken before any
   authority row.

## Options

**A. Writers poll instead of queueing** (no semantic change).
- **How.** Authority writers take the key with `pg_try_advisory_xact_lock` in a
  short bounded loop before their first row, instead of blocking in the queue.
  Readers then never queue behind a waiting writer. A writer still commits
  only after in-flight shared holders finish, so the guarantee holds.
- **Cost.**
  - Under constant read load a writer can fail after its bound (`busy`) rather
    than eventually win. That is visible to the owner and retryable.
  - Every writer needs an explicit pre-fence call, because the trigger cannot
    poll before the row is locked. That also fixes rule 1 for the two
    remaining row-first writers.

**B. Two levels: tenant key, then a principal key** (keeps the guarantee per
principal).
- **How.**
  - Every shared holder takes the tenant key shared, then the key of each
    principal whose authority it uses, in ascending order. For example, a read
    takes its own principal and its on-behalf user's principal; a settlement
    takes the worker and the task's origin.
  - A change that affects one principal takes the tenant key shared and that
    principal's key exclusively. That covers its pairing grant, credential,
    access grants targeting it, and its membership.
  - A change that affects many takes the tenant key exclusively. That covers
    access scopes and policies, protected resources (source revocation),
    users, workspaces, and the dispatch and qualification policies.
- **Effect.** Revoking one agent then waits only for that agent's (and its
  owner's) in-flight work, and stalls nothing else.
- **Cost.**
  - Every shared caller must name the principals it depends on.
  - Missing one silently weakens the guarantee for that path, so the change
    needs a source audit like 0048's and a database test per caller family.
  - A migration must redefine the trigger and each shared acquisition.

**C. Release the read fence once the snapshot exists** — **owner decision.**
- **How.** A frame would take the key shared only to establish its REPEATABLE
  READ snapshot, then release it before the query runs.
- **Effect.** Snapshot isolation alone keeps any single read consistent.
- **What changes.** A revocation could return while a query that began under
  the old authority is still running and delivering. Today revocation waits for
  it. That is a trust and semantics change, not engineering.

**D. Per-deployment lock timeout.** Make the 500 ms configurable. That moves
the trip point; it does not remove the convoy.

## Recommendation

1. **Now:** the bounded retry (done in this change). It turns most brief
   overlaps into short waits instead of failures.
2. **Next, as engineering:** A, writers poll. It removes the convoy without
   changing what a revocation guarantees, and closes rule 1 for
   `revise_access_grant` and the trigger-only writers.
3. **Then, if one tenant's queries must survive another principal's changes:**
   B, principal-scoped keys. It needs its own source audit and database proofs.
4. **Not without the owner:** C.

## What a database proof would need

- **For A:**
  - a writer that polls while a long shared holder runs;
  - a new reader that is admitted during the poll;
  - a writer that still commits after the holder.
- **For B, per caller family:**
  - a change to principal P waits for P's frame and not for Q's;
  - a tenant-wide change still waits for every frame;
  - a lock-order scan, like the 0048 audit, finding no cycle.
