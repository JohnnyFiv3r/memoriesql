"""A refused canonical apply keeps the database's reason (schema 28)."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import psycopg
from pydantic_ai.messages import ToolCallPart

from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
    IntegratedSemanticWorker,
    SemanticWorkerConfig,
)
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

    def bounded_worker(self, seconds: float) -> Any:
        worker = self.worker()
        worker._config = SemanticWorkerConfig(
            heartbeat_interval_seconds=10,
            cancellation_return_timeout_seconds=0.2,
            refusal_record_timeout_seconds=seconds,
        )
        return worker

    def cycle(self, worker: Any = None) -> Any:
        async def run() -> Any:
            active = worker or self.worker()
            receipt = await active.run_once()
            await active.wait_for_cleanup()
            return receipt

        return asyncio.run(run())

    def attempt(self) -> tuple[Any, ...]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT status, error_code FROM memoriesql.semantic_task_attempts"
            ).fetchone()
        assert row
        return tuple(row)

    def settled_within(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.attempt()[0] == "terminal_failure":
                return True
            time.sleep(0.05)
        return False

    def observe_records(self) -> tuple[list[BaseException], Callable[..., bool]]:
        """Wrap the worker's refusal record so a test sees how it ended."""
        errors: list[BaseException] = []
        original = IntegratedSemanticWorker._record_refusal

        def observed(worker: Any, *args: Any, **kwargs: Any) -> bool:
            try:
                return original(worker, *args, **kwargs)
            except Exception as error:
                errors.append(error)
                raise

        return errors, observed

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

    def test_contended_record_times_out_and_settlement_proceeds(self) -> None:
        # Another session holds the refusal table, so the record waits on a lock.
        # The database ends that wait at the bound and the attempt still settles.
        self.bead_type = "mention"
        self.setup_mentions()
        errors, observed = self.observe_records()
        worker = self.bounded_worker(0.5)
        with (
            patch.object(IntegratedSemanticWorker, "_record_refusal", observed),
            self.connection() as blocker,
            blocker.transaction(),
        ):
            # Were the bound ever lost, the database would end this session
            # after 20 idle seconds and the test would fail instead of hanging.
            blocker.execute(
                "SELECT set_config('idle_in_transaction_session_timeout', '20000', true)"
            )
            blocker.execute(
                "LOCK TABLE memoriesql.semantic_attempt_refusals IN ACCESS EXCLUSIVE MODE"
            )
            started = time.monotonic()
            receipt = self.cycle(worker)
            elapsed = time.monotonic() - started
            # Nothing still queues behind the lock: the database ended the wait.
            waiting = self.row(
                "SELECT count(*) FROM pg_catalog.pg_locks WHERE NOT granted "
                "AND relation = 'memoriesql.semantic_attempt_refusals'::regclass"
            )
        self.assertEqual(str(receipt.result_status), "invalid_output", receipt)
        self.assertEqual(self.attempt(), ("terminal_failure", "worker.canonical_apply_refused"))
        # The database ended the record's transaction at the bound.
        self.assertEqual([type(error) for error in errors], [psycopg.errors.TransactionTimeout])
        self.assertEqual(waiting, (0,))
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 20)
        self.assertEqual(worker._refusal_writes, set())
        self.assertEqual(self.refusals(), [])

    def stall_record(self, *, cancel: bool) -> None:
        """Stall the record inside its transaction and check settlement proceeds.

        The stalled transaction holds rows settlement's authorization context
        also deletes. The database ends that transaction at the bound, so
        settlement proceeds while the record's thread is still stalled; the
        cycle owns that thread until it returns, and the late call records
        nothing.
        """
        self.bead_type = "mention"
        self.setup_mentions()
        entered = threading.Event()
        release = threading.Event()
        original = PostgresSemanticTaskQueue.record_attempt_refusal
        errors, observed = self.observe_records()

        def stalled(queue: Any, fence: Any, **detail: Any) -> bool:
            entered.set()
            if not release.wait(30):
                raise AssertionError("test did not release the refusal record")
            return original(queue, fence, **detail)

        async def run() -> tuple[bool, bool]:
            worker = self.bounded_worker(0.5)
            foreground = asyncio.create_task(worker.run_once())
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            if cancel:
                foreground.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(foreground, 5)
            settled = await asyncio.to_thread(self.settled_within, 10)
            # The cycle still owns the stalled record's thread.
            owned = bool(worker._refusal_writes) and (
                worker.cleanup_pending if cancel else not foreground.done()
            )
            release.set()
            if cancel:
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(worker.wait_for_cleanup(), 10)
            else:
                receipt = await asyncio.wait_for(foreground, 10)
                self.assertEqual(str(receipt.result_status), "invalid_output", receipt)
            self.assertEqual(worker._refusal_writes, set())
            return settled, owned

        with (
            patch.object(PostgresSemanticTaskQueue, "record_attempt_refusal", stalled),
            patch.object(IntegratedSemanticWorker, "_record_refusal", observed),
        ):
            try:
                settled, owned = asyncio.run(run())
            finally:
                release.set()
        self.assertTrue(settled, "settlement waited for the stalled record")
        self.assertTrue(owned, "the stalled record was not owned by its cycle")
        self.assertEqual(self.attempt(), ("terminal_failure", "worker.canonical_apply_refused"))
        # The database ended the stalled session, so the late call failed.
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.refusals(), [])

    def test_stalled_record_does_not_hold_settlement(self) -> None:
        self.stall_record(cancel=False)

    def test_cancelled_cycle_settles_while_the_stalled_record_stays_owned(self) -> None:
        self.stall_record(cancel=True)

    def test_accepted_output_records_no_refusal_and_readers_cannot_see_refusals(self) -> None:
        self.setup_mentions()
        receipt = self.cycle()
        self.assertEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual(self.refusals(), [])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege), self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute("SELECT count(*) FROM memoriesql.semantic_attempt_refusals")


class RefusalRecordTimeout(unittest.TestCase):
    def test_timeout_is_positive_and_at_most_a_minute(self) -> None:
        for seconds in (0.0, -1.0, 60.5, float("inf"), float("nan")):
            with self.assertRaises(ValueError, msg=seconds):
                SemanticWorkerConfig(refusal_record_timeout_seconds=seconds)
        config = SemanticWorkerConfig(refusal_record_timeout_seconds=60)
        self.assertEqual(config.refusal_record_timeout_seconds, 60)


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    # Only this module's tests; the inherited mention tests run in their own module.
    suite = unittest.TestSuite(
        AttemptRefusals(name) for name in AttemptRefusals.__dict__ if name.startswith("test_")
    )
    suite.addTests(loader.loadTestsFromTestCase(RefusalRecordTimeout))
    return suite


if __name__ == "__main__":
    unittest.main()
