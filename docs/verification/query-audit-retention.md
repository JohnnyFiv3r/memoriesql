# Governed retention of query authorization audit (schema 47)

**Primary claim: authorization audit rows written by query work are charged to
a separate per-workspace audit allowance while retained, and deleted by a
governed expiry 30 days after they were recorded.** This implements the owner
decisions of 2026-10-22: option A with 30 days, and a separate audit allowance
of about 2 GiB that keeps the 512 MiB content allocation unchanged. Every
other audit row, and the content and granularity of every row, is unchanged.
Not chosen: option B (a permanent separate store) and option C2 (one row per
closure authorization).

## Which rows

The query path writes audit through `evidence_package_authorize` (M0016),
which mints a fresh request id per call. A request id therefore cannot tie a
row to a query, so attribution is explicit and recorded at write time:

- **The claim.** The trusted executor names its admitted delivery on every
  connection it opens for that delivery's work (session setting
  `memoriesql.query_audit_delivery`). The restricted query executor opens
  its connections the same way. Any connection still open when the delivery
  ends stops naming it, and one that cannot is closed. A host composition
  must keep this: delivery work runs only on connections opened through the
  executor's factory, never on pooled connections the clearing cannot reach.
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
  resource and statement, is kept. The number of times one statement
  repeated an identical check is not recorded, and neither are the dropped
  rows' unreferenced per-call request ids.
- **Scope.** Only query-attributed rows are deduplicated; every other
  decision is written exactly as before. Widening the key, for example
  across statements, would need a new owner decision.
- **Readers.** The only readers that match rows are row-security policies in
  M0013/M0014. They use `EXISTS` on a request id that their own functions set,
  outside query work. No later migration sets such a request id. A DB-free test
  pins both facts.

## Charge and expiry

- **Charge.** Each retained attributed row is charged 2048 bytes, covering
  heap, indexes and attribution, with margin. A first installed measurement
  found about 1.5 KB per retained row once expired rows' index pages were
  awaiting reuse. That exceeded the first value, 1024 bytes, so the charge
  was raised before release. The installed capacity case measures the whole
  footprint against this charge over repeated expiry and VACUUM cycles.
- **A separate allowance.** The charge draws on each workspace's own query
  audit allowance (`query_audit_allowance_bytes_v1`, 2 GiB), not on the
  512 MiB content allocation, which reservations check as before.
- **When it is spent.** Admission refuses every new query access (query,
  page or reuse, inspection, hydration) with `budget_exhausted` / `storage`
  once the retained charge reaches the allowance. The refusal comes before
  any work, so it writes no audit and discloses nothing. Expiry releases the
  charge. Every attributed row belongs to an admitted delivery, and one
  operation executes per workspace at a time, so the retained charge exceeds
  the allowance by at most the audit of the one delivery admitted just before
  the refusal.
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

- **Overdue rows.** Overdue audit rows are reported in status but do not
  gate admission. Their charge still bounds storage.
- **Vacuum.** The charge tracks physical size only while autovacuum keeps up.
  Expired rows' space, including emptied index pages, is reused only after
  VACUUM, and only when no long-lived snapshot holds back the transaction
  horizon. The expiry pass itself does not vacuum.
- **Measured scale and pattern.** The capacity case measured 256-row waves,
  each expiring everything. Larger volumes, and the rolling 30-day expiry of
  production, were not executed.
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
  restricted-query connections;
- a connection still open at the delivery's end stops naming it, and one
  that cannot is closed.

Installed runtime cases (`tests/runtime/test_query_audit_retention.py`):

- every audit row of a query (population, commit and page), a page re-read
  (reuse), an inspection and a hydration is attributed to its own delivery;
- claims outside a live delivery attribute nothing;
- one row per decision and statement;
- expiry and release;
- every refused update and delete;
- admission refused, content-free and before any work, once the audit
  allowance is spent, and admitted again after expiry;
- the content allocation not counting retained audit;
- a capacity check over five waves of at least 256 rows. Each later wave
  follows the previous wave's expiry and two VACUUMs. At every wave the whole
  footprint of both tables with all indexes stays within what the retained
  rows are charged. Over the last two waves it grows by at most one 8 KiB
  page per relation, so emptied index pages are being reused.
