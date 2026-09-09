"""Public fictional PostgreSQL coverage of the existing cleanup handoff.

Only the I/O arrival is gated; heartbeat, retention and reaping execute installed
SQL. The short expiry is fixture setup, not a migration or relaxed production
lease limit. Full-cycle cancellation/accounting is covered by CancellationDrain.
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import psycopg
from psycopg.conninfo import make_conninfo

if TYPE_CHECKING:
    from tests.runtime import test_postgres_runtime as postgres_fixture
    from tests.runtime.test_executor import fixture
else:
    import test_postgres_runtime as postgres_fixture
    from test_executor import fixture

from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
    IntegratedSemanticWorker,
    ProviderExecutionReadiness,
    ProviderExecutionState,
    SemanticWorkerConfig,
    SemanticWorkerIdentity,
    _CancellationEvent,
)


class CleanupLeaseHandoff(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = postgres_fixture.PostgresRuntime()
        self.addCleanup(self.store.doCleanups)
        self.store.setUp()
        self.store.accept(self.store.command())
        self.store.pair_worker()
        self.claimed = self.store.claim()
        executor, _, _, _ = fixture()
        self.worker = IntegratedSemanticWorker(
            connection_factory=lambda: psycopg.connect(
                make_conninfo(self.store.admin, dbname=self.store.database)
            ),
            identity=SemanticWorkerIdentity(
                credential_sha256=self.store.worker_secret,
                workspace_id=self.store.workspace,
                worker_id="orchard.worker",
                worker_instance_id="orchard.instance",
            ),
            semantic_registry=executor.semantic_registry,
            composition_provider=executor._composition_provider,
            executor=executor,
            readiness_provider=lambda: ProviderExecutionReadiness(
                ProviderExecutionState.CONFIGURED, "fictional.configured"
            ),
            config=SemanticWorkerConfig(
                heartbeat_interval_seconds=0.02,
                cancellation_poll_interval_seconds=0.01,
            ),
        )
        self.entered = threading.Event()
        self.release_heartbeat = threading.Event()
        self.heartbeat_finished = threading.Event()
        self.release_executor = asyncio.Event()
        self.executor_cancelled = asyncio.Event()
        self.retained = asyncio.Event()
        self.renewal_failed = asyncio.Event()
        self.unavailable = asyncio.Event()
        self.cancellation = _CancellationEvent()
        self.heartbeat_result: bool | None = None
        self.retention_results: list[bool] = []
        self.fail_calls: set[int] = set()
        self.fail_all = False
        self.retention_calls = 0
        self.tasks: list[asyncio.Task[Any]] = []
        loop = asyncio.get_running_loop()
        heartbeat = self.worker._heartbeat
        retain = self.worker._retain_cleanup_lease

        def gated_heartbeat(*args: Any) -> bool:
            self.entered.set()
            if not self.release_heartbeat.wait(5):
                raise AssertionError("fixture must release heartbeat")
            try:
                self.heartbeat_result = heartbeat(*args)
                return self.heartbeat_result
            finally:
                self.heartbeat_finished.set()

        def controlled_retention(*args: Any) -> bool:
            self.retention_calls += 1
            if self.fail_all or self.retention_calls in self.fail_calls:
                loop.call_soon_threadsafe(self.renewal_failed.set)
                raise psycopg.OperationalError("fictional control outage")
            result = retain(*args)
            self.retention_results.append(result)
            if result:
                loop.call_soon_threadsafe(self.retained.set)
            return result

        self.worker._heartbeat = Mock(side_effect=gated_heartbeat)  # type: ignore[method-assign]
        self.worker._retain_cleanup_lease = Mock(side_effect=controlled_retention)  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        # Release every gate even on the expected 0.0.2 failure; never detach
        # the thread, executor, handoff, observer, or renewal operation.
        self.release_heartbeat.set()
        self.release_executor.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(*self.tasks, return_exceptions=True), 3
        )
        renewal = self.worker._cleanup_heartbeat
        if renewal is not None:
            renewal.cancel()
            outcomes.extend(
                await asyncio.wait_for(
                    asyncio.gather(renewal, return_exceptions=True), 3
                )
            )
        for result in outcomes:
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                raise result
        self.assertTrue(all(task.done() for task in self.tasks))

    def shorten_fixture_expiry(self) -> datetime:
        deadline = datetime.now(UTC) + timedelta(seconds=2)
        with self.store.db.transaction():
            # The only bypass is fixture clock setup of this generated attempt.
            self.store.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts DISABLE TRIGGER semantic_task_attempts_protect_state"
            )
            self.store.db.execute(
                "UPDATE memoriesql.semantic_task_attempts SET deadline_at=%s, lease_expires_at=%s WHERE attempt_id=%s",
                (deadline, deadline, self.claimed.fence.attempt_id),
            )
            self.store.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts ENABLE TRIGGER semantic_task_attempts_protect_state"
            )
            for table in ("semantic_tasks", "semantic_concurrency_slots"):
                from psycopg import sql

                self.store.db.execute(
                    sql.SQL(
                        "UPDATE memoriesql.{} SET lease_expires_at=%s WHERE task_id=%s"
                    ).format(sql.Identifier(table)),
                    (deadline, self.claimed.fence.task_id),
                )
        self.claimed = replace(
            self.claimed,
            fence=replace(
                self.claimed.fence, deadline_at=deadline, lease_expires_at=deadline
            ),
        )
        return deadline

    async def start_handoff(self, *, lost_generation: bool = False) -> None:
        async def execute() -> Any:
            while not self.release_executor.is_set():
                try:
                    await self.release_executor.wait()
                except asyncio.CancelledError:
                    self.executor_cancelled.set()

        self.executor = asyncio.create_task(execute())
        self.heartbeat = asyncio.create_task(
            self.worker._watch_attempt(
                self.claimed.fence, self.cancellation, self.unavailable
            )
        )
        self.tasks.extend((self.executor, self.heartbeat))
        self.assertTrue(await asyncio.to_thread(self.entered.wait, 1))
        if lost_generation:
            self.claimed = replace(
                self.claimed,
                fence=replace(
                    self.claimed.fence,
                    lease_generation=self.claimed.fence.lease_generation + 1,
                ),
            )
        self.handoff = asyncio.create_task(
            self.worker._retain_cleanup_and_drain_executor(
                self.claimed.fence,
                self.heartbeat,
                self.unavailable,
                self.executor,
                self.cancellation,
                cancellation_grace_seconds=0.01,
            )
        )
        self.tasks.append(self.handoff)

    async def reap_after(self, deadline: datetime) -> int:
        await asyncio.sleep(
            max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.05
        )
        with self.store.db.transaction():
            return int(
                self.store.worker_queue().reap_expired(
                    worker_id="orchard.recovery", limit=1, reaped_at=datetime.now(UTC)
                )
            )

    def lease_expiries(self) -> tuple[datetime, ...]:
        values = []
        for table in (
            "semantic_tasks",
            "semantic_task_attempts",
            "semantic_concurrency_slots",
        ):
            from psycopg import sql

            rows = self.store.db.execute(
                sql.SQL(
                    "SELECT lease_expires_at FROM memoriesql.{} WHERE task_id=%s"
                ).format(sql.Identifier(table)),
                (self.claimed.fence.task_id,),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            values.append(rows[0][0])
        return tuple(values)

    async def test_blocked_heartbeat_cannot_make_unfinished_executor_reapable(
        self,
    ) -> None:
        deadline = self.shorten_fixture_expiry()
        await self.start_handoff()
        reaped = await self.reap_after(deadline)
        self.assertFalse(self.heartbeat_finished.is_set())
        self.assertFalse(self.executor.done())
        self.assertEqual(reaped, 0, "unfinished execution must not become reapable")
        self.assertTrue(self.retained.is_set())
        self.assertTrue(all(expiry > deadline for expiry in self.lease_expiries()))

    async def test_late_ordinary_heartbeat_cannot_shorten_retained_lease(self) -> None:
        deadline = self.shorten_fixture_expiry()
        await self.start_handoff()
        await asyncio.wait_for(self.retained.wait(), 1)
        retained = self.lease_expiries()
        self.assertTrue(all(expiry > deadline for expiry in retained))
        self.release_heartbeat.set()
        self.assertTrue(await asyncio.to_thread(self.heartbeat_finished.wait, 1))
        self.assertTrue(self.heartbeat_result)
        self.assertTrue(
            all(
                after >= before
                for before, after in zip(retained, self.lease_expiries(), strict=True)
            )
        )
        self.assertFalse(self.executor.done())

    async def test_transient_initial_and_renewal_failures_retry_without_release(
        self,
    ) -> None:
        self.fail_calls = {1, 3}
        deadline = self.shorten_fixture_expiry()
        await self.start_handoff()
        await asyncio.wait_for(self.retained.wait(), 1)
        self.assertEqual(await self.reap_after(deadline), 0)
        self.assertTrue(self.unavailable.is_set())
        self.assertGreater(self.retention_calls, 3)
        self.assertGreater(len(self.retention_results), 1)
        self.assertFalse(self.heartbeat_finished.is_set())
        self.assertFalse(self.executor.done())

    async def test_total_control_outage_does_not_claim_retention_or_revive_expiry(
        self,
    ) -> None:
        self.fail_all = True
        deadline = self.shorten_fixture_expiry()
        await self.start_handoff()
        self.assertEqual(await self.reap_after(deadline), 1)
        self.assertTrue(self.unavailable.is_set())
        self.assertFalse(self.retained.is_set())
        self.assertFalse(self.executor.done())
        self.fail_all = False
        renewal = self.worker._cleanup_heartbeat
        self.assertIsNotNone(renewal)
        await asyncio.wait_for(asyncio.shield(renewal), 1)  # type: ignore[arg-type]
        self.assertEqual(self.retention_results, [False])
        self.assertFalse(self.retained.is_set())

    async def test_lost_generation_cannot_acquire_cleanup_retention(self) -> None:
        await self.start_handoff(lost_generation=True)
        await asyncio.wait_for(self.executor_cancelled.wait(), 1)
        self.assertEqual(self.retention_results, [False])
        self.assertIsNone(self.worker._cleanup_heartbeat)
        self.assertFalse(self.executor.done())
        self.assertFalse(self.heartbeat_finished.is_set())
