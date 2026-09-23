"""Fictional installed managed-turn qualification; no provider network or keys."""

from __future__ import annotations

import asyncio
import hashlib
import unittest
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel

from memoriesql.application.managed_dispatch import (
    InferenceUsageObservation,
    ManagedDispatchTarget,
    ManagedUsageObservation,
    SupervisedQualification,
    SupervisedRoute,
)
from memoriesql.application.model_accounting import NormalizedUsage, UsageProvenance
from memoriesql.application.source_revisiting import SOURCE_REVISITING_TASK
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
    ManagedModelAdmission,
    ManagedProviderModel,
    ManagedTurnResult,
    ModelProfileBinding,
    PydanticAIModelProfileRegistry,
)

if TYPE_CHECKING:
    from tests.runtime.test_bead_classification import BeadClassification
    from tests.runtime.test_declared_evidence_scope import DeclaredScopes
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_bead_classification import BeadClassification
    from test_declared_evidence_scope import DeclaredScopes
    from test_postgres_runtime import migrate


def units(inputs: int = 11, outputs: int = 7) -> NormalizedUsage:
    return NormalizedUsage(
        input_tokens=inputs, output_tokens=outputs, total_tokens=inputs + outputs
    )


class FictionalManagedModel(ManagedProviderModel):
    def __init__(self, callback: Any, observation: ManagedUsageObservation) -> None:
        self.inner = FunctionModel(callback)
        super().__init__(profile=self.inner.profile)
        self.observation = observation
        self.calls = 0
        self.before_return: Any = None

    @property
    def model_name(self) -> str:
        return "fictional-managed"

    @property
    def system(self) -> str:
        return "fictional"

    async def request_managed(
        self, messages: Any, settings: Any, parameters: Any, qualification: Any
    ) -> ManagedTurnResult:
        self.calls += 1
        response = await self.inner.request(messages, settings, parameters)
        if self.before_return is not None:
            await self.before_return()
        return ManagedTurnResult(response, self.observation)


class UsageObservationTests(unittest.TestCase):
    def test_reported_aggregate_and_per_request_observations_are_not_added(
        self,
    ) -> None:
        observation = ManagedUsageObservation(
            turn_completion="completed",
            underlying_inference_count=2,
            inference_count_basis="host_observed",
            reported_total=units(20, 8),
            estimated_total=units(25, 9),
            request_observations_complete=True,
            request_observations=tuple(
                InferenceUsageObservation(
                    request_id_hash=hashlib.sha256(str(i).encode()).hexdigest(),
                    usage=units(10, 4),
                    provenance=UsageProvenance.PROVIDER_REPORTED,
                )
                for i in range(2)
            ),
        )
        self.assertEqual(
            observation.accounted_total(),
            (units(20, 8), UsageProvenance.PROVIDER_REPORTED),
        )
        self.assertEqual(
            observation.model_copy(update={"reported_total": None}).accounted_total()[
                0
            ],
            units(20, 8),
        )

    def test_unknown_count_unavailable_estimate_and_conflicts_are_explicit(
        self,
    ) -> None:
        self.assertEqual(
            ManagedUsageObservation().accounted_total(),
            (None, UsageProvenance.UNAVAILABLE),
        )
        estimated = ManagedUsageObservation(estimated_total=units())
        self.assertIsNone(estimated.underlying_inference_count)
        self.assertEqual(
            estimated.accounted_total()[1], UsageProvenance.FRAMEWORK_ESTIMATED
        )
        with self.assertRaises(ValidationError):
            ManagedUsageObservation(underlying_inference_count=1)
        with self.assertRaises(ValidationError):
            ManagedUsageObservation(request_observations_complete=True)


