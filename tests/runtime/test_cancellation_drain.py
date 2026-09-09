"""Owned worker cancellation with public fictional models and controlled I/O."""

from __future__ import annotations

import asyncio
import threading
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock
from uuid import uuid4

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import RequestUsage

if TYPE_CHECKING:
    from tests.runtime.test_executor import fixture
else:
    from test_executor import fixture

from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
    IntegratedSemanticWorker,
    ProviderExecutionReadiness,
    ProviderExecutionState,
    SemanticWorkerConfig,
    SemanticWorkerIdentity,
    WorkerCycleStatus,
)
from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    SemanticAuthorizationSnapshot,
    SemanticTaskFence,
)
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
)
from memoriesql.infrastructure.postgres.model_accounting import _finish_database_write


class CancellationDrain(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.cancels = 0
        self.model_returns = 0
        self.settlement_started = threading.Event()
        self.release_settlement = threading.Event()
        self.release_settlement.set()
        self.settlement_error = False
        self.retention_alive_at_settlement = False

        async def resistant(messages: Any, info: Any) -> ModelResponse:
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancels += 1
                    self.cancel_seen.set()
            self.model_returns += 1
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "typed_output": {"answer": "Four fictional trees."},
                            "used_evidence_refs": ["tree-count"],
                        },
                    )
                ],
                usage=RequestUsage(input_tokens=11, output_tokens=7),
            )

        executor, resolved, deps, self.ports = fixture(
            model_callback=resistant,
            request_target=CHARACTERIZED_TEST_REQUEST_TARGET.model_copy(
                update={"usage_provenance": "provider_reported"}
            ),
        )
        executor.cancellation_cleanup_timeout_seconds = 0.01
        now = datetime.now(UTC)
        fence = SemanticTaskFence(
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            1,
            1,
            "orchard.worker",
            "orchard.instance",
            now + timedelta(seconds=300),
            now + timedelta(seconds=120),
        )
        self.worker: Any = cast(
            Any,
            IntegratedSemanticWorker(
                connection_factory=Mock(
                    side_effect=AssertionError("unexpected database")
                ),
                identity=SemanticWorkerIdentity(
                    credential_sha256="fictional",
                    workspace_id=fence.workspace_id,
                    worker_id=fence.worker_id,
                    worker_instance_id=fence.worker_instance_id,
                ),
                semantic_registry=executor.semantic_registry,
                composition_provider=executor._composition_provider,
                executor=executor,
                readiness_provider=lambda: ProviderExecutionReadiness(
                    ProviderExecutionState.CONFIGURED, "fictional.configured"
                ),
                config=SemanticWorkerConfig(
                    cancellation_return_timeout_seconds=0.03,
                    heartbeat_interval_seconds=0.01,
                    cancellation_poll_interval_seconds=0.005,
                ),
            ),
        )
        self.worker._model_accounting = self.ports
        task_input = resolved.task_input.model_copy(
            update={"task_id": str(fence.task_id)}
        )
        self.worker._claim = Mock(
            return_value=SimpleNamespace(
                fence=fence,
                task_kind=resolved.definition.task_kind,
                contract_revision=1,
                task_contract_hash=resolved.definition.contract_hash,
                semantic_registry_hash=executor.semantic_registry.registry_hash,
                target_kind="synthetic_receipt",
            )
        )
        self.worker._start = Mock(return_value=True)
        self.worker._hydrate = Mock(
            return_value=(
                task_input,
                SemanticAuthorizationSnapshot(uuid4(), None, 1, fence.access_scope_id),
                {"tree-count": self.ports.content},
            )
        )
        self.worker._heartbeat = Mock(return_value=True)
        self.worker._attempt_is_live = Mock(return_value=True)
        self.worker._record_run_event = Mock(return_value=True)
        self.worker._retain_cleanup_lease = Mock(return_value=True)

        def persist(*args: Any, **kwargs: Any) -> tuple[str, bool]:
            self.retention_alive_at_settlement = (
                self.worker._cleanup_heartbeat is not None
                and not self.worker._cleanup_heartbeat.done()
            )
            self.settlement_started.set()
            if not self.release_settlement.wait(3):
                raise AssertionError("test did not release settlement")
            if self.settlement_error:
                raise RuntimeError("fictional settlement unavailable")
            self.assertNotEqual(args[1].status, "succeeded")
            return "cancelled", True

        self.worker._persist_result = Mock(side_effect=persist)
        self.foreground = asyncio.create_task(self.worker.run_once())
        await asyncio.wait_for(self.started.wait(), 2)

    async def asyncTearDown(self) -> None:
        self.release_settlement.set()
        self.release.set()
        self.foreground.cancel()
        await asyncio.gather(self.foreground, return_exceptions=True)
        # Every synthetic callback and its owned cycle must actually finish.
        await asyncio.wait_for(
            asyncio.gather(self.worker.wait_for_cleanup(), return_exceptions=True), 3
        )

    async def cancel_foreground(self) -> None:
        self.foreground.cancel()
        await asyncio.wait_for(self.cancel_seen.wait(), 1)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.foreground, 1)

    async def test_resistant_callback_quarantines_then_accounts_and_settles(
        self,
    ) -> None:
        await self.cancel_foreground()
        self.assertTrue(self.worker.cleanup_pending)
        self.assertEqual(self.model_returns, 0)
        self.assertEqual(self.worker._persist_result.call_count, 0)
        pending = await self.worker.run_once()
        self.assertEqual(pending.cycle_status, WorkerCycleStatus.CLEANUP_PENDING)
        self.assertEqual(pending.task_id, self.worker._claim.return_value.fence.task_id)
        self.assertIsNone(pending.task_status)
        self.assertEqual(self.worker._claim.call_count, 1)
        observer = asyncio.create_task(self.worker.wait_for_cleanup())
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
        self.assertTrue(self.worker.cleanup_pending)
        self.release.set()
        self.assertEqual(
            (await asyncio.wait_for(self.worker.wait_for_cleanup(), 2)).task_status,
            "cancelled",
        )
        self.assertEqual(self.model_returns, 1)
        self.assertEqual(len(self.ports.intents), 1)
        self.assertEqual(len(self.ports.usages), 1)
        self.assertEqual(self.ports.usages[0].usage.input_tokens, 11)
        self.assertEqual(self.ports.usages[0].outcome, "cancelled")
        self.assertEqual(self.worker._persist_result.call_count, 1)
        self.assertTrue(self.retention_alive_at_settlement)
        self.assertFalse(self.worker.cleanup_pending)
        self.assertIsNone(self.worker._cleanup_heartbeat)

    async def test_repeated_cancellation_does_not_extend_deadline(self) -> None:
        self.foreground.cancel()
        await asyncio.wait_for(self.cancel_seen.wait(), 1)
        deadline = self.worker._cleanup_deadline
        for _ in range(20):
            self.foreground.cancel()
            await asyncio.sleep(0)
        self.assertEqual(self.worker._cleanup_deadline, deadline)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.foreground, 1)
        self.assertTrue(self.worker.cleanup_pending)
        self.assertEqual(self.worker._claim.call_count, 1)

    async def test_queue_cancellation_returns_pending_without_caller_cancellation(
        self,
    ) -> None:
        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            ReauthorizationResult,
        )

        self.worker._attempt_is_live.return_value = False
        self.worker._heartbeat.return_value = False
        self.worker._attempt_watch_reauthorization = Mock(
            return_value=ReauthorizationResult.CANCEL_REQUESTED
        )
        receipt = await asyncio.wait_for(self.foreground, 1)
        self.assertEqual(receipt.cycle_status, WorkerCycleStatus.CLEANUP_PENDING)
        self.release.set()
        receipt = await asyncio.wait_for(self.worker.wait_for_cleanup(), 2)
        self.assertEqual(receipt.task_status, "cancelled")
        self.assertTrue(self.retention_alive_at_settlement)

    async def test_settlement_failure_is_observable_and_not_retried(self) -> None:
        self.settlement_error = True
        await self.cancel_foreground()
        self.release.set()
        with self.assertRaisesRegex(RuntimeError, "settlement unavailable"):
            await asyncio.wait_for(self.worker.wait_for_cleanup(), 2)
        with self.assertRaisesRegex(RuntimeError, "settlement unavailable"):
            await self.worker.run_once()
        self.assertEqual(self.worker._claim.call_count, 1)
        self.assertEqual(self.worker._persist_result.call_count, 1)
        self.assertIsNone(self.worker._cleanup_heartbeat)
        self.assertEqual(len(self.ports.usages), 1)

    async def test_late_accounting_failure_is_observable(self) -> None:
        from unittest.mock import AsyncMock

        from memoriesql.application.model_accounting import AccountingPersistenceError

        cast(Any, self.ports).append_usage = AsyncMock(
            side_effect=AccountingPersistenceError("fictional ledger failure")
        )
        await self.cancel_foreground()
        self.release.set()
        with self.assertRaisesRegex(AccountingPersistenceError, "late model usage"):
            await asyncio.wait_for(self.worker.wait_for_cleanup(), 2)
        self.assertEqual(len(self.ports.intents), 1)
        self.assertEqual(len(self.ports.usages), 0)
        self.assertEqual(self.worker._persist_result.call_count, 1)
        self.assertFalse(self.worker.cleanup_pending)

    async def test_unfinished_settlement_remains_owned_with_retention(self) -> None:
        self.release_settlement.clear()
        await self.cancel_foreground()
        self.release.set()
        self.assertTrue(await asyncio.to_thread(self.settlement_started.wait, 2))
        self.assertTrue(self.worker.cleanup_pending)
        self.assertFalse(self.worker._cleanup_heartbeat.done())
        self.assertTrue(self.retention_alive_at_settlement)
        self.assertEqual((await self.worker.run_once()).cycle_status, "cleanup_pending")
        self.release_settlement.set()
        self.assertEqual(
            (await asyncio.wait_for(self.worker.wait_for_cleanup(), 2)).task_status,
            "cancelled",
        )


class AccountingCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_started_write_finishes_before_repeated_cancellation_propagates(
        self,
    ) -> None:
        entered = threading.Event()
        release = threading.Event()
        committed: list[str] = []

        def write(value: str) -> None:
            entered.set()
            if not release.wait(2):
                raise AssertionError("test did not release ledger write")
            committed.append(value)

        operation = asyncio.create_task(_finish_database_write(write, "fictional"))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            for _ in range(5):
                operation.cancel()
                await asyncio.sleep(0)
            self.assertFalse(operation.done())
            self.assertEqual(committed, [])
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 1)
        self.assertEqual(committed, ["fictional"])
