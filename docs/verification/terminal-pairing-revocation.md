# Terminal pairing revocation (migration 0042)

## The defect

`clients revoke` records a revoked revision of a local client's pairing grant.
Core's contract and the CLI call that revocation terminal. The database did
not enforce it: `revise_pairing_grant` (migration 0006) appends the next
revision with whatever status its caller supplies.

A revocation leaves the paired client's principal, workspace membership,
credential and source grants in place. Those grant nothing on their own:
authentication and every capability check also require the grant's latest
revision to be active. But an owner holding `client.pair` could append an
active revision after the revoked one by hand. The client's old secret then
authenticated again, and read whatever its source grants allowed. No product
command does this; it took hand-written SQL in an owner's authorization context.

## The change

Migration 0042 adds one trigger, `pairing_grant_revisions_terminal`, which runs
before every insert into `pairing_grant_revisions`.
- If the grant already has a revoked revision, the insert is refused with
  SQLSTATE 23514 ("a revoked pairing grant accepts no further revision").
- The status asked for does not matter: a second revocation is refused as
  firmly as a reactivation.
- Both inserters pass through the trigger: `pair_local_client` writes a new
  grant's first revision, and `revise_pairing_grant` writes every later one.
- Authentication already refuses a grant whose latest revision is revoked.
  Combined with the trigger, a revoked client's secret never authenticates
  again.

The trigger function runs with its caller's rights, inside those owner-only
functions. It is granted to nobody and reads no membership, so the reviewed
list of membership readers stays the same.

Before creating the trigger, the migration checks for any grant whose latest
revision is active after an earlier revoked one. Such a grant could only have
been reactivated by hand before 0042. If one exists, installation fails with
SQLSTATE 55000 and changes nothing. Revoke that grant with `clients revoke`, then
install.

What callers see:
- `clients revoke` at a revoked grant's current revision now reports
  `unavailable` / `resource_unavailable` (exit status 2). Before, it appended
  another revoked revision.
- A stale revision still reports `pairing_revision_conflict`, as before,
  because the revision check runs first.
- Nothing else changes: pairing, the first revocation, authentication, every
  refusal and every receipt.

Migrations 0001–0041 keep their bytes.

## Tests

- `test_local_client_pairing.test_a_revoked_pairing_grant_is_never_revived`,
  at the latest schema:
  - an owner's attempt to append an active or a revoked revision after the
    revocation is refused;
  - the CLI's second revocation reports `resource_unavailable`, and a stale
    one still reports a conflict;
  - the grant keeps exactly its two revisions;
  - the client's old secret neither reads the source nor authenticates.
- `test_local_client_pairing.test_terminal_revocation_waits_for_a_grant_reactivated_by_hand`:
  - at schema 41, a grant is revoked and then reactivated by hand, and its old
    secret reads again;
  - installing 0042 fails, and the database stays at 41;
  - once the grant is revoked through the CLI, 0042 installs, and the grant
    accepts no further revision.
- `test_terminal_pairing_revocation_migration` (database-free) checks that 0042:
  - adds exactly one ungranted trigger function and its trigger;
  - restates no older function;
  - does not depend on the requested status;
  - reads no membership;
  - runs its install check first.
