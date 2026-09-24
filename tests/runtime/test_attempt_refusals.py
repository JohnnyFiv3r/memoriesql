"""A refused canonical apply keeps the database's reason (schema 28)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import psycopg
from pydantic_ai.messages import ToolCallPart

from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    PostgresSemanticTaskQueue,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort

if TYPE_CHECKING:
    from tests.runtime import test_local_entity_mentions as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_local_entity_mentions as fixtures
    from test_postgres_runtime import migrate


class AttemptRefusals(fixtures.LocalMentions):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=22, target_version=28)
        self.bead_type = "observation"

    def response(self, messages: Any, info: Any) -> Any:
        response = super().response(messages, info)
        part = cast(ToolCallPart, response.parts[0])
        data = part.args_as_dict()
        if data["typed_output"] is not None:
            data["typed_output"]["annotations"][0]["bead_type_key"] = self.bead_type
        part.args = data
        return response

    def cycle(self) -> Any:
        async def run() -> Any:
            worker = self.worker()
            receipt = await worker.run_once()
            await worker.wait_for_cleanup()
            return receipt

        return asyncio.run(run())

    def refusals(self) -> list[tuple[Any, ...]]:
        return self.db.execute(
            "SELECT sqlstate, message FROM memoriesql.semantic_attempt_refusals"
        ).fetchall()

    def record_as_worker(self, sqlstate: str, message: str) -> bool:
        fence = self.row(
            "SELECT tenant_id, task_id, attempt_id, lease_generation, worker_id, worker_instance_id FROM memoriesql.semantic_task_attempts"
        )
        with self.connection() as connection, connection.transaction():
            connection.execute("SET LOCAL ROLE memoriesql_worker")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=self.worker_secret, requested_workspace_id=self.workspace
            )
            row = connection.execute(
                "SELECT memoriesql.record_semantic_attempt_refusal(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (*fence, sqlstate, message, datetime.now(UTC)),
            ).fetchone()
        assert row
        return bool(row[0])

    def test_database_refusal_keeps_its_reason_once(self) -> None:
        # The author names a bead type the workspace has not registered, so
        # canonical apply refuses the whole output with a data error.
        self.bead_type = "mention"
        self.setup_mentions()
        receipt = self.cycle()
        self.assertEqual(str(receipt.result_status), "invalid_output", receipt)
        self.assertEqual(
            self.row("SELECT status, error_code FROM memoriesql.semantic_task_attempts"),
            ("terminal_failure", "worker.canonical_apply_refused"),
        )
        self.assertEqual(self.refusals(), [("22023", "bead type revision is unavailable")])
        self.assert_no_meaning()
        # A settled attempt takes no second record, and the first one stays.
        self.assertFalse(self.record_as_worker("22023", "another reason"))
        self.assertEqual(self.refusals(), [("22023", "bead type revision is unavailable")])
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.record_as_worker("bad", "a reason")

    def test_only_a_live_lease_records_a_refusal(self) -> None:
        self.setup_mentions()
        with self.connection() as connection, connection.transaction():
            connection.execute("SET LOCAL ROLE memoriesql_worker")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=self.worker_secret, requested_workspace_id=self.workspace
            )
            claimed = PostgresSemanticTaskQueue(connection).claim(
                worker_id="orchard.worker",
                worker_instance_id="orchard.instance",
                lease_seconds=120,
                deadline_seconds=300,
                executor_contract_version=1,
                claimed_at=datetime.now(UTC),
            )
        assert claimed
        lease = "UPDATE memoriesql.semantic_tasks SET lease_expires_at = clock_timestamp() + %s::interval WHERE task_id = %s"
        # A lapsed lease records nothing, as settlement would refuse that fence.
        self.db.execute(lease, ("-1 second", claimed.fence.task_id))
        self.assertFalse(self.record_as_worker("22023", "semantic statement run is unavailable"))
        self.assertEqual(self.refusals(), [])
        self.db.execute(lease, ("5 minutes", claimed.fence.task_id))
        self.assertTrue(self.record_as_worker("22023", "semantic statement run is unavailable"))
        self.assertFalse(self.record_as_worker("22023", "another reason"))
        self.assertEqual(self.refusals(), [("22023", "semantic statement run is unavailable")])

    def test_accepted_output_records_no_refusal_and_readers_cannot_see_refusals(self) -> None:
        self.setup_mentions()
        receipt = self.cycle()
        self.assertEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual(self.refusals(), [])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege), self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute("SELECT count(*) FROM memoriesql.semantic_attempt_refusals")


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    # Only this module's tests; the inherited mention tests run in their own module.
    return unittest.TestSuite(
        AttemptRefusals(name) for name in AttemptRefusals.__dict__ if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
