"""Database-free checks of how a failed control call is reported; no execution claim.

The packet reports a timeout or a storage or work limit as `budget_exhausted`.
A call that wrote nothing and failed for any other reason keeps the one
`unavailable` shape that missing and denied authority share.
"""

from __future__ import annotations

import json
import time
import unittest
from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import psycopg
from psycopg import errors

from memoriesql.application.investigation_contracts import (
    ReuseRequest,
    parse_investigation_request,
)
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QueryAuthorityProfile,
)
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
)

# A busy database: the caller may simply try again.
BUSY = {
    "57014": "time",
    "55P03": "time",
    "53400": "storage",
    "54000": "storage",
    "40001": "settlement",
    "55000": "settlement",
}
# Denied or lost authority, a duplicate, internal faults and a lost connection.
OTHERS = ("42501", "28000", "P0002", "23505", "XX000", None)
UNAVAILABLE = ("unavailable", {"code": "unavailable"})
FRAME = "memoriesql.infrastructure.postgres.agent_sql_results.relation_projection_frame"


def failure(sqlstate: str | None) -> BaseException:
    if sqlstate is None:
        return psycopg.OperationalError("fictional lost connection")
    return errors.lookup(sqlstate)("fictional refusal")


def executor(control: Any) -> PostgresAgentSqlResults:
    return PostgresAgentSqlResults(
        control_factory=control,
        reader_factory=control,
        authority_profile=cast(QueryAuthorityProfile, None),
        credential_sha256="0" * 64,
        workspace_id=UUID(int=1),
    )


def reuse_request(cursor: str | None = None) -> bytes:
    request: dict[str, Any] = {
        "contract_version": 1,
        "run_ref": str(uuid4()),
        "step_key": str(uuid4()),
        "kind": "reuse_result",
        "result": {"result_id": str(uuid4()), "content_digest": "0" * 64},
        "access": {"kind": "standalone"},
        "page_size": 2,
    }
    if cursor is not None:
        request["cursor"] = cursor
    return json.dumps(request).encode()


class AgentSqlRefusals(unittest.TestCase):
    def replies(self, error: BaseException) -> dict[str, dict[str, Any]]:
        """Every unowned control call's reply when its connection fails."""
        service = executor(MagicMock(side_effect=error))
        admission = json.loads(service.handle(reuse_request()))
        # A refused admission owns nothing: no receipt and no charge.
        for field in ("receipt_ref", "access_receipt_ref", "remaining"):
            self.assertIsNone(admission[field], field)
        return {
            "start_run": json.loads(service.start_run()),
            "close_run": json.loads(service.close_run(str(uuid4()))),
            "cleanup_status": json.loads(service.cleanup_status()),
            "admission": admission,
        }

    def test_a_busy_database_is_a_budget_refusal_at_every_unowned_call(self) -> None:
        for sqlstate, code in BUSY.items():
            for call, reply in self.replies(failure(sqlstate)).items():
                with self.subTest(sqlstate=sqlstate, call=call):
                    self.assertEqual(
                        (reply["outcome"], reply["error"]),
                        ("budget_exhausted", {"code": code}),
                    )

    def test_every_other_failure_keeps_the_shared_unavailable_shape(self) -> None:
        refusals = [failure(sqlstate) for sqlstate in OTHERS]
        refusals.append(PermissionError("fictional missing context"))
        for error in refusals:
            for call, reply in self.replies(error).items():
                with self.subTest(error=type(error).__name__, call=call):
                    self.assertEqual((reply["outcome"], reply["error"]), UNAVAILABLE)

    def test_operator_cleanup_reports_a_busy_database_with_its_progress(self) -> None:
        for sqlstate, expected in (
            *[
                (state, ("budget_exhausted", {"code": code}))
                for state, code in BUSY.items()
            ],
            *[(state, ("execution_error", {"code": "database"})) for state in OTHERS],
        ):
            with self.subTest(sqlstate=sqlstate):
                service = executor(MagicMock(side_effect=failure(sqlstate)))
                reply = json.loads(service.cleanup_expired())
                self.assertEqual((reply["outcome"], reply["error"]), expected)
                self.assertEqual(
                    reply["cleanup"],
                    {
                        "purged": {"runs": 0, "results": 0, "tombstones": 0},
                        "pending": 0,
                        "failed": 0,
                        "batches": 0,
                    },
                )

    def cursor_failure(self, error: BaseException) -> tuple[str, str | None]:
        """What resolving a reuse cursor fails with when its frame raises."""
        service = executor(MagicMock())
        request = parse_investigation_request(reuse_request(cursor=str(uuid4())))
        assert isinstance(request, ReuseRequest)
        start = time.monotonic()
        with patch(FRAME, side_effect=error):
            try:
                service._reuse(request, {}, start, start + 30)
            except Exception as refused:
                return getattr(refused, "outcome", ""), getattr(refused, "code", None)
        self.fail("cursor resolution did not refuse")

    def test_cursor_resolution_classifies_failures_as_disclosure_does(self) -> None:
        for sqlstate, expected in (
            ("55P03", ("budget_exhausted", "time")),
            ("57014", ("budget_exhausted", "time")),
            ("54000", ("budget_exhausted", "storage")),
            ("42501", ("unavailable", "unavailable")),
            ("28000", ("unavailable", "unavailable")),
            ("XX000", ("execution_error", "database")),
            (None, ("execution_error", "database")),
        ):
            with self.subTest(sqlstate=sqlstate):
                self.assertEqual(self.cursor_failure(failure(sqlstate)), expected)
        self.assertEqual(
            self.cursor_failure(PermissionError("fictional missing context")),
            ("unavailable", "unavailable"),
        )


if __name__ == "__main__":
    unittest.main()
