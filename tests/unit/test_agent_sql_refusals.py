"""Database-free checks of how the executor reports a refusal; no execution claim.

The packet reports a timeout or a storage or work limit as `budget_exhausted`.
A call that wrote nothing and failed for any other reason keeps the one
`unavailable` shape that missing and denied authority share. A statement the
executor refuses from its SQL and bound values alone says what kind of refusal
it is and, for a value expression, where.
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

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.investigation_contracts import (
    QueryRequest,
    ReuseRequest,
    parse_investigation_request,
)
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QueryAuthorityProfile,
)
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
    _admission_error,
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


def query_request(sql: str, parameters: list[dict[str, Any]]) -> QueryRequest:
    parsed = parse_investigation_request(
        json.dumps(
            {
                "contract_version": 1,
                "run_ref": str(uuid4()),
                "step_key": str(uuid4()),
                "kind": "query",
                "catalog_hash": SqlCatalog.installed().hash,
                "sql": sql,
                "parameters": parameters,
                "inputs": [],
                "parents": [],
                "scope": {
                    "source_refs": [],
                    "known_at": "2026-01-01T00:00:00Z",
                    "view": "historical",
                },
                "intent": "enumerate",
                "max_result_bytes": 8192,
                "page_size": 2,
            }
        ).encode()
    )
    assert isinstance(parsed, QueryRequest)
    return parsed


def screen(sql: str, parameters: list[dict[str, Any]]) -> tuple[Any, ...] | None:
    """The executor's refusal of `sql` before any connection or reservation."""
    unused = MagicMock(side_effect=AssertionError("screening opens no connection"))
    try:
        executor(unused)._screen(query_request(sql, parameters))
    except Exception as refused:
        return tuple(
            getattr(refused, name)
            for name in ("outcome", "code", "feature", "position")
        )
    return None


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


class AgentSqlScreenedRefusals(unittest.TestCase):
    def test_safe_codes_never_carry_an_internal_construct(self) -> None:
        # An identifier that is not visible, a lost frame and a misprovisioned
        # reader all share the one unavailable shape.
        for construct in ("parameter_anchor", "source_frame", "procedure_privileges"):
            self.assertEqual(
                _admission_error("unavailable", construct), ("unavailable", None)
            )
        self.assertEqual(
            _admission_error("invalid_request", "reference_type"),
            ("type", "reference_type"),
        )
        self.assertEqual(
            _admission_error("invalid_request", "incompatible_types"), ("type", None)
        )
        # A relation this caller may not name is unavailable, and says no more.
        self.assertEqual(
            screen("SELECT c.candidate_ref FROM evaluation_v1.candidates c", []),
            ("unavailable", "unavailable", None, None),
        )

    def test_an_identifier_parameter_must_carry_its_reference_type(self) -> None:
        identifiers = [str(uuid4()), str(uuid4())]
        sql = (
            "SELECT ss.statement_id FROM memory_v1.statement_sources ss "
            "WHERE ss.statement_id = ANY($1::uuid[])"
        )
        # A plain uuid never names an identifier: a located type error that
        # says which kind of mismatch it is.
        self.assertEqual(
            screen(sql, [{"position": 1, "type": "uuid[]", "value": identifiers}]),
            (
                "invalid_request",
                "type",
                "reference_type",
                sql.index("ss.statement_id = ANY"),
            ),
        )
        # The column's own reference type passes the screen. Whether the
        # identifiers are visible is decided after preparation.
        self.assertIsNone(
            screen(
                sql, [{"position": 1, "type": "statement_ref[]", "value": identifiers}]
            )
        )


if __name__ == "__main__":
    unittest.main()