class ManagedDispatchTests(DeclaredScopes):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=25, target_version=26)
        self.activate_scope()
        original = CHARACTERIZED_TEST_REQUEST_TARGET.model_dump()
        for name in (
            "max_input_tokens_per_request",
            "max_output_tokens_per_request",
            "transport_retries_disabled",
        ):
            original.pop(name)
        original["usage_provenance"] = UsageProvenance.PROVIDER_REPORTED
        self.target = ManagedDispatchTarget(**original)
        self.approval = SupervisedQualification(
            qualification_id=uuid.uuid4(),
            semantic_task_id=self.activation.execution_task_id,
            routes=(
                SupervisedRoute(
                    reference=SOURCE_REVISITING_TASK.model_profile,
                    target=self.target,
                    qualification_revision="fictional.managed.v1",
                ),
            ),
            deadline=datetime.now(UTC) + timedelta(minutes=3),
            reported_input_token_stop=20000,
            reported_generated_token_stop=4000,
        )
        self.intents: list[Any] = []

    def install(self, approval: SupervisedQualification | None = None) -> None:
        q = approval or self.approval
        self.db.execute(
            "INSERT INTO memoriesql.model_supervised_qualifications(tenant_id,workspace_id,access_scope_id,qualification_id,task_id,origin_principal_id,configuration,approved_by_principal_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                self.tenant,
                self.workspace,
                self.scope,
                q.qualification_id,
                q.semantic_task_id,
                self.principal,
                Jsonb(q.model_dump(mode="json")),
                self.principal,
            ),
        )

    def managed_worker(
        self,
        callback: Any = None,
        *,
        observation: ManagedUsageObservation | None = None,
        recorder: Any = True,
        approval: SupervisedQualification | None = None,
    ) -> Any:
        worker: Any = super().worker(callback, recorder=recorder)
        model = FictionalManagedModel(
            callback or self.response,
            observation
            or ManagedUsageObservation(
                turn_completion="completed", reported_total=units()
            ),
        )
        binding = ModelProfileBinding(
            reference=SOURCE_REVISITING_TASK.model_profile,
            model=model,
            request_target=self.target,
            real_model_admission=ManagedModelAdmission(
                model,
                SOURCE_REVISITING_TASK.model_profile,
                self.target,
                "fictional.managed.v1",
            ),
            supervision=approval or self.approval,
        )
        worker._executor.model_profiles = PydanticAIModelProfileRegistry((binding,))
        worker.fictional_model = model
        original = worker._model_accounting.record_intent

        async def capture(intent: Any) -> None:
            self.intents.append(intent)
            await original(intent)

        worker._model_accounting.record_intent = capture
        return worker

    def test_declared_scope_accepts_unknown_inference_count_without_fabrication(
        self,
    ) -> None:
        self.install()
        worker = self.managed_worker()
        result = asyncio.run(worker.run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(worker.fictional_model.calls, 1)
        self.assertIsNone(self.intents[0].max_input_tokens)
        self.assertEqual(
            self.row(
                "SELECT dispatch_boundary,underlying_inference_count,inference_count_basis,input_tokens,output_tokens FROM memoriesql.model_dispatch_usage_fold_v2"
            ),
            ("managed_turn", None, "unknown", 11, 7),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_request_usage_fold"), (0,)
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,)
        )

    def test_absent_approval_never_dispatches(self) -> None:
        worker = self.managed_worker()
        result = asyncio.run(worker.run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(worker.fictional_model.calls, 0)
        self.assert_no_meaning()

    def test_unknown_usage_is_retained_without_acceptance(self) -> None:
        self.install()
        worker = self.managed_worker(
            observation=ManagedUsageObservation(turn_completion="completed")
        )
        result = asyncio.run(worker.run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT usage_state,input_tokens,managed_observation->>'inference_count_basis' FROM memoriesql.model_usage_events"
            ),
            ("unavailable", None, "unknown"),
        )
        self.assert_no_meaning()

    def test_usage_cannot_replace_trusted_source_delivery(self) -> None:
        class MissingReceipt:
            async def record_delivery(self, **kwargs: Any) -> None:
                pass

        self.install()
        worker = self.managed_worker(recorder=MissingReceipt())
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,)
        )
        self.assert_no_meaning()

    def test_expired_approval_never_dispatches(self) -> None:
        self.approval = self.approval.model_copy(
            update={"deadline": datetime.now(UTC) - timedelta(seconds=1)}
        )
        self.install()
        worker = self.managed_worker()
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(worker.fictional_model.calls, 0)
        self.assert_no_meaning()

    def test_revocation_during_turn_preserves_usage_but_denies_meaning(self) -> None:
        self.install()
        worker = self.managed_worker()

        async def revoke() -> None:
            self.db.execute(
                "UPDATE memoriesql.model_supervised_qualifications SET status='revoked'"
            )

        worker.fictional_model.before_return = revoke
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,)
        )
        self.assert_no_meaning()

    def test_existing_unsupervised_path_survives_schema26(self) -> None:
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT dispatch_boundary,supervised_qualification_id FROM memoriesql.model_provider_request_intents"
            ),
            ("single_inference", None),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_request_usage_fold"), (1,)
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.bead_versions"), (1,)
        )

    def test_runtime_roles_cannot_provision_approval(self) -> None:
        for role in ("memoriesql_application", "memoriesql_worker"):
            with (
                self.subTest(role=role),
                self.assertRaises(psycopg.errors.InsufficientPrivilege),
                self.db.transaction(),
            ):
                self.db.execute("SET LOCAL ROLE " + role)
                self.install()
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_supervised_qualifications"),
            (0,),
        )

    def test_nonexistent_or_cross_tenant_approver_cannot_authorize(self) -> None:
        absent = uuid.uuid4()
        other_tenant = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.users SELECT %s,user_id,status,display_name,created_at,revoked_at FROM memoriesql.users WHERE tenant_id=%s AND user_id=%s",
            (other_tenant, self.tenant, self.user),
        )
        self.db.execute(
            "INSERT INTO memoriesql.principals SELECT %s,%s,principal_kind,user_id,owner_user_id,status,created_at,revoked_at FROM memoriesql.principals WHERE tenant_id=%s AND principal_id=%s",
            (other_tenant, absent, self.tenant, self.principal),
        )
        for approver in (uuid.uuid4(), absent):
            with (
                self.subTest(approver=approver),
                self.assertRaises(psycopg.errors.ForeignKeyViolation),
            ):
                self.db.execute(
                    "INSERT INTO memoriesql.model_supervised_qualifications(tenant_id,workspace_id,access_scope_id,qualification_id,task_id,origin_principal_id,configuration,approved_by_principal_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        self.tenant,
                        self.workspace,
                        self.scope,
                        self.approval.qualification_id,
                        self.approval.semantic_task_id,
                        self.principal,
                        Jsonb(self.approval.model_dump(mode="json")),
                        approver,
                    ),
                )
        worker = self.managed_worker()
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(worker.fictional_model.calls, 0)
        self.assert_no_meaning()

    def test_approval_cannot_refresh_or_change_identity(self) -> None:
        self.install()
        with self.assertRaises(psycopg.Error):
            self.install(
                self.approval.model_copy(update={"qualification_id": uuid.uuid4()})
            )
        with self.assertRaises(psycopg.Error):
            self.db.execute(
                "UPDATE memoriesql.model_supervised_qualifications SET configuration=jsonb_set(configuration,'{reported_input_token_stop}','30000')"
            )
        with self.assertRaises(psycopg.Error):
            self.db.execute("DELETE FROM memoriesql.model_supervised_qualifications")
        self.db.execute(
            "UPDATE memoriesql.model_supervised_qualifications SET status='revoked'"
        )
        with self.assertRaises(psycopg.Error):
            self.db.execute(
                "UPDATE memoriesql.model_supervised_qualifications SET status='active'"
            )

    def test_accounted_ambiguous_turn_cannot_accept(self) -> None:
        self.install()
        worker = self.managed_worker(
            observation=ManagedUsageObservation(reported_total=units())
        )
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(
            self.row(
                "SELECT input_tokens,managed_observation->>'turn_completion' FROM memoriesql.model_usage_events"
            ),
            (11, "ambiguous"),
        )
        self.assert_no_meaning()

    def test_reasoning_is_in_generated_stop_and_retained(self) -> None:
        self.install()
        usage = NormalizedUsage(
            input_tokens=11,
            output_tokens=4000,
            total_tokens=4011,
            reasoning_tokens=3990,
        )
        worker = self.managed_worker(
            observation=ManagedUsageObservation(
                turn_completion="completed", reported_total=usage
            )
        )
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(
            self.row(
                "SELECT output_tokens,reasoning_tokens FROM memoriesql.model_usage_events"
            ),
            (4000, 3990),
        )
        self.assert_no_meaning()

    def test_estimated_usage_retained_as_estimate_not_acceptance(self) -> None:
        self.install()
        worker = self.managed_worker(
            observation=ManagedUsageObservation(
                turn_completion="completed", estimated_total=units()
            )
        )
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(
            self.row(
                "SELECT usage_provenance,input_tokens FROM memoriesql.model_usage_events"
            ),
            ("framework_estimated", 11),
        )
        self.assert_no_meaning()

    def test_exact_route_and_qualification_pins(self) -> None:
        worker = self.managed_worker()
        binding = worker._executor.model_profiles.resolve(
            SOURCE_REVISITING_TASK.model_profile
        ).binding
        with self.assertRaises(ValueError):
            replace(binding, supervision=None)
        with self.assertRaises(ValueError):
            replace(
                binding,
                request_target=self.target.model_copy(
                    update={"model_id": "another-model"}
                ),
            )
        self.install(
            self.approval.model_copy(update={"reported_input_token_stop": 19000})
        )
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(worker.fictional_model.calls, 0)
        self.assert_no_meaning()

    def test_allowance_is_atomic_and_replay_is_not_dispatch_permission(self) -> None:
        self.install()
        worker = self.managed_worker()
        original = worker._model_accounting.record_intent
        outcomes: list[Any] = []

        async def race(intent: Any) -> None:
            results = await asyncio.gather(
                original(intent),
                original(intent.model_copy(update={"request_id": uuid.uuid4()})),
                return_exceptions=True,
            )
            outcomes.extend(results)
            self.assertEqual(sum(r is None for r in results), 1, results)
            # Whichever intent committed, an identical replay must not authorize a turn.
            for candidate in (
                intent,
                intent.model_copy(update={"request_id": uuid.uuid4()}),
            ):
                with self.assertRaises(Exception):
                    await original(candidate)
            raise RuntimeError(
                "fictional crash after durable consumption, before dispatch"
            )

        worker._model_accounting.record_intent = race
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(worker.fictional_model.calls, 0)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (1,),
        )
        self.assert_no_meaning()

    def test_late_cancelled_return_drains_existing_cleanup_and_accounts(self) -> None:
        from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
            SemanticWorkerConfig,
        )

        self.install()

        async def scenario() -> None:
            started, release = asyncio.Event(), asyncio.Event()

            async def delayed() -> None:
                started.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue

            worker = self.managed_worker()
            worker.fictional_model.before_return = delayed
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

        asyncio.run(scenario())
        self.assertEqual(
            self.row(
                "SELECT input_tokens,output_tokens FROM memoriesql.model_usage_events"
            ),
            (11, 7),
        )
        self.assert_no_meaning()

    def test_direct_ledger_rejects_malformed_observations(self) -> None:
        valid = ManagedUsageObservation(
            turn_completion="completed", reported_total=units()
        ).model_dump(mode="json")
        cases = [
            dict(valid, turn_completion=None),
            dict(valid, inference_count_basis=None),
            dict(valid, request_observations=[{"request_id_hash": "a" * 64}]),
            dict(valid, underlying_inference_count=1),
            dict(valid, request_observations_complete=True),
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    self.row(
                        "SELECT memoriesql.managed_observation_valid(%s,%s,%s)",
                        (
                            Jsonb(value),
                            Jsonb(units().model_dump()),
                            "provider_reported",
                        ),
                    ),
                    (False,),
                )

    def test_restart_new_attempt_cannot_refresh_consumed_turn(self) -> None:
        self.install()
        worker = self.managed_worker()
        saved: dict[str, Any] = {}

        def crash(claimed: Any, result: Any, sink: Any, **kwargs: Any) -> Any:
            saved.update(claimed=claimed, result=result)
            raise RuntimeError("fictional crash before canonical apply")

        worker._persist_result = crash
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

        restarted = self.managed_worker()
        result = asyncio.run(restarted.run_once())
        self.assertNotEqual(result.task_status, "succeeded")
        self.assertNotEqual(result.attempt_id, old.fence.attempt_id)
        self.assertEqual(restarted.fictional_model.calls, 0)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (1,),
        )
        self.assert_no_meaning()


