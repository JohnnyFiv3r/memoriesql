"""Independent public orchard fixtures for source-stable materialization."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.conninfo import make_conninfo

from memoriesql.application.capture.contracts import (
    CaptureSurface,
    KernelCaptureBinding,
)
from memoriesql.application.capture.host_protocol import FileIdentity
from memoriesql.application.capture.source_range import (
    SourceRangePolicyReferences,
    build_capture_source_range_command,
)
from memoriesql.application.evidence_packages import (
    EvidencePart,
    NativeFacts,
    RawEvidenceSlice,
    digest,
)
from memoriesql.infrastructure.postgres.migration_runner import migrate

if TYPE_CHECKING:
    from tests.runtime import test_logical_unit_materialization as fixtures
else:
    import test_logical_unit_materialization as fixtures


class SourceStableIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.f = fixtures.LogicalUnitMaterialization(
            "test_atomic_pin_and_unavailable_task"
        )
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.fold_keys: dict[str, Any] = {}
        migrate(self.f.db, expected_current_version=17, target_version=20)

    def enable(self) -> None:
        migrate(self.f.db, expected_current_version=20, target_version=21)
        # New immutable policy, explicitly provisioned only in this fictional DB.
        policy = uuid.uuid4()
        self.f.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies "
            "SELECT tenant_id,workspace_id,access_scope_id,%s,source_object_id,producer_principal_id,qualification_ref,normalization_policy_version,qualification_evidence_sha256,approved_by_principal_id,created_at,expires_at,status,'orchard.register.v1' "
            "FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
            (policy, self.f.policy),
        )
        self.f.policy = policy

    def package(
        self,
        revision: str,
        *,
        text: str = "A fictional tree. 🌱",
        fragments: int = 1,
        occurrence: str = "tree.7",
        native: str = "tree.7",
        fold: bool = False,
    ) -> Any:
        f = self.f
        raw = text.encode()
        receipt = f.raw.capture(
            build_capture_source_range_command(
                payload=raw,
                binding=KernelCaptureBinding(
                    tenant_id=f.tenant,
                    workspace_id=f.workspace,
                    access_scope_id=f.scope,
                    source_object_id=f.source,
                    expected_source_object_schema_version=1,
                ),
                capability_id="orchard.capture",
                connector_id="orchard.synthetic",
                observed_connector_version="1",
                capture_surface=CaptureSurface.SYNTHETIC,
                source_revision_key=revision,
                file_identity=FileIdentity(platform="fictional", device=1, inode=5),
                observed_source_format_version="orchard.v1",
                byte_start=0,
                checkpoint_key="orchard." + revision,
                expected_checkpoint_sequence=0,
                policies=SourceRangePolicyReferences(
                    capture_policy_id="orchard.capture",
                    retention_policy_ref="orchard.retention",
                ),
            ),
            recorded_at=datetime.now(UTC),
        )
        fold_id = None
        if fold:
            from memoriesql.application.capture.transcript_fold import (
                DurableTranscriptReadRequest,
                TranscriptFoldOutcome,
                TranscriptFoldOutcomeKind,
                TranscriptSpanEvidence,
                build_transcript_fold_command,
            )
            from memoriesql.application.fold_recovery import FoldOutcomeKey
            from memoriesql.infrastructure.postgres.transcript_fold import (
                PostgresAuthorizedTranscriptFoldSession,
            )

            identity = FileIdentity(platform="fictional", device=1, inode=5)
            folds = PostgresAuthorizedTranscriptFoldSession(
                f.db, credential_sha256=f.secret_hash, workspace_id=f.workspace
            )
            retained = folds.read(
                DurableTranscriptReadRequest(
                    source_object_id=f.source,
                    source_revision_key=revision,
                    file_identity_key=identity.stable_key,
                    byte_start=0,
                    max_bytes=len(raw),
                )
            )
            facts: dict[str, Any] = dict(
                byte_start=0,
                byte_end_exclusive=len(raw),
                start_record_index=0,
                end_record_index=len(raw.splitlines()),
                source_bytes_sha256=digest(raw),
            )
            folded = folds.commit(
                build_transcript_fold_command(
                    binding=KernelCaptureBinding(
                        tenant_id=f.tenant,
                        workspace_id=f.workspace,
                        access_scope_id=f.scope,
                        source_object_id=f.source,
                        expected_source_object_schema_version=1,
                    ),
                    capability_id="orchard.capture",
                    connector_id="orchard.synthetic",
                    adapter_profile_version="orchard.v1",
                    observed_source_format_version="orchard.v1",
                    source_revision_key=revision,
                    file_identity=identity,
                    outcomes=(
                        TranscriptFoldOutcome(
                            outcome_kind=TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN,
                            **facts,
                            lineage=retained.ranges,
                            transcript_span=TranscriptSpanEvidence(
                                source_object_id=f.source,
                                source_revision_key=revision,
                                file_identity_key=identity.stable_key,
                                **facts,
                            ),
                        ),
                    ),
                    source_bytes=raw,
                    checkpoint_key="orchard.fold." + revision,
                    expected_checkpoint_sequence=0,
                ),
                recorded_at=datetime.now(UTC),
            )
            fold_id = folded.transcript_fold_receipt_id
            self.fold_keys[revision] = FoldOutcomeKey(
                source_object_id=f.source, fold_receipt_id=fold_id, outcome_ordinal=0
            )
        cuts = [len(text) * i // fragments for i in range(fragments + 1)]
        parts = []
        for ordinal, (start, end) in enumerate(zip(cuts, cuts[1:])):
            content = text[start:end]
            byte_start = len(text[:start].encode())
            byte_end = len(text[:end].encode())
            parts.append(
                EvidencePart(
                    part_id=uuid.uuid4(),
                    ordinal=ordinal,
                    component_key="tree.description",
                    component_offset=start,
                    kind="message",
                    native=NativeFacts(),
                    derivation="identity_utf8",
                    lineage=(
                        RawEvidenceSlice(
                            source_range_receipt_id=receipt.source_range_receipt_id,
                            fold_receipt_id=fold_id,
                            fold_outcome_ordinal=0 if fold_id else None,
                            byte_start=byte_start,
                            byte_end_exclusive=byte_end,
                            source_bytes_sha256=digest(content.encode()),
                        ),
                    ),
                    content=content,
                    content_sha256=digest(content.encode()),
                )
            )
        package = f.create(
            tuple(parts),
            source_revision_key=revision,
            occurrence_key=occurrence,
            native=NativeFacts(native_id=native),
        )
        for part in parts:
            f.append(package, part)
        f.seal(package)
        return package

    def command(self, package: Any, **updates: Any) -> Any:
        from memoriesql.application.source_stable_identity import (
            MaterializeSourceStableUnit,
        )

        return MaterializeSourceStableUnit.model_validate(
            self.f.materialization(package, **updates).model_dump(mode="json")
            | {"contract_version": 2, "expected_schema_version": 21}
        )

    def test_released_revision_sensitive_baseline(self) -> None:
        first = self.package("baselinea")
        second = self.package("baselineb", fragments=2)
        a = self.f.materializer.materialize(self.f.materialization(first))
        b = self.f.materializer.materialize(self.f.materialization(second))
        self.assertNotEqual(a.bead_id, b.bead_id)
        self.assertEqual(self.f.counts(), (2, 2, 2, 2, 2, 0, 0))

    def test_cross_revision_representation_and_fresh_key_replay(self) -> None:
        self.enable()
        first = self.package("first")
        second = self.package("second", fragments=3)
        command = self.command(first)
        a = self.f.materializer.materialize_source_stable(command)
        replay = self.f.materializer.materialize_source_stable(command)
        self.assertEqual(a.idempotency_receipt_id, replay.idempotency_receipt_id)
        other = self.command(second)
        b = self.f.materializer.materialize_source_stable(other)
        self.assertEqual(a.bound_package, b.bound_package)
        self.assertEqual(a.task_id, b.task_id)
        self.assertEqual(
            a.initial_materialization_receipt_id, b.initial_materialization_receipt_id
        )
        self.assertNotEqual(a.idempotency_receipt_id, b.idempotency_receipt_id)
        self.assertEqual(self.f.counts(), (1, 1, 1, 1, 1, 0, 0))
        self.assertEqual(
            self.f.row(
                "SELECT count(*) FROM memoriesql.outbox_events WHERE event_kind IN ('source_unit.materialized','semantic_task.enqueued')"
            ),
            (2,),
        )
        with self.assertRaisesRegex(psycopg.Error, "idempotency_conflict"):
            self.f.materializer.materialize_source_stable(
                self.command(first, idempotency_key=other.idempotency_key)
            )

    def test_content_native_conflicts_and_distinct_occurrences(self) -> None:
        self.enable()
        original = self.f.materializer.materialize_source_stable(
            self.command(self.package("original"))
        )
        cases: tuple[tuple[str, dict[str, Any]], ...] = (
            ("content", {"text": "Different fictional tree."}),
            ("facts", {"native": "contradiction"}),
        )
        for revision, changes in cases:
            with self.assertRaisesRegex(
                psycopg.Error, "logical_occurrence_identity_conflict"
            ):
                self.f.materializer.materialize_source_stable(
                    self.command(self.package(revision, **changes))
                )
        second = self.f.materializer.materialize_source_stable(
            self.command(self.package("distinct", occurrence="tree.8", native="tree.8"))
        )
        self.assertNotEqual(original.bead_id, second.bead_id)
        self.assertEqual(original.event_id, second.event_id)

    def test_legacy_transition_is_explicitly_unsupported(self) -> None:
        old = self.package("legacy")
        command = self.f.materialization(old)
        receipt = self.f.materializer.materialize(command)
        self.enable()
        with self.assertRaisesRegex(
            psycopg.Error, "source_stable_transition_unsupported"
        ):
            self.f.materializer.materialize_source_stable(
                self.command(self.package("new"))
            )
        replay = self.f.materializer.materialize(command)
        self.assertEqual(receipt.bound_package, replay.bound_package)
        self.assertEqual(receipt.idempotency_receipt_id, replay.idempotency_receipt_id)

    def test_concurrent_first_submissions_preserve_one_winner(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from memoriesql.infrastructure.postgres.logical_unit_materialization import (
            PostgresLogicalUnitMaterialization,
        )

        self.enable()
        commands = [
            self.command(self.package("concurrenta")),
            self.command(self.package("concurrentb", fragments=2)),
        ]
        ready = Barrier(2)

        def submit(command: Any) -> Any:
            with psycopg.connect(
                make_conninfo(self.f.admin, dbname=self.f.database), autocommit=True
            ) as db:
                api = PostgresLogicalUnitMaterialization(
                    db,
                    credential_sha256=self.f.secret_hash,
                    workspace_id=self.f.workspace,
                )
                ready.wait(timeout=10)
                return api.materialize_source_stable(command)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, commands))
        self.assertEqual(results[0].bound_package, results[1].bound_package)
        self.assertEqual(results[0].task_id, results[1].task_id)
        self.assertEqual(
            {r.status for r in results}, {"materialized", "already_exists"}
        )
        self.assertEqual(self.f.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_atomic_rollback_and_legacy_denial_after_opt_in(self) -> None:
        self.enable()
        package = self.package("rollback")
        command = self.command(package)
        self.f.db.execute(
            "CREATE FUNCTION memoriesql.fictional_reject_outbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fictional_crash'; END; $$"
        )
        self.f.db.execute(
            "CREATE TRIGGER fictional_reject_outbox BEFORE INSERT ON memoriesql.outbox_events FOR EACH ROW EXECUTE FUNCTION memoriesql.fictional_reject_outbox()"
        )
        with self.assertRaisesRegex(psycopg.Error, "fictional_crash"):
            self.f.materializer.materialize_source_stable(command)
        self.assertEqual(self.f.counts(), (0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(
            self.f.row(
                "SELECT count(*) FROM memoriesql.idempotency_receipts WHERE idempotency_key=%s",
                (command.idempotency_key,),
            ),
            (0,),
        )
        self.f.db.execute(
            "DROP TRIGGER fictional_reject_outbox ON memoriesql.outbox_events"
        )
        self.f.materializer.materialize_source_stable(command)
        with self.assertRaisesRegex(
            psycopg.Error, "revision_sensitive_transition_unsupported"
        ):
            self.f.materializer.materialize(self.f.materialization(package))
        self.assertEqual(self.f.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_policy_is_explicit_and_revocation_denies_duplicates(self) -> None:
        old_policy = self.f.policy
        self.enable()
        package = self.package("policy")
        with self.assertRaisesRegex(psycopg.Error, "source_stable_policy_unavailable"):
            self.f.materializer.materialize_source_stable(
                self.command(package, producer_policy_id=old_policy)
            )
        command = self.command(package)
        self.f.materializer.materialize_source_stable(command)
        self.f.db.execute(
            "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
            (self.f.policy,),
        )
        with self.assertRaisesRegex(
            psycopg.Error, "trusted_producer_policy_unavailable"
        ):
            self.f.materializer.materialize_source_stable(command)
        self.assertEqual(self.f.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_forged_revision_lineage_is_not_a_stable_identity_workaround(self) -> None:
        self.enable()
        part = self.f.part("Exact fictional retained bytes.")
        package = self.f.create((part,), source_revision_key="invented.stable.revision")
        with self.assertRaisesRegex(psycopg.Error, "evidence_lineage_mismatch"):
            self.f.append(package, part)
        self.assertEqual(self.f.counts(), (0, 0, 0, 0, 0, 0, 0))

    def test_original_pin_activation_and_author_controlled_reread(self) -> None:
        import asyncio

        if TYPE_CHECKING:
            from tests.runtime import test_source_revisiting as revisiting
        else:
            import test_source_revisiting as revisiting
        from memoriesql.application.source_revisiting import ActivateSourceRevisiting

        r = revisiting.SourceRevisiting(
            "test_complete_context_applies_with_unique_exposure"
        )
        self.addCleanup(r.doCleanups)
        r.setUp()
        self.f = r
        self.enable()
        text = '{"tree":"fictional 🌱"}\n'
        original = r.materializer.materialize_source_stable(
            self.command(self.package("pinned", text=text, fold=True))
        )
        snapshot = r.row(
            "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (original.task_id,),
        )[0]
        duplicate = r.materializer.materialize_source_stable(
            self.command(self.package("newer", text=text, fragments=3, fold=True))
        )
        from memoriesql.application.fold_recovery import (
            InspectFoldOutcome,
            ReadFoldEvidence,
        )
        from memoriesql.infrastructure.postgres.fold_recovery import (
            PostgresFoldRecovery,
        )

        recovery = PostgresFoldRecovery(
            r.db, credential_sha256=r.secret_hash, workspace_id=r.workspace
        )
        key = self.fold_keys["pinned"]
        detail = recovery.inspect(InspectFoldOutcome(key=key))
        self.assertEqual(detail.source_revision_key, "pinned")
        recovered = recovery.read(
            ReadFoldEvidence(
                key=key,
                evidence="raw_lineage",
                lineage_ordinal=0,
                expected_sha256=digest(text.encode()),
            )
        )
        self.assertEqual(recovered.content, text.encode())
        command = ActivateSourceRevisiting(
            idempotency_key="orchard.stable.activate",
            binding_task_id=duplicate.task_id,
            dispatch_policy_id=r.dispatch_policy,
        )
        r.bound = original
        r.activation = r.complete.activate_revisiting(command)
        replay = r.complete.activate_revisiting(
            command.model_copy(
                update={"idempotency_key": "orchard.stable.activate.again"}
            )
        )
        self.assertEqual(replay.execution_task_id, r.activation.execution_task_id)
        self.assertEqual(r.activation.package, original.bound_package)

        def author(messages: Any, info: Any) -> Any:
            if len(r.steps) == 0:
                return r.step(
                    messages,
                    info,
                    "read",
                    r.selection(representation="raw", lineage_ordinal=0, limit=32768),
                )
            return r.step(messages, info, "finish")

        result = asyncio.run(r.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        read = r.steps[1]["source_delivery"]["read"]
        self.assertEqual(bytes.fromhex(read["bytes_hex"]), text.encode())
        self.assertEqual(
            r.row(
                "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
                (original.task_id,),
            )[0],
            snapshot,
        )
        self.assertEqual(
            r.row("SELECT count(*) FROM memoriesql.complete_input_executions"), (1,)
        )
        self.assertEqual(
            r.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,)
        )

    def test_pending_input_and_unknown_native_identity_remain_unmaterialized(
        self,
    ) -> None:
        self.enable()
        part = self.f.part("Pending fictional evidence.")
        base = self.f.declaration((part,))
        pending = self.f.create(
            (part,),
            qualification=base.qualification.model_copy(
                update={"physical_records": "pending_tail"}
            ),
        )
        self.f.append(pending, part)
        self.f.seal(pending)
        with self.assertRaisesRegex(
            psycopg.Error, "materialization_input_pending_or_conflicting"
        ):
            self.f.materializer.materialize_source_stable(self.command(pending))
        unknown = self.f.create(
            (part,),
            occurrence_key="unknown",
            occurrence_identity_basis="producer_assigned",
            native=NativeFacts(),
        )
        self.f.append(unknown, part)
        self.f.seal(unknown)
        with self.assertRaisesRegex(
            psycopg.Error, "source_stable_identity_unqualified"
        ):
            self.f.materializer.materialize_source_stable(self.command(unknown))
        self.assertEqual(self.f.counts(), (0, 0, 0, 0, 0, 0, 0))

    def test_conflicting_event_and_namespace_cannot_reassign_identity(self) -> None:
        self.enable()
        first = self.package("eventfirst")
        self.f.materializer.materialize_source_stable(self.command(first))
        second = self.package("eventretry")
        event = self.f.materialization(second).event.model_copy(
            update={"native": NativeFacts(native_id="contradictory-event")}
        )
        with self.assertRaisesRegex(psycopg.Error, "logical_event_identity_conflict"):
            self.f.materializer.materialize_source_stable(
                self.command(second, event=event)
            )
        policy = uuid.uuid4()
        self.f.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies SELECT tenant_id,workspace_id,access_scope_id,%s,source_object_id,producer_principal_id,qualification_ref,normalization_policy_version,qualification_evidence_sha256,approved_by_principal_id,created_at,expires_at,status,'different.namespace' FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
            (policy, self.f.policy),
        )
        with self.assertRaisesRegex(
            psycopg.Error, "source_stable_transition_unsupported"
        ):
            self.f.materializer.materialize_source_stable(
                self.command(second, producer_policy_id=policy)
            )
        with self.assertRaisesRegex(
            psycopg.Error, "materialization_identity_immutable"
        ):
            self.f.db.execute(
                "UPDATE memoriesql.evidence_producer_policies SET source_stable_namespace='changed' WHERE producer_policy_id=%s",
                (self.f.policy,),
            )
        self.assertEqual(self.f.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_stable_binding_fields_cannot_be_partial(self) -> None:
        from psycopg.types.json import Jsonb

        self.enable()
        self.f.materializer.materialize_source_stable(
            self.command(self.package("completefields"))
        )
        for field in (
            "stable_identity_namespace",
            "stable_identity_hash",
            "stable_content_hash",
        ):
            with (
                self.subTest(field=field),
                self.assertRaises(psycopg.errors.CheckViolation),
            ):
                self.f.db.execute(
                    "INSERT INTO memoriesql.logical_unit_materializations "
                    "SELECT (jsonb_populate_record(NULL::memoriesql.logical_unit_materializations,to_jsonb(b)||%s)).* "
                    "FROM memoriesql.logical_unit_materializations b",
                    (Jsonb({field: None}),),
                )

    def test_authority_expiring_during_observed_source_wait_denies_commit(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from time import monotonic, sleep

        from memoriesql.infrastructure.postgres.logical_unit_materialization import (
            PostgresLogicalUnitMaterialization,
        )

        self.enable()
        command = self.command(self.package("waitexpiry"))
        f = self.f
        with psycopg.connect(
            make_conninfo(f.admin, dbname=f.database), autocommit=True
        ) as lock:
            f.db.execute(
                "UPDATE memoriesql.authentication_credentials SET expires_at=clock_timestamp()+interval '350 milliseconds' WHERE principal_id=%s",
                (f.principal,),
            )

            def submit() -> Any:
                with psycopg.connect(
                    make_conninfo(f.admin, dbname=f.database), autocommit=True
                ) as db:
                    return PostgresLogicalUnitMaterialization(
                        db, credential_sha256=f.secret_hash, workspace_id=f.workspace
                    ).materialize_source_stable(command)

            with ThreadPoolExecutor(max_workers=1) as pool:
                with lock.transaction():
                    lock.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (str(f.tenant) + ":materialization-source:" + str(f.source),),
                    )
                    future = pool.submit(submit)
                    deadline = monotonic() + 1
                    while not f.row(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=%s AND wait_event='advisory' AND query LIKE 'SELECT memoriesql.materialize_logical_unit_v2%%')",
                        (f.database,),
                    )[0]:
                        if future.done() or monotonic() > deadline:
                            self.fail(
                                "materialization did not reach the held source lock"
                            )
                        sleep(0.005)
                    sleep(0.36)
                with self.assertRaisesRegex(psycopg.Error, "authority_unavailable"):
                    future.result(timeout=3)
        self.assertEqual(f.counts(), (0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(
            f.row(
                "SELECT count(*) FROM memoriesql.idempotency_receipts WHERE idempotency_key=%s",
                (command.idempotency_key,),
            ),
            (0,),
        )

    def test_revision_sensitive_sources_keep_legacy_behavior_at_schema21(self) -> None:
        self.enable()
        first = self.f.materializer.materialize(
            self.f.materialization(self.package("legacyfirst"))
        )
        second = self.f.materializer.materialize(
            self.f.materialization(self.package("legacysecond", fragments=2))
        )
        self.assertNotEqual(first.bead_id, second.bead_id)
        self.assertEqual(self.f.counts(), (2, 2, 2, 2, 2, 0, 0))

    def test_legacy_capture_cannot_cross_opted_source_mode(self) -> None:
        self.enable()
        self.f.materializer.materialize_source_stable(
            self.command(self.package("opted"))
        )
        with self.assertRaisesRegex(
            psycopg.Error, "source_identity_mode_transition_unsupported"
        ):
            self.f.accept(self.f.command())
        self.assertEqual(self.f.counts(), (1, 1, 1, 1, 1, 0, 0))

    def test_component_interleaving_is_not_harmless_refragmentation(self) -> None:
        self.enable()
        first_parts = tuple(
            self.f.part(content, ordinal).model_copy(
                update={"component_key": component, "component_offset": offset}
            )
            for ordinal, (component, offset, content) in enumerate(
                (("a", 0, "A"), ("b", 0, "B"), ("a", 1, "C"))
            )
        )
        first = self.f.create(first_parts)
        for part in first_parts:
            self.f.append(first, part)
        self.f.seal(first)
        original = self.f.materializer.materialize_source_stable(self.command(first))
        reordered_parts = tuple(
            self.f.part(content, ordinal).model_copy(
                update={"component_key": component, "component_offset": 0}
            )
            for ordinal, (component, content) in enumerate((("a", "AC"), ("b", "B")))
        )
        reordered = self.f.create(reordered_parts, package_revision=2)
        for part in reordered_parts:
            self.f.append(reordered, part)
        self.f.seal(reordered)
        with self.assertRaisesRegex(
            psycopg.Error, "logical_occurrence_identity_conflict"
        ):
            self.f.materializer.materialize_source_stable(self.command(reordered))
        self.assertEqual(self.f.counts(), (1, 1, 1, 1, 1, 0, 0))
        self.assertEqual(
            self.f.materializer.materialize_source_stable(
                self.command(first)
            ).bound_package,
            original.bound_package,
        )

    def test_source_mode_fences_do_not_scan_unrelated_tenant_history(self) -> None:
        from psycopg import sql
        from psycopg.types.json import Jsonb

        self.enable()
        self.f.materializer.materialize_source_stable(
            self.command(self.package("indexed"))
        )
        f = self.f
        unrelated = str(uuid.uuid4())
        # Independent fictional ledger history, with all ordinary constraints and
        # triggers active. No historical/owner events are read or imported.
        for table, count in (
            ("protected_resources", 1),
            ("source_objects", 1),
        ):
            template = f.row(f"SELECT to_jsonb(t) FROM memoriesql.{table} t LIMIT 1")[0]
            rows = []
            for i in range(count):
                item = dict(template, source_object_id=unrelated)
                if table == "protected_resources":
                    item["resource_id"] = unrelated
                elif table == "source_objects":
                    item["external_object_id"] = "unrelated.orchard"
                rows.append(item)
            columns = sql.SQL(",").join(
                sql.Identifier(row[0])
                for row in f.db.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='memoriesql' AND table_name=%s AND is_generated='NEVER' ORDER BY ordinal_position",
                    (table,),
                )
            )
            f.db.execute(
                sql.SQL(
                    "INSERT INTO memoriesql.{} ({}) SELECT {} FROM jsonb_populate_recordset(NULL::memoriesql.{},%s)"
                ).format(
                    sql.Identifier(table), columns, columns, sql.Identifier(table)
                ),
                (Jsonb(rows),),
            )
        for i in range(256):
            command = f.command()
            assert command.checkpoint is not None and command.semantic_task is not None
            f.accept(
                command.model_copy(
                    update={
                        "source_object_id": uuid.UUID(unrelated),
                        "idempotency_key": f"history.{i}",
                        "event": command.event.model_copy(
                            update={"source_identity_key": f"history.{i}"}
                        ),
                        "checkpoint": command.checkpoint.model_copy(
                            update={"checkpoint_key": f"history.{i}"}
                        ),
                        "semantic_task": command.semantic_task.model_copy(
                            update={"idempotency_key": f"history.{i}"}
                        ),
                    }
                )
            )
        for i in range(128):
            package = self.package(
                f"same{i}", occurrence=f"tree.{i + 100}", native=f"tree.{i + 100}"
            )
            command = self.command(package)
            event = command.event.model_copy(
                update={
                    "event_key": f"same.{i}",
                    "native": command.event.native.model_copy(
                        update={"native_id": f"same.{i}"}
                    ),
                }
            )
            f.materializer.materialize_source_stable(
                command.model_copy(update={"event": event})
            )
        f.db.execute("ANALYZE memoriesql.source_events")
        # Explain the actual private helper body: both canonical insertion and
        # materialization must delegate to these same first/last index lookups.
        body = f.row(
            "SELECT prosrc FROM pg_proc WHERE oid='memoriesql.source_identity_mode_conflicts(uuid,uuid,text)'::regprocedure"
        )[0]
        query = (
            body.replace("e.tenant_id=t", "e.tenant_id=%s")
            .replace("e.source_object_id=s", "e.source_object_id=%s")
            .replace("COALESCE(ns,", "COALESCE(%s::text,")
        )
        for source, namespace in (
            (f.source, "orchard.register.v1"),
            (uuid.UUID(unrelated), None),
        ):
            with self.subTest(source=source):
                plan = f.row(
                    "EXPLAIN (ANALYZE,FORMAT JSON) " + query,
                    (f.tenant, source, namespace, namespace) * 2,
                )[0][0]
                nodes = [plan["Plan"]]
                for node in nodes:
                    nodes.extend(node.get("Plans", []))
                probes = [
                    node
                    for node in nodes
                    if "source_object_id" in node.get("Index Cond", "")
                ]
                self.assertEqual(len(probes), 2, plan)
                self.assertEqual(
                    {probe["Scan Direction"] for probe in probes},
                    {"Forward", "Backward"},
                    plan,
                )
                for probe in probes:
                    self.assertEqual(
                        probe["Index Name"], "source_events_identity_mode_scope", plan
                    )
                    self.assertEqual(probe.get("Actual Rows"), 1, plan)
                self.assertFalse(
                    any(node["Node Type"] in {"Sort", "Seq Scan"} for node in nodes),
                    plan,
                )
                self.assertEqual(
                    sum(node.get("Rows Removed by Filter", 0) for node in nodes),
                    0,
                    plan,
                )
                print("source-mode first/last index lookups:", plan)
        for function in (
            "guard_source_identity_mode()",
            "materialize_logical_unit_v1(jsonb)",
            "materialize_logical_unit_v2(jsonb)",
        ):
            self.assertIn(
                "source_identity_mode_conflicts",
                f.row(
                    "SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure",
                    ("memoriesql." + function,),
                )[0],
            )
