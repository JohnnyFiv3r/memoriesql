"""Real recorder calls with fictional writes; no SQL, worker or provider."""

from __future__ import annotations

import asyncio
import contextvars
import time
import unittest
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from types import TracebackType
from typing import Any, Literal, cast
from unittest.mock import patch
from uuid import UUID

from psycopg import Connection

from memoriesql.application.bead_classification import (
    BeadTypeDefinition,
    ClassificationDecision,
    ClassificationPacket,
)
from memoriesql.application.complete_input_execution import (
    EvidenceExecutionWindow,
    EvidenceExposureSlice,
)
from memoriesql.application.evidence_packages import (
    EvidenceInventoryEntry,
    NativeFacts,
    RawEvidenceSlice,
)
from memoriesql.application.local_entity_mentions import MentionExecutionOutput
from memoriesql.application.logical_unit_materialization import SealedPackagePin
from memoriesql.application.source_revisiting import (
    ReadSourceEvidence,
    RevisitingWindow,
    SourceDelivery,
    SourceEvidenceRead,
)
from memoriesql.infrastructure.postgres.complete_input_execution import (
    PostgresCompleteInput,
    PostgresEvidenceExposureRecorder,
    _await_owned_write,
)

ID = UUID(int=1)
HASH = "a" * 64
KINDS = ("classification", "delivery", "received")
WRITE_CONTEXT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fictional_write_context", default="unset"
)


class WriteGate:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.exiting = Event()
        self.exit_release = Event()
        self.exited = Event()
        self.entries = 0
        self.calls: list[tuple[str, tuple[Any, ...], str]] = []
        self.contexts: list[str] = []
        self.write_error: BaseException | None = None
        self.exit_error: BaseException | None = None
        self.factory_error: BaseException | None = None

    def connection(self) -> Connection[Any]:
        if self.factory_error is not None:
            raise self.factory_error
        gate = self

        class FictionalConnection:
            def __enter__(self) -> FictionalConnection:
                gate.entries += 1
                return self

            def __exit__(
                self,
                kind: type[BaseException] | None,
                value: BaseException | None,
                traceback: TracebackType | None,
            ) -> Literal[False]:
                gate.exiting.set()
                if not gate.exit_release.wait(2):
                    raise AssertionError("fictional context exit gate expired")
                gate.exited.set()
                if gate.exit_error is not None:
                    raise gate.exit_error
                return False

        return cast(Connection[Any], FictionalConnection())

    def call(
        self, port: PostgresCompleteInput, query: str, parameters: tuple[Any, ...],
        *, role: str,
    ) -> None:
        self.calls.append((query, parameters, role))
        self.contexts.append(WRITE_CONTEXT.get())
        self.started.set()
        if not self.release.wait(2):
            raise AssertionError("fictional write gate expired")
        if self.write_error is not None:
            raise self.write_error

    def open(self) -> None:
        self.release.set()
        self.exit_release.set()


