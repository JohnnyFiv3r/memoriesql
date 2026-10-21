"""Unrun additive fictional SQL cases; execution needs a separate DB grant.

No inherited test methods are rediscovered. Existing historical fixtures build
real pre-cutover authority and receipts; no forged task, output or receipt IDs.
All gated calls below start fresh transactions after installation. Even the late
output scenario does NOT hold an old SQL function across the install. Old-code
cutover specifications below preserve the original checkpoint. The separate
test_legacy7_cutover module now implements additive owned fictional schedules;
those native cases also remain UNRUN until a separate resource admission.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg

from memoriesql.application.local_entity_mentions import ActivateAuthoredMentions
from memoriesql.application.model_accounting import (
    AccountingPersistenceError,
    ProviderRequestTarget,
    UsageProvenance,
)
from memoriesql.application.relation_inspection import InspectBeadRelationsV2
from memoriesql.application.stored_bead_inspection import InspectStoredBead
from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    PostgresSemanticTaskQueue,
)
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
)
from memoriesql.infrastructure.postgres.model_accounting import (
    PostgresModelRequestAccounting,
)
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
)
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)

if TYPE_CHECKING:
    from tests.runtime import test_authored_relations as relation_fixtures
    from tests.runtime import test_authored_statement_kinds as fixtures
    from tests.runtime import test_source_revisiting as source_fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_authored_relations as relation_fixtures
    import test_authored_statement_kinds as fixtures
    import test_source_revisiting as source_fixtures
    from test_postgres_runtime import migrate


OLD_CODE_CUTOVER_RACE_SPECIFICATIONS = (
    ("activation_entered_before_wait", "SPEC_ONLY: B waits for the old allowing frame."),
    ("apply_checked_before_commit", "SPEC_ONLY: no old meaning commit after B."),
    ("intent_admission_or_replay", "SPEC_ONLY: no old dispatch admission after B."),
    ("new_policy_or_custom_caller", "SPEC_ONLY: complete ingress roster required."),
)

MEANING_TABLES = (
    "bead_versions",
    "bead_semantic_statements",
    "accepted_bead_semantics",
    "semantic_task_receipts",
    "entity_mentions",
)
ACCOUNTING_TABLES = (
    "model_provider_request_intents",
    "model_usage_events",
    "model_request_usage_fold",
)


class Legacy7Unavailable(unittest.TestCase):
    def fixture(self) -> fixtures.AuthoredStatementKinds:
        fixture = fixtures.AuthoredStatementKinds(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def install(self, fixture: fixtures.AuthoredStatementKinds) -> None:
        migrate(fixture.db, expected_current_version=43, target_version=45)

    def snapshot(self, fixture: Any, tables: tuple[str, ...]) -> tuple[Any, ...]:
        # Fictional owner-only assertions; these rows are never caller replies.
        return tuple(
            fixture.row(
                "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),"
                "'[]'::jsonb) FROM memoriesql." + table + " t"
            )[0]
            for table in tables
        )

    def relations_fixture(self) -> relation_fixtures.AuthoredRelations:
        fixture = relation_fixtures.AuthoredRelations(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        migrate(fixture.db, expected_current_version=27, target_version=43)
        return fixture

    def state(self, fixture: fixtures.AuthoredStatementKinds) -> list[Any]:
        return [
            fixture.row(
                "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),"
                "'[]'::jsonb) FROM memoriesql." + table + " t"
            )[0]
            for table in (
                "semantic_tasks",
                "complete_input_executions",
                "idempotency_receipts",
                "model_provider_request_intents",
                "model_usage_events",
            )
        ]

    def test_fresh_revision7_activation_has_no_effect(self) -> None:
        fixture = self.fixture()
        fixture.setup_revisiting("Four fictional trees.", activate=False)
        self.install(fixture)
        before = self.state(fixture)
        command = ActivateAuthoredMentions(
            idempotency_key="legacy7.fresh",
            binding_task_id=fixture.bound.task_id,
            dispatch_policy_id=fixture.dispatch_policy,
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            fixture.complete.activate_authored_mentions(command)
        self.assertEqual(self.state(fixture), before)

    def test_prior_successful_activation_replay_refuses_and_preserves_history(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        before = self.state(fixture)
        self.install(fixture)
        command = ActivateAuthoredMentions(
            idempotency_key="orchard.mentions.activate.v7",
            binding_task_id=fixture.bound.task_id,
            dispatch_policy_id=fixture.dispatch_policy,
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            fixture.complete.activate_authored_mentions(command)
        self.assertEqual(self.state(fixture), before)

    def test_queued_revision7_pauses_without_provider_execution(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        self.install(fixture)

        def forbidden_provider(messages: Any, info: Any) -> Any:
            raise AssertionError("revision7 must pause before fictional provider")

        result = asyncio.run(fixture.worker(forbidden_provider).run_once())
        self.assertEqual(result.task_status, "policy_paused", result)
        self.assertEqual(
            fixture.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"),
            (0,),
        )
        fixture.assert_no_meaning()

    def test_revision4_control_still_applies(self) -> None:
        fixture = self.fixture()
        fixture.revision = 4
        fixture.setup_mentions()
        self.install(fixture)
        result = asyncio.run(fixture.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)

    def test_successful_canonical_apply_replay_refuses_but_history_remains(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        saved: dict[str, Any] = {}
        original = PostgresSemanticTaskQueue.record_canonical_result

        def capture(queue: Any, fence: Any, result: Any, *, recorded_at: Any) -> str:
            saved.update(fence=fence, result=result)
            return original(queue, fence, result, recorded_at=recorded_at)

        with patch.object(PostgresSemanticTaskQueue, "record_canonical_result", capture):
            receipt = asyncio.run(fixture.worker().run_once())
        self.assertEqual(receipt.task_status, "succeeded", receipt)
        self.assertTrue(saved)

        def replay() -> str:
            with fixture.db.transaction():
                return str(fixture.worker_queue().record_canonical_result(
                    saved["fence"], saved["result"], recorded_at=datetime.now(UTC)
                ))

        # Positive baseline on the actual accepted output/fence, not activation.
        self.assertEqual(replay(), "succeeded")
        history = self.snapshot(fixture, MEANING_TABLES + ("idempotency_receipts",))
        accounting = self.snapshot(fixture, ACCOUNTING_TABLES)
        bead = fixture.row("SELECT bead_id FROM memoriesql.bead_versions")[0]
        reader = PostgresStoredBeadInspection(
            fixture.db, credential_sha256=fixture.secret_hash,
            workspace_id=fixture.workspace,
        )
        command = InspectStoredBead(bead_id=bead)
        authorized = reader.inspect(command)
        self.assertEqual(authorized.outcome, "available")
        self.assertIsNotNone(authorized.bead)
        assert authorized.bead is not None
        self.assertIsNotNone(authorized.bead.meaning)
        self.install(fixture)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege) as refused:
            replay()
        self.assertEqual(
            refused.exception.diag.message_primary,
            "source_revisiting_qualification_required",
        )
        self.assertEqual(reader.inspect(command), authorized)
        self.assertEqual(
            self.snapshot(fixture, MEANING_TABLES + ("idempotency_receipts",)), history
        )
        self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), accounting)

    def late_apply(self, provenance: UsageProvenance) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        target = ProviderRequestTarget.model_validate({
            **CHARACTERIZED_TEST_REQUEST_TARGET.model_dump(mode="json"),
            "usage_provenance": provenance,
        })
        # Existing FunctionModel composition, never a live provider or fabricated
        # ledger row. Its actual intent records this declared fictional target.
        with patch.object(source_fixtures, "CHARACTERIZED_TEST_REQUEST_TARGET", target):
            worker = fixture.worker()
        persist = worker._persist_result
        append = PostgresModelRequestAccounting.append_usage
        saved: dict[str, Any] = {"events": []}

        async def capture_event(port: Any, event: Any) -> None:
            await append(port, event)
            saved["events"].append(event)

        def install_then_persist(
            claimed: Any, result: Any, sink: Any, **kwargs: Any
        ) -> Any:
            self.assertEqual(result.status, "succeeded")
            self.assertIsNotNone(result.typed_output)
            self.assertTrue(saved["events"])
            saved.update(fence=claimed.fence, usage=result.usage)
            saved["accounting"] = self.snapshot(fixture, ACCOUNTING_TABLES)
            self.assertTrue(saved["accounting"][0])
            self.assertTrue(saved["accounting"][1])
            # Steady-state boundary only: no old SQL body is held by this hook.
            self.install(fixture)
            meaning = self.snapshot(fixture, MEANING_TABLES)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege) as refused:
                with fixture.db.transaction():
                    fixture.worker_queue().record_canonical_result(
                        claimed.fence, result, recorded_at=datetime.now(UTC)
                    )
            self.assertEqual(
                refused.exception.diag.message_primary,
                "source_revisiting_qualification_required",
            )
            self.assertEqual(self.snapshot(fixture, MEANING_TABLES), meaning)
            return persist(claimed, result, sink, **kwargs)

        async def run_and_observe() -> Any:
            receipt = await worker.run_once()
            self.assertEqual(await worker.wait_for_cleanup(), receipt)
            self.assertFalse(worker.cleanup_pending)
            return receipt

        with (
            patch.object(PostgresModelRequestAccounting, "append_usage", capture_event),
            patch.object(worker, "_persist_result", install_then_persist),
        ):
            receipt = asyncio.run(run_and_observe())
        self.assertEqual(receipt.task_status, "policy_paused", receipt)
        fixture.assert_no_meaning()
        self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), saved["accounting"])
        rows = fixture.db.execute(
            "SELECT usage_state,input_tokens,output_tokens FROM "
            "memoriesql.model_request_usage_fold WHERE task_id=%s",
            (fixture.activation.execution_task_id,),
        ).fetchall()
        self.assertTrue(rows)
        if provenance == UsageProvenance.PROVIDER_REPORTED:
            self.assertTrue(all(row[0] == "reported" for row in rows))
            self.assertGreater(sum(row[1] for row in rows), 0)
            self.assertEqual(sum(row[1] for row in rows), saved["usage"].input_tokens)
            self.assertEqual(sum(row[2] for row in rows), saved["usage"].output_tokens)
        else:
            self.assertTrue(all(row == ("unavailable", None, None) for row in rows))

        def accounting_port() -> PostgresModelRequestAccounting:
            return PostgresModelRequestAccounting(
                connection_factory=fixture.connection,
                credential_sha256=fixture.worker_secret,
                workspace_id=fixture.workspace,
                worker_id=saved["fence"].worker_id,
                worker_instance_id=saved["fence"].worker_instance_id,
            )

        async def lose_redelivery_ack() -> None:
            await accounting_port().append_usage(saved["events"][0])
            raise RuntimeError("fictional acknowledgement lost after durable redelivery")

        # Actual captured event/request/dedupe identity; no invented refund/event.
        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            asyncio.run(lose_redelivery_ack())
        for event in saved["events"]:
            asyncio.run(accounting_port().append_usage(event))
        with fixture.db.transaction():
            self.assertEqual(fixture.worker_queue().reap_expired(
                worker_id="orchard.reaper", limit=1, reaped_at=datetime.now(UTC)
            ), 0)
        replacement = fixture.worker()
        idle = asyncio.run(replacement.run_once())
        self.assertEqual(idle.cycle_status, "idle", idle)
        self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), saved["accounting"])
        fixture.assert_no_meaning()
        # A replacement object/reaper observation is not fresh-process recovery.
        # Explicit resume after REAL fictional request consumption cannot reset
        # the ledger, original request ceilings, input budget or attempt count.
        task = fixture.activation.execution_task_id
        query = (
            "SELECT attempt_count,max_attempts,input_hash,"
            "input_payload->'requested_budget' FROM memoriesql.semantic_tasks "
            "WHERE task_id=%s"
        )
        limits = fixture.row(query, (task,))
        with fixture.db.transaction():
            fixture.begin()
            resumed = PostgresSemanticTaskQueue(fixture.db).resume(
                tenant_id=fixture.tenant, task_id=task,
                resumed_at=datetime.now(UTC),
            )
        self.assertEqual(resumed, limits[0] < limits[1])
        self.assertEqual(fixture.row(query, (task,)), limits)
        if resumed:
            def forbidden_provider(messages: Any, info: Any) -> Any:
                raise AssertionError("resumed7 must not obtain another provider request")

            again = asyncio.run(fixture.worker(forbidden_provider).run_once())
            self.assertEqual(again.task_status, "policy_paused", again)
            current = fixture.row(query, (task,))
            self.assertGreaterEqual(current[0], limits[0])
            self.assertEqual(current[1:], limits[1:])
        for event in saved["events"]:
            asyncio.run(accounting_port().append_usage(event))
        self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), saved["accounting"])
        fixture.assert_no_meaning()

    def test_denied_late_apply_preserves_reported_usage_and_redelivery(self) -> None:
        self.late_apply(UsageProvenance.PROVIDER_REPORTED)

    def test_denied_late_apply_preserves_unknown_usage_and_redelivery(self) -> None:
        self.late_apply(UsageProvenance.UNAVAILABLE)

    def test_explicit_resume_never_resets_attempt_or_model_allowances(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        self.install(fixture)

        def limits() -> tuple[Any, ...]:
            return fixture.row(
                "SELECT attempt_count,max_attempts,input_hash,"
                "input_payload->'requested_budget' FROM memoriesql.semantic_tasks "
                "WHERE task_id=%s", (fixture.activation.execution_task_id,),
            )

        def forbidden_provider(messages: Any, info: Any) -> Any:
            raise AssertionError("resumed7 must not dispatch a fictional provider")

        original = limits()
        for _ in range(2):
            starting = limits()
            receipt = asyncio.run(fixture.worker(forbidden_provider).run_once())
            self.assertEqual(receipt.task_status, "policy_paused", receipt)
            paused = limits()
            self.assertGreaterEqual(paused[0], starting[0])
            self.assertLessEqual(paused[0], paused[1])
            self.assertEqual(paused[1:], original[1:])
            attempts = self.snapshot(fixture, ("semantic_task_attempts",))
            runs = self.snapshot(fixture, ("semantic_task_runs",))
            with fixture.db.transaction():
                fixture.begin()
                resumed = PostgresSemanticTaskQueue(fixture.db).resume(
                    tenant_id=fixture.tenant,
                    task_id=fixture.activation.execution_task_id,
                    resumed_at=datetime.now(UTC),
                )
            self.assertEqual(resumed, paused[0] < paused[1])
            self.assertEqual(limits(), paused)
            self.assertEqual(self.snapshot(fixture, ("semantic_task_attempts",)), attempts)
            self.assertEqual(self.snapshot(fixture, ("semantic_task_runs",)), runs)
            self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), ([], [], []))
            if not resumed:
                self.assertEqual(paused[0], paused[1])
                break
        fixture.assert_no_meaning()

    def test_revision6_relation_history_and_new_authoring_remain_available(self) -> None:
        fixture = self.relations_fixture()
        cause, effect, _claim, relation = fixture.related_pair()
        before = fixture.inspect(effect)
        self.assertEqual(before.outcome, "available")
        self.assertIn(relation, [item.relation_id for item in before.relations])
        history = self.snapshot(fixture, MEANING_TABLES + relation_fixtures.RELATION_TABLES)
        migrate(fixture.db, expected_current_version=43, target_version=45)
        self.assertIsNotNone(before.known_at)
        self.assertEqual(fixture.inspect(effect, known_at=before.known_at), before)
        self.assertEqual(
            self.snapshot(fixture, MEANING_TABLES + relation_fixtures.RELATION_TABLES),
            history,
        )
        fresh = fixture.author(
            "Fictional next crew also waited at the north gate.", "after-gate",
            candidates=(cause,),
            plan=lambda extras, bead, candidates: fixture.relate(
                extras, bead, candidates[0], "caused_by"
            ),
        )
        self.assertEqual(fixture.inspect(fresh).outcome, "available")
        self.assertEqual(len(fixture.inspect(fresh).relations), 1)
        # Compare the same knowledge frame; current authorization still applies.
        self.assertEqual(fixture.inspect(effect, known_at=before.known_at), before)

    def test_hidden_candidate_family_preserves_missing_equivalence(self) -> None:
        fixture = self.relations_fixture()
        hidden_text = "Fictional hidden candidate: west orchard flooded."
        hidden_source = fixture.add_source("legacy7.hidden-candidate")
        target = fixture.author("Fictional target: audit passed.", "target")
        hidden = fixture.author(hidden_text, "hidden", source=hidden_source)

        def plan(extras: Any, bead: Any, candidates: Any) -> None:
            candidate = next(c for c in candidates if c["bead_id"] == str(target))
            fixture.relate(extras, bead, candidate, "caused_by")
            extras["relations"][0]["rationale"] = "Unless " + hidden_text

        authoring = fixture.author(
            "Fictional authoring note: ledger balanced.", "authoring",
            candidates=(target, hidden), plan=plan,
        )
        reader = PostgresRelationAssessments(
            fixture.db, credential_sha256=fixture.secret_hash,
            workspace_id=fixture.workspace,
        )

        def both(bead: uuid.UUID) -> tuple[Any, Any]:
            return (reader.inspect_relations(InspectBeadRelationsV2(bead_id=bead)),
                    fixture.inspect(bead))

        for bead in (authoring, target):
            current, legacy = both(bead)
            self.assertEqual((current.outcome, legacy.outcome), ("available", "available"))
            self.assertIn(hidden_text, current.model_dump_json())
        fixture.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',"
            "revoked_at=clock_timestamp() WHERE resource_id=%s", (hidden_source,),
        )
        notices: list[str] = []

        def heard(notice: psycopg.errors.Diagnostic) -> None:
            notices.append(str(notice.message_primary))

        def assert_missing_equivalence() -> None:
            audit = "SELECT count(*) FROM memoriesql.authorization_audit_events"
            before = fixture.row(audit)[0]
            missing = both(uuid.uuid4())
            missing_audits = fixture.row(audit)[0] - before
            for bead in (authoring, target):
                before = fixture.row(audit)[0]
                self.assertEqual(both(bead), missing)
                self.assertEqual(fixture.row(audit)[0] - before, missing_audits)

        history = self.snapshot(fixture, MEANING_TABLES + relation_fixtures.RELATION_TABLES)
        fixture.db.add_notice_handler(heard)
        fixture.db.execute("SET client_min_messages=log")
        try:
            assert_missing_equivalence()
            migrate(fixture.db, expected_current_version=43, target_version=45)
            assert_missing_equivalence()
        finally:
            fixture.db.execute("RESET client_min_messages")
            fixture.db.remove_notice_handler(heard)
        self.assertEqual(
            self.snapshot(fixture, MEANING_TABLES + relation_fixtures.RELATION_TABLES),
            history,
        )
        relation = fixture.row(
            "SELECT relation_id FROM memoriesql.bead_relations "
            "WHERE authoring_bead_id=%s", (authoring,),
        )[0]
        forbidden = (
            hidden_text, str(hidden), str(hidden_source), str(relation),
            "withheld", "memoriesql relation inspection",
        )
        self.assertFalse([notice for notice in notices if any(x in notice for x in forbidden)])

    def first_write_ack_loss(self, loss: str) -> None:
        """Fault only after a real FIRST write; no old SQL frame spans install."""
        self.assertIn(loss, ("intent", "usage"))
        fixture = self.fixture()
        fixture.setup_mentions()
        self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), ([], [], []))
        saved: dict[str, Any] = {
            "provider_calls": 0, "faults": 0, "persist_calls": 0,
        }
        first_usage_committed = asyncio.Event()
        release_usage_ack = asyncio.Event()

        def provider(messages: Any, info: Any) -> Any:
            saved["provider_calls"] += 1
            self.assertEqual(loss, "usage", "lost intent ACK must prevent dispatch")
            return fixture.response(messages, info)

        worker = fixture.worker(provider)
        record = PostgresModelRequestAccounting.record_intent
        append = PostgresModelRequestAccounting.append_usage
        persist = worker._persist_result

        def request_count(table: str, request_id: Any) -> int:
            return int(fixture.row(
                "SELECT count(*) FROM memoriesql." + table + " WHERE request_id=%s",
                (request_id,),
            )[0])

        def install_and_lose_ack(kind: str) -> None:
            self.assertEqual(saved["faults"], 0)
            saved["faults"] += 1
            saved["committed"] = self.snapshot(fixture, ACCOUNTING_TABLES)
            # The awaited adapter has finished its actual transaction. This is
            # loss of its first reply, not duplicate delivery or cutover proof.
            self.install(fixture)
            saved["gate_installed"] = True
            raise AccountingPersistenceError(
                "fictional first " + kind + " acknowledgement lost after commit"
            )

        async def first_intent(port: Any, intent: Any) -> None:
            self.assertNotIn("intent", saved, "no second intent may be admitted")
            self.assertEqual(request_count("model_provider_request_intents", intent.request_id), 0)
            await record(port, intent)
            self.assertEqual(request_count("model_provider_request_intents", intent.request_id), 1)
            saved.update(intent=intent, intent_port=port)
            if loss == "intent":
                install_and_lose_ack("intent")

        async def first_usage(port: Any, event: Any) -> None:
            self.assertEqual(loss, "usage")
            self.assertNotIn("event", saved)
            self.assertEqual(event.request_id, saved["intent"].request_id)
            self.assertEqual(request_count("model_usage_events", event.request_id), 0)
            await append(port, event)
            self.assertEqual(request_count("model_usage_events", event.request_id), 1)
            saved.update(event=event, usage_port=port)
            first_usage_committed.set()
            # Delay only the original reply. The foreground caller can request
            # cancellation while the real, already-committed write stays owned.
            await release_usage_ack.wait()
            install_and_lose_ack("usage")

        def observe_persistence(
            claimed: Any, result: Any, sink: Any, **kwargs: Any
        ) -> Any:
            self.assertTrue(saved["gate_installed"])
            saved["persist_calls"] += 1
            self.assertEqual(str(claimed.fence.task_id), saved["intent"].semantic_task_id)
            self.assertEqual(str(claimed.fence.attempt_id), saved["intent"].semantic_attempt_id)
            with fixture.db.transaction():
                authority = fixture.worker_queue().reauthorize(
                    claimed.fence, phase="outcome", checked_at=datetime.now(UTC)
                )
            self.assertEqual(authority, "policy_paused")
            return persist(claimed, result, sink, **kwargs)

        async def scenario() -> None:
            with (
                patch.object(PostgresModelRequestAccounting, "record_intent", first_intent),
                patch.object(PostgresModelRequestAccounting, "append_usage", first_usage),
                patch.object(worker, "_persist_result", observe_persistence),
            ):
                if loss == "usage":
                    # The sticky accounting-error contract is for owned cleanup.
                    # Enter it by actual caller cancellation, not a private flag.
                    foreground = asyncio.create_task(worker.run_once())
                    try:
                        await asyncio.wait_for(first_usage_committed.wait(), timeout=10)
                        foreground.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(asyncio.shield(foreground), timeout=10)
                        self.assertTrue(worker.cleanup_pending)
                    finally:
                        # Even a failed coordination assertion must unblock the
                        # original writer; never strand its owned cleanup.
                        release_usage_ack.set()
                    with self.assertRaisesRegex(
                        AccountingPersistenceError, "late model usage persistence failed"
                    ):
                        await asyncio.wait_for(worker.wait_for_cleanup(), timeout=10)
                else:
                    receipt = await worker.run_once()
                    self.assertEqual(receipt.task_status, "policy_paused", receipt)
                    self.assertEqual(await worker.wait_for_cleanup(), receipt)

            self.assertEqual(saved["faults"], 1)
            self.assertEqual(saved["persist_calls"], 1)
            self.assertEqual(saved["provider_calls"], 1 if loss == "usage" else 0)
            self.assertEqual(
                fixture.row("SELECT status FROM memoriesql.semantic_tasks WHERE task_id=%s",
                            (fixture.activation.execution_task_id,)),
                ("policy_paused",),
            )
            fixture.assert_no_meaning()
            self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), saved["committed"])
            intent = saved["intent"]
            folded = fixture.row(
                "SELECT usage_state,outcome,input_tokens,output_tokens FROM "
                "memoriesql.model_request_usage_fold WHERE request_id=%s",
                (intent.request_id,),
            )
            if loss == "intent":
                self.assertEqual(request_count("model_usage_events", intent.request_id), 0)
                self.assertEqual(folded, ("uncertain", "unknown", None, None))
            else:
                self.assertEqual(request_count("model_usage_events", intent.request_id), 1)
                self.assertEqual(folded, ("unavailable", "succeeded", None, None))

            # The first intent's original request/attempt/run/worker attribution
            # is retained, but its endpoint cannot replay success after the gate.
            with self.assertRaises(AccountingPersistenceError):
                await saved["intent_port"].record_intent(intent)
            if loss == "usage":
                # Only NOW is a duplicate of the first committed event delivered.
                # Reconciliation cannot clear the old worker's accounting error.
                await saved["usage_port"].append_usage(saved["event"])
                with self.assertRaises(AccountingPersistenceError):
                    await worker.wait_for_cleanup()
                with self.assertRaises(AccountingPersistenceError):
                    await worker.run_once()
            else:
                idle = await worker.run_once()
                self.assertEqual(idle.cycle_status, "idle", idle)
            self.assertEqual(self.snapshot(fixture, ACCOUNTING_TABLES), saved["committed"])
            self.assertEqual(saved["provider_calls"], 1 if loss == "usage" else 0)
            fixture.assert_no_meaning()

        asyncio.run(scenario())

    def test_original_intent_ack_loss_retains_uncertain_usage_after_gate(self) -> None:
        self.first_write_ack_loss("intent")

    def test_first_usage_ack_loss_preserves_event_and_worker_error_after_gate(self) -> None:
        self.first_write_ack_loss("usage")
