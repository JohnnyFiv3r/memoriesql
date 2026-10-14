"""Fictional server-side staging failures; no capacity or quality claim."""

from __future__ import annotations

import os
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg
from psycopg import sql

from memoriesql.infrastructure.postgres import restricted_query
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_results as results_tests
else:
    import test_agent_sql_results as results_tests


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AgentSqlStaging(unittest.TestCase):
    def setUp(self) -> None:
        self.r = results_tests.AgentSqlResults(methodName="runTest")
        self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        self.db = self.r.db
        self.r.fixture.assertion()

    def assert_rejection_settles(self, code: str, outcome: str) -> None:
        run = self.r.start()
        original = relation_projection_frame
        entered = 0

        @contextmanager
        def reject(*args: Any, **kwargs: Any) -> Iterator[Any]:
            nonlocal entered
            with original(*args, **kwargs) as frame:
                entered += 1
                # A real PostgreSQL ErrorResponse aborts the staging frame.
                # This injects an error class, not a real resource exhaustion.
                frame.execute(
                    sql.SQL(
                        "DO $failure$ BEGIN RAISE EXCEPTION 'fictional staging failure' "
                        "USING ERRCODE = {}; END $failure$"
                    ).format(sql.Literal(code))
                )
                yield frame

        with patch.object(restricted_query, "relation_projection_frame", reject):
            _, reply = self.r.query(
                run, "SELECT bead_id FROM memory_v1.observations ORDER BY bead_id"
            )
        self.assertEqual(entered, 1)
        self.assertEqual(reply["outcome"], outcome, reply)
        self.assertNotIn("page", reply)
        self.assertNotIn("result", reply)
        self.assertEqual(
            self.r.h.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries "
                "WHERE state <> 'settled'"
            ),
            0,
        )
        self.assertEqual(
            self.r.h.scalar("SELECT count(*) FROM memoriesql_query.invocations"), 0
        )
        self.assertEqual(
            self.r.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations "
                "WHERE state <> 'discarded'"
            ),
            0,
        )
        # The same workspace can make progress without host crash recovery.
        _, next_reply = self.r.query(
            run, "SELECT bead_id FROM memory_v1.observations ORDER BY bead_id"
        )
        self.assertEqual(next_reply["outcome"], "available", next_reply)

    def test_lock_timeout_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("55P03", "budget_exhausted")

    def test_statement_timeout_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("57014", "budget_exhausted")

    def test_configuration_limit_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("53400", "budget_exhausted")

    def test_program_limit_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("54000", "budget_exhausted")

    def test_permission_refusal_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("42501", "unavailable")

    def test_authorization_refusal_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("28000", "unavailable")

    def test_server_error_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("XX000", "execution_error")

    def test_serialization_refusal_releases_the_delivery(self) -> None:
        self.assert_rejection_settles("40001", "budget_exhausted")

    def test_lost_commit_acknowledgment_keeps_recovery_ownership(self) -> None:
        run = self.r.start()
        original = relation_projection_frame
        committed = False

        @contextmanager
        def lost_ack(*args: Any, **kwargs: Any) -> Iterator[Any]:
            nonlocal committed
            with original(*args, **kwargs) as frame:
                yield frame
            committed = True
            raise psycopg.OperationalError("fictional lost commit acknowledgment")

        with patch.object(restricted_query, "relation_projection_frame", lost_ack):
            _, reply = self.r.query(
                run, "SELECT bead_id FROM memory_v1.observations ORDER BY bead_id"
            )
        self.assertTrue(committed)
        self.assertEqual(reply["outcome"], "settlement_pending", reply)
        self.assertNotIn("page", reply)
        self.assertEqual(
            self.r.h.scalar("SELECT count(*) FROM memoriesql_query.invocations"), 1
        )
        self.assertEqual(
            self.r.h.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries "
                "WHERE state <> 'settled'"
            ),
            1,
        )

    def test_unconfirmed_rollback_keeps_recovery_ownership(self) -> None:
        run = self.r.start()

        @contextmanager
        def broken_rollback(*args: Any, **kwargs: Any) -> Iterator[Any]:
            with relation_projection_frame(*args, **kwargs) as frame:
                try:
                    frame.execute(
                        "DO $failure$ BEGIN RAISE EXCEPTION 'fictional refusal' "
                        "USING ERRCODE = '55P03'; END $failure$"
                    )
                except psycopg.Error:
                    # No healthy connection remains to confirm the rollback.
                    frame.close()
                    raise
                yield frame

        with patch.object(
            restricted_query, "relation_projection_frame", broken_rollback
        ):
            _, reply = self.r.query(
                run, "SELECT bead_id FROM memory_v1.observations ORDER BY bead_id"
            )
        self.assertEqual(reply["outcome"], "settlement_pending", reply)
        self.assertEqual(
            self.r.h.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries "
                "WHERE state <> 'settled'"
            ),
            1,
        )
