# Cancellation and local failure settlement

Contract-conforming development repair on the existing #76 conformance branch.
No migration, policy constant, authority, wire record or contract byte changes.

## Reproduced before repair

CPU-only tests invoke the actual `PostgresRestrictedQuery.execute` control flow
with fictional connection objects and a deliberately delayed supervisor:

- An explicit cancellation event set during the final source revalidation,
  after row fetching ended, returned `complete` when the supervisor had not
  been scheduled yet. The final query-thread check consulted only the deadline
  and the supervisor's cancellation marker.
- A `RuntimeError` from row fetching escaped after connection cleanup, before
  the owned invocation's journal settlement. The public executor's generic
  handler could then settle its delivery as an execution error while native
  invocation ownership was still unresolved.
- A `RuntimeError` while leaving the staging frame also escaped instead of
  returning a pending outcome. Its commit acknowledgment is uncertain.

The valid pre-repair run at `457365a208ab8b00b91db1669b7c74ff011be731`
had one assertion failure, two escaped exceptions and one passing success
control. An earlier fixture setup failure used a nonexistent catalog attribute;
it is not defect evidence.

## Repair

The final check now also reads the explicit cancellation event. Unexpected
ordinary exceptions become the existing safe `execution_error/database` code
and continue through the existing connection cleanup and invocation settlement.
Exception text and partial rows, columns and query metadata are not returned.
The unchanged staging-confirmation rule still forces `settlement_pending`
after any unconfirmed staging exit, even if subsequent journal work returns.
Unknown work remains owned for recovery; no retry or refund is introduced.

Known SQLSTATE and type-error classifications keep their existing handlers.
The success control still completes. KeyboardInterrupt/SystemExit are not
classified as normal execution failures by this ordinary-Exception handler.

## Verification boundary

`tests/unit/test_restricted_query_failures.py` reproduces these cases without a
database. It checks final outcomes, no partial private result payload, and the
owned settlement call for cancelled/local-error invocations, with an uncertain
staging exit as a negative control. It models scheduling delay, not a measured
scheduler guarantee. Mock receipts do not prove remote backend termination,
actual PostgreSQL settlement, recovery, physical accounting or installed
qualification. Those checks require the coordinator-assigned database slot and
final combined stack. No constant-time or resource-isolation claim follows.

Database-free development checks: 119 tests passed on Python 3.14.0; strict
mypy passed for 189 files; Ruff, generated catalogs, public boundary (69 records),
runtime closure (81 files) and migration export (38 SQL resources) passed. The
final test file also fails against the unchanged original runtime: one failure,
two exceptions and one passing control. A development wheel builds locally with
existing pins. PostgreSQL regressions are added but remain unexecuted pending
the assigned slot; complete installed qualification is not claimed.