class ManagedClassificationTests(BeadClassification):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=23, target_version=26)
        self.setup_classification()

    def supervised_worker(
        self,
        *,
        input_stop: int = 20000,
        observation: ManagedUsageObservation | None = None,
    ) -> Any:
        from memoriesql.application.bead_classification import (
            CLASSIFICATION_TASK,
            CLASSIFIER_PROFILE,
        )
        from memoriesql.infrastructure.models.pydanticai_executor import (
            BoundedProviderModel,
            RealModelAdmission,
        )

        worker = self.worker()
        original = CHARACTERIZED_TEST_REQUEST_TARGET.model_dump()
        for key in (
            "max_input_tokens_per_request",
            "max_output_tokens_per_request",
            "transport_retries_disabled",
        ):
            original.pop(key)
        original["usage_provenance"] = UsageProvenance.PROVIDER_REPORTED
        managed = ManagedDispatchTarget(**original)
        hard = CHARACTERIZED_TEST_REQUEST_TARGET.model_copy(
            update={
                "max_input_tokens_per_request": 6000,
                "max_output_tokens_per_request": 1000,
                "usage_provenance": UsageProvenance.PROVIDER_REPORTED,
            }
        )
        approval = SupervisedQualification(
            qualification_id=uuid.uuid4(),
            semantic_task_id=self.activation.execution_task_id,
            routes=(
                SupervisedRoute(
                    reference=CLASSIFICATION_TASK.model_profile,
                    target=managed,
                    qualification_revision="fictional.managed.v1",
                ),
                SupervisedRoute(
                    reference=CLASSIFIER_PROFILE,
                    target=hard,
                    qualification_revision="fictional.hard.v1",
                ),
            ),
            deadline=datetime.now(UTC) + timedelta(minutes=3),
            reported_input_token_stop=input_stop,
            reported_generated_token_stop=4000,
            reported_cash_stop_microunits=100000,
        )
        self.db.execute(
            "INSERT INTO memoriesql.model_supervised_qualifications(tenant_id,workspace_id,access_scope_id,qualification_id,task_id,origin_principal_id,configuration,approved_by_principal_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                self.tenant,
                self.workspace,
                self.scope,
                approval.qualification_id,
                approval.semantic_task_id,
                self.principal,
                Jsonb(approval.model_dump(mode="json")),
                self.principal,
            ),
        )
        primary = FictionalManagedModel(
            self.response,
            observation
            or ManagedUsageObservation(
                turn_completion="completed",
                reported_total=units(),
                underlying_inference_count=3,
                inference_count_basis="provider_reported",
            ),
        )
        inner = FunctionModel(self.classify)

        class Hard(BoundedProviderModel):
            @property
            def model_name(self) -> str:
                return "fictional-hard"

            @property
            def system(self) -> str:
                return "fictional"

            async def request_bounded(
                self, messages: Any, settings: Any, parameters: Any, bounds: Any
            ) -> Any:
                return await inner.request(messages, settings, parameters)

        secondary = Hard(profile=inner.profile)
        worker._executor.model_profiles = PydanticAIModelProfileRegistry(
            (
                ModelProfileBinding(
                    reference=CLASSIFICATION_TASK.model_profile,
                    model=primary,
                    request_target=managed,
                    supervision=approval,
                    real_model_admission=ManagedModelAdmission(
                        primary,
                        CLASSIFICATION_TASK.model_profile,
                        managed,
                        "fictional.managed.v1",
                    ),
                ),
                ModelProfileBinding(
                    reference=CLASSIFIER_PROFILE,
                    model=secondary,
                    request_target=hard,
                    supervision=approval,
                    real_model_admission=RealModelAdmission(
                        secondary, CLASSIFIER_PROFILE, hard, "fictional.hard.v1"
                    ),
                ),
            )
        )
        worker.fictional_model = primary
        return worker

    def test_serial_managed_author_and_hard_classifier_preserve_classification(
        self,
    ) -> None:
        worker = self.supervised_worker()
        result = asyncio.run(worker.run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(worker.fictional_model.calls, 1)
        self.assertEqual(self.classifier_calls, 1)
        self.assertEqual(
            self.db.execute(
                "SELECT dispatch_boundary,underlying_inference_count FROM memoriesql.model_dispatch_usage_fold_v2 ORDER BY dispatch_boundary"
            ).fetchall(),
            [("managed_turn", 3), ("single_inference", 1)],
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_request_usage_fold"), (1,)
        )
        self.assertEqual(
            self.row(
                "SELECT classification_contribution#>>'{decision,outcome}' FROM memoriesql.bead_versions"
            ),
            ("selected",),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (1,)
        )

    def test_followup_must_fit_remaining_allowance(self) -> None:
        worker = self.supervised_worker(input_stop=6000)
        result = asyncio.run(worker.run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(worker.fictional_model.calls, 1)
        self.assertEqual(self.classifier_calls, 0)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (1,),
        )
        self.assert_no_meaning()

    def test_ambiguous_first_turn_never_dispatches_followup(self) -> None:
        worker = self.supervised_worker(
            observation=ManagedUsageObservation(reported_total=units())
        )
        self.assertNotEqual(asyncio.run(worker.run_once()).task_status, "succeeded")
        self.assertEqual(self.classifier_calls, 0)
        self.assert_no_meaning()


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        [
            loader.loadTestsFromTestCase(UsageObservationTests),
            *(
                ManagedClassificationTests(n)
                for n in ManagedClassificationTests.__dict__
                if n.startswith("test_")
            ),
            *(
                ManagedDispatchTests(n)
                for n in ManagedDispatchTests.__dict__
                if n.startswith("test_")
            ),
        ]
    )
