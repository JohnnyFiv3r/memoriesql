"""Fault-injected arithmetic boundaries; not natural SQL reachability evidence."""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import psycopg

from memoriesql.application.investigation_contracts import QueryRequest
from memoriesql.infrastructure.postgres import agent_sql_results as module
from memoriesql.infrastructure.postgres.query_result_commit import (
    InternalResultCandidate,
)
from memoriesql.infrastructure.postgres.restricted_query import NativeQueryExecution


class AgentSqlArithmeticBoundaries(unittest.TestCase):
    def request(self) -> QueryRequest:
        return QueryRequest.model_validate(
            {
                "contract_version": 1,
                "kind": "query",
                "run_ref": "60000000-0000-4000-8000-000000000001",
                "step_key": "60000000-0000-4000-8000-000000000002",
                "catalog_hash": "0" * 64,
                "sql": "SELECT bead_id FROM memory_v1.observations",
                "parameters": [],
                "inputs": [],
                "parents": [],
                "scope": {
                    "source_refs": [],
                    "known_at": "2026-10-08T00:00:00Z",
                    "view": "historical",
                },
                "intent": "discover",
                "max_result_bytes": 8192,
                "page_size": 1,
            }
        )

    def service(self) -> Any:
        service: Any = object.__new__(module.PostgresAgentSqlResults)
        service._control = lambda: nullcontext(MagicMock())
        service._credential = "0" * 64
        service._workspace = UUID("60000000-0000-4000-8000-000000000003")
        service._screen = MagicMock()
        service._discard = MagicMock()
        service._disclose = MagicMock()
        service._executor = MagicMock()
        service._executor.execute.return_value = NativeQueryExecution("complete")
        return service

    def boundary_failure(self, boundary: str, error: psycopg.Error) -> module._Failure:
        service = self.service()
        store = MagicMock()
        store.reserve.return_value.replayed = False
        commit = MagicMock()
        if boundary == "reserve":
            store.reserve.side_effect = error
        else:
            commit.commit.side_effect = error
        with (
            patch.object(module, "PostgresResultPreparation", return_value=store),
            patch.object(module, "PostgresQueryResultCommit", return_value=commit),
            patch.object(
                module,
                "prepare_query_population",
                return_value=nullcontext(MagicMock()),
            ),
            patch.object(
                InternalResultCandidate, "construct", return_value=MagicMock()
            ),
        ):
            with self.assertRaises(module._Failure) as captured:
                service._query(self.request(), "0" * 64, {"replay": False}, 0, 10**12)
        failure = captured.exception
        service._disclose.assert_not_called()
        self.assertNotIn("private-server-detail", str(failure.error()))
        if boundary == "reserve":
            service._executor.execute.assert_not_called()
            service._discard.assert_not_called()
        elif failure.pending:
            service._discard.assert_not_called()
        else:
            service._discard.assert_called_once()
        return failure

    def test_reservation_arithmetic_is_execution_error(self) -> None:
        for cls in (
            psycopg.errors.NumericValueOutOfRange,
            psycopg.errors.DivisionByZero,
        ):
            with self.subTest(sqlstate=cls.sqlstate):
                failure = self.boundary_failure("reserve", cls("private-server-detail"))
                self.assertEqual(
                    (failure.outcome, failure.code, failure.pending),
                    ("execution_error", "arithmetic", False),
                )

    def test_commit_arithmetic_is_execution_error(self) -> None:
        for cls in (
            psycopg.errors.NumericValueOutOfRange,
            psycopg.errors.DivisionByZero,
        ):
            with self.subTest(sqlstate=cls.sqlstate):
                failure = self.boundary_failure("commit", cls("private-server-detail"))
                self.assertEqual(
                    (failure.outcome, failure.code, failure.pending),
                    ("execution_error", "arithmetic", False),
                )

    def test_commit_without_sqlstate_stays_recovery_owned(self) -> None:
        failure = self.boundary_failure(
            "commit", psycopg.OperationalError("private-server-detail")
        )
        self.assertEqual(
            (failure.outcome, failure.code, failure.pending),
            ("settlement_pending", "settlement", True),
        )

    def test_commit_connection_loss_stays_recovery_owned(self) -> None:
        failure = self.boundary_failure(
            "commit", psycopg.errors.ConnectionFailure("private-server-detail")
        )
        self.assertEqual(
            (failure.outcome, failure.code, failure.pending),
            ("settlement_pending", "settlement", True),
        )

    def test_storage_refusal_keeps_its_existing_budget_outcome(self) -> None:
        for boundary in ("reserve", "commit"):
            with self.subTest(boundary=boundary):
                failure = self.boundary_failure(
                    boundary,
                    psycopg.errors.ProgramLimitExceeded("private-server-detail"),
                )
                self.assertEqual(
                    (failure.outcome, failure.code, failure.pending),
                    ("budget_exhausted", "storage", False),
                )
