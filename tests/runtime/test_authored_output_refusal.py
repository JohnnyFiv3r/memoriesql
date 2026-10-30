"""Authored output canonical apply would refuse: one repair round, then named.

Fictional data in a disposable database, composing the complete-input fixture.
A complete unit whose annotation names another event or source unit was refused
by canonical apply as an authority error (complete_input_exposure_required),
which the worker re-raised: the cycle failed, the worker refused every later
cycle, and the attempt waited for the reaper. The executor now checks the
output first. The model gets one evidence-free repair frame inside the same
attempt, which records no exposure or dispatch receipt; output still refused
settles as invalid output with a code that names what is wrong, and the worker
carries on.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

if TYPE_CHECKING:
    from tests.runtime import test_complete_input_execution as complete
    from tests.runtime import test_source_revisiting as revisiting
else:
    import test_complete_input_execution as complete
    import test_source_revisiting as revisiting


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AuthoredOutputRefusal(unittest.TestCase):
    def setUp(self) -> None:
        self.h = complete.CompleteInputExecution(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.h.setup_execution()
        self.repairs: list[dict[str, Any]] = []

    def run_with(self, change: Any, repair: Any) -> tuple[Any, Any]:
        """`change` corrupts the final output; `repair` answers the repair frame."""

        def respond(messages: Any, info: Any) -> Any:
            prompts = [
                p.content
                for m in messages
                if isinstance(m, ModelRequest)
                for p in m.parts
                if isinstance(p, UserPromptPart)
            ]
            frame = json.loads(str(prompts[0]))
            if "repair_frame" in frame:
                self.repairs.append(frame)
                previous = frame["previous_output"]
                repair(previous["typed_output"]["annotations"][0])
                return ModelResponse(
                    parts=[ToolCallPart(info.output_tools[0].name, previous)],
                    usage=RequestUsage(input_tokens=5, output_tokens=5),
                )
            response = self.h.response(messages, info)
            part = response.parts[0]
            assert isinstance(part, ToolCallPart)
            data: Any = part.args
            if isinstance(data, dict) and "typed_output" in data:
                change(data["typed_output"]["annotations"][0])
            return response

        worker = self.h.worker(respond)
        first = asyncio.run(worker.run_once())
        # The worker is not wedged: its next cycle runs normally.
        second = asyncio.run(worker.run_once())
        return first, second

    def count(self, table: str) -> int:
        row = self.h.row(f"SELECT count(*) FROM memoriesql.{table}")
        return int(row[0])

    def outcome(self) -> tuple[Any, ...]:
        return self.h.row(
            "SELECT t.status,a.result_status,a.error_code,a.retry_class FROM "
            "memoriesql.semantic_tasks t JOIN memoriesql.semantic_task_attempts a "
            "USING (tenant_id,task_id) WHERE t.task_id=%s",
            (self.h.activation.execution_task_id,),
        )

    def assert_repair_frame_delivered_nothing(self) -> None:
        self.assertEqual(len(self.repairs), 1)
        frame = self.repairs[0]
        self.assertEqual(
            set(frame),
            {"repair_frame", "instruction", "task_input", "previous_output", "refusal_codes"},
        )
        # One intent per window plus the repair; exposure and dispatch receipts
        # only for the windows.
        windows = len(self.h.received)
        self.assertEqual(self.count("model_provider_request_intents"), windows + 1)
        self.assertEqual(self.count("complete_input_dispatch_receipts"), windows)
        exposed = self.h.row(
            "SELECT count(DISTINCT request_id) FROM memoriesql.complete_input_exposures"
        )
        self.assertEqual(exposed, (windows,))

    def assert_named_refusal(self, change: Any, code: str) -> None:
        first, second = self.run_with(change, repair=lambda annotation: None)
        self.assertEqual(first.result_status, "invalid_output", first)
        self.assertEqual(
            self.outcome(), ("failed_terminal", "invalid_output", code, "never")
        )
        self.assertEqual(second.cycle_status, "idle", second)
        self.assert_repair_frame_delivered_nothing()
        self.h.assert_no_meaning()

    def test_another_event_is_named_after_a_failed_repair(self) -> None:
        def other_event(annotation: dict[str, Any]) -> None:
            annotation["event_id"] = str(uuid.uuid4())

        self.assert_named_refusal(other_event, "output.annotation.event_mismatch")

    def test_another_evidence_hash_is_named_after_a_failed_repair(self) -> None:
        def other_hash(annotation: dict[str, Any]) -> None:
            annotation["statements"][0]["evidence"][0]["content_hash"] = "0" * 64

        self.assert_named_refusal(other_hash, "output.evidence.outside_manifest")

    def test_a_repaired_source_unit_is_accepted_without_new_exposure(self) -> None:
        original: dict[str, str] = {}
        stray = str(uuid.uuid4())

        def other_unit(annotation: dict[str, Any]) -> None:
            # Keep the observation self-evidenced, as the typed contract requires.
            original["unit"] = annotation["source_unit_id"]
            annotation["source_unit_id"] = stray
            annotation["statements"][0]["evidence"][0]["source_unit_id"] = stray

        def restore(annotation: dict[str, Any]) -> None:
            annotation["source_unit_id"] = original["unit"]
            annotation["statements"][0]["evidence"][0]["source_unit_id"] = original["unit"]

        first, second = self.run_with(other_unit, repair=restore)
        self.assertEqual(first.task_status, "succeeded", first)
        self.assertIn(
            "annotation.source_unit_mismatch", " ".join(self.repairs[0]["refusal_codes"])
        )
        self.assert_repair_frame_delivered_nothing()
        self.assertEqual(
            self.h.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,)
        )
        self.assertEqual(second.cycle_status, "idle", second)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class RevisitingRepair(unittest.TestCase):
    """The revisiting family repairs the same way and records no source delivery."""

    def setUp(self) -> None:
        self.h = revisiting.SourceRevisiting(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.h.setup_revisiting()

    def test_a_repaired_event_pin_is_accepted_without_new_delivery(self) -> None:
        repairs: list[dict[str, Any]] = []
        event: dict[str, str] = {}

        def respond(messages: Any, info: Any) -> Any:
            prompts = [
                p.content
                for m in messages
                if isinstance(m, ModelRequest)
                for p in m.parts
                if isinstance(p, UserPromptPart)
            ]
            frame = json.loads(str(prompts[0]))
            if "repair_frame" in frame:
                repairs.append(frame)
                previous = frame["previous_output"]
                previous["typed_output"]["annotations"][0]["event_id"] = event["id"]
                return ModelResponse(
                    parts=[ToolCallPart(info.output_tools[0].name, previous)],
                    usage=RequestUsage(input_tokens=5, output_tokens=5),
                )
            response = self.h.step(messages, info)
            part = response.parts[0]
            assert isinstance(part, ToolCallPart)
            data: Any = part.args
            if data.get("action") == "finish":
                annotation = data["typed_output"]["annotations"][0]
                event["id"] = annotation["event_id"]
                annotation["event_id"] = str(uuid.uuid4())
            return response

        receipt = asyncio.run(self.h.worker(respond).run_once())
        self.assertEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual(len(repairs), 1)
        self.assertIn("annotation.event_mismatch", " ".join(repairs[0]["refusal_codes"]))
        self.assertNotIn("source_delivery", repairs[0])
        steps = len(self.h.steps)
        self.assertEqual(
            self.h.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (steps + 1,),
        )
        self.assertEqual(
            self.h.row("SELECT count(*) FROM memoriesql.complete_input_dispatch_receipts"),
            (steps,),
        )
        self.assertEqual(
            self.h.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,)
        )


if __name__ == "__main__":
    unittest.main()
