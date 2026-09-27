"""Disposable DB producer-clock faults; no host clock or production SQL changes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock, patch

import psycopg

from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    PostgresSemanticTaskQueue,
    SemanticAuthorizationSnapshot,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort

if TYPE_CHECKING:
    from tests.runtime import test_complete_input_execution as fixtures
    from tests.runtime.test_executor import fixture as generic_execution
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_complete_input_execution as fixtures
    from test_executor import fixture as generic_execution
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

    def test_cancellation_during_downstream_refused_write_keeps_recovery_owned(
        self,
    ) -> None:
        self.setup_execution()
        worker = self.active_worker()
        entered, release = threading.Event(), threading.Event()
        original = worker._persist_result

        def persist(claimed: Any, result: Any, *args: Any, **kwargs: Any) -> Any:
            if str(result.status) == "succeeded":
                self.clock(self.attempt()[1] - timedelta(milliseconds=3))
                entered.set()
                if not release.wait(5):
                    raise AssertionError("fixture did not release the output write")
            return original(claimed, result, *args, **kwargs)

        worker._persist_result = persist

        async def run() -> None:
            observed = self.observe_settlement(worker, asyncio.get_running_loop())
            foreground = asyncio.create_task(worker.run_once())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                deadline = worker._attempt_deadline_ns
                calls = len(self.received)
                self.assertGreater(calls, 0)
                foreground.cancel()
                await asyncio.wait_for(worker._cleanup_started.wait(), 3)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await foreground
                await asyncio.wait_for(observed.wait(), 3)
                self.assertTrue(worker.cleanup_pending)
                first = self.attempt()
                self.assertEqual(first[0], "running")
                self.assertEqual(worker._attempt_deadline_ns, deadline)
                self.assert_no_meaning()
                self.clock(self.safe_time())
                settled = await asyncio.wait_for(worker.wait_for_cleanup(), 3)
                self.assertEqual(settled.task_status, "cancelled")
                self.assertEqual(len(self.received), calls)
                self.assertEqual(self.attempt()[1], first[1])
                self.assertEqual(self.attempt()[4:6], first[4:6])
                self.assertEqual(self.attempt()[7:], first[7:])
                self.assertEqual(
                    self.row(
                        "SELECT error_code,retry_class FROM memoriesql.semantic_task_attempts"
                    ),
                    ("worker.cancelled", "never"),
                )
                self.assert_no_meaning()
            finally:
                release.set()
                if not foreground.done():
                    foreground.cancel()
                await asyncio.gather(foreground, return_exceptions=True)

        self.run_owned(worker, run)

    def test_revocation_while_pending_cannot_settle_with_old_credentials(self) -> None:
        self.revocation_boundary("before")

    def test_revocation_after_context_returns_late_stale_fence(self) -> None:
        self.revocation_boundary("after")

    def revocation_boundary(self, boundary: str) -> None:
        """Commit revocation on the chosen side of the real context call."""
        self.setup_execution()
        worker = self.active_worker()
        self.refuse_start(worker)
        release = threading.Event()
        gated = threading.Event()
        local = threading.local()
        transaction: list[tuple[int, int]] = []
        original_persist = worker._persist_result
        original_begin = PostgresAuthorizationPort.begin_context

        def persist(*args: Any, **kwargs: Any) -> Any:
            local.settlement = True
            try:
                return original_persist(*args, **kwargs)
            finally:
                local.settlement = False

        worker._persist_result = persist

        async def run() -> None:
            loop = asyncio.get_running_loop()
            entered = asyncio.Event()

            def begin(port: Any, **kwargs: Any) -> Any:
                if not getattr(local, "settlement", False) or gated.is_set():
                    return original_begin(port, **kwargs)
                gated.set()
                transaction.append(
                    port._connection.execute(
                        "SELECT pg_backend_pid(),txid_current()"
                    ).fetchone()
                )
                context = original_begin(port, **kwargs) if boundary == "after" else None
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(5):
                    raise AssertionError("fixture did not release context boundary")
                return context if boundary == "after" else original_begin(port, **kwargs)

            with patch.object(PostgresAuthorizationPort, "begin_context", begin):
                foreground = asyncio.create_task(worker.run_once())
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    pending = await asyncio.wait_for(foreground, 3)
                    self.assertEqual(str(pending.cycle_status), "cleanup_pending")
                    self.assertTrue(worker.cleanup_pending)
                    first = self.attempt()
                    deadline = worker._attempt_deadline_ns
                    self.db.execute(
                        "UPDATE memoriesql.authentication_credentials SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
                        (self.worker_principal,),
                    )
                    self.clock(self.safe_time())
                    release.set()
                    ended = await asyncio.wait_for(worker.wait_for_cleanup(), 3)
                    expected = (
                        ("authorization_unavailable", None)
                        if boundary == "before"
                        else ("late_output_discarded", "stale_fence")
                    )
                    self.assertEqual((str(ended.cycle_status), ended.task_status), expected)
                    self.assertEqual(self.attempt(), first)
                    self.assertEqual(worker._attempt_deadline_ns, deadline)
                    self.assertFalse(worker.cleanup_pending)
                    self.assertFalse(worker._refusal_writes)
                    self.assertEqual(
                        self.row(
                            "SELECT count(*) FROM memoriesql.authorization_contexts WHERE backend_pid=%s AND transaction_id=%s",
                            transaction[0],
                        ),
                        (0 if boundary == "before" else 1,),
                    )
                    self.assert_not_dispatched()
                finally:
                    release.set()
                    if not foreground.done():
                        foreground.cancel()
                    await asyncio.gather(foreground, return_exceptions=True)

        self.run_owned(worker, run)

    def test_unrelated_authentication_sql_error_after_context_is_visible(self) -> None:
        self.authentication_error_boundary("after", "invalid authentication context")

    def test_unrecognized_context_authentication_sql_error_is_visible(self) -> None:
        self.authentication_error_boundary("before", "fictional unrelated auth error")

    def authentication_error_boundary(self, boundary: str, message: str) -> None:
        self.setup_execution()
        worker = self.active_worker()
        self.refuse_start(worker)
        self.db.execute(
            """CREATE FUNCTION public.fictional_auth_failure(text) RETURNS text
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '%', $1
            USING ERRCODE='28000'; END $$"""
        )
        original = PostgresAuthorizationPort.begin_context

        def fail(port: Any, **kwargs: Any) -> Any:
            return port._connection.execute(
                "SELECT public.fictional_auth_failure(%s)", (message,)
            ).fetchone()

        def reauthorize(queue: Any, *args: Any, **kwargs: Any) -> Any:
            return fail(PostgresAuthorizationPort(queue._connection))

        # Limit the injected failure to settlement, after claim/start refusal.
        original_persist = worker._persist_result

        def persist(*args: Any, **kwargs: Any) -> Any:
            target, name, function = (
                (PostgresAuthorizationPort, "begin_context", fail)
                if boundary == "before"
                else (PostgresSemanticTaskQueue, "reauthorize", reauthorize)
            )
            with patch.object(target, name, function):
                return original_persist(*args, **kwargs)

        worker._persist_result = persist

        async def run() -> None:
            with self.assertRaises(psycopg.errors.InvalidAuthorizationSpecification):
                await worker.run_once()
                await worker.wait_for_cleanup()
            self.assertFalse(worker.cleanup_pending)
            self.assert_not_dispatched()
            self.assertEqual(self.attempt()[0:3:2], ("claimed", None))
            self.assertEqual(original, PostgresAuthorizationPort.begin_context)

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

    def test_valid_requested_budget_does_not_restore_recovery_capacity(self) -> None:
        self.setup_execution()
        worker = self.active_worker()
        claimed = worker._claim(datetime.now(UTC))
        self.assertIsNotNone(claimed)
        self.clock(self.attempt()[1] - timedelta(milliseconds=3))
        with self.assertRaises(psycopg.errors.CheckViolation) as refused:
            worker._start(claimed.fence, datetime.now(UTC))
        first = self.attempt()

        # The generic envelope admits requested budgets, unlike the complete-
        # input task. Its trusted queue/accounting are isolated fictional ports;
        # the refusal is real PostgreSQL, and SQL/auth are proved above.
        executor, resolved, _deps, ports = generic_execution()
        requested = resolved.definition.run_budget.model_copy(
            update={"wall_clock_seconds": 1}
        )
        self.assertTrue(requested.is_not_wider_than(resolved.definition.run_budget))
        task_input = resolved.task_input.model_copy(
            update={
                "task_id": str(claimed.fence.task_id),
                "requested_budget": requested,
            }
        )
        worker._semantic_registry = executor.semantic_registry
        worker._composition_provider = executor._composition_provider
        worker._executor = executor
        worker._model_accounting = ports
        worker._claim = Mock(
            return_value=SimpleNamespace(
                **(
                    asdict(claimed)
                    | {
                        "fence": claimed.fence,
                        "task_kind": resolved.definition.task_kind,
                        "contract_revision": resolved.definition.contract_revision,
                        "task_contract_hash": resolved.definition.contract_hash,
                        "semantic_registry_hash": executor.semantic_registry.registry_hash,
                        "target_kind": "synthetic_receipt",
                    }
                )
            )
        )
        worker._start = Mock(return_value=True)
        worker._hydrate = Mock(
            return_value=(
                task_input,
                SemanticAuthorizationSnapshot(
                    self.worker_principal, None, 1, self.scope
                ),
                {"tree-count": ports.content},
            )
        )
        worker._heartbeat = Mock(return_value=True)
        worker._attempt_is_live = Mock(return_value=True)
        worker._record_run_event = Mock(return_value=True)
        worker._persist_result = Mock(side_effect=refused.exception)

        async def run() -> None:
            try:
                pending = await asyncio.wait_for(worker.run_once(), 3)
                self.assertEqual(str(pending.cycle_status), "cleanup_pending")
                calls = len(ports.intents)
                self.assertGreater(calls, 0)
                with self.assertRaises(psycopg.errors.CheckViolation):
                    await asyncio.wait_for(worker.wait_for_cleanup(), 2)
                self.assertEqual(len(ports.intents), calls)
                self.assertFalse(worker.cleanup_pending)
                self.assertEqual(self.attempt(), first)
                self.assert_no_meaning()
            finally:
                # A pre-fix negative probe must not leave its longer cycle alive.
                worker._attempt_deadline_ns = time.monotonic_ns() - 1
                if worker._cycle_task is not None:
                    await asyncio.gather(worker._cycle_task, return_exceptions=True)

        asyncio.run(run())


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        DatabaseTimeRefusal(name)
        for name in DatabaseTimeRefusal.__dict__
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
