"""Wall-clock bounds for the kernel functions that bound their own work.

Each function below limits its own work to WORK_DEADLINE_MS inside the
database and leaves its lock waits out of that budget (migration
`*_work_deadlines_after_lock_waits.sql`). Every lock it can wait on is bounded
by LOCK_TIMEOUT_MS. An adapter's statement timeout is therefore the work
deadline plus one lock timeout per such wait: contention alone never ends a
call the database would finish, and every call is still interrupted within a
fixed bound.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WORK_DEADLINE_MS = 2_000
LOCK_TIMEOUT_MS = 500


@dataclass(frozen=True)
class LockWaits:
    """The lock acquisitions one call can wait on, outside its work budget.

    A later authorization in the same call reuses the fence and rows this
    transaction already holds, and never waits again.
    """

    fence: int
    """The tenant outcome-authority fence, taken shared by the first authorization."""
    role_rows: int
    """Role-capability rows that authorization locks shared, one wait each."""
    advisory: int
    """Keyed advisory locks."""
    rows: int
    """Other row locks, such as an evidence package's."""

    @property
    def total(self) -> int:
        return self.fence + self.role_rows + self.advisory + self.rows


LOCK_WAITS: dict[str, LockWaits] = {
    # Operation and occurrence locks; the package row for append and seal.
    "write_evidence_package_v1": LockWaits(fence=1, role_rows=0, advisory=2, rows=1),
    "read_evidence_package_v1": LockWaits(fence=1, role_rows=0, advisory=0, rows=0),
    # Its own shared lock on the source.raw.read capability row.
    "recover_transcript_fold_v1": LockWaits(fence=1, role_rows=1, advisory=0, rows=0),
    # Source revisiting authorization locks up to three capability rows; then
    # the source, operation, event and occurrence locks.
    "materialize_logical_unit_v2": LockWaits(fence=1, role_rows=3, advisory=4, rows=0),
    "materialize_declared_scope_v1": LockWaits(
        fence=1, role_rows=3, advisory=4, rows=0
    ),
    "inspect_stored_bead_v1": LockWaits(fence=1, role_rows=3, advisory=0, rows=0),
}

_CALL = re.compile(r"\bmemoriesql\.([a-z0-9_]+)\(")


def statement_timeout_ms(function: str) -> int:
    """The wall-clock bound for one call of `function`: work plus lock waits."""
    return WORK_DEADLINE_MS + LOCK_WAITS[function].total * LOCK_TIMEOUT_MS


def statement_timeout_for(query: str) -> int | None:
    """The bound for the one kernel function `query` calls, if it is listed."""
    calls = _CALL.findall(query)
    if len(calls) == 1 and calls[0] in LOCK_WAITS:
        return statement_timeout_ms(calls[0])
    return None
