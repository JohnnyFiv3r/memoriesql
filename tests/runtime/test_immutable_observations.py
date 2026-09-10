"""Public fictional orchard proofs for the installed schema-15 transition."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import psycopg
from pydantic import BaseModel

from memoriesql.application.authorization import uuid7
from memoriesql.application.builtin_semantic_tasks import (
    CANONICAL_AUTHORING_TASK,
    load_builtin_semantic_task_registry,
)
from memoriesql.application.canonical_transactions import (
    CanonicalSemanticAuthoringPayload,
)
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.observation_commands import (
    AcceptedBeadPin,
    AcceptSourceEventV2Command,
    AuthorInitialObservationsCommand,
    CorrectObservationCommand,
    ObservationCorrectionInput,
)
from memoriesql.application.observation_tasks import (
    INITIAL_OBSERVATIONS_TASK,
    OBSERVATION_CORRECTION_TASK,
    load_observation_task_registry,
)
from memoriesql.application.semantic_task_contracts import (
    EvidenceManifest,
    EvidenceReference,
    SemanticAgentContractIdentity,
    SemanticResultStatus,
    SemanticRootRunCorrelation,
    SemanticRunEvent,
    SemanticRunEventKind,
    SemanticTaskInput,
    SemanticTaskResult,
    canonical_json_bytes,
    canonical_sha256,
)
from memoriesql.infrastructure.jobs.postgres_semantic_queue import EnqueueSemanticTask
from memoriesql.infrastructure.postgres.canonical_transactions import (
    PostgresCanonicalTransactions,
)
from memoriesql.infrastructure.postgres.migration_runner import migrate

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import PostgresRuntime
else:
    from test_postgres_runtime import PostgresRuntime


class ImmutableObservations(PostgresRuntime):
    def setUp(self) -> None:
        super().setUp()
        if (
            self._testMethodName
            != "test_upgrade_preserves_old_versions_payloads_and_receipts"
        ):
            migrate(self.db, expected_current_version=14, target_version=15)

    def setup_initial(self, *, legacy: bool = False) -> tuple[Any, Any, Any, Any]:
        capture: Any = self.command()
        if legacy:
            accepted = self.accept(capture)
        else:
            capture = AcceptSourceEventV2Command.model_validate(
                {
                    **capture.model_dump(mode="json"),
                    "contract_version": 2,
                    "expected_schema_version": 15,
                }
            )
            with self.db.transaction():
                self.begin()
                accepted = PostgresCanonicalTransactions(
                    self.db
                ).accept_source_event_v2(capture, recorded_at=datetime.now(UTC))
        self.pair_worker()
        if not legacy:
            self.allow_correction_claims()
        claimed = self.claim()
        result = self.result_for(
            accepted,
            capture,
            claimed,
            CANONICAL_AUTHORING_TASK if legacy else INITIAL_OBSERVATIONS_TASK,
        )
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertEqual(
                queue.record_canonical_result(
                    claimed.fence, result, recorded_at=datetime.now(UTC)
                ),
                "succeeded",
            )
        return capture, accepted, claimed, result

    def allow_correction_claims(self) -> None:
        self.db.execute(
            """
            INSERT INTO memoriesql.semantic_worker_claim_policies
                (tenant_id,workspace_id,principal_id,pairing_grant_id,semantic_registry_hash,task_kind,contract_revision,queue_name)
            SELECT claim.tenant_id,claim.workspace_id,claim.principal_id,claim.pairing_grant_id,
                   policy.semantic_registry_hash,policy.task_kind,policy.contract_revision,policy.queue_name
            FROM memoriesql.semantic_worker_claim_policies AS claim
            JOIN memoriesql.semantic_task_admission_policies AS policy
              ON policy.semantic_registry_hash=claim.semantic_registry_hash
             AND policy.task_kind='memory.semantic.correct-observation'
            WHERE claim.tenant_id=%s AND claim.principal_id=%s AND claim.contract_revision=2
            ON CONFLICT DO NOTHING
        """,
            (self.tenant, self.worker_principal),
        )

    def result_for(
        self,
        accepted: Any,
        capture: Any,
        claimed: Any,
        definition: Any,
        *,
        pins: tuple[AcceptedBeadPin, ...] = (),
        text: str = "Four fictional trees.",
        expected: int = 0,
        superseded_statement: str | None = None,
    ) -> Any:
        statement = str(uuid.uuid4())
        clause = {"text": text, "statement_ids": [statement]}
        statement_data: dict[str, Any] = {
            "statement_id": statement,
            "statement_kind": "observation",
            "statement_text": text,
            "evidence": [
                {
                    "source_unit_id": str(accepted.source_unit_ids[0]),
                    "content_hash": capture.units[0].content_hash,
                }
            ],
            "model_run_ref": "orchard.run",
        }
        if superseded_statement is not None:
            statement_data.update(
                statement_kind="correction",
                supersedes_statement_id=superseded_statement,
                correction_reason="Fictional counting correction.",
            )
        data: dict[str, Any] = {
            "annotations": [
                {
                    "bead_id": str(accepted.bead_ids[0]),
                    "event_id": str(accepted.event_id),
                    "source_unit_id": str(accepted.source_unit_ids[0]),
                    "bead_version_id": str(uuid.uuid4()),
                    "expected_bead_version": expected,
                    "bead_type_key": "observation",
                    "bead_type_revision": 1,
                    "statements": [statement_data],
                    "render": {"title": clause, "summary": [clause]},
                }
            ]
        }
        if pins:
            data.update(
                supersedes=[pin.model_dump(mode="json") for pin in pins],
                correction_reason="Fictional count reconciliation.",
            )
        payload = definition.output_contract.model_type.model_validate(data)
        with self.db.transaction():
            queue = self.worker_queue()
            event = SemanticRunEvent(
                event_kind=SemanticRunEventKind.RUN_STARTED,
                task_id=str(claimed.fence.task_id),
                attempt_id=str(claimed.fence.attempt_id),
                correlation=SemanticRootRunCorrelation(
                    run_id="orchard.run",
                    run_role="direct_leaf",
                    agent_contract=SemanticAgentContractIdentity(
                        agent_key=definition.leaf_agent_key,
                        input_contract=definition.input_contract.reference,
                        output_contract=definition.output_contract.reference,
                    ),
                    model_profile=definition.model_profile,
                ),
            )
            self.assertTrue(
                queue.record_run_event(
                    claimed.fence, event, recorded_at=datetime.now(UTC)
                )
            )
        return SemanticTaskResult[definition.output_contract.model_type](
            status=SemanticResultStatus.SUCCEEDED,
            task_id=str(claimed.fence.task_id),
            attempt_id=str(claimed.fence.attempt_id),
            task_kind=definition.task_kind,
            contract_revision=definition.contract_revision,
            output_contract_hash=definition.output_contract.schema_hash,
            typed_output=payload,
            output_hash=canonical_sha256(payload.model_dump(mode="json")),
            used_evidence_refs=(str(accepted.source_unit_ids[0]),),
            model_run_refs=("orchard.run",),
        )

    def enqueue_authorship(
        self,
        capture: Any,
        accepted: Any,
        *,
        pins: tuple[AcceptedBeadPin, ...] = (),
        legacy: bool = False,
    ) -> tuple[Any, Any]:
        definition: Any = (
            CANONICAL_AUTHORING_TASK if legacy else OBSERVATION_CORRECTION_TASK
        )
        modules = BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
        registry = (
            load_builtin_semantic_task_registry(modules)
            if legacy
            else load_observation_task_registry(modules)
        )
        bead = accepted.bead_ids[0] if legacy else uuid.uuid4()
        target = accepted.model_copy(update={"bead_ids": (bead,)})
        payload_data: dict[str, Any] = {
            "event_id": accepted.event_id,
            "bead_ids": (bead,),
            "source_unit_ids": accepted.source_unit_ids,
        }
        payload = (
            CanonicalSemanticAuthoringPayload.model_validate(payload_data)
            if legacy
            else ObservationCorrectionInput.model_validate(
                {**payload_data, "supersedes": pins}
            )
        )
        task_id = uuid7()
        task_input = definition.input_contract.model_type(
            task_id=str(task_id),
            task_kind=definition.task_kind,
            contract_revision=definition.contract_revision,
            target_reference=str(accepted.event_id),
            expected_target_revision=1,
            evidence_manifest=EvidenceManifest(
                manifest_id="orchard.correction." + str(task_id),
                revision=1,
                references=(
                    EvidenceReference(
                        reference_id=str(accepted.source_unit_ids[0]),
                        content_hash=capture.units[0].content_hash,
                        declared_characters=len(capture.units[0].content_text),
                    ),
                ),
            ),
            payload=payload,
        )
        with self.db.transaction():
            self.begin()
            enqueue_at = datetime.now(UTC)
            registry_hash = registry.registry_hash
            if legacy:
                historical = self.db.execute(
                    "SELECT semantic_registry_hash FROM memoriesql.semantic_tasks WHERE task_id=%s",
                    (accepted.semantic_task_id,),
                ).fetchone()
                assert historical is not None
                registry_hash = str(historical[0])
            from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
                PostgresSemanticTaskQueue,
            )

            PostgresSemanticTaskQueue(self.db).enqueue(
                EnqueueSemanticTask(
                    task_id=task_id,
                    idempotency_key="orchard.authorship." + str(task_id),
                    definition=cast(Any, definition),
                    semantic_registry_hash=registry_hash,
                    task_input=cast(SemanticTaskInput[BaseModel], task_input),
                    access_scope_id=self.scope,
                    available_at=enqueue_at,
                ),
                recorded_at=enqueue_at,
            )
        return target, self.claim()

    def pin(self, result: Any) -> AcceptedBeadPin:
        draft = result.typed_output.annotations[0]
        return AcceptedBeadPin(
            bead_id=draft.bead_id, bead_version_id=draft.bead_version_id
        )

    def command_for(self, claimed: Any, result: Any) -> Any:
        model = (
            CorrectObservationCommand
            if result.task_kind == "memory.semantic.correct-observation"
            else AuthorInitialObservationsCommand
        )
        return model.model_validate(
            {
                "idempotency_key": "semantic-apply." + str(claimed.fence.attempt_id),
                "tenant_id": self.tenant,
                "workspace_id": self.workspace,
                "access_scope_id": self.scope,
                "task_id": claimed.fence.task_id,
                "attempt_id": claimed.fence.attempt_id,
                "lease_generation": claimed.fence.lease_generation,
                "task_kind": result.task_kind,
                "contract_revision": result.contract_revision,
                "output_contract_hash": result.output_contract_hash,
                "semantic_result_hash": result.output_hash,
                "semantic_payload_canonical_json": canonical_json_bytes(
                    result.typed_output.model_dump(mode="json")
                ).decode(),
                "used_evidence_refs": result.used_evidence_refs,
                "model_run_refs": result.model_run_refs,
                "payload": result.typed_output.model_dump(mode="json"),
            }
        )

    def persist(self, claimed: Any, result: Any) -> Any:
        with self.db.transaction():
            self.worker_queue()
            adapter = PostgresCanonicalTransactions(self.db)
            apply = (
                adapter.correct_observation
                if result.task_kind == "memory.semantic.correct-observation"
                else adapter.author_initial_observations
            )
            return apply(
                self.command_for(claimed, result),
                worker_id=claimed.fence.worker_id,
                worker_instance_id=claimed.fence.worker_instance_id,
                recorded_at=datetime.now(UTC),
            )

    def test_initial_replay_same_evidence_branches_and_reconciliation(self) -> None:
        capture, accepted, initial_claim, initial_result = self.setup_initial()
        replay = self.persist(initial_claim, initial_result)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.operation, "initial_observations.apply.v2")
        original = self.pin(initial_result)
        # Both branches were authorized against the same immutable target before
        # either completed. A later branch must not invalidate its independent peer.
        branches = [
            self.enqueue_authorship(capture, accepted, pins=(original,))
            for _ in range(2)
        ]
        results = []
        for (target, claimed), wording in zip(
            branches, ("Three fictional trees.", "Five fictional trees."), strict=True
        ):
            result = self.result_for(
                target,
                capture,
                claimed,
                OBSERVATION_CORRECTION_TASK,
                pins=(original,),
                text=wording,
            )
            receipt = self.persist(claimed, result)
            self.assertFalse(receipt.replayed)
            self.assertEqual(receipt.supersedes, (original,))
            self.assertEqual(receipt.bead_ids, target.bead_ids)
            self.assertTrue(self.persist(claimed, result).replayed)
            results.append(result)
        pins = tuple(self.pin(result) for result in results)
        target, claimed = self.enqueue_authorship(capture, accepted, pins=pins)
        reconciled = self.result_for(
            target,
            capture,
            claimed,
            OBSERVATION_CORRECTION_TASK,
            pins=pins,
            text="Four fictional trees, with the two branch counts reconciled.",
        )
        # Exercise the worker's existing canonical sink dispatch as well as adapters.
        with self.db.transaction():
            self.assertEqual(
                self.worker_queue().record_canonical_result(
                    claimed.fence, reconciled, recorded_at=datetime.now(UTC)
                ),
                "succeeded",
            )
        self.assertEqual(self.persist(claimed, reconciled).supersedes, pins)
        with self.db.transaction():
            self.begin()
            adapter = PostgresCanonicalTransactions(self.db)
            self.assertEqual(
                adapter.accept_source_event_v2(
                    capture, recorded_at=datetime.now(UTC)
                ).bead_ids,
                accepted.bead_ids,
            )
            # New idempotency key exercises natural occurrence replay after branches.
            natural = capture.model_copy(
                update={"idempotency_key": "orchard.natural-replay", "checkpoint": None}
            )
            self.assertEqual(
                adapter.accept_source_event_v2(
                    natural, recorded_at=datetime.now(UTC)
                ).bead_ids,
                accepted.bead_ids,
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT source_unit_count,observation_unit_count,bead_count FROM memoriesql.source_event_cardinality"
                ).fetchone(),
                (1, 1, 1),
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT count(*) FROM memoriesql.bead_supersessions"
                ).fetchone(),
                (4,),
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT count(*) FROM memoriesql.current_bead_versions"
                ).fetchone(),
                (4,),
            )
        self.assertEqual(
            self.db.execute(
                "SELECT summary FROM memoriesql.bead_versions WHERE bead_version_id=%s",
                (original.bead_version_id,),
            ).fetchone(),
            ("Four fictional trees.",),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.semantic_tasks WHERE status='succeeded'"
            ).fetchone(),
            (4,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.semantic_task_runs WHERE NOT settled"
            ).fetchone(),
            (0,),
        )

    def pending_correction(self) -> tuple[Any, Any, Any, Any, Any]:
        capture, accepted, _, original = self.setup_initial()
        pins = (self.pin(original),)
        target, claimed = self.enqueue_authorship(capture, accepted, pins=pins)
        result = self.result_for(
            target,
            capture,
            claimed,
            OBSERVATION_CORRECTION_TASK,
            pins=pins,
            text="Three fictional trees.",
        )
        return capture, accepted, target, claimed, result

    def assert_no_correction(self, claimed: Any) -> None:
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.beads WHERE origin_kind='correction'"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.bead_supersessions"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT result_attempt_id FROM memoriesql.semantic_tasks WHERE task_id=%s",
                (claimed.fence.task_id,),
            ).fetchone(),
            (None,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT run_status,settled FROM memoriesql.semantic_task_runs WHERE task_id=%s",
                (claimed.fence.task_id,),
            ).fetchone(),
            ("running", False),
        )

    def test_stale_generation_and_tampered_target_roll_back(self) -> None:
        _, _, _, claimed, result = self.pending_correction()
        command = self.command_for(claimed, result)
        for mutation in (
            {"lease_generation": command.lease_generation + 1},
            {"tenant_id": uuid.uuid4()},
            {"output_contract_hash": "0" * 64},
        ):
            with self.subTest(mutation=next(iter(mutation))):
                with self.assertRaises(psycopg.Error), self.db.transaction():
                    self.worker_queue()
                    PostgresCanonicalTransactions(self.db).correct_observation(
                        command.model_copy(update=mutation),
                        worker_id=claimed.fence.worker_id,
                        worker_instance_id=claimed.fence.worker_instance_id,
                        recorded_at=datetime.now(UTC),
                    )
                self.assert_no_correction(claimed)
        payload = result.typed_output.model_copy(
            update={
                "supersedes": (
                    AcceptedBeadPin(bead_id=uuid.uuid4(), bead_version_id=uuid.uuid4()),
                )
            }
        )
        tampered = result.model_copy(
            update={
                "typed_output": payload,
                "output_hash": canonical_sha256(payload.model_dump(mode="json")),
            }
        )
        with self.assertRaisesRegex(psycopg.Error, "targets do not match"):
            self.persist(claimed, tampered)
        self.assert_no_correction(claimed)
        self.assertFalse(self.persist(claimed, result).replayed)

    def test_cancellation_and_expired_lease_deny_correction(self) -> None:
        _, _, _, claimed, result = self.pending_correction()
        # Set only this fictional task's lease into the past; no sleeps or weaker limits.
        with self.db.transaction():
            self.db.execute(
                "UPDATE memoriesql.semantic_tasks SET heartbeat_at=clock_timestamp()-interval '2 seconds',lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s",
                (claimed.fence.task_id,),
            )
        with self.assertRaisesRegex(psycopg.Error, "stale_fence"):
            self.persist(claimed, result)
        self.assert_no_correction(claimed)
        with self.db.transaction():
            self.begin()
            from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
                PostgresSemanticTaskQueue,
            )

            status = PostgresSemanticTaskQueue(self.db).cancel(
                tenant_id=self.tenant,
                task_id=claimed.fence.task_id,
                reason="orchard.cancel",
                cancelled_at=datetime.now(UTC),
            )
            self.assertNotEqual(status, "unavailable")
        with self.assertRaisesRegex(psycopg.Error, "stale_fence"):
            self.persist(claimed, result)
        self.assert_no_correction(claimed)

    def test_forced_rls_and_revoked_target_authority(self) -> None:
        _, _, _, claimed, result = self.pending_correction()
        for table in ("accepted_bead_semantics", "bead_supersessions"):
            self.assertEqual(
                self.db.execute(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                    ("memoriesql." + table,),
                ).fetchone(),
                (True, True),
            )
            with self.db.transaction():
                self.db.execute("SET LOCAL ROLE memoriesql_application")
                self.assertEqual(
                    self.db.execute(
                        "SELECT count(*) FROM memoriesql." + table
                    ).fetchone(),
                    (0,),
                )
            with (
                self.assertRaises(psycopg.errors.InsufficientPrivilege),
                self.db.transaction(),
            ):
                self.begin()
                self.db.execute("DELETE FROM memoriesql." + table)
        with self.db.transaction():
            self.db.execute(
                "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
                (self.source,),
            )
        with self.db.transaction():
            self.worker_queue()
            self.assertEqual(
                self.db.execute(
                    "SELECT memoriesql.current_context_accepted_bead_maintain_authorized(%s,%s,%s,%s)",
                    (
                        self.tenant,
                        self.workspace,
                        self.scope,
                        result.typed_output.supersedes[0].bead_version_id,
                    ),
                ).fetchone(),
                (False,),
            )
        with self.assertRaises(psycopg.Error):
            self.persist(claimed, result)
        self.assert_no_correction(claimed)
        with self.db.transaction():
            self.begin()
            self.assertEqual(
                self.db.execute(
                    "SELECT count(*) FROM memoriesql.accepted_bead_semantics"
                ).fetchone(),
                (0,),
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT count(*) FROM memoriesql.bead_versions"
                ).fetchone(),
                (0,),
            )

    def test_accepted_semantics_reject_appends_and_legacy_revision(self) -> None:
        from psycopg import sql
        from psycopg.types.json import Jsonb

        capture, accepted, _, original = self.setup_initial(legacy=True)
        with (
            self.assertRaisesRegex(psycopg.Error, "correction bead requires"),
            self.db.transaction(),
        ):
            self.db.execute(
                "INSERT INTO memoriesql.beads(tenant_id,workspace_id,access_scope_id,bead_id,event_id,source_unit_id,created_at,origin_kind) VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp(),'correction')",
                (
                    self.tenant,
                    self.workspace,
                    self.scope,
                    uuid.uuid4(),
                    accepted.event_id,
                    accepted.source_unit_ids[0],
                ),
            )
        # New typed command is not necessary to protect the storage invariant.
        for table, key, updates in (
            (
                "bead_versions",
                "bead_version_id",
                {"bead_version_id": str(uuid.uuid4()), "version": 2},
            ),
            (
                "bead_semantic_statements",
                "statement_id",
                {
                    "statement_id": str(uuid.uuid4()),
                    "statement_sequence": 2,
                    "statement_text": "Changed meaning.",
                },
            ),
            ("bead_semantic_statement_evidence", "statement_id", {}),
            ("bead_statement_revisions", "bead_version_id", {}),
        ):
            columns = [
                row[0]
                for row in self.db.execute(
                    "SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum",
                    ("memoriesql." + table,),
                ).fetchall()
            ]
            record_row = self.db.execute(
                sql.SQL("SELECT to_jsonb(t) FROM memoriesql.{} AS t LIMIT 1").format(
                    sql.Identifier(table)
                )
            ).fetchone()
            assert record_row is not None
            record = record_row[0]
            record.update(updates)
            statement = sql.SQL(
                "INSERT INTO memoriesql.{} ({}) SELECT {} FROM jsonb_populate_record(NULL::memoriesql.{}, %s)"
            ).format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.Identifier(table),
            )
            with (
                self.subTest(table=table),
                self.assertRaisesRegex(psycopg.Error, "accepted_bead_immutable"),
                self.db.transaction(),
            ):
                self.db.execute(statement, (Jsonb(record),))
        for table, column in (
            ("bead_versions", "bead_type_id"),
            ("bead_versions", "render_payload"),
            ("bead_semantic_statements", "statement_text"),
        ):
            with (
                self.subTest(column=column),
                self.assertRaises(psycopg.Error),
                self.db.transaction(),
            ):
                self.db.execute(
                    sql.SQL("UPDATE memoriesql.{} SET {}={}").format(
                        sql.Identifier(table),
                        sql.Identifier(column),
                        sql.Identifier(column),
                    )
                )
        _, claimed = self.enqueue_authorship(capture, accepted, legacy=True)
        revision = self.result_for(
            accepted,
            capture,
            claimed,
            CANONICAL_AUTHORING_TASK,
            expected=1,
            text="Three fictional trees.",
            superseded_statement=str(
                original.typed_output.annotations[0].statements[0].statement_id
            ),
        )
        with (
            self.assertRaisesRegex(psycopg.Error, "accepted_bead_immutable"),
            self.db.transaction(),
        ):
            self.worker_queue().record_canonical_result(
                claimed.fence, revision, recorded_at=datetime.now(UTC)
            )
        self.assertEqual(
            self.db.execute(
                "SELECT version,summary FROM memoriesql.bead_versions"
            ).fetchall(),
            [(1, "Four fictional trees.")],
        )
        self.assert_no_correction(claimed)

    def test_upgrade_preserves_old_versions_payloads_and_receipts(self) -> None:
        from psycopg import sql

        capture, accepted, first_claim, first = self.setup_initial(legacy=True)
        _, second_claim = self.enqueue_authorship(capture, accepted, legacy=True)
        second = self.result_for(
            accepted,
            capture,
            second_claim,
            CANONICAL_AUTHORING_TASK,
            expected=1,
            text="Three fictional trees.",
            superseded_statement=str(
                first.typed_output.annotations[0].statements[0].statement_id
            ),
        )
        with self.db.transaction():
            self.assertEqual(
                self.worker_queue().record_canonical_result(
                    second_claim.fence, second, recorded_at=datetime.now(UTC)
                ),
                "succeeded",
            )
        tables = (
            "source_events",
            "source_units",
            "accepted_source_events",
            "bead_versions",
            "bead_semantic_statements",
            "bead_semantic_statement_evidence",
            "bead_statement_revisions",
            "semantic_task_receipts",
            "idempotency_receipts",
            "outbox_events",
        )

        def snapshots() -> dict[str, Any]:
            return {
                table: self.db.execute(
                    sql.SQL(
                        "SELECT to_jsonb(t) FROM memoriesql.{} AS t ORDER BY to_jsonb(t)::text"
                    ).format(sql.Identifier(table))
                ).fetchall()
                for table in tables
            }

        before = snapshots()
        upgrade = migrate(self.db, expected_current_version=14, target_version=15)
        self.assertEqual([m.version for m in upgrade.applied_migrations], [15])
        self.assertEqual(snapshots(), before)
        self.assertEqual(
            self.db.execute(
                "SELECT bead_version_id FROM memoriesql.accepted_bead_semantics"
            ).fetchone(),
            (self.pin(second).bead_version_id,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT version,summary FROM memoriesql.bead_versions ORDER BY version"
            ).fetchall(),
            [(1, "Four fictional trees."), (2, "Three fictional trees.")],
        )
        self.assertEqual(
            self.accept(capture), accepted.model_copy(update={"replayed": True})
        )
        for claimed, result in ((first_claim, first), (second_claim, second)):
            with self.db.transaction():
                self.assertEqual(
                    self.worker_queue().record_canonical_result(
                        claimed.fence, result, recorded_at=datetime.now(UTC)
                    ),
                    "succeeded",
                )
        self.assertEqual(snapshots(), before)
        with self.db.transaction():
            self.begin()
            self.assertEqual(
                self.db.execute(
                    "SELECT count(*) FROM memoriesql.bead_versions"
                ).fetchone(),
                (2,),
            )
        # The original published task and typed payload remain usable for initial
        # tasks. Their former accepted-bead update is explicitly denied at schema 15.
        _, third_claim = self.enqueue_authorship(capture, accepted, legacy=True)
        third = self.result_for(
            accepted,
            capture,
            third_claim,
            CANONICAL_AUTHORING_TASK,
            expected=2,
            text="Another fictional count.",
            superseded_statement=str(
                second.typed_output.annotations[0].statements[0].statement_id
            ),
        )
        with (
            self.assertRaisesRegex(psycopg.Error, "accepted_bead_immutable"),
            self.db.transaction(),
        ):
            self.worker_queue().record_canonical_result(
                third_claim.fence, third, recorded_at=datetime.now(UTC)
            )
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM memoriesql.bead_versions").fetchone(),
            (2,),
        )

    def test_settlement_failure_is_atomic_and_retryable(self) -> None:
        _, _, _, claimed, result = self.pending_correction()
        # Fail at the final root-run update, after all semantic writes and outcome
        # work. The transaction must roll back every bead, edge, receipt and outcome.
        self.db.execute(
            """CREATE FUNCTION public.orchard_reject_settlement() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.settled AND NOT OLD.settled THEN RAISE EXCEPTION 'fictional settlement failure'; END IF; RETURN NEW; END; $$"""
        )
        self.db.execute(
            "CREATE TRIGGER orchard_settlement_gate BEFORE UPDATE ON memoriesql.semantic_task_runs FOR EACH ROW EXECUTE FUNCTION public.orchard_reject_settlement()"
        )
        try:
            with self.assertRaisesRegex(psycopg.Error, "fictional settlement failure"):
                self.persist(claimed, result)
            self.assert_no_correction(claimed)
            self.assertEqual(
                self.db.execute(
                    "SELECT count(*) FROM memoriesql.idempotency_receipts WHERE operation_kind='observation_correction.apply.v2'"
                ).fetchone(),
                (0,),
            )
        finally:
            self.db.execute(
                "DROP TRIGGER orchard_settlement_gate ON memoriesql.semantic_task_runs"
            )
            self.db.execute("DROP FUNCTION public.orchard_reject_settlement()")
        self.assertFalse(self.persist(claimed, result).replayed)

    def test_expiry_after_blocked_bead_lock_is_rechecked(self) -> None:
        import threading
        import time

        from psycopg.conninfo import make_conninfo

        from memoriesql.infrastructure.postgres.authorization import (
            PostgresAuthorizationPort,
        )

        _, _, target, claimed, result = self.pending_correction()
        command = self.command_for(claimed, result)
        errors: list[BaseException] = []
        connection_ready = threading.Event()
        worker_pid: list[int] = []
        url = make_conninfo(self.admin, dbname=self.database)

        def apply_in_worker() -> None:
            try:
                with psycopg.connect(url, autocommit=True) as connection:
                    with connection.transaction():
                        connection.execute("SET LOCAL lock_timeout='3s'")
                        connection.execute("SET LOCAL statement_timeout='4s'")
                        connection.execute("SET LOCAL ROLE memoriesql_worker")
                        PostgresAuthorizationPort(connection).begin_context(
                            credential_sha256=self.worker_secret,
                            requested_workspace_id=self.workspace,
                        )
                        worker_pid.append(connection.info.backend_pid)
                        connection_ready.set()
                        PostgresCanonicalTransactions(connection).correct_observation(
                            command,
                            worker_id=claimed.fence.worker_id,
                            worker_instance_id=claimed.fence.worker_instance_id,
                            recorded_at=datetime.now(UTC),
                        )
            except BaseException as error:
                errors.append(error)
                connection_ready.set()

        with psycopg.connect(url, autocommit=True) as blocker:
            thread = threading.Thread(target=apply_in_worker)
            try:
                with blocker.transaction():
                    blocker.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (
                            str(self.tenant)
                            + ":bead-semantic:"
                            + str(target.bead_ids[0]),
                        ),
                    )
                    expires = self.db.execute(
                        "UPDATE memoriesql.semantic_tasks SET lease_expires_at=clock_timestamp()+interval '500 milliseconds' WHERE task_id=%s RETURNING lease_expires_at",
                        (claimed.fence.task_id,),
                    ).fetchone()
                    assert expires is not None
                    thread.start()
                    self.assertTrue(
                        connection_ready.wait(2), "worker did not establish its context"
                    )
                    self.assertTrue(worker_pid, errors)
                    deadline = time.monotonic() + 2
                    while self.db.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s",
                        (worker_pid[0],),
                    ).fetchone() != ("advisory",):
                        self.assertLess(
                            time.monotonic(),
                            deadline,
                            "worker never reached the controlled lock",
                        )
                        time.sleep(0.01)
                    while self.db.execute(
                        "SELECT clock_timestamp()>%s", (expires[0],)
                    ).fetchone() != (True,):
                        self.assertLess(
                            time.monotonic(), deadline, "fictional lease did not expire"
                        )
                        time.sleep(0.01)
                # Release the gate only after the actual database lease expires.
            finally:
                if thread.ident is not None:
                    thread.join(5)
                    self.assertFalse(
                        thread.is_alive(),
                        "bounded database operation did not terminate",
                    )
        self.assertEqual(len(errors), 1, errors)
        self.assertIsInstance(errors[0], psycopg.errors.SerializationFailure)
        self.assertIn("stale_fence", str(errors[0]))
        self.assert_no_correction(claimed)

    def test_missing_target_version_and_wrong_evidence_deny_atomically(self) -> None:
        capture, accepted, _, original = self.setup_initial()
        wrong_pin = AcceptedBeadPin(
            bead_id=self.pin(original).bead_id, bead_version_id=uuid.uuid4()
        )
        target, claimed = self.enqueue_authorship(capture, accepted, pins=(wrong_pin,))
        result = self.result_for(
            target, capture, claimed, OBSERVATION_CORRECTION_TASK, pins=(wrong_pin,)
        )
        with self.assertRaisesRegex(psycopg.Error, "target unavailable or stale"):
            self.persist(claimed, result)
        self.assert_no_correction(claimed)
        # A correct target does not authorize invented evidence bytes.
        target, claimed = self.enqueue_authorship(
            capture, accepted, pins=(self.pin(original),)
        )
        result = self.result_for(
            target,
            capture,
            claimed,
            OBSERVATION_CORRECTION_TASK,
            pins=(self.pin(original),),
        )
        raw = result.typed_output.model_dump(mode="json")
        raw["annotations"][0]["statements"][0]["evidence"][0]["content_hash"] = "0" * 64
        payload = OBSERVATION_CORRECTION_TASK.output_contract.model_type.model_validate(
            raw
        )
        changed = result.model_copy(
            update={
                "typed_output": payload,
                "output_hash": canonical_sha256(payload.model_dump(mode="json")),
            }
        )
        with self.assertRaisesRegex(psycopg.Error, "evidence_hash_mismatch"):
            self.persist(claimed, changed)
        self.assert_no_correction(claimed)

    def test_unrelated_source_update_does_not_invalidate_correction(self) -> None:
        _, _, _, claimed, result = self.pending_correction()
        # Resource metadata is unrelated to the pinned accepted meaning/evidence.
        self.db.execute(
            "UPDATE memoriesql.source_objects SET metadata='{"
            + '"fictional_note":"independent maintenance"'
            + "}'::jsonb WHERE source_object_id=%s",
            (self.source,),
        )
        self.assertFalse(self.persist(claimed, result).replayed)

    def test_v2_task_contracts_and_evidence_limits_are_explicit(self) -> None:
        from pydantic import ValidationError

        capture, accepted, _, original = self.setup_initial()
        modules = BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
        registry = load_observation_task_registry(modules)
        for definition in (INITIAL_OBSERVATIONS_TASK, OBSERVATION_CORRECTION_TASK):
            self.assertEqual(
                self.db.execute(
                    "SELECT task_contract_hash FROM memoriesql.semantic_task_admission_policies WHERE task_kind=%s AND contract_revision=2 AND semantic_registry_hash=%s",
                    (definition.task_kind, registry.registry_hash),
                ).fetchone(),
                (definition.contract_hash,),
            )
        self.assertEqual(
            CANONICAL_AUTHORING_TASK.contract_hash,
            "df6b7094bdc407ea10464a1b9ff9020cbbc99d57160a3b86d7702d57db89f92e",
        )
        self.assertEqual(
            CANONICAL_AUTHORING_TASK.output_contract.schema_hash,
            "cfaf34db7deb7eb6e91169420b7ffd6fc7580292f658afa8c5c0c60eb9b597b4",
        )
        target, claimed = self.enqueue_authorship(
            capture, accepted, pins=(self.pin(original),)
        )
        result = self.result_for(
            target,
            capture,
            claimed,
            OBSERVATION_CORRECTION_TASK,
            pins=(self.pin(original),),
        )
        raw = result.typed_output.model_dump(mode="json")
        raw["annotations"][0]["expected_bead_version"] = 1
        with self.assertRaises(ValidationError):
            OBSERVATION_CORRECTION_TASK.output_contract.model_type.model_validate(raw)
        raw = result.typed_output.model_dump(mode="json")
        raw["supersedes"] *= 2
        with self.assertRaises(ValidationError):
            OBSERVATION_CORRECTION_TASK.output_contract.model_type.model_validate(raw)
        row = self.db.execute(
            "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (claimed.fence.task_id,),
        ).fetchone()
        assert row is not None
        from copy import deepcopy

        from psycopg.types.json import Jsonb

        for mode in ("items", "characters"):
            candidate = deepcopy(row[0])
            if mode == "items":
                original_ref = candidate["evidence_manifest"]["references"][0]
                candidate["evidence_manifest"]["references"] = [
                    {**original_ref, "reference_id": str(uuid.uuid4())}
                    for _ in range(9)
                ]
            else:
                candidate["evidence_manifest"]["references"][0][
                    "declared_characters"
                ] = 4097
            with self.subTest(mode=mode):
                self.assertEqual(
                    self.db.execute(
                        "SELECT memoriesql.semantic_task_input_reference_safe(%s,32768)",
                        (Jsonb(candidate),),
                    ).fetchone(),
                    (False,),
                )
        self.assertFalse(self.persist(claimed, result).replayed)


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    # Legacy schema-14 tests remain in their own module; reuse only their fixture.
    return unittest.TestSuite(
        ImmutableObservations(name)
        for name in loader.getTestCaseNames(ImmutableObservations)
        if name in ImmutableObservations.__dict__
    )
