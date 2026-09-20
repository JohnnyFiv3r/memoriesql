"""Fictional installed scope mechanics; no model understanding or live calls claimed."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import psycopg
from psycopg.conninfo import make_conninfo
from pydantic import ValidationError

from memoriesql.application.canonical_transactions import SourceType
from memoriesql.application.capture.contracts import (
    CaptureSurface,
    KernelCaptureBinding,
)
from memoriesql.application.capture.host_protocol import FileIdentity
from memoriesql.application.capture.source_range import (
    SourceRangePolicyReferences,
    build_capture_source_range_command,
)
from memoriesql.application.declared_evidence_scope import (
    DeclaredEvidenceScope,
    InspectScopedEvidencePackage,
    MaterializeDeclaredScope,
    ScopeCarryForward,
)
from memoriesql.application.evidence_packages import (
    EvidencePart,
    NativeFacts,
    RawEvidenceSlice,
    SourceQualification,
    digest,
)
from memoriesql.application.source_revisiting import ActivateSourceRevisiting
from memoriesql.infrastructure.postgres.migration_runner import migrate

if TYPE_CHECKING:
    from tests.runtime.test_source_revisiting import SourceRevisiting
else:
    from test_source_revisiting import SourceRevisiting


class DeclaredScopes(SourceRevisiting):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=20, target_version=25)
        previous = self.policy
        self.native_only_policy = previous
        self.policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies SELECT tenant_id,workspace_id,access_scope_id,%s,source_object_id,producer_principal_id,qualification_ref,normalization_policy_version,qualification_evidence_sha256,approved_by_principal_id,created_at,expires_at,status,'orchard.register.v1',true FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
            (self.policy, previous),
        )
        self.raw_receipts: dict[tuple[str, int, int], Any] = {}
        self.declared = DeclaredEvidenceScope(
            scope_id=uuid.uuid4(),
            boundary_basis="Explicit fictional complete-record selection; native episode unknown.",
        )

    def scope_package(
        self,
        *,
        scope: DeclaredEvidenceScope | None = None,
        text: str = "User: Ship Friday.\nUser: Only if security signs off; otherwise Monday. 🌱\nTool: approval pending.\n",
        revision: str = "orchard.scope.r1",
        fragments: int = 1,
        chunk_size: int = 65536,
        physical: Literal["complete", "pending_tail"] = "complete",
        start: int = 0,
        package_revision: int = 1,
    ) -> Any:
        selected = scope or self.declared
        raw = text.encode()
        ranges = []
        for relative in range(0, len(raw), chunk_size):
            segment = raw[relative : relative + chunk_size]
            lo = start + relative
            hi = lo + len(segment)
            cache_key = (revision, lo, hi)
            receipt = self.raw_receipts.get(cache_key)
            if receipt is None:
                receipt = self.raw.capture(
                    build_capture_source_range_command(
                        segment,
                        binding=KernelCaptureBinding(
                            tenant_id=self.tenant,
                            workspace_id=self.workspace,
                            access_scope_id=self.scope,
                            source_object_id=self.source,
                            expected_source_object_schema_version=1,
                        ),
                        capability_id="orchard.capture",
                        connector_id="orchard.synthetic",
                        observed_connector_version="1",
                        capture_surface=CaptureSurface.SYNTHETIC,
                        source_revision_key=revision,
                        file_identity=FileIdentity(
                            platform="fictional", device=1, inode=5
                        ),
                        observed_source_format_version="orchard.v1",
                        byte_start=lo,
                        checkpoint_key="scope.raw." + uuid.uuid4().hex,
                        expected_checkpoint_sequence=0,
                        policies=SourceRangePolicyReferences(
                            capture_policy_id="orchard.capture",
                            retention_policy_ref="orchard.retention",
                        ),
                    ),
                    recorded_at=datetime.now(UTC),
                )
                self.raw_receipts[cache_key] = receipt
            ranges.append((lo, hi, receipt))
        parts = []
        byte_offset = start
        for i in range(fragments):
            left = i * len(text) // fragments
            right = (i + 1) * len(text) // fragments
            content = text[left:right]
            payload = content.encode()
            parts.append(
                EvidencePart(
                    part_id=uuid.uuid4(),
                    ordinal=i,
                    component_key="declared.records",
                    component_offset=left,
                    kind="records",
                    native=NativeFacts(),
                    derivation="identity_utf8",
                    lineage=tuple(
                        RawEvidenceSlice(
                            source_range_receipt_id=receipt.source_range_receipt_id,
                            byte_start=max(lo, byte_offset),
                            byte_end_exclusive=min(hi, byte_offset + len(payload)),
                            source_bytes_sha256=digest(
                                raw[
                                    max(lo, byte_offset) - start : min(
                                        hi, byte_offset + len(payload)
                                    )
                                    - start
                                ]
                            ),
                        )
                        for lo, hi, receipt in ranges
                        if lo < byte_offset + len(payload) and hi > byte_offset
                    ),
                    content=content,
                    content_sha256=digest(payload),
                )
            )
            byte_offset += len(payload)
        package = self.create(
            tuple(parts),
            source_revision_key=revision,
            occurrence_key=selected.occurrence_key,
            occurrence_identity_basis="producer_assigned",
            native=NativeFacts(),
            package_revision=package_revision,
            qualification=SourceQualification(
                qualification_ref="orchard.fictional-qualification.v1",
                boundary="unresolved",
                boundary_basis=selected.boundary_basis,
                physical_records=physical,
                topology="unknown",
                normalized_input="complete",
                source_completeness="unresolved",
            ),
        )
        for part in parts:
            self.append(package, part)
        self.seal(package)
        return package

    def scope_command(self, package: Any, **updates: Any) -> MaterializeDeclaredScope:
        return MaterializeDeclaredScope.model_validate(
            dict(
                idempotency_key="scope.materialize." + uuid.uuid4().hex,
                package_id=package.package_id,
                expected_inventory_sha256=self.status(package).inventory_sha256,
                producer_policy_id=self.policy,
                expected_source_object_schema_version=1,
                source_type=SourceType.TRANSCRIPT,
                scope=self.declared,
                carry_forward=None,
            )
            | updates
        )

    def bind_scope(self, package: Any, **updates: Any) -> Any:
        return self.materializer.materialize_declared_scope(
            self.scope_command(package, **updates)
        )

    def test_initial_unknown_scope_inspection_and_real_worker_path(self) -> None:
        package = self.scope_package()
        self.bound = self.bind_scope(package)
        status = self.api.inspect_scope(
            InspectScopedEvidencePackage(package_id=package.package_id)
        )
        self.assertEqual(status.scope_readiness, "materialized_declared_scope")
        self.assertEqual(status.package.readiness, "pending_source_qualification")
        self.assertFalse(status.package.independently_proven_source_complete)
        assert status.scope_binding is not None
        self.assertEqual(status.scope_binding.scope, self.declared)
        self.assertEqual(
            self.row(
                "SELECT external_event_id,session_id FROM memoriesql.source_events"
            ),
            (None, None),
        )
        self.activation = self.complete.activate_revisiting(
            ActivateSourceRevisiting(
                idempotency_key="scope.activate",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
            )
        )
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        declaration = self.steps[0]["task_input"]["payload"]["declaration"]
        self.assertEqual(
            declaration["qualification"]["source_completeness"], "unresolved"
        )
        self.assertEqual(declaration["qualification"]["boundary"], "unresolved")
        self.assertIsNone(declaration["native"]["native_id"])
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.bead_versions"), (1,)
        )
        self.assertEqual(
            self.bind_scope(package).bound_package, self.bound.bound_package
        )

    def test_replay_refragmentation_preserves_first_pins(self) -> None:
        original = self.scope_package()
        command = self.scope_command(original)
        first = self.materializer.materialize_declared_scope(command)
        replay = self.materializer.materialize_declared_scope(command)
        self.assertTrue(replay.replayed)
        self.assertEqual(
            first.initial_materialization_receipt_id,
            replay.initial_materialization_receipt_id,
        )
        repacked = self.scope_package(fragments=7, package_revision=2)
        rebound = self.bind_scope(repacked)
        acquired_again = self.scope_package(
            revision="orchard.scope.r2", fragments=7, chunk_size=7
        )
        carried = self.bind_scope(
            acquired_again,
            carry_forward=ScopeCarryForward(
                original_package_id=original.package_id,
                correspondence_basis="Qualified fictional carry-forward of the same declared records across acquisition windows.",
            ),
        )
        self.assertEqual(carried.bound_package, first.bound_package)
        self.assertEqual(rebound.status, "already_exists")
        self.assertEqual(
            (rebound.bound_package, rebound.bead_id, rebound.task_id),
            (first.bound_package, first.bead_id, first.task_id),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.logical_unit_materializations"),
            (1,),
        )

    def test_cross_revision_requires_explicit_carry_forward(self) -> None:
        package = self.scope_package()
        first = self.bind_scope(package)
        next_package = self.scope_package(revision="orchard.scope.r2")
        with self.assertRaisesRegex(psycopg.Error, "correspondence_required"):
            self.bind_scope(next_package)
        proof = ScopeCarryForward(
            original_package_id=package.package_id,
            correspondence_basis="Qualified fictional producer explicitly identifies the same declared record selection in revision r2.",
        )
        second = self.bind_scope(next_package, carry_forward=proof)
        self.assertEqual(second.bound_package, first.bound_package)
        self.assertEqual(second.carry_forward, proof)
        self.assertEqual(
            self.row(
                "SELECT response_receipt->'carry_forward' FROM memoriesql.idempotency_receipts WHERE idempotency_receipt_id=%s",
                (second.idempotency_receipt_id,),
            )[0],
            proof.model_dump(mode="json"),
        )

    def test_equal_text_overlapping_distinct_scopes_and_amendment(self) -> None:
        package = self.scope_package()
        first = self.bind_scope(package)
        other = DeclaredEvidenceScope(
            scope_id=uuid.uuid4(), boundary_basis=self.declared.boundary_basis
        )
        overlap = self.scope_package(scope=other)
        second = self.bind_scope(overlap, scope=other)
        self.assertNotEqual(first.bead_id, second.bead_id)
        amended = DeclaredEvidenceScope(
            scope_id=uuid.uuid4(),
            boundary_basis="Explicit wider fictional scope.",
            amends_source_unit_id=first.source_unit_id,
            amendment_reason="Include the subsequent qualifier as declared evidence; no accepted meaning replacement.",
        )
        pkg = self.scope_package(
            scope=amended,
            revision="orchard.scope.amended",
            text="Ship Friday only if approved; otherwise Monday.\nLater: security has not signed off.\n",
        )
        amended_result = self.bind_scope(pkg, scope=amended)
        self.assertEqual(
            amended_result.scope.amends_source_unit_id, first.source_unit_id
        )
        self.assertEqual(self.bind_scope(package).bound_package, first.bound_package)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.logical_unit_materializations"),
            (3,),
        )

    def test_changed_membership_and_changed_declaration_conflict(self) -> None:
        package = self.scope_package()
        self.bind_scope(package)
        moved = self.scope_package(start=4096, package_revision=2)
        with self.assertRaisesRegex(psycopg.Error, "membership_conflict"):
            self.bind_scope(moved)
        changed = self.scope_package(
            text="Different proposed meaning.\n", revision="orchard.scope.r2"
        )
        with self.assertRaisesRegex(psycopg.Error, "identity_conflict"):
            self.bind_scope(
                changed,
                carry_forward=ScopeCarryForward(
                    original_package_id=package.package_id,
                    correspondence_basis="Explicit assertion cannot conceal changed scope content.",
                ),
            )
        with self.assertRaises(ValidationError):
            DeclaredEvidenceScope(
                scope_id=uuid.uuid4(),
                boundary_basis="scope",
                amends_source_unit_id=uuid.uuid4(),
            )

    def test_pending_physical_records_never_materialize(self) -> None:
        package = self.scope_package(
            physical="pending_tail", text="Complete record\nPartial 🌱"
        )
        with self.assertRaisesRegex(psycopg.Error, "pending_or_conflicting"):
            self.bind_scope(package)
        status = self.api.inspect_scope(
            InspectScopedEvidencePackage(package_id=package.package_id)
        )
        self.assertIsNone(status.scope_binding)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.beads"), (0,))

    def test_scope_requires_explicit_policy_and_reauthorizes_replay(self) -> None:
        package = self.scope_package()
        command = self.scope_command(package)
        self.materializer.materialize_declared_scope(command)
        self.db.execute(
            "UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s",
            (self.policy,),
        )
        with self.assertRaises(psycopg.Error):
            self.materializer.materialize_declared_scope(command)

    def activate_scope(self, contexts: tuple[Any, ...] = ()) -> None:
        package = self.scope_package()
        self.bound = self.bind_scope(package)
        self.activation = self.complete.activate_revisiting(
            ActivateSourceRevisiting(
                idempotency_key="scope.activate",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
                authorized_context=contexts,
            )
        )

    def response(self, messages: Any, info: Any) -> Any:
        # An independent authored fixture tests persisted/rendered fidelity, not
        # the model's ability to derive these qualifications from the source.
        from pydantic_ai.messages import ModelResponse, ToolCallPart

        result = self.step(messages, info)
        part = result.parts[0]
        assert isinstance(part, ToolCallPart)
        data = part.args_as_dict()
        if data["action"] == "finish":
            annotation = data["typed_output"]["annotations"][0]
            text = "The user proposed shipping Friday only if security signs off; otherwise Monday. Approval remains pending."
            annotation["statements"][0]["statement_text"] = text
            annotation["render"]["title"]["text"] = "Conditional shipping proposal"
            annotation["render"]["summary"][0]["text"] = text
        return ModelResponse(
            parts=[ToolCallPart(part.tool_name, data)], usage=result.usage
        )

    def test_default_deny_policy_and_native_binding_coexistence(self) -> None:
        from memoriesql.application.source_stable_identity import (
            MaterializeSourceStableUnit,
        )

        native = self.package()
        command = self.materialization(native).model_dump(mode="json") | {
            "contract_version": 2,
            "expected_schema_version": 21,
        }
        native_binding = self.materializer.materialize_source_stable(
            MaterializeSourceStableUnit.model_validate(command)
        )
        scoped = self.scope_package()
        with self.assertRaisesRegex(
            psycopg.Error, "trusted_producer_policy_unavailable"
        ):
            self.bind_scope(scoped, producer_policy_id=self.native_only_policy)
        binding = self.bind_scope(scoped)
        self.assertNotEqual(binding.source_unit_id, native_binding.source_unit_id)
        replay = self.materializer.materialize_source_stable(
            MaterializeSourceStableUnit.model_validate(command)
        )
        self.assertEqual(replay.bound_package, native_binding.bound_package)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.source_units"), (2,))

    def test_concurrent_scope_submissions_preserve_one_binding(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from memoriesql.infrastructure.postgres.logical_unit_materialization import (
            PostgresLogicalUnitMaterialization,
        )

        package = self.scope_package()
        commands = [self.scope_command(package), self.scope_command(package)]
        barrier = Barrier(2)

        def submit(command: Any) -> Any:
            with psycopg.connect(
                make_conninfo(self.admin, dbname=self.database), autocommit=True
            ) as connection:
                api = PostgresLogicalUnitMaterialization(
                    connection,
                    credential_sha256=self.secret_hash,
                    workspace_id=self.workspace,
                )
                barrier.wait(timeout=5)
                return api.materialize_declared_scope(command)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, commands))
        self.assertEqual(
            {r.status for r in results}, {"materialized", "already_exists"}
        )
        self.assertEqual(results[0].bound_package, results[1].bound_package)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.logical_unit_materializations"),
            (1,),
        )

    def test_missing_trusted_exposure_cannot_accept(self) -> None:
        self.activate_scope()
        result = asyncio.run(self.worker(recorder=None).run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()

    def test_required_context_incomplete_preserves_thin_scope(self) -> None:
        self.activate_scope()
        result = asyncio.run(
            self.worker(lambda m, i: self.step(m, i, "incomplete")).run_once()
        )
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,)
        )
        self.assert_no_meaning()

    def test_authorized_revisit_does_not_duplicate_unique_scope_coverage(self) -> None:
        self.activate_scope()

        def author(messages: Any, info: Any) -> Any:
            if not self.steps:
                return self.step(messages, info, "read", self.selection(limit=18))
            return self.response(messages, info)

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.steps[1]["source_delivery"]["read"]["content"], "User: Ship Friday."
        )
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"
            ),
            (self.bound.bound_package.required_characters,),
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        stored = self.row(
            "SELECT statement_text FROM memoriesql.bead_semantic_statements"
        )[0]
        self.assertIn("only if security signs off; otherwise Monday", stored)

    def test_cancelled_scope_keeps_accounting_without_meaning(self) -> None:
        self.activate_scope()

        def cancel(messages: Any, info: Any) -> Any:
            with self.db.transaction():
                self.begin()
                self.db.execute(
                    "SELECT memoriesql.cancel_semantic_task(%s,%s,'scope.cancel',clock_timestamp())",
                    (self.tenant, self.activation.execution_task_id),
                )
            return self.response(messages, info)

        result = asyncio.run(self.worker(cancel).run_once())
        self.assertEqual(result.task_status, "cancelled", result)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,)
        )
        self.assert_no_meaning()

    def test_dispatch_revocation_at_apply_fences_scope_acceptance(self) -> None:
        self.activate_scope()
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

    def test_scope_inspection_source_revocation_and_immutability(self) -> None:
        package = self.scope_package()
        binding = self.bind_scope(package)
        with self.assertRaisesRegex(psycopg.Error, "immutable"):
            self.db.execute(
                "UPDATE memoriesql.logical_unit_materializations SET scope_declaration=scope_declaration-'amendment_reason' WHERE source_unit_id=%s",
                (binding.source_unit_id,),
            )
        self.db.execute(
            "DELETE FROM memoriesql.role_capabilities WHERE role_key='personal_owner' AND capability_key='source.raw.read'"
        )
        with self.assertRaises(psycopg.Error):
            self.api.inspect_scope(
                InspectScopedEvidencePackage(package_id=package.package_id)
            )

    def test_classified_scope_preserves_mentions_contribution_and_inspection(
        self,
    ) -> None:
        from memoriesql.application.bead_classification import (
            ActivateClassifiedAuthorship,
            BeadTypePin,
        )
        from memoriesql.application.stored_bead_inspection import InspectStoredBead
        from memoriesql.infrastructure.postgres.stored_bead_inspection import (
            PostgresStoredBeadInspection,
        )

        if TYPE_CHECKING:
            from tests.runtime.test_bead_classification import BeadClassification
        else:
            from test_bead_classification import BeadClassification
        f: Any = BeadClassification()
        self.addCleanup(f.doCleanups)
        f.setUp()
        migrate(f.db, expected_current_version=23, target_version=25)
        old = f.policy
        f.policy = uuid.uuid4()
        f.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies SELECT tenant_id,workspace_id,access_scope_id,%s,source_object_id,producer_principal_id,qualification_ref,normalization_policy_version,qualification_evidence_sha256,approved_by_principal_id,created_at,expires_at,status,'orchard.register.v1',true FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
            (f.policy, old),
        )
        f.declared = self.declared
        f.raw_receipts = {}
        package = DeclaredScopes.scope_package(
            f,
            text="Alex proposed counting four fictional trees; no count has occurred.\n",
        )
        f.bound = f.materializer.materialize_declared_scope(
            DeclaredScopes.scope_command(f, package)
        )
        f.activation = f.complete.activate_classified(
            ActivateClassifiedAuthorship(
                idempotency_key="scope.classified",
                binding_task_id=f.bound.task_id,
                dispatch_policy_id=f.dispatch_policy,
                classification_vocabulary=(
                    BeadTypePin(key="observation", revision=1),
                    BeadTypePin(key="action", revision=1),
                ),
            )
        )
        result = asyncio.run(f.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        inspection = PostgresStoredBeadInspection(
            f.db, credential_sha256=f.secret_hash, workspace_id=f.workspace
        ).inspect(InspectStoredBead(bead_id=f.bound.bead_id))
        assert inspection.bead is not None and inspection.bead.meaning is not None
        meaning = inspection.bead.meaning
        self.assertIsNotNone(meaning.classification)
        assert meaning.mentions is not None
        self.assertEqual(meaning.mentions[0].surface_text, "Alex")
        self.assertEqual(meaning.mentions[0].local_identity_state, "ambiguous")
        self.assertEqual(
            f.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,)
        )
        self.assertEqual(
            f.steps[0]["task_input"]["payload"]["declaration"]["qualification"][
                "source_completeness"
            ],
            "unresolved",
        )

    def test_unused_optional_context_is_not_mandatory_coverage(self) -> None:
        from memoriesql.application.logical_unit_materialization import SealedPackagePin
        from memoriesql.application.source_revisiting import AuthorizedContextPin

        context = self.package(
            "Optional neighboring evidence.\n",
            occurrence_key="optional.context",
            seal=False,
        )
        seal = self.seal(context)
        status = self.status(context)
        pin = SealedPackagePin(
            package_id=context.package_id,
            sealed_receipt_id=seal.idempotency_receipt_id,
            inventory_sha256=status.inventory_sha256,
            required_parts=status.appended_parts,
            required_characters=status.appended_characters,
            required_utf8_bytes=status.appended_utf8_bytes,
        )
        self.activate_scope(
            (
                AuthorizedContextPin(
                    package=pin, source_object_id=self.source, source_schema_version=1
                ),
            )
        )
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT sum(end_character-start_character),count(DISTINCT package_id) FROM memoriesql.complete_input_exposures"
            ),
            (self.bound.bound_package.required_characters, 1),
        )

    def test_unbacked_carry_forward_and_amendment_fail_atomically(self) -> None:
        package = self.scope_package()
        before = self.counts()
        with self.assertRaisesRegex(psycopg.Error, "without_binding"):
            self.bind_scope(
                package,
                carry_forward=ScopeCarryForward(
                    original_package_id=package.package_id,
                    correspondence_basis="Cannot invent a prior scope binding.",
                ),
            )
        missing = self.declared.model_copy(
            update={
                "amends_source_unit_id": uuid.uuid4(),
                "amendment_reason": "Missing declaration cannot be an amendment target.",
            }
        )
        with self.assertRaisesRegex(psycopg.Error, "amendment_unavailable"):
            self.bind_scope(package, scope=missing)
        self.assertEqual(before, self.counts())


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        DeclaredScopes(name)
        for name in DeclaredScopes.__dict__
        if name.startswith("test_")
    )
