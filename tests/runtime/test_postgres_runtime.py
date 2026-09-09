"""Public fictional orchard acceptance against disposable PostgreSQL."""

from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.authorization import uuid7
from memoriesql.application.canonical_transactions import (
    AcceptSourceEventCommand,
    CanonicalSemanticTaskRequest,
    CaptureCheckpoint,
    ConversationTurnDetail,
    SourceEventInput,
    SourceType,
    SourceUnitInput,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.canonical_transactions import (
    PostgresCanonicalTransactions,
)
from memoriesql.infrastructure.postgres.migration_runner import migrate


class PostgresRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.admin = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "n2_orchard_" + uuid.uuid4().hex
        with psycopg.connect(self.admin, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.db = psycopg.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        )
        self.addCleanup(self.cleanup)
        migrate(self.db, expected_current_version=0, target_version=14)
        self.ids = [uuid.uuid4() for _ in range(9)]
        (
            self.tenant,
            self.user,
            identity,
            self.principal,
            self.workspace,
            self.scope,
            membership,
            credential,
            session,
        ) = self.ids
        self.secret_hash = hashlib.sha256(
            b"fictional orchard session for public acceptance"
        ).hexdigest()
        self.now = datetime.now(UTC)
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.bootstrap_personal_local(%s,%s,%s,%s,%s,%s,%s,%s,%s,'memoriesql.local',%s,'Fictional orchard',%s,%s,%s)",
                (
                    *self.ids,
                    str(identity),
                    self.secret_hash,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        self.source = uuid.uuid4()
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO memoriesql.protected_resources(tenant_id,workspace_id,access_scope_id,resource_id,resource_kind,owner_user_id,status,created_by_principal_id,created_at) VALUES(%s,%s,%s,%s,'source',%s,'active',%s,%s)",
                (
                    self.tenant,
                    self.workspace,
                    self.scope,
                    self.source,
                    self.user,
                    self.principal,
                    self.now,
                ),
            )
            self.db.execute(
                "INSERT INTO memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,source_object_id,source_system,object_kind,external_object_id,schema_version,metadata,created_at,last_observed_at,owner_user_id) VALUES(%s,%s,%s,%s,'orchard','conversation','orchard.plot',1,'{}',%s,%s,%s)",
                (
                    self.tenant,
                    self.workspace,
                    self.scope,
                    self.source,
                    self.now,
                    self.now,
                    self.user,
                ),
            )

    def cleanup(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def begin(self) -> None:
        self.db.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(self.db).begin_context(
            credential_sha256=self.secret_hash, requested_workspace_id=self.workspace
        )

    def command(self) -> AcceptSourceEventCommand:
        content = "The fictional orchard has four trees."
        digest = hashlib.sha256(content.encode()).hexdigest()
        return AcceptSourceEventCommand(
            tenant_id=self.tenant,
            workspace_id=self.workspace,
            access_scope_id=self.scope,
            source_object_id=self.source,
            expected_source_object_schema_version=1,
            idempotency_key="orchard.capture",
            event=SourceEventInput(
                event_id=uuid.uuid4(),
                source_type=SourceType.TRANSCRIPT,
                source_system="orchard",
                external_id_scope="orchard",
                source_identity_key="orchard.event",
                actor_id="orchard.gardener",
                actor_kind="human",
                parser_contract_version="orchard.v1",
                observation_unit_policy_version="orchard.turn.v1",
                captured_at=self.now,
                source_ref="fictional:orchard",
                content_hash=digest,
            ),
            units=(
                SourceUnitInput(
                    source_unit_id=uuid.uuid4(),
                    unit_kind="turn",
                    is_observation=True,
                    external_unit_id="orchard.turn",
                    unit_ordinal=0,
                    content_text=content,
                    content_hash=digest,
                    schema_version=1,
                    detail=ConversationTurnDetail(
                        conversation_id="orchard.conversation",
                        session_id="orchard.session",
                        turn_id="orchard.turn",
                        participant_role="user",
                    ),
                ),
            ),
            checkpoint=CaptureCheckpoint(
                checkpoint_key="orchard.cursor",
                expected_sequence=None,
                next_sequence=1,
                checkpoint_hash=digest,
            ),
            semantic_task=CanonicalSemanticTaskRequest(
                task_id=uuid7(), idempotency_key="orchard.task"
            ),
        )

    def accept(self, command: AcceptSourceEventCommand) -> Any:
        with self.db.transaction():
            self.begin()
            return PostgresCanonicalTransactions(self.db).accept_source_event(
                command, recorded_at=self.now
            )

    def test_accept_replay_conflict_and_transaction_rollback(self) -> None:
        command = self.command()
        first = self.accept(command)
        replay = self.accept(command)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.idempotency_receipt_id, replay.idempotency_receipt_id)
        self.assertEqual(first.bead_ids, replay.bead_ids)
        conflict = command.model_copy(
            update={
                "event": command.event.model_copy(update={"actor_id": "orchard.other"})
            }
        )
        with self.assertRaises(psycopg.Error):
            self.accept(conflict)
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM memoriesql.source_events").fetchone(),
            (1,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.semantic_tasks"
            ).fetchone(),
            (1,),
        )

    def test_revoked_principal_cannot_accept(self) -> None:
        command = self.command()
        with self.db.transaction():
            self.db.execute(
                "UPDATE memoriesql.principals SET status='revoked',revoked_at=%s WHERE principal_id=%s",
                (self.now, self.principal),
            )
        with self.assertRaises(psycopg.Error):
            self.accept(command)
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM memoriesql.source_events").fetchone(),
            (0,),
        )

    def test_forged_tenant_is_denied_before_canonical_write(self) -> None:
        with self.assertRaises(psycopg.Error):
            self.accept(self.command().model_copy(update={"tenant_id": uuid.uuid4()}))
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM memoriesql.source_events").fetchone(),
            (0,),
        )

    def pair_worker(self) -> None:
        self.worker_principal, pairing, grant, credential = [
            uuid.uuid4() for _ in range(4)
        ]
        self.worker_secret = hashlib.sha256(
            b"fictional orchard worker session"
        ).hexdigest()
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,'service','background_service',%s,%s,%s,%s,%s)",
                (
                    self.worker_principal,
                    pairing,
                    grant,
                    credential,
                    ["memory.maintain"],
                    [self.scope],
                    self.worker_secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO memoriesql.semantic_worker_claim_policies(tenant_id,workspace_id,principal_id,pairing_grant_id,semantic_registry_hash,task_kind,contract_revision,queue_name) SELECT %s,%s,%s,%s,semantic_registry_hash,task_kind,contract_revision,queue_name FROM memoriesql.semantic_task_admission_policies WHERE task_kind='memory.semantic.author-observations'",
                (self.tenant, self.workspace, self.worker_principal, grant),
            )

    def worker_queue(self) -> Any:
        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
        )

        self.db.execute("SET LOCAL ROLE memoriesql_worker")
        PostgresAuthorizationPort(self.db).begin_context(
            credential_sha256=self.worker_secret, requested_workspace_id=self.workspace
        )
        return PostgresSemanticTaskQueue(self.db)

    def claim(self) -> Any:
        with self.db.transaction():
            queue = self.worker_queue()
            claimed = queue.claim(
                worker_id="orchard.worker",
                worker_instance_id="orchard.instance",
                lease_seconds=120,
                deadline_seconds=300,
                executor_contract_version=1,
                claimed_at=datetime.now(UTC),
            )
            self.assertIsNotNone(claimed)
            self.assertTrue(queue.start(claimed.fence, started_at=datetime.now(UTC)))
            return claimed

    def test_queue_hydration_and_stale_generation_fencing(self) -> None:
        from dataclasses import replace

        self.accept(self.command())
        self.pair_worker()
        claimed = self.claim()
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertIsNotNone(
                queue.hydrate_input(claimed.fence, hydrated_at=datetime.now(UTC))
            )
            self.assertIsNone(
                queue.hydrate_input(
                    replace(
                        claimed.fence,
                        lease_generation=claimed.fence.lease_generation + 1,
                    ),
                    hydrated_at=datetime.now(UTC),
                )
            )
            self.assertEqual(
                queue.reauthorize(
                    replace(
                        claimed.fence,
                        lease_generation=claimed.fence.lease_generation + 1,
                    ),
                    phase="outcome",
                    checked_at=datetime.now(UTC),
                ),
                "stale_fence",
            )

    def test_principal_revocation_blocks_hydration_and_apply(self) -> None:
        self.accept(self.command())
        self.pair_worker()
        claimed = self.claim()
        with self.db.transaction():
            self.db.execute(
                "UPDATE memoriesql.principals SET status='revoked',revoked_at=%s WHERE principal_id=%s",
                (datetime.now(UTC), self.principal),
            )
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertIsNone(
                queue.hydrate_input(claimed.fence, hydrated_at=datetime.now(UTC))
            )
            self.assertNotEqual(
                queue.reauthorize(
                    claimed.fence, phase="outcome", checked_at=datetime.now(UTC)
                ),
                "authorized",
            )

    def test_cancelled_task_blocks_hydration_retains_settlement(self) -> None:
        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
        )

        command = self.command()
        accepted = self.accept(command)
        self.pair_worker()
        claimed = self.claim()
        result = self.authored_result(accepted, command, claimed)
        with self.db.transaction():
            self.begin()
            PostgresSemanticTaskQueue(self.db).cancel(
                tenant_id=self.tenant,
                task_id=accepted.semantic_task_id,
                reason="orchard.cancel",
                cancelled_at=datetime.now(UTC),
            )
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertIsNone(
                queue.hydrate_input(claimed.fence, hydrated_at=datetime.now(UTC))
            )
            self.assertEqual(
                queue.reauthorize(
                    claimed.fence, phase="outcome", checked_at=datetime.now(UTC)
                ),
                "authorized",
            )

        with self.assertRaises(psycopg.Error), self.db.transaction():
            queue = self.worker_queue()
            queue.record_canonical_result(
                claimed.fence, result, recorded_at=datetime.now(UTC)
            )
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM memoriesql.bead_versions").fetchone(),
            (0,),
        )

    def authored_result(self, captured: Any, command: Any, claimed: Any) -> Any:
        from memoriesql.application.builtin_semantic_tasks import (
            CANONICAL_AUTHORING_TASK,
        )
        from memoriesql.application.canonical_transactions import (
            ApplySemanticAnnotationsPayload,
        )
        from memoriesql.application.semantic_task_contracts import (
            SemanticAgentContractIdentity,
            SemanticResultStatus,
            SemanticRootRunCorrelation,
            SemanticRunEvent,
            SemanticRunEventKind,
            SemanticTaskResult,
            canonical_sha256,
        )

        statement = str(uuid.uuid4())
        clause = {"text": "Four fictional trees.", "statement_ids": [statement]}
        payload = ApplySemanticAnnotationsPayload.model_validate(
            {
                "annotations": [
                    {
                        "bead_id": str(captured.bead_ids[0]),
                        "event_id": str(captured.event_id),
                        "source_unit_id": str(captured.source_unit_ids[0]),
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
                                            captured.source_unit_ids[0]
                                        ),
                                        "content_hash": command.units[0].content_hash,
                                    }
                                ],
                                "model_run_ref": "orchard.run",
                            }
                        ],
                        "render": {"title": clause, "summary": [clause]},
                    }
                ]
            }
        )
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
                        agent_key="memory.semantic.observation-author",
                        input_contract=CANONICAL_AUTHORING_TASK.input_contract.reference,
                        output_contract=CANONICAL_AUTHORING_TASK.output_contract.reference,
                    ),
                    model_profile=CANONICAL_AUTHORING_TASK.model_profile,
                ),
            )
            self.assertTrue(
                queue.record_run_event(
                    claimed.fence, event, recorded_at=datetime.now(UTC)
                )
            )
        return SemanticTaskResult[ApplySemanticAnnotationsPayload](
            status=SemanticResultStatus.SUCCEEDED,
            task_id=str(claimed.fence.task_id),
            attempt_id=str(claimed.fence.attempt_id),
            task_kind=claimed.task_kind,
            contract_revision=1,
            output_contract_hash=CANONICAL_AUTHORING_TASK.output_contract.schema_hash,
            typed_output=payload,
            output_hash=canonical_sha256(payload.model_dump(mode="json")),
            used_evidence_refs=(str(captured.source_unit_ids[0]),),
            model_run_refs=("orchard.run",),
        )

    def test_historical_semantic_apply_and_replay(self) -> None:
        command = self.command()
        captured = self.accept(command)
        self.pair_worker()
        claimed = self.claim()
        result = self.authored_result(captured, command, claimed)
        for _ in range(2):
            with self.db.transaction():
                queue = self.worker_queue()
                self.assertEqual(
                    queue.record_canonical_result(
                        claimed.fence, result, recorded_at=datetime.now(UTC)
                    ),
                    "succeeded",
                )
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM memoriesql.bead_versions").fetchone(),
            (1,),
        )

    def test_expired_lease_cannot_hydrate_or_apply(self) -> None:
        self.accept(self.command())
        self.pair_worker()
        claimed = self.claim()
        # Synthetic time fixture: expire this test's lease without wall-clock sleeps.
        with self.db.transaction():
            self.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts DISABLE TRIGGER USER"
            )
            self.db.execute(
                "UPDATE memoriesql.semantic_tasks SET heartbeat_at=clock_timestamp()-interval '2 seconds', lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s",
                (claimed.fence.task_id,),
            )
            self.db.execute(
                "UPDATE memoriesql.semantic_task_attempts SET claimed_at=clock_timestamp()-interval '10 seconds', started_at=clock_timestamp()-interval '5 seconds',heartbeat_at=clock_timestamp()-interval '2 seconds',lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s",
                (claimed.fence.task_id,),
            )
            self.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts ENABLE TRIGGER USER"
            )
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertIsNone(
                queue.hydrate_input(claimed.fence, hydrated_at=datetime.now(UTC))
            )
            self.assertEqual(
                queue.reauthorize(
                    claimed.fence, phase="outcome", checked_at=datetime.now(UTC)
                ),
                "stale_fence",
            )
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertEqual(
                queue.reap_expired(
                    worker_id="orchard.reaper", limit=1, reaped_at=datetime.now(UTC)
                ),
                1,
            )
        # Advance only the fictional retry availability, not the runtime backoff.
        with self.db.transaction():
            self.db.execute(
                "UPDATE memoriesql.semantic_tasks SET available_at=clock_timestamp() WHERE task_id=%s",
                (claimed.fence.task_id,),
            )
        reclaimed = self.claim()
        self.assertGreater(
            reclaimed.fence.lease_generation, claimed.fence.lease_generation
        )
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertIsNone(
                queue.hydrate_input(claimed.fence, hydrated_at=datetime.now(UTC))
            )
            self.assertIsNotNone(
                queue.hydrate_input(reclaimed.fence, hydrated_at=datetime.now(UTC))
            )
