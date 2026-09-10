"""Fictional canonical transaction proofs. No models or provider calls."""

from __future__ import annotations

import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from memoriesql.application.canonical_transactions import SourceType
from memoriesql.application.evidence_packages import (
    NativeFacts,
    PageEvidenceInventory,
    ReadEvidencePart,
    SourceQualification,
)
from memoriesql.application.logical_unit_materialization import (
    COMPLETE_UNIT_REGISTRY_HASH,
    COMPLETE_UNIT_TASK_CONTRACT_HASH,
    CompleteUnitTaskInput,
    InspectLogicalEvent,
    LogicalEventDeclaration,
    MaterializeLogicalUnit,
)
from memoriesql.infrastructure.postgres.logical_unit_materialization import (
    PostgresLogicalUnitMaterialization,
)
from memoriesql.infrastructure.postgres.migration_runner import migrate

if TYPE_CHECKING:
    from tests.runtime.test_evidence_packages import EvidencePackages
    from tests.runtime.test_immutable_observations import ImmutableObservations
else:
    from test_evidence_packages import EvidencePackages
    from test_immutable_observations import ImmutableObservations


class LogicalUnitMaterialization(EvidencePackages):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=16, target_version=17)
        self.materializer = PostgresLogicalUnitMaterialization(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        self.policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies VALUES(%s,%s,%s,%s,%s,%s,'orchard.fictional-qualification.v1','orchard.normalization.v1',%s,%s,%s,%s,'active')",
            (
                self.tenant,
                self.workspace,
                self.scope,
                self.policy,
                self.source,
                self.principal,
                "a" * 64,
                self.principal,
                self.now,
                self.now + timedelta(hours=1),
            ),
        )

    def package(
        self, text: str = "Four fictional trees.", *, seal: bool = True, **updates: Any
    ) -> Any:
        parts = (self.part(text),)
        package = self.create(parts, **updates)
        self.append(package, parts[0])
        if seal:
            self.seal(package)
        return package

    def materialization(
        self, package: Any, *, total: int | None = None, **updates: Any
    ) -> MaterializeLogicalUnit:
        return MaterializeLogicalUnit.model_validate(
            dict(
                idempotency_key="materialize." + str(uuid.uuid4()),
                package_id=package.package_id,
                expected_inventory_sha256=self.status(
                    package
                ).declaration.expected_inventory_sha256,
                producer_policy_id=self.policy,
                expected_source_object_schema_version=1,
                event=LogicalEventDeclaration(
                    event_key="orchard.event.1",
                    identity_basis="native",
                    source_type=SourceType.TRANSCRIPT,
                    native=NativeFacts(native_id="event.1"),
                    expected_units=total,
                ),
            )
            | updates
        )

    def row(self, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
        row = self.db.execute(query, params).fetchone()
        assert row is not None
        return row

    def counts(self) -> tuple[int, ...]:
        return tuple(
            self.row("SELECT count(*) FROM memoriesql." + table)[0]
            for table in (
                "source_events",
                "source_units",
                "beads",
                "semantic_tasks",
                "logical_unit_materializations",
                "bead_versions",
                "accepted_bead_semantics",
            )
        )

    def test_atomic_pin_and_unavailable_task(self) -> None:
        package = self.package("Complete fictional evidence. " * 500)
        result = self.materializer.materialize(self.materialization(package))
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))
        self.assertEqual(result.bound_package.required_characters, 14500)
        row = self.row(
            "SELECT input_payload,status,pause_reason_code,attempt_count FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (result.task_id,),
        )
        task = CompleteUnitTaskInput.model_validate(row[0])
        self.assertEqual(task.payload.package, result.bound_package)
        self.assertEqual(
            row[1:], ("policy_paused", "complete_input_executor_unavailable", 0)
        )
        self.assertEqual(
            self.row(
                "SELECT content_text,external_unit_id,unit_ordinal FROM memoriesql.source_units"
            ),
            (None, "native-unit.7", None),
        )
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.outbox_events WHERE event_kind IN ('source_unit.materialized','semantic_task.enqueued')"
            ),
            (2,),
        )
        for status in ("queued", "running", "succeeded"):
            with self.assertRaises(psycopg.Error), self.db.transaction():
                self.db.execute(
                    "UPDATE memoriesql.semantic_tasks SET status=%s,pause_reason_code=NULL WHERE task_id=%s",
                    (status, result.task_id),
                )
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_replay_repackaging_and_identity_conflicts(self) -> None:
        package = self.package()
        command = self.materialization(package)
        result = self.materializer.materialize(command)
        replay = self.materializer.materialize(command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.idempotency_receipt_id, result.idempotency_receipt_id)
        repack = self.package(
            "Repackaged complete representation. " * 200, package_revision=2
        )
        second = self.materializer.materialize(self.materialization(repack))
        self.assertEqual(second.status, "already_exists")
        self.assertEqual(second.task_id, result.task_id)
        self.assertEqual(second.bound_package, result.bound_package)
        self.assertEqual(second.submitted_package_id, repack.package_id)
        self.assertNotEqual(
            second.idempotency_receipt_id, result.idempotency_receipt_id
        )
        with self.assertRaisesRegex(psycopg.Error, "idempotency_conflict"):
            self.materializer.materialize(
                self.materialization(repack, idempotency_key=command.idempotency_key)
            )
        changed = self.package(
            package_revision=3, native=NativeFacts(native_id="different-native-unit")
        )
        with self.assertRaisesRegex(
            psycopg.Error, "logical_occurrence_identity_conflict"
        ):
            self.materializer.materialize(self.materialization(changed))
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_unsealed_pending_and_untrusted_rejected(self) -> None:
        package = self.package(seal=False)
        with self.assertRaisesRegex(psycopg.Error, "materialization_input_pending"):
            self.materializer.materialize(self.materialization(package))
        self.seal(package)
        with self.assertRaisesRegex(
            psycopg.Error, "trusted_producer_policy_unavailable"
        ):
            self.materializer.materialize(
                self.materialization(package, producer_policy_id=uuid.uuid4())
            )
        pending = self.package(
            package_revision=2,
            qualification=SourceQualification(
                qualification_ref="orchard.fictional-qualification.v1",
                boundary="unresolved",
                boundary_basis="Fictional open physical tail.",
                physical_records="pending_tail",
                topology="unknown",
                normalized_input="incomplete",
                source_completeness="unresolved",
                unresolved_coverage=("tail",),
            ),
        )
        with self.assertRaisesRegex(psycopg.Error, "materialization_input_pending"):
            self.materializer.materialize(self.materialization(pending))
        self.assertEqual(self.counts(), (0, 0, 0, 0, 0, 0, 0))

    def test_twelve_genuine_units_partial_progress_and_parent_identity(self) -> None:
        first = None
        for i in range(12):
            native = NativeFacts(
                native_id=f"unit.{i}",
                parent_native_id="unit.0" if i else None,
                session_native_id="session.2",
                branch_native_id="branch.3",
                source_order=i,
            )
            package = self.package(occurrence_key=f"unit.{i}", native=native)
            result = self.materializer.materialize(
                self.materialization(
                    package,
                    total=12,
                    parent_source_unit_id=first.source_unit_id
                    if first and i == 1
                    else None,
                )
            )
            if first is None:
                first = result
            self.assertEqual(result.event_progress_at_commit.materialized_units, i + 1)
            self.assertEqual(
                result.event_progress_at_commit.inventory_state,
                "partial" if i < 11 else "declared_inventory_materialized",
            )
        assert first is not None
        progress = self.materializer.inspect_event(
            InspectLogicalEvent(event_id=first.event_id)
        )
        self.assertEqual(progress.remaining_declared_units, 0)
        self.assertFalse(progress.independently_proven_source_complete)
        self.assertEqual(first.event_progress_at_commit.materialized_units, 1)
        self.assertEqual(self.counts(), (1, 12, 12, 12, 12, 0, 0))
        self.assertEqual(
            self.row(
                "SELECT parent_unit_id,structure#>>'{native,parent_native_id}',structure->>'parent_resolution' FROM memoriesql.source_units WHERE external_unit_id='unit.1'"
            ),
            (first.source_unit_id, "unit.0", "canonical"),
        )
        self.assertEqual(
            self.row(
                "SELECT parent_unit_id,structure#>>'{native,parent_native_id}',structure->>'parent_resolution' FROM memoriesql.source_units WHERE external_unit_id='unit.2'"
            ),
            (None, "unit.0", "native_identity_only"),
        )
        self.assertEqual(
            self.row(
                "SELECT declared_observation_unit_count,observation_unit_count,bead_count FROM memoriesql.source_event_cardinality"
            ),
            (12, 12, 12),
        )

    def test_rollback_after_enqueue_then_recover_and_lost_response_replay(self) -> None:
        package = self.package()
        command = self.materialization(package)
        # Simulate transaction abort after durable queue/outbox writes, before commit.
        self.db.execute(
            "CREATE FUNCTION public.fail_materialization() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fictional_crash'; END; $$"
        )
        self.db.execute(
            "CREATE TRIGGER fictional_crash AFTER INSERT ON memoriesql.logical_unit_materializations FOR EACH ROW EXECUTE FUNCTION public.fail_materialization()"
        )
        before = self.row("SELECT count(*) FROM memoriesql.idempotency_receipts")
        with self.assertRaisesRegex(psycopg.Error, "fictional_crash"):
            self.materializer.materialize(command)
        self.assertEqual(self.counts(), (0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.idempotency_receipts"),
            before,
        )
        self.db.execute(
            "DROP TRIGGER fictional_crash ON memoriesql.logical_unit_materializations"
        )
        committed = self.materializer.materialize(command)
        replay = self.materializer.materialize(command)
        self.assertEqual(replay.task_id, committed.task_id)
        self.assertTrue(replay.replayed)

    def test_current_authorization_and_policy_revocation(self) -> None:
        package = self.package()
        command = self.materialization(package)
        result = self.materializer.materialize(command)
        self.db.execute(
            "UPDATE memoriesql.evidence_producer_policies SET status='revoked'"
        )
        with self.assertRaisesRegex(
            psycopg.Error, "trusted_producer_policy_unavailable"
        ):
            self.materializer.materialize(command)
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
            (self.source,),
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.materializer.inspect_event(
                InspectLogicalEvent(event_id=result.event_id)
            )
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_direct_new_task_cannot_bypass_binding(self) -> None:
        package = self.package()
        result = self.materializer.materialize(self.materialization(package))
        row = self.row(
            "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (result.task_id,),
        )[0]
        # Direct SQL validator rejects previews and a weakened evidence pin.
        row["payload"]["preview"] = "A summary is not the evidence."
        self.assertFalse(
            self.row(
                "SELECT memoriesql.semantic_task_input_reference_safe(%s,32768)",
                (Jsonb(row),),
            )[0]
        )
        with self.db.transaction():
            self.begin()
            with (
                self.assertRaises(psycopg.errors.InsufficientPrivilege),
                self.db.transaction(),
            ):
                self.db.execute("SELECT * FROM memoriesql.evidence_producer_policies")

    def test_simultaneous_occurrence_converges(self) -> None:
        package = self.package()
        commands = [self.materialization(package) for _ in range(2)]

        def run(command: MaterializeLogicalUnit) -> Any:
            with psycopg.connect(
                make_conninfo(self.admin, dbname=self.database), autocommit=True
            ) as connection:
                return PostgresLogicalUnitMaterialization(
                    connection,
                    credential_sha256=self.secret_hash,
                    workspace_id=self.workspace,
                ).materialize(command)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, commands))
        self.assertEqual(results[0].task_id, results[1].task_id)
        self.assertEqual(
            {r.status for r in results}, {"materialized", "already_exists"}
        )
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_native_events_share_revision_without_fake_boundaries(self) -> None:
        first = self.package()
        a = self.materializer.materialize(self.materialization(first, total=2))
        pending = self.package(
            occurrence_key="other-unit",
            native=NativeFacts(native_id="other-unit"),
            seal=False,
        )
        with self.assertRaisesRegex(psycopg.Error, "materialization_input_pending"):
            self.materializer.materialize(self.materialization(pending, total=2))
        self.assertEqual(
            self.materializer.inspect_event(
                InspectLogicalEvent(event_id=a.event_id)
            ).remaining_declared_units,
            1,
        )
        self.seal(pending)
        event = LogicalEventDeclaration(
            event_key="second-event",
            identity_basis="producer_assigned",
            source_type=SourceType.TRANSCRIPT,
            native=NativeFacts(),
        )
        b = self.materializer.materialize(self.materialization(pending, event=event))
        self.assertNotEqual(a.event_id, b.event_id)
        self.assertEqual(self.counts(), (2, 2, 2, 2, 2, 0, 0))
        self.assertEqual(
            self.row(
                "SELECT actor_id,actor_kind FROM memoriesql.source_events WHERE event_id=%s",
                (b.event_id,),
            ),
            (None, None),
        )

    def test_read_page_size_changes_do_not_change_occurrence(self) -> None:
        package = self.package("Fictional完整 evidence. " * 500)
        command = self.materialization(package)
        result = self.materializer.materialize(command)
        inventory = self.api.inventory(
            PageEvidenceInventory(package_id=package.package_id)
        )
        for size in (31, 1024):
            cursor = None
            content = ""
            while True:
                page = self.api.read(
                    ReadEvidencePart(
                        package_id=package.package_id,
                        part_id=inventory.entries[0].part_id,
                        max_characters=size,
                        continuation=cursor,
                    )
                )
                content += page.content
                if page.terminal:
                    break
                cursor = page.continuation
            self.assertEqual(len(content), result.bound_package.required_characters)
            self.assertEqual(
                self.materializer.materialize(command).source_unit_id,
                result.source_unit_id,
            )
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_unavailable_task_cannot_be_claimed_or_author_meaning(self) -> None:
        result = self.materializer.materialize(self.materialization(self.package()))
        self.pair_worker()
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies(tenant_id,workspace_id,principal_id,pairing_grant_id,semantic_registry_hash,task_kind,contract_revision,queue_name) SELECT tenant_id,workspace_id,principal_id,pairing_grant_id,%s,'memory.semantic.author-complete-unit',1,'capture' FROM memoriesql.semantic_worker_claim_policies LIMIT 1",
            (COMPLETE_UNIT_REGISTRY_HASH,),
        )
        with self.db.transaction():
            queue = self.worker_queue()
            self.assertIsNone(
                queue.claim(
                    worker_id="orchard.worker",
                    worker_instance_id="orchard.instance",
                    lease_seconds=120,
                    deadline_seconds=300,
                    executor_contract_version=1,
                    claimed_at=datetime.now(UTC),
                )
            )
        with (
            self.assertRaisesRegex(
                psycopg.Error, "complete_input_exposure_validation_unavailable"
            ),
            self.db.transaction(),
        ):
            self.db.execute(
                "INSERT INTO memoriesql.bead_versions(tenant_id,bead_id) VALUES(%s,%s)",
                (self.tenant, result.bead_id),
            )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.semantic_task_attempts"),
            (0,),
        )

    def test_direct_enqueue_without_canonical_binding_rolls_back(self) -> None:
        result = self.materializer.materialize(self.materialization(self.package()))
        row = self.row(
            "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (result.task_id,),
        )[0]
        tid = self.row("SELECT uuidv7()")[0]
        row["task_id"] = str(tid)
        with (
            self.assertRaisesRegex(psycopg.Error, "complete_unit_binding_required"),
            self.db.transaction(),
        ):
            self.begin()
            self.db.execute(
                "SELECT memoriesql.enqueue_semantic_task(%s,'fictional-direct','memoriesql.kernel','memory.semantic.author-complete-unit',1,%s,%s,%s,0,%s,%s,%s,clock_timestamp(),NULL,clock_timestamp())",
                (
                    tid,
                    COMPLETE_UNIT_TASK_CONTRACT_HASH,
                    COMPLETE_UNIT_REGISTRY_HASH,
                    row["target_reference"],
                    Jsonb(row),
                    row["evidence_manifest"]["manifest_id"],
                    self.scope,
                ),
            )
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_module_admission_closed_is_atomic(self) -> None:
        package = self.package()
        self.db.execute(
            "INSERT INTO memoriesql.semantic_module_queue_controls(tenant_id,workspace_id,owning_module,state,revision,reason_code,changed_by_principal_id,changed_at) VALUES(%s,%s,'memoriesql.kernel','disabled',1,'fictional.disabled',%s,clock_timestamp())",
            (self.tenant, self.workspace, self.principal),
        )
        with self.assertRaisesRegex(psycopg.Error, "module is not accepting work"):
            self.materializer.materialize(self.materialization(package))
        self.assertEqual(self.counts(), (0, 0, 0, 0, 0, 0, 0))

    def test_revocation_wait_uses_current_snapshot(self) -> None:
        package = self.package()
        command = self.materialization(package)
        ready = Event()

        def run() -> Any:
            with psycopg.connect(
                make_conninfo(
                    self.admin,
                    dbname=self.database,
                    application_name="fictional-policy-wait",
                ),
                autocommit=True,
            ) as connection:
                connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                ready.set()
                return PostgresLogicalUnitMaterialization(
                    connection,
                    credential_sha256=self.secret_hash,
                    workspace_id=self.workspace,
                ).materialize(command)

        with psycopg.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        ) as blocker:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with blocker.transaction():
                    # Holds the shared authority fence exclusively until commit.
                    blocker.execute(
                        "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
                        (self.policy,),
                    )
                    future = pool.submit(run)
                    self.assertTrue(ready.wait(1))
                    deadline = monotonic() + 0.3
                    blocked = False
                    while monotonic() < deadline:
                        row = self.db.execute(
                            "SELECT wait_event FROM pg_stat_activity WHERE datname=%s AND application_name='fictional-policy-wait'",
                            (self.database,),
                        ).fetchone()
                        if row and row[0] == "advisory":
                            blocked = True
                            break
                        sleep(0.005)
                    self.assertTrue(blocked, "fixture must reach the authority fence")
                with self.assertRaisesRegex(
                    psycopg.Error, "trusted_producer_policy_unavailable"
                ):
                    future.result(timeout=3)
        self.assertEqual(self.counts(), (0, 0, 0, 0, 0, 0, 0))

    def test_sql_entry_points_reject_stale_snapshot_isolation(self) -> None:
        command = self.materialization(self.package())
        result = self.materializer.materialize(command)
        for isolation in ("REPEATABLE READ", "SERIALIZABLE"):
            for function, request in (
                ("materialize_logical_unit_v1", command),
                (
                    "inspect_logical_event_v1",
                    InspectLogicalEvent(event_id=result.event_id),
                ),
            ):
                with (
                    self.assertRaisesRegex(
                        psycopg.Error, "materialization_requires_read_committed"
                    ),
                    self.db.transaction(),
                ):
                    self.db.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
                    self.begin()
                    self.db.execute(
                        "SELECT memoriesql." + function + "(%s)",
                        (Jsonb(request.model_dump(mode="json")),),
                    )
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0, 0))


class LegacyAtSchema17(ImmutableObservations):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=15, target_version=17)

    def test_legacy_receipts_and_correction_lineage(self) -> None:
        self.test_initial_replay_same_evidence_branches_and_reconciliation()

    def test_legacy_accepted_immutability(self) -> None:
        self.test_accepted_semantics_reject_appends_and_legacy_revision()

    def test_legacy_authorization_fences(self) -> None:
        self.test_forced_rls_and_revoked_target_authority()


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    return unittest.TestSuite(
        cls(name)
        for cls in (LogicalUnitMaterialization, LegacyAtSchema17)
        for name in loader.getTestCaseNames(cls)
        if name in cls.__dict__
    )
