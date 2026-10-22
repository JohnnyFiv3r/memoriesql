# Governed retention of query authorization audit (schema 47)

**Primary claim: authorization audit rows written by query work are charged to
the workspace allocation while retained and deleted by a governed expiry 30
days after they were recorded.** This implements owner decision 2026-10-22
(option A, 30 days). Every other audit row, and the content and granularity of
every row, is unchanged. Not chosen: option B (a permanent separate store) and
option C2 (one row per closure authorization).

## Which rows

The query path writes audit through `evidence_package_authorize` (M0016),
which mints a fresh request id per call. A request id therefore cannot tie a
row to a query, so attribution is explicit and recorded at write time:

- **The claim.** The trusted executor names its admitted delivery on every
  connection it opens for that delivery's work (session setting
  `memoriesql.query_audit_delivery`).
- **The verification.** The kernel writer `application_authorize_resource`
  attributes a row only while that delivery is reserved and unexpired, and its
  run is owned by the exact current principal tuple. These are the checks that
  helper work already uses (M0046). A missing, malformed, settled, expired or
  foreign claim attributes nothing, and the row is kept exactly as before.
- **The attribution.** Each attributed row has one private, insert-only row in
  `query_audit_events`, inserted in the same call. A delivery's concurrent
  transactions, such as the open population frame and the commit, never wait
  on a shared row.

Checkpoint access goes through the same delivery path and is attributed the
same way once it lands.

## Duplicates within one request (C1)

- **The duplicate key.** Inside one delivery and one statement, a second
  identical decision is not written. The key digests the delivery, the
  statement time and every audit column except the row's own event id and the
  caller's per-call request id.
- **What is preserved.** Every distinct decision, and at least one row per
  resource and statement, is kept.
- **Readers.** The only readers that match rows are row-security policies in
  M0013/M0014. They use `EXISTS` on a request id that their own functions set,
  outside query work. No later migration sets such a request id. A DB-free test
  pins both facts.

## Charge and expiry

- **Charge.** Each retained attributed row is charged 1024 bytes, covering
  heap, indexes and attribution, with margin. Reservations add the workspace's
  retained audit charge to the 512 MiB workspace allocation
  (`reserve_result_preparation_v1`, otherwise unchanged).
- **Expiry.** `expire_query_audits_v1` deletes due rows oldest first, bounded
  per call. Their attribution goes with them, and so does their charge. The
  host's `cleanup_expired` runs it after the query-state pass, and run start
  runs it for the caller's workspace. Results are reported as
  `cleanup.audits`, and `cleanup_status` reports `audit_retention`.
- **The trigger exception.** The append-only trigger keeps one exception: a
  delete while the expiry marker is set, of a row whose attribution is past
  its window. Only the expiry sets the marker, around its one delete. Every
  update, and every other delete, is refused as before. The application role
  holds no delete privilege on the table.

## Limits

- **Post-hoc charges.** Charges are accounted after the fact, like transport.
  Page reads and reuse reserve no allocation, so their audit is charged but
  never refused. It is bounded by run and workspace access, time and transport
  budgets, and by expiry.
- **Erasure.** Governed erasure does not yet remove attributed rows before
  their window ends.
- **Trust in the host.** The claim is a trusted-host statement checked against
  a live delivery of the same principal. A misbehaving host could attribute
  its own principal's other decisions written during its own live delivery.
  Those rows would be retained and charged for 30 days instead of
  indefinitely.

## Evidence

DB-free (`tests/unit/test_query_audit_retention_migration.py`):

- each kernel redefinition equals its original apart from the stated change;
- the one trigger exception and its marker;
- the duplicate key's columns;
- the reader check;
- default-deny privileges and the inventory.

Executor (`tests/unit/test_agent_sql_results.py`):

- only connections opened for a delivery carry its claim, including
  restricted-query connections.

Installed runtime cases (`tests/runtime/test_query_audit_retention.py`):

- attribution and non-attribution;
- one row per decision and statement;
- expiry and release;
- every refused update and delete;
- the reservation decision flipping on the retained charge;
- a small capacity check that audit growth stays at or below its charge and is
  released.
