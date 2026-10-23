"""Authored output canonical apply would refuse settles as invalid output, named.

Fictional data in a disposable database, composing the complete-input fixture.
A complete unit whose annotation names another event or source unit was refused
by canonical apply as an authority error (complete_input_exposure_required),
which the worker re-raised: the cycle failed, the worker refused every later
cycle, and the attempt waited for the reaper. The executor now refuses such
output before apply, as invalid output with a code that names what is wrong,
and the worker carries on.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ToolCallPart

if TYPE_CHECKING:
    from tests.runtime import test_complete_input_execution as complete
else:
    import test_complete_input_execution as complete


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AuthoredOutputRefusal(unittest.TestCase):
    def setUp(self) -> None:
        self.h = complete.CompleteInputExecution(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.h.setup_execution()

    def run_with(self, change: Any) -> tuple[Any, Any]:
        def respond(messages: Any, info: Any) -> Any:
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

    def outcome(self) -> tuple[Any, ...]:
        return self.h.row(
            "SELECT t.status,a.result_status,a.error_code,a.retry_class FROM "
            "memoriesql.semantic_tasks t JOIN memoriesql.semantic_task_attempts a "
            "USING (tenant_id,task_id) WHERE t.task_id=%s",
            (self.h.activation.execution_task_id,),
        )

    def assert_named_refusal(self, change: Any, code: str) -> None:
        first, second = self.run_with(change)
        self.assertEqual(first.result_status, "invalid_output", first)
        self.assertEqual(
            self.outcome(), ("failed_terminal", "invalid_output", code, "never")
        )
        self.assertEqual(second.cycle_status, "idle", second)
        self.h.assert_no_meaning()

    def test_another_event_is_named_and_the_worker_continues(self) -> None:
        def other_event(annotation: dict[str, Any]) -> None:
            annotation["event_id"] = str(uuid.uuid4())

        self.assert_named_refusal(other_event, "output.annotation.event_mismatch")

    def test_another_source_unit_is_named(self) -> None:
        def other_unit(annotation: dict[str, Any]) -> None:
            annotation["source_unit_id"] = str(uuid.uuid4())
            # Keep the observation self-evidenced, as the typed contract requires.
            annotation["statements"][0]["evidence"][0]["source_unit_id"] = annotation[
                "source_unit_id"
            ]

        self.assert_named_refusal(other_unit, "output.annotation.source_unit_mismatch")

    def test_evidence_with_another_hash_is_named(self) -> None:
        def other_hash(annotation: dict[str, Any]) -> None:
            annotation["statements"][0]["evidence"][0]["content_hash"] = "0" * 64

        self.assert_named_refusal(other_hash, "output.evidence.outside_manifest")


if __name__ == "__main__":
    unittest.main()
