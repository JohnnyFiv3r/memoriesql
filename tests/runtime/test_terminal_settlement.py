"""Terminal failure settles local dependencies without acknowledging persistence."""

from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic_ai import CancellationToken
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

if TYPE_CHECKING:
    from tests.runtime.test_executor import fixture
else:
    from test_executor import fixture

from memoriesql.application.semantic_task_contracts import (
    SemanticAgentContractIdentity,
    SemanticDelegatedRunCorrelation,
    SemanticRootRunCorrelation,
    SemanticRunEventKind,
)
from memoriesql.infrastructure.models.pydanticai_executor import (
    EventSinkError,
    _TreeState,
)


class TerminalSettlement(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.executor, task, deps, self.ports = fixture(conductor=True)
        self.executor.cancellation_cleanup_timeout_seconds = 0.01
        self.rejected: SemanticRunEventKind | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.recorded: list[tuple[SemanticRunEventKind, str]] = []
        owner = self

        class Sink:
            async def append(self, event: Any) -> None:
                if (
                    event.correlation.run_id == "orchard.child"
                    and event.event_kind == owner.rejected
                ):
                    owner.entered.set()
                    # An already-started write owns settlement despite cancellation.
                    while not owner.release.is_set():
                        try:
                            await owner.release.wait()
                        except asyncio.CancelledError:
                            continue
                    raise RuntimeError(
                        "semantic run event lost its durable attempt fence"
                    )
                owner.recorded.append((event.event_kind, event.correlation.run_id))

        self.state = _TreeState(
            executor=self.executor,
            task=task,
            deps=replace(deps, event_sink=Sink()),
            composition=self.executor._composition_provider(),
            usage=RunUsage(),
            usage_limits=UsageLimits(),
            monotonic_deadline_ns=time.monotonic_ns() + 1_000_000_000,
            cancellation_token=CancellationToken(),
            delegate_semaphore=asyncio.Semaphore(1),
        )
        identity = SemanticAgentContractIdentity(
            agent_key="orchard.author",
            input_contract=task.definition.input_contract.reference,
            output_contract=task.definition.output_contract.reference,
        )
        self.root = SemanticRootRunCorrelation(
            run_id="orchard.root",
            run_role="conductor",
            agent_contract=identity,
            model_profile=task.definition.model_profile,
        )
        self.child = SemanticDelegatedRunCorrelation(
            run_id="orchard.child",
            parent_run_id=self.root.run_id,
            agent_contract=identity,
            model_profile=self.root.model_profile,
            delegation_id="orchard.delegation",
        )
        await self.state.add_run(self.root.run_id)
        await self.state.add_run(self.child.run_id)
        await self.executor._emit(
            self.state, SemanticRunEventKind.RUN_STARTED, self.root
        )
        await self.executor._emit(
            self.state, SemanticRunEventKind.DELEGATION_STARTED, self.child
        )
        self.state.open_delegations += 1
        self.state.delegations_closed.clear()
        await self.executor._emit(
            self.state, SemanticRunEventKind.RUN_STARTED, self.child
        )

    async def asyncTearDown(self) -> None:
        self.release.set()
        pending = tuple(self.state.pending_terminal_event_tasks)
        for task in pending:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 1)

    async def finish_tree(self) -> list[Any]:
        calls = (
            self.executor._emit_run_failed(self.state, self.root),
            self.executor._emit_delegation_finished(self.state, self.child),
        )
        return list(
            await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), 0.5)
        )

    def assert_failed_settlement(self) -> None:
        self.assertEqual(self.state.open_delegations, 0)
        self.assertTrue(self.state.delegations_closed.is_set())
        self.assertFalse(self.state.run_terminal_events[self.root.run_id].is_set())
        self.assertNotIn(
            (SemanticRunEventKind.RUN_FAILED, self.root.run_id), self.recorded
        )
        self.assertNotIn(
            (SemanticRunEventKind.DELEGATION_FINISHED, self.child.run_id), self.recorded
        )
        self.assertIsNotNone(self.state.terminal_failure)
        self.assertFalse(self.state.pending_terminal_event_tasks)
        self.assertTrue(self.state.terminal_event_errors)
        self.assertFalse(self.ports.intents)
        self.assertFalse(self.ports.usages)

    async def test_successful_persistence_keeps_child_before_delegation_before_root(
        self,
    ) -> None:
        await self.executor._emit_run_failed(self.state, self.child)
        self.assertEqual(await self.finish_tree(), [None, None])
        self.assertEqual(
            self.recorded[-3:],
            [
                (SemanticRunEventKind.RUN_FAILED, self.child.run_id),
                (SemanticRunEventKind.DELEGATION_FINISHED, self.child.run_id),
                (SemanticRunEventKind.RUN_FAILED, self.root.run_id),
            ],
        )
        self.assertFalse(self.state.terminal_event_errors)

    async def test_rejected_child_run_failure_reaches_all_dependencies(self) -> None:
        self.rejected = SemanticRunEventKind.RUN_FAILED
        with self.assertRaises(EventSinkError):
            await self.executor._emit_run_failed(self.state, self.child)
        self.assertFalse(self.state.run_terminal_events[self.child.run_id].is_set())
        results = await self.finish_tree()
        self.assertTrue(
            all(isinstance(error, EventSinkError) for error in results), results
        )
        self.assert_failed_settlement()

    async def test_rejected_delegation_finish_reaches_root(self) -> None:
        self.rejected = SemanticRunEventKind.DELEGATION_FINISHED
        await self.executor._emit_run_failed(self.state, self.child)
        results = await self.finish_tree()
        self.assertTrue(
            all(isinstance(error, EventSinkError) for error in results), results
        )
        self.assertTrue(self.state.run_terminal_events[self.child.run_id].is_set())
        self.assert_failed_settlement()

    async def test_repeated_caller_cancellation_retains_started_write_and_failure(
        self,
    ) -> None:
        self.rejected = SemanticRunEventKind.RUN_FAILED
        self.release.clear()
        caller = asyncio.create_task(
            self.executor._emit_run_failed(self.state, self.child)
        )
        await asyncio.wait_for(self.entered.wait(), 0.5)
        caller.cancel()
        await asyncio.sleep(0)
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertEqual(len(self.state.pending_terminal_event_tasks), 1)
        self.assertFalse(self.state.run_terminal_events[self.child.run_id].is_set())
        self.assertFalse(self.state.run_completions[self.child.run_id].settled.is_set())
        owned = tuple(self.state.pending_terminal_event_tasks)
        self.release.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(*owned, return_exceptions=True), 0.5
        )
        self.assertIsInstance(outcomes[0], EventSinkError)
        self.assertIn(outcomes[0], self.state.terminal_event_errors)
        results = await self.finish_tree()
        self.assertTrue(
            all(isinstance(error, EventSinkError) for error in results), results
        )
        self.assert_failed_settlement()

    async def test_generic_delegate_sink_failure_cannot_strand_conductor(self) -> None:
        calls = 0

        def respond(messages: Any, info: Any) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                part = ToolCallPart(
                    "delegate_semantic_task",
                    {
                        "request": {
                            "agent_key": "orchard.author",
                            "effort_key": "frontier",
                            "expected_output_contract": task.definition.output_contract.reference.model_dump(
                                mode="json"
                            ),
                            "brief": "Count the fictional trees.",
                            "child_input_json": task.task_input.model_dump_json(),
                            "evidence_reference_ids": ["tree-count"],
                        }
                    },
                )
            else:
                part = ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "typed_output": {"answer": "Four fictional trees."},
                        "used_evidence_refs": ["tree-count"],
                    },
                )
            return ModelResponse(
                parts=[part], usage=RequestUsage(input_tokens=11, output_tokens=7)
            )

        executor, task, deps, ports = fixture(conductor=True, model_callback=respond)
        rejected = []

        class Sink:
            async def append(self, event: Any) -> None:
                if (
                    event.event_kind == SemanticRunEventKind.RUN_SUCCEEDED
                    and isinstance(event.correlation, SemanticDelegatedRunCorrelation)
                ):
                    rejected.append(event)
                    raise RuntimeError("fictional rejected child terminal event")
                await ports.append(event)

        result = await asyncio.wait_for(
            executor.execute(task, replace(deps, event_sink=Sink())), 0.5
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "runtime.event_sink_failed")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(len(ports.intents), 2)
        self.assertEqual(len(ports.usages), 2)
        self.assertIsNone(result.typed_output)
        self.assertFalse(
            any(
                event.event_kind == SemanticRunEventKind.DELEGATION_FINISHED
                for event in ports.events
            )
        )
