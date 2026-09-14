"""Fictional complete-input execution; no network model or production trust."""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import psycopg
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from memoriesql.application.complete_input_execution import (
    ActivateCompleteInput,
)
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.semantic_task_contracts import AgentContract
from memoriesql.application.source_revisiting import (
    SOURCE_REVISITING_TASK,
    ActivateSourceRevisiting,
    ReadSourceEvidence,
    load_source_revisiting_task_registry,
)
from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
    IntegratedSemanticWorker,
    ProviderExecutionReadiness,
    ProviderExecutionState,
    SemanticWorkerConfig,
    SemanticWorkerIdentity,
)
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
    LeafAgentSpec,
    ModelProfileBinding,
    PydanticAIAgentRegistry,
    PydanticAIModelProfileRegistry,
    PydanticAISemanticExecutor,
)
from memoriesql.infrastructure.postgres.complete_input_execution import (
    PostgresCompleteInput,
    PostgresEvidenceExposureRecorder,
)
from memoriesql.infrastructure.postgres.migration_runner import migrate

if TYPE_CHECKING:
    from tests.runtime.test_complete_input_execution import CompleteInputExecution
else:
    from test_complete_input_execution import CompleteInputExecution


class SourceRevisiting(CompleteInputExecution):
    activation: Any

    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=18, target_version=20)
        original_policy = self.dispatch_policy
        self.dispatch_policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,workspace_id,access_scope_id,source_object_id,attestor_principal_id,approved_by_principal_id,qualification_evidence_sha256,created_at,expires_at,status,3 FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (self.dispatch_policy, original_policy),
        )
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,p.queue_name FROM memoriesql.semantic_worker_claim_policies c JOIN memoriesql.semantic_task_admission_policies p ON p.task_kind=c.task_kind AND p.contract_revision=3 WHERE c.principal_id=%s AND c.contract_revision=2",
            (self.worker_principal,),
        )
        self.steps: list[dict[str, Any]] = []

    def setup_revisiting(
        self, text: str = "Four fictional trees.", contexts: tuple[Any, ...] = ()
    ) -> None:
        parts = tuple(
            self.part(text[offset : offset + 16384], ordinal).model_copy(
                update={"component_key": "orchard.whole", "component_offset": offset}
            )
            for ordinal, offset in enumerate(range(0, len(text), 16384))
        )
        package = self.create(parts)
        for part in parts:
            self.append(package, part)
        self.seal(package)
        self.bound = self.materializer.materialize(self.materialization(package))
        self.revisiting_command = ActivateSourceRevisiting(
            idempotency_key="orchard.revisit.activate",
            binding_task_id=self.bound.task_id,
            dispatch_policy_id=self.dispatch_policy,
            authorized_context=contexts,
        )
        self.activation = self.complete.activate_revisiting(self.revisiting_command)

    def step(
        self, messages: Any, info: Any, action: str | None = None, read: Any = None
    ) -> ModelResponse:
        prompts = [
            p.content
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        self.assertEqual(len(prompts), 1)
        frame = json.loads(cast(str, prompts[0]))
        self.steps.append(frame)
        if action is None:
            action = "finish" if frame["forward_delivery_complete"] else "next"
        data = {
            "action": action,
            "read": read,
            "notes": "Fictional navigation only.",
            "typed_output": None,
            "used_evidence_refs": [],
        }
        if action == "finish":
            # Reuse the existing fictional output fixture, preserving its exact target pins.
            fake_task = dict(frame["task_input"])
            fake_task["contract_revision"] = 2
            fake_task["payload"] = dict(fake_task["payload"])
            fake_task["payload"].pop("authorized_context")
            fake_task["payload"]["required_execution"] = (
                "trusted_complete_input_exposure_v1"
            )
            response = super().response(
                [
                    ModelRequest(
                        parts=[
                            UserPromptPart(
                                json.dumps(
                                    {
                                        "task_input": fake_task,
                                        "complete_input_window": {"final": True},
                                    }
                                )
                            )
                        ]
                    )
                ],
                info,
            )
            output = cast(ToolCallPart, response.parts[0]).args_as_dict()
            data.update(output)
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, data)],
            usage=RequestUsage(input_tokens=11, output_tokens=7),
        )

    def response(self, messages: Any, info: Any) -> ModelResponse:
        return self.step(messages, info)

    def worker(
        self, callback: Any = None, *, recorder: Any = True
    ) -> IntegratedSemanticWorker:
        task = SOURCE_REVISITING_TASK
        modules = BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
        registry = load_source_revisiting_task_registry(modules)
        agents = PydanticAIAgentRegistry(
            leaf_specs=(
                LeafAgentSpec(
                    agent_key=cast(str, task.leaf_agent_key),
                    input_contract=task.input_contract.reference,
                    output_contract=task.output_contract.reference,
                    input_model_type=task.input_contract.model_type,
                    output_model_type=task.output_contract.model_type,
                    instructions="Use only fictional evidence; preserve uncertainty.",
                    model_profiles=(task.model_profile,),
                ),
            ),
            conductors=(),
            agent_contracts=(
                AgentContract(
                    agent_key=cast(str, task.leaf_agent_key),
                    input_contract=task.input_contract.reference,
                    output_contract=task.output_contract.reference,
                    maximum_effort_key="standard",
                ),
            ),
        )
        profiles = PydanticAIModelProfileRegistry(
            (
                ModelProfileBinding(
                    reference=task.model_profile,
                    model=FunctionModel(callback or self.response),
                    request_target=CHARACTERIZED_TEST_REQUEST_TARGET,
                ),
            )
        )
        executor = PydanticAISemanticExecutor(
            semantic_registry=registry,
            composition_provider=lambda: modules.compose(("core",)),
            agent_registry=agents,
            model_profiles=profiles,
            run_id_factory=lambda _: "orchard.complete.run",
        )
        exposure = (
            PostgresEvidenceExposureRecorder(
                connection_factory=self.connection,
                credential_sha256=self.attestor_secret,
                workspace_id=self.workspace,
            )
            if recorder is True
            else recorder
        )
        return IntegratedSemanticWorker(
            connection_factory=self.connection,
            identity=SemanticWorkerIdentity(
                credential_sha256=self.worker_secret,
                workspace_id=self.workspace,
                worker_id="orchard.worker",
                worker_instance_id="orchard.instance",
            ),
            semantic_registry=registry,
            composition_provider=lambda: modules.compose(("core",)),
            executor=cast(Any, executor),
            readiness_provider=lambda: ProviderExecutionReadiness(
                ProviderExecutionState.CONFIGURED, "fictional.configured"
            ),
            config=SemanticWorkerConfig(heartbeat_interval_seconds=10),
            exposure_recorder=exposure,
        )

    def test_complete_context_applies_with_unique_exposure(self) -> None:
        self.setup_revisiting()
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(len(self.steps), 1)
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (21,),
        )
        self.assertEqual(
            self.row(
                "SELECT delivery_version,delivered_units FROM memoriesql.complete_input_dispatch_receipts"
            ),
            (2, 21),
        )

    def selection(self, **updates: Any) -> dict[str, Any]:
        return ReadSourceEvidence(
            package_id=self.activation.package.package_id,
            inventory_sha256=self.activation.package.inventory_sha256,
            part_ordinal=0,
            **updates,
        ).model_dump(mode="json")

    def test_revisit_early_normalized_and_raw_after_forward_coverage(self) -> None:
        text = "Early α🌳 evidence. " + "Later fictional evidence. " * 4000
        self.setup_revisiting(text)

        def author(messages: Any, info: Any) -> ModelResponse:
            index = len(self.steps)
            if index == 0:
                return self.step(messages, info, "next")
            if index == 1:
                return self.step(messages, info, "read", self.selection(limit=19))
            if index == 2:
                return self.step(
                    messages,
                    info,
                    "read",
                    self.selection(representation="raw", lineage_ordinal=0, limit=21),
                )
            if index == 3:
                return self.step(messages, info, "continue")
            return self.step(messages, info, "finish")

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(len(self.steps), 5)
        self.assertTrue(self.steps[1]["forward_delivery_complete"])
        normalized = self.steps[2]["source_delivery"]["read"]
        raw = self.steps[3]["source_delivery"]["read"]
        self.assertEqual(normalized["content"], text[:19])
        self.assertEqual(bytes.fromhex(raw["bytes_hex"]), text.encode()[:21])
        self.assertEqual(normalized["inventory"], raw["inventory"])
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (len(text),),
        )
        self.assertEqual(
            self.row(
                "SELECT sum(delivered_units),count(*) FROM memoriesql.complete_input_dispatch_receipts"
            ),
            (len(text) + 40, 5),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (5,),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (5,)
        )

    def test_missing_mandatory_exposure_and_last_interval_only_cannot_apply(
        self,
    ) -> None:
        self.setup_revisiting("Four fictional trees. " * 5000)
        real = PostgresEvidenceExposureRecorder(
            connection_factory=self.connection,
            credential_sha256=self.attestor_secret,
            workspace_id=self.workspace,
        )

        class OmitFirst:
            async def record_received(self, **kwargs: Any) -> None:
                raise AssertionError("legacy exposure forbidden")

            async def record_delivery(self, **kwargs: Any) -> None:
                delivery = kwargs["delivery"]
                if delivery.window is not None and delivery.window.final:
                    await real.record_delivery(**kwargs)

        result = asyncio.run(self.worker(recorder=OmitFirst()).run_once())
        self.assertEqual(result.task_status, "failed_terminal", result)
        self.assertGreater(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            )[0],
            0,
        )
        self.assert_no_meaning()

    def test_tampering_cross_attempt_and_receipt_replay(self) -> None:
        self.setup_revisiting()
        real = PostgresEvidenceExposureRecorder(
            connection_factory=self.connection,
            credential_sha256=self.attestor_secret,
            workspace_id=self.workspace,
        )
        owner = self

        class Checked:
            async def record_received(self, **kwargs: Any) -> None:
                raise AssertionError("legacy exposure forbidden")

            async def record_delivery(self, **kwargs: Any) -> None:
                delivery = kwargs["delivery"]
                bad_slice = delivery.window.slices[0].model_copy(
                    update={"content": "forged"}
                )
                bad_window = delivery.window.model_copy(update={"slices": (bad_slice,)})
                for bad in (
                    delivery.model_copy(update={"window": bad_window}),
                    delivery.model_copy(
                        update={"attempt_id": uuid.uuid4(), "window": None}
                    ),
                ):
                    with owner.assertRaises(psycopg.Error):
                        await real.record_delivery(**(kwargs | {"delivery": bad}))
                await real.record_delivery(**kwargs)
                await real.record_delivery(**kwargs)
                with owner.assertRaises(psycopg.Error):
                    await real.record_delivery(
                        **(
                            kwargs
                            | {"delivery": delivery.model_copy(update={"window": None})}
                        )
                    )

        result = asyncio.run(self.worker(recorder=Checked()).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.complete_input_dispatch_receipts"
            ),
            (1,),
        )

    def test_optional_context_is_readable_without_mandatory_context_coverage(
        self,
    ) -> None:
        from memoriesql.application.logical_unit_materialization import SealedPackagePin
        from memoriesql.application.source_revisiting import AuthorizedContextPin

        context = self.package(
            "Optional neighboring context. " * 800,
            occurrence_key="orchard.context",
            seal=False,
        )
        sealed = self.seal(context)
        status = self.status(context)
        pin = SealedPackagePin(
            package_id=context.package_id,
            sealed_receipt_id=sealed.idempotency_receipt_id,
            inventory_sha256=status.inventory_sha256,
            required_parts=status.appended_parts,
            required_characters=status.appended_characters,
            required_utf8_bytes=status.appended_utf8_bytes,
        )
        self.setup_revisiting(
            contexts=(
                AuthorizedContextPin(
                    package=pin, source_object_id=self.source, source_schema_version=1
                ),
            )
        )
        read = ReadSourceEvidence(
            package_id=pin.package_id,
            inventory_sha256=pin.inventory_sha256,
            part_ordinal=0,
            limit=8,
        ).model_dump(mode="json")

        def author(messages: Any, info: Any) -> ModelResponse:
            return (
                self.step(messages, info, "read", read)
                if not self.steps
                else self.step(messages, info, "finish")
            )

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.steps[1]["source_delivery"]["read"]["content"], "Optional"
        )
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character),count(DISTINCT package_id) FROM memoriesql.complete_input_exposures"
            ),
            (21, 1),
        )

    def test_scope_escape_and_stale_inventory_fail_closed(self) -> None:
        self.setup_revisiting()

        def author(messages: Any, info: Any) -> ModelResponse:
            return self.step(
                messages,
                info,
                "read",
                self.selection() | {"package_id": str(uuid.uuid4())},
            )

        result = asyncio.run(self.worker(author).run_once())
        self.assertNotEqual(result.task_status, "succeeded")
        self.assertEqual(len(self.steps), 1)
        self.assert_no_meaning()

    def test_revision2_policy_cannot_qualify_revisiting(self) -> None:
        package = self.package()
        bound = self.materializer.materialize(self.materialization(package))
        old_policy = self.row(
            "SELECT dispatch_policy_id FROM memoriesql.complete_input_dispatch_policies WHERE execution_contract_revision=2"
        )[0]
        with self.assertRaises(psycopg.Error):
            self.complete.activate_revisiting(
                ActivateSourceRevisiting(
                    idempotency_key="old.policy",
                    binding_task_id=bound.task_id,
                    dispatch_policy_id=old_policy,
                )
            )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.complete_input_executions"), (0,)
        )
        self.assert_no_meaning()

    def test_activation_replay_conflict_and_old_activation_rejection(self) -> None:
        self.setup_revisiting()
        replay = self.complete.activate_revisiting(self.revisiting_command)
        duplicate = self.complete.activate_revisiting(
            self.revisiting_command.model_copy(update={"idempotency_key": "new.key"})
        )
        self.assertTrue(replay.replayed and duplicate.replayed)
        self.assertEqual(replay.execution_task_id, duplicate.execution_task_id)
        self.assertNotEqual(
            replay.idempotency_receipt_id, duplicate.idempotency_receipt_id
        )
        with self.assertRaises(psycopg.Error):
            self.complete.activate(
                ActivateCompleteInput(
                    idempotency_key="legacy.attempt",
                    binding_task_id=self.bound.task_id,
                    dispatch_policy_id=self.dispatch_policy,
                )
            )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.complete_input_executions"), (1,)
        )

    def test_budget_exhaustion_after_coverage_keeps_thin_bead(self) -> None:
        self.setup_revisiting()
        result = asyncio.run(
            self.worker(lambda m, i: self.step(m, i, "continue")).run_once()
        )
        self.assertEqual(result.result_status, "budget_exhausted", result)
        self.assertEqual(len(self.steps), 12)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (12,),
        )
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (21,),
        )
        self.assert_no_meaning()

    def test_revocation_during_revisit_response_preserves_accounting(self) -> None:
        self.setup_revisiting()

        def author(messages: Any, info: Any) -> ModelResponse:
            if not self.steps:
                return self.step(messages, info, "read", self.selection(limit=5))
            self.db.execute(
                "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
                (self.policy,),
            )
            return self.step(messages, info, "finish")

        result = asyncio.run(self.worker(author).run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        self.assert_no_meaning()

    def test_cancel_after_complete_coverage_before_reread(self) -> None:
        self.setup_revisiting()

        def author(messages: Any, info: Any) -> ModelResponse:
            with self.db.transaction():
                self.begin()
                self.db.execute(
                    "SELECT memoriesql.cancel_semantic_task(%s,%s,'fictional.cancel',clock_timestamp())",
                    (self.tenant, self.activation.execution_task_id),
                )
            return self.step(messages, info, "read", self.selection())

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "cancelled", result)
        self.assertEqual(len(self.steps), 1)
        self.assert_no_meaning()

    def test_owner_revocation_at_canonical_apply(self) -> None:
        self.setup_revisiting()
        worker = self.worker()
        original = worker._persist_result

        def revoke(claimed: Any, result: Any, sink: Any, **kwargs: Any) -> Any:
            self.db.execute(
                "UPDATE memoriesql.complete_input_dispatch_policies SET status='revoked' WHERE dispatch_policy_id=%s",
                (self.dispatch_policy,),
            )
            return original(claimed, result, sink, **kwargs)

        worker._persist_result = revoke  # type: ignore[method-assign]
        result = asyncio.run(worker.run_once())
        self.assertEqual(result.task_status, "policy_paused", result)
        self.assert_no_meaning()

    def test_complete_context_40000_characters_crosses_storage_parts(self) -> None:
        self.setup_revisiting("x" * 40000)
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(len(self.steps), 1)
        window = self.steps[0]["source_delivery"]["window"]
        self.assertEqual("".join(s["content"] for s in window["slices"]), "x" * 40000)
        self.assertEqual(len({s["inventory"]["part_id"] for s in window["slices"]}), 3)

    def test_unicode_many_part_delivery_and_reread_measurement(self) -> None:
        import time
        import tracemalloc
        from unittest.mock import patch

        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
        )

        parts = tuple(
            self.part("α🌳" * 500, i).model_copy(
                update={"component_key": "whole", "component_offset": i * 1000}
            )
            for i in range(64)
        )
        package = self.create(parts)
        for part in parts:
            self.append(package, part)
        self.seal(package)
        self.bound = self.materializer.materialize(self.materialization(package))
        self.activation = self.complete.activate_revisiting(
            ActivateSourceRevisiting(
                idempotency_key="many.parts",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
            )
        )
        reads = {"batches": 0, "rereads": 0}
        original = PostgresSemanticTaskQueue.read_complete_evidence
        original_revisit = PostgresSemanticTaskQueue.read_source_evidence

        def batch(port: Any, *args: Any, **kwargs: Any) -> Any:
            reads["batches"] += 1
            return original(port, *args, **kwargs)

        def reread(port: Any, *args: Any, **kwargs: Any) -> Any:
            reads["rereads"] += 1
            return original_revisit(port, *args, **kwargs)

        revisited = False

        def author(messages: Any, info: Any) -> ModelResponse:
            nonlocal revisited
            frame = json.loads(
                cast(
                    str,
                    next(
                        p.content
                        for m in messages
                        if isinstance(m, ModelRequest)
                        for p in m.parts
                        if isinstance(p, UserPromptPart)
                    ),
                )
            )
            if frame["forward_delivery_complete"] and not revisited:
                revisited = True
                return self.step(
                    messages, info, "read", self.selection(offset=1, limit=3)
                )
            return self.step(messages, info)

        tracemalloc.start()
        started = time.perf_counter()
        with (
            patch.object(PostgresSemanticTaskQueue, "read_complete_evidence", batch),
            patch.object(PostgresSemanticTaskQueue, "read_source_evidence", reread),
        ):
            result = asyncio.run(self.worker(author).run_once())
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(reads, {"batches": 8, "rereads": 1})
        supplied = "".join(
            s["content"]
            for frame in self.steps
            if frame["source_delivery"]["window"]
            for s in frame["source_delivery"]["window"]["slices"]
        )
        self.assertEqual(supplied, "α🌳" * 32000)
        self.assertEqual(self.steps[-1]["source_delivery"]["read"]["content"], "🌳α🌳")
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (64000,),
        )
        print(
            json.dumps(
                {
                    "revisiting_benchmark": True,
                    "characters": 64000,
                    "utf8_bytes": 192000,
                    "parts": 64,
                    "reader_batches": reads["batches"],
                    "typed_rereads": reads["rereads"],
                    "fictional_model_interactions": len(self.steps),
                    "real_provider_calls": 0,
                    "elapsed_seconds": round(elapsed, 3),
                    "peak_python_bytes": peak,
                }
            )
        )

    def test_expired_worker_authority_after_confirmed_wait(self) -> None:
        import time
        from concurrent.futures import ThreadPoolExecutor

        self.setup_revisiting()
        claimed = self.claim()
        expires = datetime.now(UTC) + timedelta(milliseconds=300)
        self.db.execute(
            "UPDATE memoriesql.authentication_credentials SET expires_at=%s WHERE secret_sha256=%s",
            (expires, self.worker_secret),
        )
        with self.connection() as blocker, ThreadPoolExecutor(1) as pool:
            with blocker.transaction():
                blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":semantic_outcome_authority:",),
                )
                waiting = pool.submit(
                    self.worker()._transaction,
                    lambda queue, _: queue.read_source_evidence(
                        claimed.fence,
                        ReadSourceEvidence.model_validate(self.selection()),
                    ),
                )
                until = time.monotonic() + 0.2
                saw_wait = False
                while time.monotonic() < until:
                    saw_wait = bool(
                        self.row(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))"
                        )[0]
                    )
                    if saw_wait:
                        break
                    time.sleep(0.005)
                self.assertTrue(saw_wait, "reader must really wait")
                time.sleep(max(0, (expires - datetime.now(UTC)).total_seconds()) + 0.02)
            with self.assertRaises((PermissionError, psycopg.Error)):
                waiting.result(timeout=3)
        self.assert_no_meaning()

    def test_stale_selection_and_missing_raw_are_explicit(self) -> None:
        self.setup_revisiting("α🌳 across raw bytes")
        claimed = self.claim()
        for updates in (
            {"inventory_sha256": "0" * 64},
            {"package_id": uuid.uuid4()},
            {"offset": 999},
        ):
            request = ReadSourceEvidence.model_validate(self.selection() | updates)
            with self.assertRaises((PermissionError, psycopg.Error)):
                self.worker()._transaction(
                    lambda queue, _: queue.read_source_evidence(claimed.fence, request)
                )
        # Administrative corruption in this disposable fixture only. Restore the guard before reading.
        with self.db.transaction():
            self.db.execute(
                "ALTER TABLE memoriesql.captured_source_ranges DISABLE TRIGGER USER"
            )
            self.db.execute("DELETE FROM memoriesql.captured_source_ranges")
            self.db.execute(
                "ALTER TABLE memoriesql.captured_source_ranges ENABLE TRIGGER USER"
            )
        request = ReadSourceEvidence.model_validate(
            self.selection(representation="raw", lineage_ordinal=0, limit=1)
        )
        with self.assertRaises(psycopg.Error):
            self.worker()._transaction(
                lambda queue, _: queue.read_source_evidence(claimed.fence, request)
            )
        self.assert_no_meaning()

    def test_direct_apply_without_exposure_is_denied(self) -> None:
        self.setup_revisiting()
        claimed = self.claim()
        # A fictional model output is created without running an executor.
        from types import SimpleNamespace

        if TYPE_CHECKING:
            from tests.runtime.test_immutable_observations import ImmutableObservations
        else:
            from test_immutable_observations import ImmutableObservations
        result = ImmutableObservations.result_for(
            cast(Any, self),
            SimpleNamespace(
                source_unit_ids=(self.bound.source_unit_id,),
                bead_ids=(self.bound.bead_id,),
                event_id=self.bound.event_id,
            ),
            SimpleNamespace(
                units=(
                    SimpleNamespace(
                        content_hash=self.bound.bound_package.inventory_sha256
                    ),
                )
            ),
            claimed,
            SOURCE_REVISITING_TASK,
        )
        with (
            self.assertRaisesRegex(psycopg.Error, "exposure_required"),
            self.db.transaction(),
        ):
            self.worker_queue().record_canonical_result(
                claimed.fence, result, recorded_at=datetime.now(UTC)
            )
        self.assert_no_meaning()

    def test_restart_requires_new_attempt_exposure(self) -> None:
        self.setup_revisiting()
        worker = self.worker()
        saved: dict[str, Any] = {}

        def crash(claimed: Any, result: Any, sink: Any, **kwargs: Any) -> Any:
            saved.update(claimed=claimed, result=result)
            raise RuntimeError("fictional crash before canonical apply")

        worker._persist_result = crash  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "fictional crash"):
            asyncio.run(worker.run_once())
        old = saved["claimed"]
        self.assertGreater(
            self.row("SELECT count(*) FROM memoriesql.complete_input_exposures")[0], 0
        )
        self.assert_no_meaning()
        # Test-only clock fixture. Restore the guard before recovery operations.
        with self.db.transaction():
            self.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts DISABLE TRIGGER semantic_task_attempts_protect_state"
            )
            self.db.execute(
                "UPDATE memoriesql.semantic_tasks SET heartbeat_at=clock_timestamp()-interval '2 seconds',lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s",
                (old.fence.task_id,),
            )
            self.db.execute(
                "UPDATE memoriesql.semantic_task_attempts SET claimed_at=clock_timestamp()-interval '10 seconds',started_at=clock_timestamp()-interval '5 seconds',heartbeat_at=clock_timestamp()-interval '2 seconds',lease_expires_at=clock_timestamp()-interval '1 second' WHERE attempt_id=%s",
                (old.fence.attempt_id,),
            )
            self.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts ENABLE TRIGGER semantic_task_attempts_protect_state"
            )
        with self.db.transaction():
            self.assertEqual(
                self.worker_queue().reap_expired(
                    worker_id="orchard.reaper", limit=1, reaped_at=datetime.now(UTC)
                ),
                1,
            )
        self.db.execute(
            "UPDATE memoriesql.semantic_tasks SET available_at=clock_timestamp() WHERE task_id=%s",
            (old.fence.task_id,),
        )
        with self.assertRaises(psycopg.Error), self.db.transaction():
            self.worker_queue().record_canonical_result(
                old.fence, saved["result"], recorded_at=datetime.now(UTC)
            )

        class NoNewExposure:
            async def record_delivery(self, **kwargs: Any) -> None:
                pass

            async def record_received(self, **kwargs: Any) -> None:
                pass

        result = asyncio.run(self.worker(recorder=NoNewExposure()).run_once())
        self.assertEqual(result.task_status, "failed_terminal")
        self.assertNotEqual(result.attempt_id, old.fence.attempt_id)
        self.assert_no_meaning()

    def test_owned_exposure_write_drains_after_caller_cancellation(self) -> None:
        import threading
        from unittest.mock import patch

        self.setup_revisiting()
        started = threading.Event()
        release = threading.Event()
        original = PostgresCompleteInput._call

        def blocked(port: Any, query: str, parameters: Any, **kwargs: Any) -> Any:
            if "record_source_delivery" in query:
                started.set()
                if not release.wait(10):
                    raise AssertionError("fictional write gate timed out")
            return original(port, query, parameters, **kwargs)

        async def scenario() -> None:
            worker = self.worker()
            worker._config = SemanticWorkerConfig(
                cancellation_return_timeout_seconds=0.01,
                heartbeat_interval_seconds=0.1,
                cancellation_poll_interval_seconds=0.01,
            )
            foreground = asyncio.create_task(worker.run_once())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                foreground.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await foreground
                self.assertTrue(worker.cleanup_pending)
                self.assertEqual(
                    (await worker.run_once()).cycle_status, "cleanup_pending"
                )
            finally:
                release.set()
            settled = await worker.wait_for_cleanup()
            self.assertIsNotNone(settled)
            self.assertEqual(settled.task_status if settled else None, "cancelled")
            self.assertFalse(worker.cleanup_pending)

        with patch.object(PostgresCompleteInput, "_call", blocked):
            asyncio.run(scenario())
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,)
        )
        self.assert_no_meaning()

    def test_repeated_delivery_budget_is_separate_from_unique_coverage(self) -> None:
        self.setup_revisiting("x" * 131072)

        def author(messages: Any, info: Any) -> ModelResponse:
            if not self.steps:
                return self.step(messages, info, "next")
            return self.step(messages, info, "read", self.selection(limit=16384))

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.result_status, "budget_exhausted", result)
        self.assertEqual(len(self.steps), 10)
        self.assertEqual(
            self.row(
                "SELECT sum(delivered_units) FROM memoriesql.complete_input_dispatch_receipts"
            ),
            (262144,),
        )
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (131072,),
        )
        self.assert_no_meaning()

    def test_transformed_normalized_content_keeps_exact_raw_derivation(self) -> None:
        normalized = "Four fictional trees."
        raw = '{"body":"Four fictional trees.","role":"unknown"}'
        part = self.part(raw, chunk_size=3).model_copy(
            update={
                "content": normalized,
                "content_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
                "derivation": "producer_normalized",
            }
        )
        package = self.create((part,))
        self.append(package, part)
        self.seal(package)
        self.bound = self.materializer.materialize(self.materialization(package))
        self.activation = self.complete.activate_revisiting(
            ActivateSourceRevisiting(
                idempotency_key="transform",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
            )
        )

        def author(messages: Any, info: Any) -> ModelResponse:
            if not self.steps:
                return self.step(
                    messages,
                    info,
                    "read",
                    self.selection(
                        representation="raw", lineage_ordinal=0, limit=32768
                    ),
                )
            return self.step(messages, info, "finish")

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        initial = self.steps[0]["source_delivery"]["window"]["slices"][0]
        revisited = self.steps[1]["source_delivery"]["read"]
        self.assertEqual(initial["content"], normalized)
        self.assertEqual(bytes.fromhex(revisited["bytes_hex"]), raw.encode())
        self.assertEqual(initial["inventory"]["derivation"], "producer_normalized")
        self.assertEqual(initial["inventory"], revisited["inventory"])
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (21,),
        )


class LegacyExecutionAtSchema20(CompleteInputExecution):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=18, target_version=20)

    def test_revision2_contract_stays_forward_only_and_keeps_receipts(self) -> None:
        self.setup_execution("x" * 40000)
        original = self.row(
            "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (self.bound.task_id,),
        )[0]
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(len(self.received), 3)
        self.assertEqual(
            self.row(
                "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
                (self.bound.task_id,),
            )[0],
            original,
        )
        self.assertEqual(
            self.complete.activate(self.activation_command).execution_task_id,
            self.activation.execution_task_id,
        )


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    return unittest.TestSuite(
        [
            SourceRevisiting(name)
            for name in SourceRevisiting.__dict__
            if name.startswith("test_")
        ]
        + [
            LegacyExecutionAtSchema20(
                "test_revision2_contract_stays_forward_only_and_keeps_receipts"
            )
        ]
    )
