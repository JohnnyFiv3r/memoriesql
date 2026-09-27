"""Disposable DB producer-clock faults; no host clock or production SQL changes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import psycopg

from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    PostgresSemanticTaskQueue,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort

if TYPE_CHECKING:
    from tests.runtime import test_complete_input_execution as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_complete_input_execution as fixtures
    from test_postgres_runtime import migrate


class DatabaseTimeRefusal(fixtures.CompleteInputExecution):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=18, target_version=30)
        self.originals: list[str] = []
        self.trace: list[dict[str, Any]] = []
        self.refusals = 0
        self.db.execute("CREATE TABLE public.fictional_queue_clock(at timestamptz)")
        self.db.execute("INSERT INTO public.fictional_queue_clock VALUES(NULL)")
        self.db.execute("""CREATE FUNCTION public.fictional_queue_clock() RETURNS timestamptz
            LANGUAGE sql VOLATILE AS $$ SELECT COALESCE(at,pg_catalog.clock_timestamp())
            FROM public.fictional_queue_clock $$""")
        for name in (
            "start_semantic_task_attempt",
            "record_semantic_task_outcome",
            "record_integrated_semantic_task_failure",
            "reap_expired_semantic_tasks",
        ):
            (definition,) = self.row(
                """SELECT pg_get_functiondef(p.oid) FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='memoriesql' AND p.proname=%s""",
                (name,),
            )
            self.assertIn("pg_catalog.clock_timestamp()", definition)
            self.originals.append(definition)
            self.db.execute(
                definition.replace(
                    "pg_catalog.clock_timestamp()", "public.fictional_queue_clock()"
                )
            )
        self.addCleanup(self.restore_producers)

    def restore_producers(self) -> None:
        for definition in self.originals:
            self.db.execute(definition)
        for definition in self.originals:
            name = definition.split("memoriesql.")[1].split("(")[0]
            (actual,) = self.row(
                """SELECT pg_get_functiondef(p.oid) FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='memoriesql' AND p.proname=%s""",
                (name,),
            )
            self.assertEqual(actual, definition)
        print(
            "fictional queue-clock trace "
            + json.dumps(
                {
                    "events": self.trace,
                    "settlement_refusal_count": self.refusals,
                    "production_function_sha256": [
                        hashlib.sha256(x.encode()).hexdigest() for x in self.originals
                    ],
                },
                sort_keys=True,
            )
        )

    def clock(self, at: datetime | None) -> None:
        self.db.execute("UPDATE public.fictional_queue_clock SET at=%s", (at,))
        self.trace.append(
            {"producer_clock": at.isoformat() if at else "native_database"}
        )

    def attempt(self) -> tuple[Any, ...]:
        return self.row("""SELECT status,claimed_at,started_at,heartbeat_at,
            lease_expires_at,deadline_at,finished_at,lease_generation,attempt_id
            FROM memoriesql.semantic_task_attempts""")

    def refuse_start(self, worker: Any) -> None:
        original = worker._start

        def start(*args: Any, **kwargs: Any) -> Any:
            self.fence = args[0]
            first = self.attempt()
            self.trace.append(
                {"claimed_at": first[1].isoformat(), "attempt_id": str(first[-1])}
            )
            self.clock(first[1] - timedelta(milliseconds=3))
            return original(*args, **kwargs)

        worker._start = start

    def active_worker(self) -> Any:
        worker = self.worker()
        worker._config = replace(
            worker._config,
            cancellation_return_timeout_seconds=0.02,
            cancellation_poll_interval_seconds=0.02,
        )
        return worker

    def run_owned(self, worker: Any, case: Any) -> None:
        async def run() -> None:
            try:
                await case()
            finally:
                # End the disposable fault before loop shutdown even when an
                # assertion fails. Drain the owned cycle; do not kill its thread.
                self.clock(self.safe_time())
                if worker._cycle_task is not None:
                    await asyncio.gather(worker._cycle_task, return_exceptions=True)

        asyncio.run(run())

    def observe_settlement(self, worker: Any, loop: Any) -> asyncio.Event:
        observed = asyncio.Event()
        original = worker._settle_preflight_failure

        def settle(*args: Any, **kwargs: Any) -> Any:
            try:
                return original(*args, **kwargs)
            except psycopg.errors.CheckViolation as error:
                self.refusals += 1
                if self.refusals <= 3:
                    self.trace.append(
                        {
                            "refused_constraint": error.diag.constraint_name,
                            "transaction_rolled_back": True,
                        }
                    )
                loop.call_soon_threadsafe(observed.set)
                raise

        worker._settle_preflight_failure = settle
        return observed

    def safe_time(self) -> datetime:
        return cast(
            datetime,
            max(self.attempt()[1], self.row("SELECT clock_timestamp()")[0])
            + timedelta(seconds=1),
        )

    def assert_not_dispatched(self) -> None:
        self.assertEqual(self.received, [])
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (0,),
        )
        self.assert_no_meaning()

    def test_start_and_repeated_settlement_refusals_preserve_owned_attempt(
        self,
    ) -> None:
        self.setup_execution()
        worker = self.active_worker()
        self.refuse_start(worker)

        async def run() -> None:
            observed = self.observe_settlement(worker, asyncio.get_running_loop())
            pending = await asyncio.wait_for(worker.run_once(), 3)
            await asyncio.wait_for(observed.wait(), 3)
            self.assertEqual(str(pending.cycle_status), "cleanup_pending")
            self.assertTrue(worker.cleanup_pending)
            first = self.attempt()
            self.assertEqual(first[0:3:2], ("claimed", None))
            self.assertEqual(first[3], first[1])
            self.assertEqual(first[6], None)
            self.assert_not_dispatched()
            # Observing another cycle does not claim or execute another attempt.
            again = await worker.run_once()
            self.assertEqual(again, pending)
            self.assertEqual(self.attempt(), first)
            self.clock(self.safe_time())
            settled = await asyncio.wait_for(worker.wait_for_cleanup(), 3)
            self.assertEqual(
                (str(settled.cycle_status), settled.task_status),
                ("settled", "failed_terminal"),
            )
            after = self.attempt()
            self.assertEqual(after[1], first[1])
            self.assertEqual(after[4:6], first[4:6])
            self.assertEqual(after[7:], first[7:])
            self.assertGreaterEqual(after[6], first[1])
            self.assertEqual(
                self.row(
                    "SELECT error_code,retry_class FROM memoriesql.semantic_task_attempts"
                ),
                ("worker.database_time_order_refused", "never"),
            )
            self.assertFalse(worker.cleanup_pending)
            self.assert_not_dispatched()

        self.run_owned(worker, run)

    def test_downstream_refusal_rolls_back_output_and_never_reauthors(self) -> None:
        self.setup_execution()
        worker = self.active_worker()
        original = worker._persist_result

        def persist(claimed: Any, result: Any, *args: Any, **kwargs: Any) -> Any:
            if str(result.status) == "succeeded":
                self.clock(self.attempt()[1] - timedelta(milliseconds=3))
            return original(claimed, result, *args, **kwargs)

        worker._persist_result = persist

        async def run() -> None:
            self.observe_settlement(worker, asyncio.get_running_loop())
            pending = await asyncio.wait_for(worker.run_once(), 5)
            self.assertEqual(str(pending.cycle_status), "cleanup_pending")
            calls = len(self.received)
            self.assertGreater(calls, 0)
            self.assert_no_meaning()
            first = self.attempt()
            self.clock(self.safe_time())
            settled = await asyncio.wait_for(worker.wait_for_cleanup(), 3)
            self.assertEqual(settled.task_status, "failed_terminal")
            self.assertEqual(len(self.received), calls)
            self.assertEqual(self.attempt()[1], first[1])
            self.assertEqual(self.attempt()[4:6], first[4:6])
            self.assertEqual(
                self.row("SELECT count(*) FROM memoriesql.semantic_task_attempts"), (1,)
            )
            self.assertEqual(
                self.row(
                    "SELECT count(*) FROM memoriesql.semantic_task_runs WHERE NOT settled"
                ),
                (0,),
            )
            self.assert_no_meaning()

        self.run_owned(worker, run)

    def test_cancellation_during_refusal_stays_owned_until_safe_settlement(
        self,
    ) -> None:
        self.setup_execution()
        worker = self.active_worker()
        worker._config = replace(worker._config, cancellation_return_timeout_seconds=1)
        self.refuse_start(worker)

        async def run() -> None:
            observed = self.observe_settlement(worker, asyncio.get_running_loop())
            foreground = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(observed.wait(), 3)
            foreground.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await foreground
            self.assertTrue(worker.cleanup_pending)
            self.assertEqual(self.attempt()[0], "claimed")
            self.clock(self.safe_time())
            settled = await asyncio.wait_for(worker.wait_for_cleanup(), 3)
            self.assertEqual(settled.task_status, "cancelled")
            self.assertEqual(
                self.row(
                    "SELECT error_code,retry_class FROM memoriesql.semantic_task_attempts"
                ),
                ("worker.cancelled", "never"),
            )
            self.assert_not_dispatched()

        self.run_owned(worker, run)

    def test_revocation_while_pending_cannot_settle_with_old_credentials(self) -> None:
        self.setup_execution()
        worker = self.active_worker()
        self.refuse_start(worker)

        async def run() -> None:
            await asyncio.wait_for(worker.run_once(), 3)
            first = self.attempt()
            self.db.execute(
                "UPDATE memoriesql.authentication_credentials SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
                (self.worker_principal,),
            )
            self.clock(self.safe_time())
            ended = await asyncio.wait_for(worker.wait_for_cleanup(), 3)
            self.assertEqual(
                (str(ended.cycle_status), ended.task_status),
                ("late_output_discarded", "stale_fence"),
            )
            self.assertEqual(self.attempt(), first)
            self.assert_not_dispatched()

        self.run_owned(worker, run)

    def test_unrelated_check_violation_is_not_retried_or_hidden(self) -> None:
        self.setup_execution()
        self.db.execute(
            "ALTER TABLE memoriesql.semantic_task_attempts ADD CONSTRAINT fictional_other_check CHECK(started_at IS NULL)"
        )
        worker = self.active_worker()
        with self.assertRaises(psycopg.errors.CheckViolation) as raised:
            asyncio.run(worker.run_once())
        self.assertEqual(raised.exception.diag.constraint_name, "fictional_other_check")
        self.assertFalse(worker.cleanup_pending)
        self.assertEqual(self.attempt()[0], "claimed")
        self.assert_not_dispatched()

    def test_blocked_recovery_transaction_exits_without_abandoning_its_thread(
        self,
    ) -> None:
        self.setup_execution()
        worker = self.active_worker()
        worker._config = replace(worker._config, refusal_record_timeout_seconds=0.2)
        self.refuse_start(worker)

        async def run() -> None:
            observed = self.observe_settlement(worker, asyncio.get_running_loop())
            await asyncio.wait_for(worker.run_once(), 3)
            await asyncio.wait_for(observed.wait(), 3)
            first = self.attempt()
            with self.connection() as blocker, blocker.transaction():
                blocker.execute(
                    "LOCK TABLE memoriesql.semantic_task_attempts IN ACCESS EXCLUSIVE MODE"
                )
                self.assertTrue(worker.cleanup_pending)
                # A real lock wait exceeds the existing configured transaction
                # bound. The worker must expose that different database error
                # after its owned call exits, rather than retry or claim success.
                with self.assertRaises(psycopg.errors.TransactionTimeout):
                    await asyncio.wait_for(worker.wait_for_cleanup(), 3)
                self.assertFalse(worker.cleanup_pending)
            self.assertEqual(self.attempt(), first)
            self.assert_not_dispatched()

        self.run_owned(worker, run)

    def test_cumulative_elapsed_bound_survives_clock_refusals_then_reaper_recovers(
        self,
    ) -> None:
        self.setup_execution()
        worker = self.active_worker()
        self.refuse_start(worker)

        async def run() -> None:
            observed = self.observe_settlement(worker, asyncio.get_running_loop())
            await asyncio.wait_for(worker.run_once(), 3)
            await asyncio.wait_for(observed.wait(), 3)
            first = self.attempt()
            worker._attempt_deadline_ns = time.monotonic_ns() - 1
            with self.assertRaises(psycopg.errors.CheckViolation):
                await asyncio.wait_for(worker.wait_for_cleanup(), 3)
            self.assertFalse(worker.cleanup_pending)
            self.assertEqual(self.attempt(), first)
            self.assert_not_dispatched()

        self.run_owned(worker, run)
        # Fresh connection/queue, not the old cycle: no attempt/lease mutation.
        self.clock(self.attempt()[4] + timedelta(seconds=1))
        with self.connection() as connection, connection.transaction():
            connection.execute("SET LOCAL ROLE memoriesql_worker")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=self.attestor_secret,
                requested_workspace_id=self.workspace,
            )
            queue = PostgresSemanticTaskQueue(connection)
            self.assertEqual(
                queue.reap_expired(
                    worker_id="orchard.reaper",
                    limit=1,
                    reaped_at=self.row("SELECT at FROM public.fictional_queue_clock")[
                        0
                    ],
                ),
                1,
            )
            self.assertEqual(
                queue.reap_expired(
                    worker_id="orchard.reaper",
                    limit=1,
                    reaped_at=self.row("SELECT at FROM public.fictional_queue_clock")[
                        0
                    ],
                ),
                0,
            )
        self.assertEqual(self.attempt()[0], "lease_lost")
        after = self.attempt()
        with self.connection() as connection, connection.transaction():
            connection.execute("SET LOCAL ROLE memoriesql_worker")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=self.worker_secret,
                requested_workspace_id=self.workspace,
            )
            queue = PostgresSemanticTaskQueue(connection)
            at = self.row("SELECT at FROM public.fictional_queue_clock")[0]
            self.assertFalse(queue.start(self.fence, started_at=at))
            row = connection.execute(
                """SELECT memoriesql.record_semantic_task_outcome(
                %s,%s,%s,%s,%s,%s,'failed',NULL,NULL,
                'worker.database_time_order_refused','worker','never',NULL,0,%s)""",
                queue._fence_parameters(self.fence) + (at,),
            ).fetchone()
            self.assertEqual(row, ("stale_fence",))
        self.assertEqual(self.attempt(), after)
        self.assert_not_dispatched()


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        DatabaseTimeRefusal(name)
        for name in DatabaseTimeRefusal.__dict__
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