def invocation(kind: str, gate: WriteGate) -> Coroutine[Any, Any, None]:
    package = SealedPackagePin(
        package_id=ID, sealed_receipt_id=ID, inventory_sha256=HASH,
        required_parts=1, required_characters=1, required_utf8_bytes=1,
    )
    inventory = EvidenceInventoryEntry(
        part_id=ID, ordinal=0, component_key="fictional", component_offset=0,
        parent_component_key=None, kind="text", native=NativeFacts(),
        derivation="identity_utf8", lineage=(RawEvidenceSlice(
            source_range_receipt_id=ID, byte_start=0, byte_end_exclusive=1,
            source_bytes_sha256=HASH,
        ),), content_sha256=HASH, characters=1, utf8_bytes=1,
    )
    slices = (EvidenceExposureSlice(inventory=inventory, start_character=0, content="x"),)
    port = PostgresEvidenceExposureRecorder(
        connection_factory=gate.connection, credential_sha256=HASH, workspace_id=ID,
    )
    if kind == "received":
        return port.record_received(
            request_id=ID, request_payload_hash=HASH,
            window=EvidenceExecutionWindow(
                task_id=ID, attempt_id=ID, package=package, slices=slices, final=True,
            ),
        )
    if kind == "delivery":
        return port.record_delivery(
            request_id=ID, request_payload_hash=HASH,
            delivery=SourceDelivery(
                task_id=ID, attempt_id=ID, package=package,
                window=RevisitingWindow(
                    task_id=ID, attempt_id=ID, package=package, slices=slices, final=True,
                ),
            ),
        )
    if kind != "classification":
        raise AssertionError("unknown recorder method")
    clause = {"text": "Fictional note.", "statement_ids": [str(ID)]}
    proposal = MentionExecutionOutput.model_validate({"annotations": [{
        "bead_id": str(ID), "event_id": str(ID), "source_unit_id": str(ID),
        "bead_version_id": str(ID), "expected_bead_version": 0,
        "bead_type_key": "observation", "bead_type_revision": 1,
        "statements": [{
            "statement_id": str(ID), "statement_kind": "observation",
            "statement_text": "Fictional note.",
            "evidence": [{"source_unit_id": str(ID), "content_hash": HASH}],
            "model_run_ref": "fictional.author",
        }], "render": {"title": clause, "summary": [clause]}, "mentions": [],
    }]})
    return port.record_classification(
        request_id=ID, request_payload_hash=HASH,
        packet=ClassificationPacket(
            task_id=ID, attempt_id=ID, author_run_id="fictional.author",
            proposal=proposal,
            vocabulary=(BeadTypeDefinition(
                key="observation", revision=1, definition="Fictional observation.",
            ),), evidence=(SourceEvidenceRead(
                request=ReadSourceEvidence(
                    package_id=ID, inventory_sha256=HASH, part_ordinal=0, limit=1,
                ), inventory=inventory, content="x", bytes_hex=None,
                sha256=HASH, next_offset=None,
            ),),
        ),
        decision=ClassificationDecision(outcome="no_fit", proposal_alignment="uncertain"),
    )


async def wait_flag(flag: Event) -> None:
    deadline = time.monotonic() + 2
    while not flag.is_set():
        if time.monotonic() >= deadline:
            raise AssertionError("fictional stage not reached")
        await asyncio.sleep(0.002)


def execution_future(task: asyncio.Task[None]) -> asyncio.Future[None]:
    coroutine: Any = task.get_coro()
    while coroutine is not None:
        if getattr(coroutine, "cr_code", None) is _await_owned_write.__code__:
            operation = coroutine.cr_frame.f_locals["_operation"]
            if not isinstance(operation, asyncio.Future) or isinstance(operation, asyncio.Task):
                raise AssertionError("write completion must not be owned by an inner Task")
            return cast(asyncio.Future[None], operation)
        coroutine = getattr(coroutine, "cr_await", None)
    raise AssertionError("actual recorder ownership frame missing")


