"""Fictional two-contributor acceptance; no provider transport or owner data."""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from memoriesql.application.bead_classification import (
    CLASSIFICATION_TASK,
    CLASSIFIER_AGENT,
    CLASSIFIER_INPUT,
    CLASSIFIER_KEY,
    CLASSIFIER_OUTPUT,
    CLASSIFIER_PROFILE,
    ActivateClassifiedAuthorship,
    BeadTypePin,
    load_classification_task_registry,
)
from memoriesql.application.semantic_task_contracts import AgentContract
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
    LeafAgentSpec,
    ModelProfileBinding,
    PydanticAIAgentRegistry,
    PydanticAIModelProfileRegistry,
    PydanticAISemanticExecutor,
)

if TYPE_CHECKING:
    from tests.runtime import test_local_entity_mentions as fixtures
    from tests.runtime import test_source_revisiting as source_fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_local_entity_mentions as fixtures
    import test_source_revisiting as source_fixtures
    from test_postgres_runtime import migrate


class BeadClassification(fixtures.LocalMentions):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=22, target_version=23)
        old = self.dispatch_policy
        self.dispatch_policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,workspace_id,access_scope_id,source_object_id,attestor_principal_id,approved_by_principal_id,qualification_evidence_sha256,created_at,expires_at,status,5 FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (self.dispatch_policy, old),
        )
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,p.queue_name FROM memoriesql.semantic_worker_claim_policies c JOIN memoriesql.semantic_task_admission_policies p ON p.task_kind=c.task_kind AND p.contract_revision=5 WHERE c.principal_id=%s AND c.contract_revision=4",
            (self.worker_principal,),
        )
        self.outcome = "selected"
        self.label = "observation"
        self.alignment = "consistent"
        self.classifier_calls = 0

    def setup_classification(self, contexts: tuple[Any, ...] = ()) -> None:
        self.setup_revisiting(
            "Alex proposed counting four fictional trees; no count has occurred.",
            activate=False,
            contexts=contexts,
        )
        self.activation = self.complete.activate_classified(
            ActivateClassifiedAuthorship(
                idempotency_key="orchard.classified",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
                authorized_context=contexts,
                classification_vocabulary=(
                    BeadTypePin(key="observation", revision=1),
                    BeadTypePin(key="action", revision=1),
                ),
            )
        )

    def response(self, messages: Any, info: Any) -> Any:
        response = super().response(messages, info)
        part = cast(ToolCallPart, response.parts[0])
        data = part.args_as_dict()
        data["supporting_selections"] = (
            [self.selection(limit=16384)] if data["action"] == "finish" else []
        )
        if data["typed_output"] is not None:
            bead = data["typed_output"]["annotations"][0]
            text = "Alex proposed a count; no count has occurred."
            bead["statements"][0]["statement_text"] = text
            bead["statements"][0]["model_run_ref"] = "orchard.direct_leaf"
            bead["render"]["title"]["text"] = text
            bead["render"]["summary"][0]["text"] = text
        part.args = data
        return response

    def classify(self, messages: Any, info: Any) -> Any:
        self.classifier_calls += 1
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        packet = json.loads(
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
        self.assertIn("no count has occurred", packet["evidence"][0]["content"])
        self.assertTrue(packet["proposal"]["annotations"][0]["statements"])
        self.assertEqual(packet["vocabulary"][0]["revision"], 1)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    dict(
                        contract_version=1,
                        outcome=self.outcome,
                        proposal_alignment=self.alignment,
                        selected_type=dict(key=self.label, revision=1)
                        if self.outcome == "selected"
                        else None,
                        rationale=None,
                        confidence=0.7,
                        distribution=[
                            dict(type=dict(key="observation", revision=1), score=0.7),
                            dict(type="ambiguous", score=0.3),
                        ],
                        alignment_distribution=[
                            dict(alignment="consistent", score=0.9),
                            dict(alignment="uncertain", score=0.1),
                        ],
                        alignment_confidence=0.8,
                    ),
                )
            ],
            usage=RequestUsage(input_tokens=31, output_tokens=9),
        )

    def worker(
        self, callback: Any = None, *, recorder: Any = True, **kwargs: Any
    ) -> Any:
        from unittest.mock import patch

        # Reuse O's worker/identity/cleanup composition, substituting typed registries only.
        original = PydanticAISemanticExecutor
        owner = self

        def executor(**kw: Any) -> Any:
            t = CLASSIFICATION_TASK
            author = LeafAgentSpec(
                agent_key=cast(str, t.leaf_agent_key),
                input_contract=t.input_contract.reference,
                output_contract=t.output_contract.reference,
                input_model_type=t.input_contract.model_type,
                output_model_type=t.output_contract.model_type,
                instructions="Fictional author; preserve qualifiers.",
                model_profiles=(t.model_profile,),
            )
            classifier = LeafAgentSpec(
                agent_key=CLASSIFIER_KEY,
                input_contract=CLASSIFIER_INPUT.reference,
                output_contract=CLASSIFIER_OUTPUT.reference,
                input_model_type=CLASSIFIER_INPUT.model_type,
                output_model_type=CLASSIFIER_OUTPUT.model_type,
                instructions="Classify the exact evidence-backed proposal against supplied definitions; abstain if needed.",
                model_profiles=(CLASSIFIER_PROFILE,),
            )
            kw["agent_registry"] = PydanticAIAgentRegistry(
                leaf_specs=(author, classifier),
                conductors=(),
                agent_contracts=(
                    AgentContract(
                        agent_key=cast(str, t.leaf_agent_key),
                        input_contract=t.input_contract.reference,
                        output_contract=t.output_contract.reference,
                        maximum_effort_key="standard",
                    ),
                    CLASSIFIER_AGENT,
                ),
            )
            kw["model_profiles"] = PydanticAIModelProfileRegistry(
                (
                    ModelProfileBinding(
                        reference=t.model_profile,
                        model=FunctionModel(callback or owner.response),
                        request_target=CHARACTERIZED_TEST_REQUEST_TARGET,
                    ),
                    ModelProfileBinding(
                        reference=CLASSIFIER_PROFILE,
                        model=FunctionModel(owner.classify),
                        request_target=CHARACTERIZED_TEST_REQUEST_TARGET,
                    ),
                )
            )
            kw["run_id_factory"] = lambda role: "orchard." + role
            return original(**kw)

        fixture_module = source_fixtures
        with patch.object(fixture_module, "PydanticAISemanticExecutor", executor):
            return fixture_module.SourceRevisiting.worker(
                self,
                callback,
                recorder=recorder,
                task_definition=CLASSIFICATION_TASK,
                registry_factory=load_classification_task_registry,
            )

    def test_two_attributable_runs_one_immutable_accepted_bundle(self) -> None:
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(self.classifier_calls, 1)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (2,),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (1,)
        )
        self.assertEqual(
            self.row(
                "SELECT classification_contribution#>>'{decision,outcome}' FROM memoriesql.bead_versions"
            ),
            ("selected",),
        )
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (67,),
        )
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.semantic_task_runs WHERE settled"
            ),
            (2,),
        )

    def test_bounded_admission_and_classification_share_exact_request_accounting(self) -> None:
        from unittest.mock import patch

        from memoriesql.infrastructure.models.pydanticai_executor import (
            BoundedProviderModel,
            RealModelAdmission,
        )

        dispatched: list[Any] = []
        original_binding = ModelProfileBinding

        class BoundedDouble(BoundedProviderModel):
            def __init__(self, inner: Any) -> None:
                self.inner = inner
                super().__init__(profile=inner.profile)

            @property
            def model_name(self) -> str:
                return "fictional-integrated"

            @property
            def system(self) -> str:
                return "fictional"

            async def request_bounded(self, messages: Any, settings: Any,
                                      parameters: Any, bounds: Any) -> Any:
                dispatched.append(bounds)
                return await self.inner.request(messages, settings, parameters)

        def binding(**kwargs: Any) -> Any:
            model = BoundedDouble(kwargs["model"])
            target = kwargs["request_target"].model_copy(update={
                "max_input_tokens_per_request": 6000,
                "max_output_tokens_per_request": 1000,
            })
            return original_binding(
                reference=kwargs["reference"], model=model, request_target=target,
                real_model_admission=RealModelAdmission(
                    model, kwargs["reference"], target, "fictional.integration.v1"
                ),
            )

        self.setup_classification()
        with patch(f"{__name__}.ModelProfileBinding", side_effect=binding):
            result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(len(dispatched), 2)
        self.assertEqual(self.classifier_calls, 1)
        self.assertEqual(
            self.db.execute(
                "SELECT request_sequence FROM memoriesql.model_provider_request_intents ORDER BY request_sequence"
            ).fetchall(), [(1,), (2,)],
        )
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (1,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.bead_versions"), (1,))

    def test_abstention_retains_evidence_without_accepted_meaning(self) -> None:
        self.outcome = "no_fit"
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        self.assertEqual(
            self.row(
                "SELECT selection#>>'{decision,outcome}' FROM memoriesql.complete_input_dispatch_receipts WHERE delivery_version=3"
            ),
            ("no_fit",),
        )

    def test_disagreement_cannot_relabel_authored_proposal(self) -> None:
        self.label = "action"
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()

    def test_missing_primary_attestation_cannot_be_replaced_by_classifier(self) -> None:
        self.setup_classification()

        class NoPrimary:
            async def record_delivery(self, **kw: Any) -> None:
                pass

            async def record_classification(self, **kw: Any) -> None:
                pass

        result = asyncio.run(self.worker(recorder=NoPrimary()).run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()

    def test_conflicting_rationale_with_same_label_is_not_accepted(self) -> None:
        self.alignment = "conflicting"
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(self.classifier_calls, 1)
        self.assert_no_meaning()

    def test_insufficient_and_ambiguous_are_explicit_non_acceptance(self) -> None:
        self.outcome = "insufficient_evidence"
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT selection#>>'{decision,outcome}' FROM memoriesql.complete_input_dispatch_receipts WHERE delivery_version=3"
            ),
            (self.outcome,),
        )
        self.assert_no_meaning()

    def test_ambiguous_is_explicit_non_acceptance(self) -> None:
        self.outcome = "ambiguous"
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT selection#>>'{decision,outcome}' FROM memoriesql.complete_input_dispatch_receipts WHERE delivery_version=3"
            ),
            (self.outcome,),
        )
        self.assert_no_meaning()

    def test_unregistered_classifier_label_is_rejected(self) -> None:
        self.label = "invented"
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )

    def test_classifier_failure_keeps_retained_source_and_usage(self) -> None:
        self.setup_classification()

        def failed(*args: Any) -> Any:
            raise RuntimeError("fictional classifier unavailable")

        self.classify = failed  # type: ignore[method-assign, assignment]
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        self.assertEqual(
            self.row(
                "SELECT sum(char_length(content)) FROM memoriesql.evidence_package_parts"
            ),
            (67,),
        )

    def test_classifier_revocation_rejects_atomically_and_keeps_usage(self) -> None:
        self.setup_classification()
        original = self.classify

        def revoke(*args: Any) -> Any:
            self.db.execute(
                "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
                (self.policy,),
            )
            return original(*args)

        self.classify = revoke  # type: ignore[method-assign, assignment]
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )

    def test_failed_classifier_after_revocation_settles_rejected_terminal_events(self) -> None:
        self.setup_classification()
        original = self.classify

        def revoke_and_fail(*args: Any) -> Any:
            original(*args)
            self.db.execute(
                "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
                (self.policy,),
            )
            raise RuntimeError("fictional classifier failed after revocation")

        self.classify = revoke_and_fail  # type: ignore[method-assign, assignment]
        worker = self.worker()
        record = worker._record_run_event
        rejected = []

        def observe(*args: Any, **kwargs: Any) -> bool:
            accepted = bool(record(*args, **kwargs))
            if not accepted:
                rejected.append(args[1].event_kind)
            return accepted

        worker._record_run_event = observe

        async def execute() -> Any:
            receipt = await asyncio.wait_for(worker.run_once(), 5)
            if worker.cleanup_pending:
                receipt = await asyncio.wait_for(worker.wait_for_cleanup(), 5)
            self.assertFalse(worker._event_writes)
            self.assertIsNone(worker._cleanup_heartbeat)
            return receipt

        result = asyncio.run(execute())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertIn("run.failed", rejected)
        self.assert_no_meaning()
        self.assertEqual(self.classifier_calls, 1)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"), (2,))

    def test_authored_empty_mentions_and_classification_are_distinct(self) -> None:
        self.mentions = []
        self.setup_classification()
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (0,)
        )
        self.assertEqual(
            self.row(
                "SELECT r.task_contract_version,v.classification_contribution IS NOT NULL FROM memoriesql.bead_versions v JOIN memoriesql.semantic_task_receipts r USING(tenant_id,semantic_task_receipt_id)"
            ),
            (5, True),
        )

    def test_exhausted_shared_request_budget_prevents_classifier_dispatch(self) -> None:
        self.setup_classification()

        def author(messages: Any, info: Any) -> Any:
            if len(self.steps) < 11:
                return self.step(messages, info, "continue")
            return self.response(messages, info)

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.result_status, "budget_exhausted", result)
        self.assertEqual(self.classifier_calls, 0)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (12,),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (12,)
        )
        self.assert_no_meaning()

    def test_primary_reread_remains_distinct_from_classifier_exposure(self) -> None:
        self.setup_classification()

        def author(messages: Any, info: Any) -> Any:
            if not self.steps:
                return self.step(messages, info, "read", self.selection(limit=67))
            self.assertEqual(self.steps[0]["forward_delivery_complete"], True)
            return self.response(messages, info)

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (67,),
        )
        self.assertEqual(
            self.row(
                "SELECT sum(delivered_units) FROM memoriesql.complete_input_dispatch_receipts"
            ),
            (201,),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (3,)
        )

    def test_classifier_late_usage_and_cleanup_after_caller_cancellation(self) -> None:
        from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
            SemanticWorkerConfig,
        )

        self.setup_classification()
        original = self.classify

        async def scenario() -> None:
            started = asyncio.Event()
            release = asyncio.Event()

            async def slow(messages: Any, info: Any) -> Any:
                started.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue
                return original(messages, info)

            self.classify = slow  # type: ignore[method-assign]
            worker = self.worker()
            worker._config = SemanticWorkerConfig(
                cancellation_return_timeout_seconds=0.01,
                heartbeat_interval_seconds=0.1,
                cancellation_poll_interval_seconds=0.01,
            )
            foreground = asyncio.create_task(worker.run_once())
            try:
                await asyncio.wait_for(started.wait(), 5)
                foreground.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await foreground
                self.assertTrue(worker.cleanup_pending)
                self.assertEqual(
                    (await worker.run_once()).cycle_status, "cleanup_pending"
                )
            finally:
                release.set()
            settled = await asyncio.wait_for(worker.wait_for_cleanup(), 5)
            self.assertEqual(settled.task_status if settled else None, "cancelled")
            self.assertFalse(worker.cleanup_pending)

        # Capture a wedged loop even when cancellation prevents asyncio timeouts.
        import faulthandler

        faulthandler.dump_traceback_later(60, exit=True)
        try:
            asyncio.run(scenario())
        finally:
            faulthandler.cancel_dump_traceback_later()
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.semantic_task_runs WHERE settled"
            ),
            (2,),
        )
        self.assert_no_meaning()

    def test_owned_classifier_receipt_drains_after_caller_cancellation(self) -> None:
        import threading
        from unittest.mock import patch

        from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
            SemanticWorkerConfig,
        )
        from memoriesql.infrastructure.postgres.complete_input_execution import (
            PostgresCompleteInput,
        )

        self.setup_classification()
        started = threading.Event()
        release = threading.Event()
        original = PostgresCompleteInput._call

        def blocked(port: Any, query: str, parameters: Any, **kwargs: Any) -> Any:
            if "record_classification_v1" in query:
                started.set()
                if not release.wait(10):
                    raise AssertionError("fictional classifier receipt gate timed out")
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
            self.assertEqual(settled.task_status if settled else None, "cancelled")
            self.assertFalse(worker.cleanup_pending)

        with patch.object(PostgresCompleteInput, "_call", blocked):
            asyncio.run(scenario())
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        self.assert_no_meaning()

    def test_exact_classified_replay_reauthorizes_optional_source(self) -> None:
        from datetime import UTC, datetime
        from unittest.mock import patch

        import psycopg
        from psycopg.types.json import Jsonb

        from memoriesql.application.logical_unit_materialization import SealedPackagePin
        from memoriesql.application.source_revisiting import (
            AuthorizedContextPin,
            ReadSourceEvidence,
        )
        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
        )

        target_source = self.source
        optional_source = uuid.uuid4()
        for table, key in (
            ("protected_resources", "resource_id"),
            ("source_objects", "source_object_id"),
        ):
            extra = {key: str(optional_source)}
            if table == "source_objects":
                extra["external_object_id"] = "orchard.optional"
            from psycopg import sql

            columns = [
                r[0]
                for r in self.db.execute(
                    "SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum",
                    ("memoriesql." + table,),
                )
            ]
            names = sql.SQL(",").join(sql.Identifier(c) for c in columns)
            self.db.execute(
                sql.SQL(
                    "INSERT INTO memoriesql.{} ({}) SELECT {} FROM jsonb_populate_record(NULL::memoriesql.{},(SELECT to_jsonb(r)||%s FROM memoriesql.{} r WHERE {}=%s))"
                ).format(
                    sql.Identifier(table),
                    names,
                    names,
                    sql.Identifier(table),
                    sql.Identifier(table),
                    sql.Identifier(key),
                ),
                (Jsonb(extra), target_source),
            )
        self.source = optional_source
        import importlib

        capture_fixture = importlib.import_module(cast(Any, self.part).__module__)
        builder = capture_fixture.build_capture_source_range_command
        with patch.object(
            capture_fixture,
            "build_capture_source_range_command",
            side_effect=lambda **kw: builder(
                **(kw | {"checkpoint_key": "orchard.optional.raw"})
            ),
        ):
            context = self.package(
                "Optional source confirms no count has occurred.",
                occurrence_key="orchard.optional",
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
        self.source = target_source
        self.offset = self.sequence = 0
        self.setup_classification(
            (
                AuthorizedContextPin(
                    package=pin,
                    source_object_id=optional_source,
                    source_schema_version=1,
                ),
            )
        )
        read = ReadSourceEvidence(
            package_id=pin.package_id,
            inventory_sha256=pin.inventory_sha256,
            part_ordinal=0,
            limit=100,
        ).model_dump(mode="json")

        def author(messages: Any, info: Any) -> Any:
            if not self.steps:
                return self.step(messages, info, "read", read)
            result = self.response(messages, info)
            part = cast(ToolCallPart, result.parts[0])
            data = part.args_as_dict()
            data["supporting_selections"].append(read)
            part.args = data
            return result

        recorded: list[tuple[Any, Any]] = []
        original = PostgresSemanticTaskQueue.record_canonical_result

        def capture(queue: Any, fence: Any, result: Any, **kwargs: Any) -> Any:
            recorded.append((fence, result))
            return original(queue, fence, result, **kwargs)

        with patch.object(
            PostgresSemanticTaskQueue, "record_canonical_result", capture
        ):
            result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        fence, output = recorded[0]
        with self.db.transaction():
            self.assertEqual(
                self.worker_queue().record_canonical_result(
                    fence, output, recorded_at=datetime.now(UTC)
                ),
                "succeeded",
            )
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
            (optional_source,),
        )
        with self.assertRaises(psycopg.Error), self.db.transaction():
            self.worker_queue().record_canonical_result(
                fence, output, recorded_at=datetime.now(UTC)
            )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.bead_versions"), (1,)
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (3,)
        )

    def test_forged_contribution_rolls_back_and_exact_replay_is_stable(self) -> None:
        import copy
        import hashlib
        from unittest.mock import patch

        import psycopg
        from psycopg.types.json import Jsonb

        from memoriesql.application.bead_classification import ApplyClassifiedAuthorship
        from memoriesql.application.semantic_task_contracts import canonical_json_bytes
        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
        )

        self.setup_classification()
        original = PostgresSemanticTaskQueue.record_canonical_result
        owner = self

        def intercept(queue: Any, fence: Any, result: Any, *, recorded_at: Any) -> str:
            payload = result.typed_output.model_dump(mode="json")
            encoded = canonical_json_bytes(payload)
            command = ApplyClassifiedAuthorship(
                idempotency_key=f"semantic-apply.{fence.attempt_id}",
                tenant_id=fence.tenant_id,
                workspace_id=fence.workspace_id,
                access_scope_id=fence.access_scope_id,
                task_id=fence.task_id,
                attempt_id=fence.attempt_id,
                lease_generation=fence.lease_generation,
                output_contract_hash=result.output_contract_hash,
                semantic_result_hash=hashlib.sha256(encoded).hexdigest(),
                semantic_payload_canonical_json=encoded.decode(),
                used_evidence_refs=result.used_evidence_refs,
                model_run_refs=result.model_run_refs,
                payload=payload,
            ).model_dump(mode="json")
            bad = copy.deepcopy(command)
            bad["payload"]["classification"]["decision"]["rationale"] = (
                "Substituted classifier result."
            )
            encoded = canonical_json_bytes(bad["payload"])
            bad.update(
                semantic_payload_canonical_json=encoded.decode(),
                semantic_result_hash=hashlib.sha256(encoded).hexdigest(),
            )
            with (
                owner.assertRaisesRegex(
                    psycopg.Error, "classification_acceptance_required"
                ),
                queue._connection.transaction(),
            ):
                queue._connection.execute(
                    "SELECT * FROM memoriesql.apply_semantic_annotations(%s,%s,%s,%s)",
                    (
                        Jsonb(bad),
                        fence.worker_id,
                        fence.worker_instance_id,
                        recorded_at,
                    ),
                )
            queue._connection.execute("SET LOCAL ROLE NONE")
            for table in (
                "bead_versions",
                "entity_mentions",
                "bead_semantic_statements",
                "semantic_task_receipts",
            ):
                owner.assertEqual(
                    queue._connection.execute(
                        "SELECT count(*) FROM memoriesql." + table
                    ).fetchone(),
                    (0,),
                )
            queue._connection.execute("SET LOCAL ROLE memoriesql_worker")
            status = original(queue, fence, result, recorded_at=recorded_at)
            owner.assertEqual(
                original(queue, fence, result, recorded_at=recorded_at), status
            )
            return status

        with patch.object(
            PostgresSemanticTaskQueue, "record_canonical_result", intercept
        ):
            result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        with self.assertRaises(psycopg.Error), self.db.transaction():
            self.db.execute(
                "UPDATE memoriesql.bead_versions SET classification_contribution=NULL"
            )


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        BeadClassification(name)
        for name in BeadClassification.__dict__
        if name.startswith("test_")
    )
