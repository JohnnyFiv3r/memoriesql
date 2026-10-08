# Definite staging rollback versus uncertain completion

This is a contract-conforming executor repair, not a new query capability,
authorization path, policy number or release claim.

## Defect and boundary

The restricted executor previously changed every failure between entering the
staging frame and confirming its exit into `settlement_pending`. That includes
server refusals which abort the staging transaction before it can commit an
invocation. The outer result executor consequently retained its delivery and
preparation reservation until recovery, blocking subsequent workspace queries.

A definite pre-commit refusal is distinguishable from an uncertain commit:

- The staging body has not completed.
- PostgreSQL supplied a non-connection-class SQLSTATE.
- The owned control connection is still open and returned to `IDLE` after the
  frame exited, confirming transaction cleanup.

Only this case preserves the existing typed error instead of forcing a pending
outcome. The result executor then uses its existing owned discard and settlement
path. Reader-cleanup failures and unsettled cancellation still retain recovery
ownership. A missing SQLSTATE, connection failure, unconfirmed rollback, or an
exception during/after the staging commit is not proof of safe cleanup.

There is no automatic query retry, new authority, response field, migration,
refund for unknown work or weakened result-disclosure check.

## Regression evidence

`tests/runtime/test_agent_sql_staging.py` injects real PostgreSQL ErrorResponses
inside the actual staging transaction on fictional fixtures. It covers:

- Lock and statement timeouts: `55P03`, `57014`.
- Configuration and program limits: `53400`, `54000`.
- Permission and authorization refusals: `42501`, `28000`.
- Internal error and serialization refusal: `XX000`, `40001`.

For each definite refusal it checks the typed outcome, no disclosed result or
page, no unsettled delivery, no invocation, discarded preparation, and a new
successful query in the same workspace without host crash recovery.

Two negative controls preserve `settlement_pending` and the owned reservation:
a simulated lost acknowledgment after an actual staging commit, and a control
connection closed before rollback can be confirmed.

The lock-timeout case failed before the repair with `settlement_pending` instead
of `budget_exhausted`. SQLSTATE injection does not claim that real storage or
memory exhaustion has been measured. These are focused development regressions,
not complete installed qualification, privacy timing certification, useful
workload fit or semantic-quality evidence. Final stack integration and the
applicable independent review remain required.
