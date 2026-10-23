"""Request headroom for the repair round, at head schema (guard audit P1-f D2).

Fictional data in a disposable database, composing the complete-input fixture.
author-complete-unit revision 2 spends its whole request ceiling (8) on up to
eight inspection windows, so a unit that uses them all gets no repair round.
Revision 8 is revision 2 with one more request: the same unit gets its one
evidence-free repair frame and its corrected output is accepted. Revision 8 is
opt-in: its activation requires a dispatch policy provisioned for it, and
revision-2 activation keeps creating revision-2 tasks.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from memoriesql.application.complete_input_execution import (
    COMPLETE_EXECUTION_TASK,
    REPAIRABLE_EXECUTION_TASK,
    ActivateCompleteInput,
    ActivateRepairableCompleteInput,
    RepairableExecutionInput,
    load_repairable_complete_input_task_registry,
)
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime import test_complete_input_execution as complete
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_complete_input_execution as complete
    from test_postgres_runtime import migrate

REGISTRY8 = load_repairable_complete_input_task_registry(
    BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
).registry_hash
# Two 60,000-character parts: eight 16,384-character inspection windows.
FULL_UNIT = ("x" * 60000, "y" * 60000)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class RepairHeadroom(unittest.TestCase):
    def setUp(self) -> None:
        self.h = complete.CompleteInputExecution(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        h = self.h
        migrate(h.db, expected_current_version=18, target_version=discover_migrations()[-1].version)
        self.revision_two_policy = h.dispatch_policy
        self.revision_eight_policy = uuid.uuid4()
        h.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,workspace_id,"
            "access_scope_id,source_object_id,attestor_principal_id,approved_by_principal_id,"
            "qualification_evidence_sha256,created_at,expires_at,status,8 FROM "
            "memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (self.revision_eight_policy, self.revision_two_policy),
        )
        h.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,"
            "c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,"
            "p.queue_name FROM memoriesql.semantic_worker_claim_policies c JOIN "
            "memoriesql.semantic_task_admission_policies p ON p.task_kind=c.task_kind AND "
            "p.contract_revision=8 WHERE c.principal_id=%s AND c.contract_revision=2",
            (h.worker_principal,),
        )
        self.repairs: list[dict[str, Any]] = []

    def bind(self, *texts: str) -> Any:
        h = self.h
        parts = tuple(h.part(text, i) for i, text in enumerate(texts))
        package = h.create(parts)
        for part in parts:
            h.append(package, part)
        h.seal(package)
        return h.materializer.materialize(h.materialization(package)).task_id

    def activate(self, revision: int, binding: Any, key: str) -> Any:
        h = self.h
        if revision == 8:
            h.activation = h.complete.activate_repairable(
                ActivateRepairableCompleteInput(
                    idempotency_key=key,
                    binding_task_id=binding,
                    dispatch_policy_id=self.revision_eight_policy,
                )
            )
        else:
            h.activation = h.complete.activate(
                ActivateCompleteInput(
                    idempotency_key=key,
                    binding_task_id=binding,
                    dispatch_policy_id=self.revision_two_policy,
                )
            )
        return h.activation

    def task(self) -> tuple[Any, ...]:
        return self.h.row(
            "SELECT t.contract_revision,t.task_contract_hash,t.semantic_registry_hash,"
            "t.input_payload->>'contract_revision',e.execution_contract_revision FROM "
            "memoriesql.semantic_tasks t JOIN memoriesql.complete_input_executions e "
            "ON e.tenant_id=t.tenant_id AND e.execution_task_id=t.task_id WHERE t.task_id=%s",
            (self.h.activation.execution_task_id,),
        )

    def execute(self, revision: int) -> Any:
        """Final windows name another event; a repair frame gets the corrected output."""
        h = self.h

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
                annotation = previous["typed_output"]["annotations"][0]
                annotation["event_id"] = frame["task_input"]["payload"]["event_id"]
                return ModelResponse(
                    parts=[ToolCallPart(info.output_tools[0].name, previous)],
                    usage=RequestUsage(input_tokens=5, output_tokens=5),
                )
            response = h.response(messages, info)
            part = response.parts[0]
            assert isinstance(part, ToolCallPart)
            data: Any = part.args
            if isinstance(data, dict) and "typed_output" in data:
                data["typed_output"]["annotations"][0]["event_id"] = str(uuid.uuid4())
            return response

        with ExitStack() as stack:
            if revision == 8:
                for name, value in (
                    ("COMPLETE_EXECUTION_TASK", REPAIRABLE_EXECUTION_TASK),
                    ("load_complete_input_task_registry", load_repairable_complete_input_task_registry),
                    ("CompleteExecutionInput", RepairableExecutionInput),
                ):
                    stack.enter_context(patch.object(complete, name, value))
            return asyncio.run(h.worker(respond).run_once())

    def count(self, table: str) -> int:
        return int(self.h.row(f"SELECT count(*) FROM memoriesql.{table}")[0])

    def outcome(self) -> tuple[Any, ...]:
        return self.h.row(
            "SELECT t.status,a.result_status,a.error_code FROM memoriesql.semantic_tasks t "
            "JOIN memoriesql.semantic_task_attempts a USING (tenant_id,task_id) WHERE t.task_id=%s",
            (self.h.activation.execution_task_id,),
        )

    def test_revision_eight_is_opt_in_with_its_own_pins(self) -> None:
        h = self.h
        binding = self.bind("Four fictional trees.")
        # A revision-2 dispatch policy cannot activate revision 8.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            h.complete.activate_repairable(
                ActivateRepairableCompleteInput(
                    idempotency_key="pins.refused",
                    binding_task_id=binding,
                    dispatch_policy_id=self.revision_two_policy,
                )
            )
        first = self.activate(8, binding, "pins.v7")
        self.assertEqual(first.contract_version, 7)
        self.assertEqual(
            self.task(),
            (8, REPAIRABLE_EXECUTION_TASK.contract_hash, REGISTRY8, "8", 8),
        )
        replay = self.activate(8, binding, "pins.v7")
        self.assertEqual(replay.execution_task_id, first.execution_task_id)
        self.assertTrue(replay.replayed)
        # Revision-2 activation is unchanged: another unit, revision 2's pins.
        self.activate(2, self.bind("One fictional gate."), "pins.v1")
        self.assertEqual(self.task()[:2], (2, COMPLETE_EXECUTION_TASK.contract_hash))

    def test_a_full_unit_gets_its_repair_round_under_revision_eight(self) -> None:
        self.activate(8, self.bind(*FULL_UNIT), "full.v7")
        result = self.execute(8)
        self.assertEqual(result.task_status, "succeeded", result)
        windows = len(self.h.received)
        self.assertEqual(windows, 8)
        self.assertEqual(len(self.repairs), 1)
        # Nine requests in one attempt: eight windows and the repair, which
        # records no exposure or dispatch receipt.
        self.assertEqual(self.count("model_provider_request_intents"), windows + 1)
        self.assertEqual(self.count("complete_input_dispatch_receipts"), windows)
        self.assertEqual(self.count("semantic_task_attempts"), 1)
        self.assertEqual(self.count("bead_versions"), 1)

    def test_revision_two_has_no_request_left_for_a_full_unit(self) -> None:
        self.activate(2, self.bind(*FULL_UNIT), "full.v1")
        result = self.execute(2)
        self.assertEqual(result.result_status, "invalid_output", result)
        self.assertEqual(
            self.outcome(),
            ("failed_terminal", "invalid_output", "output.annotation.event_mismatch"),
        )
        self.assertEqual((len(self.h.received), len(self.repairs)), (8, 0))
        self.assertEqual(self.count("model_provider_request_intents"), 8)
        self.h.assert_no_meaning()


if __name__ == "__main__":
    unittest.main()
