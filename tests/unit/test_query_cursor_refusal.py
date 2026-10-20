"""Cursor failures must preserve current whole-result availability and privacy."""

from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
from typing import Any, cast
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from psycopg.errors import InsufficientPrivilege, QueryCanceled

from memoriesql.application.investigation_contracts import parse_investigation_request
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
    _Failure,
)

MODULE = "memoriesql.infrastructure.postgres.agent_sql_results"


class QueryCursorRefusal(unittest.TestCase):
    def setUp(self) -> None:
        self.request = cast(
            Any,
            parse_investigation_request(
                json.dumps(
                    {
                        "contract_version": 1,
                        "run_ref": str(uuid4()),
                        "step_key": str(uuid4()),
                        "kind": "reuse_result",
                        "result": {"result_id": str(uuid4()), "content_digest": "0" * 64},
                        "access": {"kind": "standalone"},
                        "page_size": 1,
                        "cursor": str(uuid4()),
                    }
                ).encode()
            ),
        )
        self.service = cast(Any, object.__new__(PostgresAgentSqlResults))
        self.service._credential = "fictional"
        self.service._workspace = uuid4()
        self.service._control = Mock(return_value=nullcontext(None))
        self.service._remaining_ms = Mock(return_value=123)
        self.service._disclose = Mock(return_value=b"page")
        self.frame = Mock()
        self.binding = {
            "result_id": self.request.result.result_id,
            "content_digest": self.request.result.content_digest,
            "access_context": {"kind": "standalone"},
            "next_row": 2,
        }

    def reuse(self) -> bytes:
        with patch(MODULE + ".relation_projection_frame", return_value=nullcontext(self.frame)):
            return cast(bytes, self.service._reuse(self.request, {}, 0, 1))

    def absent_binding(self, proof: Any = True, error: Exception | None = None) -> None:
        def execute(sql: str, values: Any) -> Mock:
            if "resolve_query_cursor" in sql:
                return Mock(fetchone=Mock(return_value=(None,)))
            self.assertIn("check_query_result_closure_v2", sql)
            self.assertEqual(values, (UUID(self.request.result.result_id), self.request.result.content_digest))
            if error is not None:
                raise error
            return Mock(fetchone=Mock(return_value=proof))

        self.frame.execute.side_effect = execute

    def refuse(self, outcome: str, code: str) -> None:
        with self.assertRaises(_Failure) as caught:
            self.reuse()
        self.assertEqual((caught.exception.outcome, caught.exception.code), (outcome, code))
        self.service._disclose.assert_not_called()

    def test_absent_cursor_with_unavailable_result_is_unavailable(self) -> None:
        self.absent_binding(error=InsufficientPrivilege("private dependency unavailable"))
        self.refuse("unavailable", "unavailable")

    def test_unknown_cursor_for_currently_usable_result_is_invalid(self) -> None:
        self.absent_binding(proof=(True,))
        self.refuse("invalid_request", "cursor")
        self.assertEqual(self.frame.execute.call_count, 2)

    def test_absent_or_negative_availability_proof_never_validates_result(self) -> None:
        for proof in (None, (False,), (None,)):
            with self.subTest(proof=proof):
                self.absent_binding(proof=proof)
                self.refuse("unavailable", "unavailable")

    def test_authority_fence_denial_stays_unavailable(self) -> None:
        self.absent_binding(error=PermissionError("private authority denial"))
        self.refuse("unavailable", "unavailable")

    def test_availability_check_timeout_keeps_existing_time_refusal(self) -> None:
        self.absent_binding(error=QueryCanceled("private query text"))
        self.refuse("budget_exhausted", "time")

    def test_known_other_result_cursor_never_discloses_or_changes_target(self) -> None:
        self.frame.execute.return_value.fetchone.return_value = ({**self.binding, "result_id": str(uuid4())},)
        self.refuse("invalid_request", "cursor")
        self.assertEqual(self.frame.execute.call_count, 1)

    def test_valid_owner_binding_reaches_normal_disclosure(self) -> None:
        self.frame.execute.return_value.fetchone.return_value = (self.binding,)
        self.assertEqual(self.reuse(), b"page")
        self.assertEqual(self.service._disclose.call_args.args[6:], (2, 1))
        self.assertEqual(self.frame.execute.call_count, 1)