class CompleteInputWriteOwnership(unittest.TestCase):
    def observe(self, kind: str, gate: WriteGate) -> None:
        self.assertEqual(gate.entries, 1)
        self.assertEqual(len(gate.calls), 1)
        query, parameters, role = gate.calls[0]
        expected = {
            "classification": "record_classification_v1",
            "delivery": "record_source_delivery_v1",
            "received": "record_complete_input_exposure_v1",
        }
        self.assertIn(expected[kind], query)
        self.assertEqual(parameters[:2], (ID, HASH))
        self.assertEqual(role, "memoriesql_worker")
        self.assertTrue(gate.exited.is_set())
        self.assertEqual(gate.contexts, ["fictional.scope"])

    def run_case(
        self, kind: str, *, cancellations: int = 0, lose_future: bool = False,
        write_error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        gate = WriteGate()
        gate.write_error, gate.exit_error = write_error, exit_error

        async def scenario() -> None:
            token = WRITE_CONTEXT.set("fictional.scope")
            task = asyncio.create_task(invocation(kind, gate))
            try:
                await wait_flag(gate.started)
                if lose_future:
                    future = execution_future(task)
                    self.assertTrue(future.cancel())
                    self.assertTrue(future.cancelled())
                for _ in range(cancellations):
                    task.cancel()
                    await asyncio.sleep(0.01)
                # A responsive observer and even a lost bridge are not completion.
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
                gate.release.set()
                await wait_flag(gate.exiting)
                await asyncio.sleep(0.01)
                self.assertFalse(task.done(), "write return must not precede context exit")
                gate.exit_release.set()
                failure = exit_error or write_error
                if failure is not None:
                    with self.assertRaises(type(failure)) as caught:
                        await task
                    self.assertIs(caught.exception, failure)
                elif cancellations:
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertIsNone(await cast(asyncio.Task[object], task))
            finally:
                gate.open()
                await asyncio.gather(task, return_exceptions=True)
                WRITE_CONTEXT.reset(token)

        def call(port: PostgresCompleteInput, query: str, parameters: tuple[Any, ...],
                 *, role: str) -> None:
            gate.call(port, query, parameters, role=role)

        with patch.object(PostgresCompleteInput, "_call", call):
            asyncio.run(scenario())
        self.observe(kind, gate)

    def test_success_waits_for_full_context_and_preserves_contextvars(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind)

    def test_observer_cancellation_drains_each_recorder(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind, cancellations=1)

    def test_repeated_cancellation_does_not_resubmit_or_return_early(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind, cancellations=3)

    def test_cancelled_executor_future_still_waits_for_actual_write(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind, lose_future=True)

    def test_lost_future_and_observer_cancellation_keep_write_error(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind, lose_future=True, cancellations=2,
                              write_error=ValueError("fictional write failure"))

    def test_write_failure_is_not_replaced_with_none(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind, write_error=ValueError("fictional write failure"))

    def test_context_exit_failure_remains_visible_after_cancellation(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind, cancellations=1,
                              exit_error=RuntimeError("fictional context failure"))

    def test_context_exit_error_keeps_existing_with_statement_precedence(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.run_case(kind, write_error=ValueError("fictional write failure"),
                              exit_error=RuntimeError("fictional context failure"))

    def test_runner_shutdown_cancels_all_tasks_without_losing_write(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                gate = WriteGate()
                closing = Event()

                def release_on_close() -> None:
                    if closing.wait(2):
                        time.sleep(0.03)
                    gate.open()

                controller = Thread(target=release_on_close, daemon=True)
                controller.start()

                async def launch() -> asyncio.Task[None]:
                    token = WRITE_CONTEXT.set("fictional.scope")
                    try:
                        task = asyncio.create_task(invocation(kind, gate))
                    finally:
                        WRITE_CONTEXT.reset(token)
                    await wait_flag(gate.started)
                    execution_future(task)  # Actual direct Future, not a new Task.
                    return task

                def call(port: PostgresCompleteInput, query: str,
                         parameters: tuple[Any, ...], *, role: str) -> None:
                    gate.call(port, query, parameters, role=role)

                try:
                    with patch.object(PostgresCompleteInput, "_call", call):
                        with asyncio.Runner() as runner:
                            task = runner.run(launch())
                            closing.set()
                        # Runner.close really cancels ALL its remaining Tasks.
                        self.assertTrue(task.cancelled())
                finally:
                    closing.set()
                    gate.open()
                    controller.join(2)
                self.assertFalse(controller.is_alive())
                self.observe(kind, gate)

    def test_cancellation_while_executor_is_queued_preserves_one_write(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                gate = WriteGate()
                blocker = Event()

                async def scenario() -> None:
                    loop = asyncio.get_running_loop()
                    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
                    busy = loop.run_in_executor(None, blocker.wait, 2)
                    token = WRITE_CONTEXT.set("fictional.scope")
                    task = asyncio.create_task(invocation(kind, gate))
                    WRITE_CONTEXT.reset(token)
                    try:
                        await asyncio.sleep(0.02)
                        self.assertFalse(gate.started.is_set())
                        task.cancel()
                        await asyncio.sleep(0.02)
                        self.assertFalse(task.done())
                        blocker.set()
                        await busy
                        await wait_flag(gate.started)
                        gate.open()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    finally:
                        blocker.set()
                        gate.open()
                        await asyncio.gather(task, return_exceptions=True)

                def call(port: PostgresCompleteInput, query: str,
                         parameters: tuple[Any, ...], *, role: str) -> None:
                    gate.call(port, query, parameters, role=role)

                with patch.object(PostgresCompleteInput, "_call", call):
                    asyncio.run(scenario())
                self.observe(kind, gate)

    def test_connection_factory_failure_is_not_success(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                gate = WriteGate()
                failure = RuntimeError("fictional factory failure")
                gate.factory_error = failure
                with self.assertRaises(RuntimeError) as caught:
                    asyncio.run(invocation(kind, gate))
                self.assertIs(caught.exception, failure)
                self.assertEqual(gate.calls, [])
                self.assertEqual(gate.entries, 0)
