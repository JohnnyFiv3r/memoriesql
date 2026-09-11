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
from psycopg.conninfo import make_conninfo
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from memoriesql.application.complete_input_execution import (
    COMPLETE_EXECUTION_TASK,
    ActivateCompleteInput,
    CompleteExecutionInput,
    load_complete_input_task_registry,
)
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.semantic_task_contracts import AgentContract
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
    from tests.runtime.test_logical_unit_materialization import (
        LegacyAtSchema17,
        LogicalUnitMaterialization,
    )
else:
    from test_logical_unit_materialization import (
        LegacyAtSchema17,
        LogicalUnitMaterialization,
    )


class CompleteInputExecution(LogicalUnitMaterialization):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=17, target_version=18)
        self.db.execute(
            "INSERT INTO memoriesql.role_capabilities VALUES('background_service','source.raw.read') ON CONFLICT DO NOTHING"
        )
        self.worker_principal, self.worker_secret, grant = self.pair_complete_service()
        self.attestor, self.attestor_secret, _ = self.pair_complete_service()
        self.dispatch_policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')",
            (
                self.tenant,
                self.dispatch_policy,
                self.workspace,
                self.scope,
                self.source,
                self.attestor,
                self.principal,
                "b" * 64,
                self.now,
                self.now + timedelta(hours=1),
            ),
        )
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT %s,%s,%s,%s,semantic_registry_hash,task_kind,contract_revision,queue_name FROM memoriesql.semantic_task_admission_policies WHERE task_kind='memory.semantic.author-complete-unit' AND contract_revision=2",
            (self.tenant, self.workspace, self.worker_principal, grant),
        )
        self.complete = PostgresCompleteInput(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        self.received: list[dict[str, Any]] = []

    def pair_complete_service(self) -> tuple[uuid.UUID, str, uuid.UUID]:
        principal, pairing, grant, credential = [uuid.uuid4() for _ in range(4)]
        secret = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,'service','background_service',%s,%s,%s,%s,%s)",
                (
                    principal,
                    pairing,
                    grant,
                    credential,
                    ["memory.maintain", "source.raw.read"],
                    [self.scope],
                    secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        return principal, secret, grant

    def setup_execution(self, text: str = "Four fictional trees.") -> Any:
        package = self.package(text)
        self.bound = self.materializer.materialize(self.materialization(package))
        self.activation_command = ActivateCompleteInput(
            idempotency_key="orchard.activate",
            binding_task_id=self.bound.task_id,
            dispatch_policy_id=self.dispatch_policy,
        )
        self.activation = self.complete.activate(self.activation_command)
        return self.activation

    def connection(self) -> Any:
        return psycopg.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        )

    def response(self, messages: Any, info: Any) -> ModelResponse:
        prompts = [
            p.content
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        self.assertEqual(len(prompts), 1)
        frame = json.loads(cast(str, prompts[0]))
        self.received.append(frame)
        if not frame["complete_input_window"]["final"]:
            data: Any = {
                "notes": "Fictional orchard evidence inspected; preserve uncertainty."
            }
        else:
            task = CompleteExecutionInput.model_validate(frame["task_input"])
            statement = str(uuid.uuid4())
            clause = {"text": "Four fictional trees.", "statement_ids": [statement]}
            data = {
                "typed_output": {
                    "annotations": [
                        {
                            "bead_id": str(task.payload.bead_ids[0]),
                            "event_id": str(task.payload.event_id),
                            "source_unit_id": str(task.payload.source_unit_ids[0]),
                            "bead_version_id": str(uuid.uuid4()),
                            "expected_bead_version": 0,
                            "bead_type_key": "observation",
                            "bead_type_revision": 1,
                            "statements": [
                                {
                                    "statement_id": statement,
                                    "statement_kind": "observation",
                                    "statement_text": "Four fictional trees.",
                                    "evidence": [
                                        {
                                            "source_unit_id": str(
                                                task.payload.source_unit_ids[0]
                                            ),
                                            "content_hash": task.payload.package.inventory_sha256,
                                        }
                                    ],
                                    "model_run_ref": "orchard.complete.run",
                                }
                            ],
                            "render": {"title": clause, "summary": [clause]},
                        }
                    ]
                },
                "used_evidence_refs": [str(task.payload.source_unit_ids[0])],
            }
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, data)],
            usage=RequestUsage(input_tokens=11, output_tokens=7),
        )

    def worker(
        self, callback: Any = None, *, recorder: Any = True
    ) -> IntegratedSemanticWorker:
        task = COMPLETE_EXECUTION_TASK
        modules = BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
        registry = load_complete_input_task_registry(modules)
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

    def test_complete_atomic_apply_and_original_pin_preserved(self) -> None:
        self.setup_execution("Four fictional trees. " * 900)
        receipt = asyncio.run(self.worker().run_once())
        self.assertEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual(len(self.received), 2)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,)
        )
        self.assertEqual(
            self.row(
                "SELECT status,input_payload#>>'{payload,execution_availability}' FROM memoriesql.semantic_tasks WHERE task_id=%s",
                (self.bound.task_id,),
            ),
            ("cancelled", "unavailable"),
        )
        replay = self.complete.activate(self.activation_command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.execution_task_id, self.activation.execution_task_id)

    def assert_no_meaning(self) -> None:
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.bead_versions"), (0,)
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (0,)
        )
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.beads"), (1,))

    def test_missing_trusted_dispatch_is_explicitly_unavailable(self) -> None:
        self.setup_execution()
        result = asyncio.run(self.worker(recorder=None).run_once())
        self.assertEqual(result.result_status, "unavailable")
        self.assertEqual(self.received, [])
        self.assert_no_meaning()

    def test_reader_or_model_claim_cannot_supply_exposure(self) -> None:
        self.setup_execution()

        class NoExposure:
            async def record_received(self, **kwargs: Any) -> None:
                pass

        result = asyncio.run(self.worker(recorder=NoExposure()).run_once())
        self.assertEqual(result.task_status, "failed_terminal")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.complete_input_exposures"), (0,)
        )
        self.assert_no_meaning()

    def test_ordinary_worker_cannot_forge_attestation(self) -> None:
        self.setup_execution()
        recorder = PostgresEvidenceExposureRecorder(
            connection_factory=self.connection,
            credential_sha256=self.worker_secret,
            workspace_id=self.workspace,
        )
        result = asyncio.run(self.worker(recorder=recorder).run_once())
        self.assertNotEqual(result.task_status, "succeeded")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.complete_input_exposures"), (0,)
        )
        self.assert_no_meaning()

    def test_omitted_last_window_cannot_commit(self) -> None:
        self.setup_execution("fictional trees " * 1500)
        real = PostgresEvidenceExposureRecorder(
            connection_factory=self.connection,
            credential_sha256=self.attestor_secret,
            workspace_id=self.workspace,
        )

        class OmitFinal:
            async def record_received(self, **kwargs: Any) -> None:
                if not kwargs["window"].final:
                    await real.record_received(**kwargs)

        result = asyncio.run(self.worker(recorder=OmitFinal()).run_once())
        self.assertEqual(result.task_status, "failed_terminal")
        self.assertGreater(
            self.row("SELECT count(*) FROM memoriesql.complete_input_exposures")[0], 0
        )
        self.assert_no_meaning()

    def test_tampered_content_and_cross_attempt_are_rejected(self) -> None:
        self.setup_execution()
        real = PostgresEvidenceExposureRecorder(
            connection_factory=self.connection,
            credential_sha256=self.attestor_secret,
            workspace_id=self.workspace,
        )
        owner = self

        class Tamper:
            async def record_received(self, **kwargs: Any) -> None:
                window = kwargs["window"]
                bad_slice = window.slices[0].model_copy(update={"content": "forged"})
                for bad in (
                    window.model_copy(update={"slices": (bad_slice,)}),
                    window.model_copy(update={"attempt_id": uuid.uuid4()}),
                    window.model_copy(
                        update={
                            "package": window.package.model_copy(
                                update={"package_id": uuid.uuid4()}
                            )
                        }
                    ),
                ):
                    with owner.assertRaises(psycopg.Error):
                        await real.record_received(**(kwargs | {"window": bad}))
                await real.record_received(**kwargs)
                # Reply-loss replay is idempotent; it adds no coverage.
                await real.record_received(**kwargs)

        result = asyncio.run(self.worker(recorder=Tamper()).run_once())
        self.assertEqual(result.task_status, "succeeded")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.complete_input_exposures"), (1,)
        )

    def test_producer_revocation_during_model_wait_preserves_usage(self) -> None:
        self.setup_execution()

        def revoke(messages: Any, info: Any) -> ModelResponse:
            self.db.execute(
                "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
                (self.policy,),
            )
            return self.response(messages, info)

        result = asyncio.run(self.worker(revoke).run_once())
        self.assertNotEqual(result.task_status, "succeeded")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,)
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.complete_input_exposures"), (0,)
        )
        self.assert_no_meaning()

    def test_cancel_during_model_wait_retains_accounting(self) -> None:
        self.setup_execution()

        def cancel(messages: Any, info: Any) -> ModelResponse:
            with self.db.transaction():
                self.begin()
                self.db.execute(
                    "SELECT memoriesql.cancel_semantic_task(%s,%s,'orchard.cancel',clock_timestamp())",
                    (self.tenant, self.activation.execution_task_id),
                )
            return self.response(messages, info)

        result = asyncio.run(self.worker(cancel).run_once())
        self.assertEqual(result.task_status, "cancelled")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,)
        )
        self.assert_no_meaning()

    def test_activation_failure_rolls_back_transfer(self) -> None:
        package = self.package()
        self.bound = self.materializer.materialize(self.materialization(package))
        self.db.execute(
            "CREATE FUNCTION public.fictional_abort_execution() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.contract_revision=2 AND NEW.task_kind='memory.semantic.author-complete-unit' THEN RAISE EXCEPTION 'fictional_crash'; END IF; RETURN NEW; END $$"
        )
        self.db.execute(
            "CREATE TRIGGER fictional_abort BEFORE INSERT ON memoriesql.semantic_tasks FOR EACH ROW EXECUTE FUNCTION public.fictional_abort_execution()"
        )
        with self.assertRaisesRegex(psycopg.Error, "fictional_crash"):
            self.complete.activate(
                ActivateCompleteInput(
                    idempotency_key="orchard.abort",
                    binding_task_id=self.bound.task_id,
                    dispatch_policy_id=self.dispatch_policy,
                )
            )
        self.assertEqual(
            self.row("SELECT status FROM memoriesql.semantic_tasks"), ("policy_paused",)
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.complete_input_executions"), (0,)
        )
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.idempotency_receipts WHERE operation_kind='complete_input.activate.v1'"
            ),
            (0,),
        )
        self.assert_no_meaning()

    def test_direct_apply_without_exposure_is_denied(self) -> None:
        self.setup_execution()
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
            COMPLETE_EXECUTION_TASK,
        )
        with (
            self.assertRaisesRegex(psycopg.Error, "exposure_required"),
            self.db.transaction(),
        ):
            self.worker_queue().record_canonical_result(
                claimed.fence, result, recorded_at=datetime.now(UTC)
            )
        self.assert_no_meaning()

    def test_unicode_fragments_and_many_parts_are_one_semantic_unit(self) -> None:
        texts = [('🌳e\u0301\\"\n' * 4000)[:21000], "未知の順序。" * 2500]
        parts = tuple(self.part(text, i) for i, text in enumerate(texts))
        package = self.create(parts)
        for part in parts:
            self.append(package, part)
        self.seal(package)
        self.bound = self.materializer.materialize(self.materialization(package))
        self.activation = self.complete.activate(
            ActivateCompleteInput(
                idempotency_key="unicode.activate",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
            )
        )
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded")
        supplied = "".join(
            s["content"]
            for frame in self.received
            for s in frame["complete_input_window"]["slices"]
        )
        self.assertEqual(supplied, "".join(texts))
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.bead_versions"), (1,)
        )
        self.assertGreater(len(self.received), 1)
        self.assertLess(len(self.received), len(supplied) // 1024)
        self.assertTrue(
            all(len(json.dumps(frame).encode()) < 180000 for frame in self.received)
        )

    def test_budget_exhaustion_preserves_raw_and_thin_bead(self) -> None:
        parts = tuple(self.part("x" * 60000, i) for i in range(3))
        package = self.create(parts)
        for part in parts:
            self.append(package, part)
        self.seal(package)
        self.bound = self.materializer.materialize(self.materialization(package))
        self.activation = self.complete.activate(
            ActivateCompleteInput(
                idempotency_key="budget.activate",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
            )
        )
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.result_status, "budget_exhausted")
        self.assertEqual(self.received, [])
        self.assertEqual(
            self.row(
                "SELECT sum(octet_length(content)) FROM memoriesql.evidence_package_parts"
            ),
            (180000,),
        )
        self.assert_no_meaning()

    def test_restart_requires_new_attempt_exposure(self) -> None:
        self.setup_execution()
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
            async def record_received(self, **kwargs: Any) -> None:
                pass

        result = asyncio.run(self.worker(recorder=NoNewExposure()).run_once())
        self.assertEqual(result.task_status, "failed_terminal")
        self.assertNotEqual(result.attempt_id, old.fence.attempt_id)
        self.assert_no_meaning()

    def test_canonical_apply_rechecks_policy_after_exposure(self) -> None:
        self.setup_execution()
        worker = self.worker()
        original = worker._persist_result

        def revoke_then_apply(
            claimed: Any, result: Any, sink: Any, **kwargs: Any
        ) -> Any:
            self.db.execute(
                "UPDATE memoriesql.complete_input_dispatch_policies SET status='revoked' WHERE dispatch_policy_id=%s",
                (self.dispatch_policy,),
            )
            return original(claimed, result, sink, **kwargs)

        worker._persist_result = revoke_then_apply  # type: ignore[method-assign]
        result = asyncio.run(worker.run_once())
        self.assertEqual(result.task_status, "policy_paused")
        self.assertGreater(
            self.row("SELECT count(*) FROM memoriesql.complete_input_exposures")[0], 0
        )
        self.assert_no_meaning()

    def test_owned_exposure_write_drains_after_caller_cancellation(self) -> None:
        import threading
        from unittest.mock import patch

        self.setup_execution()
        started = threading.Event()
        release = threading.Event()
        original = PostgresCompleteInput._call

        def blocked(port: Any, query: str, parameters: Any, **kwargs: Any) -> Any:
            if "record_complete_input_exposure" in query:
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

    def test_authority_wait_observes_revocation_and_rejects_snapshot_isolation(
        self,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from memoriesql.application.complete_input_execution import ReadCompleteEvidence

        self.setup_execution()
        claimed = self.claim()
        request = ReadCompleteEvidence(
            package_id=self.bound.bound_package.package_id,
            inventory_sha256=self.bound.bound_package.inventory_sha256,
            next_ordinal=0,
        )
        started = Event()

        def hydrate() -> Any:
            started.set()
            return self.worker()._transaction(
                lambda queue, _: queue.read_complete_evidence(claimed.fence, request)
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.db.transaction():
                self.db.execute(
                    "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
                    (self.policy,),
                )
                waiting = pool.submit(hydrate)
                self.assertTrue(started.wait(1))
                from time import monotonic, sleep

                deadline = monotonic() + 0.4
                observed_wait = False
                while monotonic() < deadline:
                    observed_wait = bool(
                        self.row(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))"
                        )[0]
                    )
                    if observed_wait:
                        break
                    sleep(0.005)
                self.assertTrue(
                    observed_wait, "hydration must actually wait on the authority fence"
                )
            with self.assertRaises(PermissionError):
                waiting.result(timeout=3)
        for isolation in ("REPEATABLE READ", "SERIALIZABLE"):
            with (
                self.assertRaisesRegex(psycopg.Error, "requires_read_committed"),
                self.db.transaction(),
            ):
                self.db.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
                self.begin()
                from psycopg.types.json import Jsonb

                self.db.execute(
                    "SELECT memoriesql.read_complete_evidence_v2(%s)",
                    (Jsonb(request.model_dump(mode="json")),),
                )
        self.assert_no_meaning()

    def test_independent_ninth_unit_progress_with_pending_sibling(self) -> None:
        from memoriesql.application.evidence_packages import (
            NativeFacts,
            SourceQualification,
        )

        for i in range(9):
            package = self.package(
                "Fictional unit " + str(i),
                occurrence_key="unit." + str(i),
                native=NativeFacts(native_id="native." + str(i)),
            )
            self.bound = self.materializer.materialize(
                self.materialization(package, total=12)
            )
        pending = self.package(
            "Unfinished sibling",
            occurrence_key="pending",
            native=NativeFacts(native_id="pending"),
            qualification=SourceQualification(
                qualification_ref="orchard.fictional-qualification.v1",
                boundary="unresolved",
                boundary_basis="No terminal record",
                physical_records="pending_tail",
                topology="unknown",
                normalized_input="incomplete",
                source_completeness="unresolved",
                unresolved_coverage=("terminal record absent",),
            ),
        )
        with self.assertRaises(psycopg.Error):
            self.materializer.materialize(self.materialization(pending, total=12))
        self.activation = self.complete.activate(
            ActivateCompleteInput(
                idempotency_key="ninth.activate",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
            )
        )
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded")
        self.assertEqual(
            self.row(
                "SELECT materialized_unit_count FROM memoriesql.source_event_materializations"
            ),
            (9,),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,)
        )
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.evidence_packages WHERE sealed_receipt_id IS NOT NULL"
            ),
            (10,),
        )


class LegacyAtSchema18(LegacyAtSchema17):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=17, target_version=18)


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    return unittest.TestSuite(
        [
            CompleteInputExecution(name)
            for name in CompleteInputExecution.__dict__
            if name.startswith("test_")
        ]
        + [
            LegacyAtSchema18(name)
            for name in LegacyAtSchema17.__dict__
            if name.startswith("test_")
        ]
    )
