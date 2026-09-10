"""Fictional public-owned source qualification and storage boundary proofs."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.conninfo import make_conninfo
from pydantic import ValidationError

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
    AppendEvidencePart,
    CreateEvidencePackage,
    EvidenceContinuation,
    EvidencePart,
    InspectEvidencePackage,
    NativeFacts,
    PackageDeclaration,
    PageEvidenceInventory,
    RawEvidenceSlice,
    ReadEvidencePart,
    SealEvidencePackage,
    SourceQualification,
    digest,
    inventory_digest,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.evidence_packages import (
    PostgresEvidencePackages,
)
from memoriesql.infrastructure.postgres.migration_runner import migrate
from memoriesql.infrastructure.postgres.source_range import (
    PostgresAuthorizedSourceRangeSession,
)

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import PostgresRuntime
else:
    from test_postgres_runtime import PostgresRuntime


class EvidencePackages(PostgresRuntime):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=14, target_version=16)
        self.api = PostgresEvidencePackages(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        self.raw = PostgresAuthorizedSourceRangeSession(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        self.offset = 0
        self.sequence = 0

    def part(
        self,
        content: str,
        ordinal: int = 0,
        *,
        kind: str = "message",
        chunk_size: int = 65536,
    ) -> EvidencePart:
        payload = content.encode()
        command = build_capture_source_range_command(
            payload=payload,
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
            source_revision_key="orchard.revision",
            file_identity=FileIdentity(platform="fictional", device=1, inode=5),
            observed_source_format_version="orchard.v1",
            byte_start=self.offset,
            checkpoint_key="orchard.raw",
            expected_checkpoint_sequence=self.sequence,
            policies=SourceRangePolicyReferences(
                capture_policy_id="orchard.capture",
                retention_policy_ref="orchard.retention",
            ),
            chunk_size=chunk_size,
        )
        receipt = self.raw.capture(command, recorded_at=datetime.now(UTC))
        result = EvidencePart(
            part_id=uuid.uuid4(),
            ordinal=ordinal,
            component_key=f"orchard.component.{ordinal}",
            kind=kind,
            native=NativeFacts(),
            derivation="identity_utf8",
            lineage=(
                RawEvidenceSlice(
                    source_range_receipt_id=receipt.source_range_receipt_id,
                    byte_start=self.offset,
                    byte_end_exclusive=self.offset + len(payload),
                    source_bytes_sha256=digest(payload),
                ),
            ),
            content=content,
            content_sha256=digest(payload),
        )
        self.offset += len(payload)
        self.sequence += 1
        return result

    def declaration(
        self, parts: tuple[EvidencePart, ...], **updates: Any
    ) -> PackageDeclaration:
        values = dict(
            source_object_id=self.source,
            source_revision_key="orchard.revision",
            occurrence_key="orchard.native-unit.7",
            occurrence_identity_basis="native",
            normalization_policy_version="orchard.normalization.v1",
            native=NativeFacts(native_id="native-unit.7"),
            qualification=SourceQualification(
                qualification_ref="orchard.fictional-qualification.v1",
                boundary="qualified_native_unit",
                boundary_basis="Explicit fictional unit-end record.",
                physical_records="complete",
                topology="unknown",
                normalized_input="complete",
                source_completeness="producer_attested",
            ),
            expected_parts=len(parts),
            expected_characters=sum(len(p.content) for p in parts),
            expected_utf8_bytes=sum(len(p.content.encode()) for p in parts),
            expected_inventory_sha256=inventory_digest(parts),
        )
        return PackageDeclaration.model_validate(values | updates)

    def create(self, parts: tuple[EvidencePart, ...], **updates: Any) -> Any:
        return self.api.write(
            CreateEvidencePackage(
                idempotency_key="create." + str(uuid.uuid4()),
                declaration=self.declaration(parts, **updates),
            )
        )

    def append(self, package: Any, part: EvidencePart, key: str | None = None) -> Any:
        return self.api.write(
            AppendEvidencePart(
                idempotency_key=key or "append." + str(uuid.uuid4()),
                package_id=package.package_id,
                part=part,
            )
        )

    def seal(self, package: Any, key: str | None = None) -> Any:
        return self.api.write(
            SealEvidencePackage(
                idempotency_key=key or "seal." + str(uuid.uuid4()),
                package_id=package.package_id,
            )
        )

    def status(self, package: Any) -> Any:
        return self.api.inspect(InspectEvidencePackage(package_id=package.package_id))

    def test_complete_messages_and_tools_have_exact_inventory_and_no_semantic_effects(
        self,
    ) -> None:
        parts = tuple(
            self.part(text, i, kind=kind)
            for i, (text, kind) in enumerate(
                (
                    ("Plant four trees.", "message"),
                    ("Count saplings.", "tool_request"),
                    ("Four saplings ready.", "tool_result"),
                    ("Planting recorded.", "message"),
                    ("Plot seven.", "structural_context"),
                )
            )
        )
        parts = tuple(
            EvidencePart.model_validate(
                part.model_dump()
                | {
                    "native": NativeFacts(
                        native_id=f"message.{i}",
                        source_order=i,
                        role="tool" if part.kind.startswith("tool") else "user",
                        parent_native_id="message.1"
                        if part.kind == "tool_result"
                        else None,
                        session_native_id="session.4",
                        branch_native_id="branch.2",
                        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                        occurred_at_raw="2026-01-01T00:00:00Z",
                        time_precision="second",
                    ),
                    "parent_component_key": "orchard.component.1"
                    if part.kind == "tool_result"
                    else None,
                }
            )
            for i, part in enumerate(parts)
        )
        package = self.create(parts)
        for part in parts:
            self.append(package, part)
        self.seal(package)
        entries: list[dict[str, object]] = []
        cursor = None
        while True:
            page = self.api.inventory(
                PageEvidenceInventory(
                    package_id=package.package_id, continuation=cursor
                )
            )
            entries.extend(entry.model_dump(mode="json") for entry in page.entries)
            self.assertLessEqual(
                len(canonical_json_bytes(page.model_dump(mode="json"))), 65536
            )
            if page.terminal:
                break
            cursor = page.continuation
        self.assertEqual(entries, [p.inventory_entry() for p in parts])
        status = self.status(package)
        self.assertEqual(status.readiness, "ready_producer_attested")
        self.assertFalse(status.independently_proven_source_complete)
        self.assertEqual(status.producer_principal_id, self.principal)
        for table in ("source_events", "source_units", "beads", "semantic_tasks"):
            self.assertEqual(
                self.db.execute("SELECT count(*) FROM memoriesql." + table).fetchone(),
                (0,),
            )

    def test_large_unicode_and_escaping_round_trip_across_different_page_sizes(
        self,
    ) -> None:
        content = ('a"\\\n😀é\t' * 1800)[:12000]
        part = self.part(content, chunk_size=4096)
        package = self.create((part,))
        self.append(package, part)
        self.seal(package)
        for size in (127, 1024):
            cursor = None
            pieces = []
            position = 0
            calls = 0
            while True:
                page = self.api.read(
                    ReadEvidencePart(
                        package_id=package.package_id,
                        part_id=part.part_id,
                        continuation=cursor,
                        max_characters=size,
                    )
                )
                self.assertEqual(page.character_start, position)
                self.assertEqual(page.content_sha256, digest(page.content.encode()))
                self.assertLessEqual(
                    len(canonical_json_bytes(page.model_dump(mode="json"))), 65536
                )
                pieces.append(page.content)
                position = page.character_end_exclusive
                calls += 1
                if page.terminal:
                    break
                cursor = page.continuation
            self.assertEqual("".join(pieces), content)
            self.assertGreater(calls, 1)
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.evidence_packages"
            ).fetchone(),
            (1,),
        )

    def test_unknown_topology_user_only_tool_only_and_pending_sibling(self) -> None:
        for role in ("user", "tool"):
            part = self.part("Fictional " + role + " only.")
            part = EvidencePart.model_validate(
                part.model_dump() | {"native": NativeFacts(role=role)}
            )
            package = self.create((part,), occurrence_key="orchard." + role)
            self.append(package, part)
            self.seal(package)
            self.assertEqual(self.status(package).readiness, "ready_producer_attested")
            self.assertIsNone(part.native.participant_native_id)
        pending = self.part("A physically unfinished fictional record")
        q = self.declaration((pending,)).qualification.model_copy(
            update={
                "physical_records": "pending_tail",
                "boundary": "unresolved",
                "source_completeness": "unresolved",
                "normalized_input": "incomplete",
                "unresolved_coverage": ("No terminal delimiter.",),
            }
        )
        package = self.create(
            (pending,), occurrence_key="orchard.pending", qualification=q
        )
        self.append(package, pending)
        self.seal(package)
        status = self.status(package)
        self.assertEqual(status.readiness, "pending_source_qualification")
        self.assertEqual(
            status.declaration.qualification.physical_records, "pending_tail"
        )
        self.assertEqual(
            self.api.read(
                ReadEvidencePart(package_id=package.package_id, part_id=pending.part_id)
            ).content,
            pending.content,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.source_range_capture_receipts"
            ).fetchone(),
            (3,),
        )

    def test_duplicate_operations_natural_identity_and_changed_key_conflict(
        self,
    ) -> None:
        part = self.part("The same words.")
        declaration = self.declaration((part,))
        command = CreateEvidencePackage(
            idempotency_key="fixed.create", declaration=declaration
        )
        package = self.api.write(command)
        self.assertTrue(self.api.write(command).replayed)
        duplicate = self.create((part,))
        self.assertEqual(duplicate.package_id, package.package_id)
        self.assertTrue(duplicate.already_exists)
        self.append(package, part, "fixed.append")
        self.assertTrue(self.append(package, part, "fixed.append").replayed)
        self.seal(package, "fixed.seal")
        self.assertTrue(self.seal(package, "fixed.seal").replayed)
        self.assertTrue(self.append(package, part).already_exists)
        with self.assertRaisesRegex(
            psycopg.errors.UniqueViolation, "idempotency_conflict"
        ):
            self.api.write(
                CreateEvidencePackage(
                    idempotency_key="fixed.create",
                    declaration=declaration.model_copy(
                        update={"occurrence_key": "different"}
                    ),
                )
            )
        later = self.create((part,), occurrence_key="genuinely.later")
        self.assertNotEqual(later.package_id, package.package_id)

    def test_missing_parts_changed_hashes_and_premature_seal(self) -> None:
        first = self.part("First.")
        second = self.part("Second.", 1)
        package = self.create((first, second))
        self.append(package, first)
        with self.assertRaisesRegex(psycopg.Error, "evidence_inventory_incomplete"):
            self.seal(package)
        self.assertFalse(self.status(package).sealed)
        with self.assertRaises(ValidationError):
            self.append(package, second.model_copy(update={"content": "Changed"}))
        tampered = EvidencePart.model_validate(
            second.model_dump()
            | {
                "lineage": (
                    second.lineage[0].model_copy(
                        update={"source_bytes_sha256": "0" * 64}
                    ),
                )
            }
        )
        with self.assertRaisesRegex(psycopg.Error, "evidence_source_integrity"):
            self.append(package, tampered)
        self.assertEqual(self.status(package).appended_parts, 1)
        self.append(package, second)
        self.seal(package)

    def test_incomplete_producer_inventory_is_never_independent_source_proof(
        self,
    ) -> None:
        part = self.part("Only the first message, despite a claimed complete unit.")
        package = self.create((part,))
        self.append(package, part)
        self.seal(package)
        # A lying qualified producer cannot be detected by content hashes.
        status = self.status(package)
        self.assertEqual(status.readiness, "ready_producer_attested")
        self.assertFalse(status.independently_proven_source_complete)
        self.assertEqual(
            status.declaration.qualification.source_completeness, "producer_attested"
        )
        # A producer that reports the missing coverage cannot become ready.
        q = status.declaration.qualification.model_copy(
            update={
                "normalized_input": "incomplete",
                "unresolved_coverage": ("Required second message missing.",),
            }
        )
        other = self.create(
            (part,), occurrence_key="reported.incomplete", qualification=q
        )
        self.append(other, part)
        self.seal(other)
        self.assertEqual(self.status(other).readiness, "pending_source_qualification")

    def test_invalid_cursors_revocation_and_workspace_denial(self) -> None:
        part = self.part("Fictional content.")
        package = self.create((part,))
        self.append(package, part)
        self.seal(package)
        for cursor in (
            EvidenceContinuation(
                package_id=uuid.uuid4(),
                inventory_sha256=inventory_digest((part,)),
                part_id=part.part_id,
                position=1,
            ),
            EvidenceContinuation(
                package_id=package.package_id,
                inventory_sha256="0" * 64,
                part_id=part.part_id,
                position=1,
            ),
            EvidenceContinuation(
                package_id=package.package_id,
                inventory_sha256=inventory_digest((part,)),
                part_id=part.part_id,
                position=999,
            ),
        ):
            with self.assertRaisesRegex(psycopg.Error, "invalid_evidence_continuation"):
                self.api.read(
                    ReadEvidencePart(
                        package_id=package.package_id,
                        part_id=part.part_id,
                        continuation=cursor,
                    )
                )
        wrong = PostgresEvidencePackages(
            self.db, credential_sha256=self.secret_hash, workspace_id=uuid.uuid4()
        )
        with self.assertRaises(
            (
                PermissionError,
                psycopg.errors.InsufficientPrivilege,
                psycopg.errors.InvalidAuthorizationSpecification,
            )
        ):
            wrong.inspect(InspectEvidencePackage(package_id=package.package_id))
        page = self.api.read(
            ReadEvidencePart(
                package_id=package.package_id, part_id=part.part_id, max_characters=2
            )
        )
        self.db.execute(
            "UPDATE memoriesql.authentication_credentials SET status='revoked',revoked_at=clock_timestamp() WHERE tenant_id=%s",
            (self.tenant,),
        )
        with self.assertRaises(
            (
                PermissionError,
                psycopg.errors.InsufficientPrivilege,
                psycopg.errors.InvalidAuthorizationSpecification,
            )
        ):
            self.api.read(
                ReadEvidencePart(
                    package_id=package.package_id,
                    part_id=part.part_id,
                    continuation=page.continuation,
                )
            )

    def test_interrupted_append_rolls_back_and_committed_replies_replay(self) -> None:
        part = self.part("Restart preserves the source.")
        package = self.create((part,))
        command = AppendEvidencePart(
            idempotency_key="restart.append", package_id=package.package_id, part=part
        )
        with self.assertRaisesRegex(RuntimeError, "fictional interruption"):
            with self.db.transaction():
                self.begin()
                from psycopg.types.json import Jsonb

                self.db.execute(
                    "SELECT memoriesql.write_evidence_package_v1(%s)",
                    (Jsonb(command.model_dump(mode="json")),),
                )
                raise RuntimeError("fictional interruption before commit")
        self.assertEqual(self.status(package).appended_parts, 0)
        self.api.write(command)
        restarted = PostgresEvidencePackages(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        self.assertTrue(restarted.write(command).replayed)
        seal = SealEvidencePackage(
            idempotency_key="restart.seal", package_id=package.package_id
        )
        with self.assertRaisesRegex(RuntimeError, "seal interruption"):
            with self.db.transaction():
                self.begin()
                self.db.execute(
                    "SELECT memoriesql.write_evidence_package_v1(%s)",
                    (Jsonb(seal.model_dump(mode="json")),),
                )
                raise RuntimeError("seal interruption")
        self.assertFalse(self.status(package).sealed)
        restarted.write(seal)
        self.assertTrue(restarted.write(seal).replayed)

    def test_maximum_inventory_and_storage_fragments_keep_one_occurrence(self) -> None:
        # Reuse one retained fictional block as explicit repeated source evidence.
        # 256 storage fragments of one component exercise the 16 MiB package cap.
        first = self.part("x" * 65536, chunk_size=256)
        parts = tuple(
            EvidencePart.model_validate(
                first.model_dump()
                | {
                    "part_id": uuid.uuid4(),
                    "ordinal": i,
                    "component_offset": i * 65536,
                }
            )
            for i in range(256)
        )
        package = self.create(parts)
        elapsed = []
        for part in parts:
            started = monotonic()
            self.append(package, part)
            elapsed.append(monotonic() - started)
        started = monotonic()
        self.seal(package)
        elapsed.append(monotonic() - started)
        status = self.status(package)
        self.assertEqual(status.appended_utf8_bytes, 16777216)
        self.assertEqual(status.appended_parts, 256)
        self.assertEqual(status.readiness, "ready_producer_attested")
        self.assertLess(max(elapsed), 3)
        page = self.api.read(
            ReadEvidencePart(
                package_id=package.package_id,
                part_id=parts[-1].part_id,
                max_characters=1024,
            )
        )
        self.assertEqual(page.content, "x" * 1024)
        # Keyset inventory query can use the bounded package/ordinal primary key.
        with self.db.transaction():
            self.db.execute("SET LOCAL enable_seqscan=off")
            plan_row = self.db.execute(
                "EXPLAIN (ANALYZE, FORMAT JSON) SELECT inventory FROM memoriesql.evidence_package_parts WHERE tenant_id=%s AND package_id=%s AND ordinal>=252 ORDER BY ordinal LIMIT 4",
                (self.tenant, package.package_id),
            ).fetchone()
            assert plan_row is not None
            plan = plan_row[0][0]["Plan"]
        self.assertEqual(plan["Actual Rows"], 4)
        self.assertIn("Index", str(plan))
        with self.assertRaises(ValidationError):
            self.declaration(parts, expected_parts=257)
        with self.assertRaises(ValidationError):
            self.declaration(parts, expected_utf8_bytes=16777217)
        self.assertEqual(
            self.db.execute(
                "SELECT count(DISTINCT occurrence_hash) FROM memoriesql.evidence_packages"
            ).fetchone(),
            (1,),
        )

    def test_cross_principal_capture_is_not_read_authority_and_delegation_revokes(
        self,
    ) -> None:
        part = self.part("Only an authorized fictional reader can inspect.")
        package = self.create((part,))
        self.append(package, part)
        self.seal(package)
        # Fictional configured role policy exercises delegation intersection;
        # the migration itself does not grant raw-read to any additional role.
        self.db.execute(
            "INSERT INTO memoriesql.role_capabilities(role_key,capability_key) VALUES('paired_device','source.raw.read')"
        )
        clients = []
        for capabilities in (["memory.capture"], ["source.raw.read"]):
            principal, pairing, grant, credential = [uuid.uuid4() for _ in range(4)]
            secret = digest(str(credential).encode())
            with self.db.transaction():
                self.begin()
                self.db.execute(
                    "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,'device','paired_device',%s,%s,%s,%s,%s)",
                    (
                        principal,
                        pairing,
                        grant,
                        credential,
                        capabilities,
                        [self.scope],
                        secret,
                        self.now,
                        self.now + timedelta(hours=1),
                    ),
                )
            clients.append(
                (
                    PostgresEvidencePackages(
                        self.db, credential_sha256=secret, workspace_id=self.workspace
                    ),
                    grant,
                )
            )
        request = ReadEvidencePart(
            package_id=package.package_id, part_id=part.part_id, max_characters=1
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            clients[0][0].read(request)
        page = clients[1][0].read(request)
        self.assertFalse(page.terminal)
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'revoked',%s,%s,%s)",
                (
                    clients[1][1],
                    ["source.raw.read"],
                    [self.scope],
                    self.now,
                    self.now + timedelta(hours=1),
                    datetime.now(UTC),
                ),
            )
        with self.assertRaises(
            (
                PermissionError,
                psycopg.errors.InsufficientPrivilege,
                psycopg.errors.InvalidAuthorizationSpecification,
            )
        ):
            clients[1][0].read(
                request.model_copy(update={"continuation": page.continuation})
            )

    def test_multiple_acquisition_ranges_and_applicable_fold_lineage(self) -> None:
        from memoriesql.application.capture.transcript_fold import (
            DurableTranscriptReadRequest,
            TranscriptFoldOutcome,
            TranscriptFoldOutcomeKind,
            TranscriptSpanEvidence,
            build_transcript_fold_command,
        )
        from memoriesql.infrastructure.postgres.transcript_fold import (
            PostgresAuthorizedTranscriptFoldSession,
        )

        a = self.part("Fictional ", chunk_size=3)
        b = self.part("unit.\n", chunk_size=6)
        content = a.content + b.content
        identity = FileIdentity(platform="fictional", device=1, inode=5)
        session = PostgresAuthorizedTranscriptFoldSession(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        retained = session.read(
            DurableTranscriptReadRequest(
                source_object_id=self.source,
                source_revision_key="orchard.revision",
                file_identity_key=identity.stable_key,
                byte_start=0,
                max_bytes=len(content.encode()),
            )
        )
        span = TranscriptSpanEvidence(
            source_object_id=self.source,
            source_revision_key="orchard.revision",
            file_identity_key=identity.stable_key,
            byte_start=0,
            byte_end_exclusive=len(content.encode()),
            start_record_index=0,
            end_record_index=1,
            source_bytes_sha256=digest(content.encode()),
        )
        command = build_transcript_fold_command(
            binding=KernelCaptureBinding(
                tenant_id=self.tenant,
                workspace_id=self.workspace,
                access_scope_id=self.scope,
                source_object_id=self.source,
                expected_source_object_schema_version=1,
            ),
            capability_id="orchard.capture",
            connector_id="orchard.synthetic",
            adapter_profile_version="orchard.v1",
            observed_source_format_version="orchard.v1",
            source_revision_key="orchard.revision",
            file_identity=identity,
            outcomes=(
                TranscriptFoldOutcome(
                    outcome_kind=TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN,
                    byte_start=0,
                    byte_end_exclusive=len(content.encode()),
                    start_record_index=0,
                    end_record_index=1,
                    source_bytes_sha256=digest(content.encode()),
                    lineage=retained.ranges,
                    transcript_span=span,
                ),
            ),
            source_bytes=content.encode(),
            checkpoint_key="orchard.fold",
            expected_checkpoint_sequence=0,
        )
        fold = session.commit(command, recorded_at=self.now)
        part = EvidencePart.model_validate(
            a.model_dump()
            | {
                "content": content,
                "content_sha256": digest(content.encode()),
                "lineage": tuple(
                    item.model_copy(
                        update={
                            "fold_receipt_id": fold.transcript_fold_receipt_id,
                            "fold_outcome_ordinal": 0,
                        }
                    )
                    for item in a.lineage + b.lineage
                ),
            }
        )
        package = self.create((part,))
        wrong = part.model_copy(
            update={
                "lineage": (
                    part.lineage[0].model_copy(update={"fold_outcome_ordinal": 1}),
                )
                + part.lineage[1:]
            }
        )
        with self.assertRaisesRegex(psycopg.Error, "evidence_fold_mismatch"):
            self.append(package, wrong)
        self.append(package, part)
        self.seal(package)
        self.assertEqual(
            self.api.read(
                ReadEvidencePart(package_id=package.package_id, part_id=part.part_id)
            ).content,
            content,
        )
        self.assertEqual(
            self.api.inventory(PageEvidenceInventory(package_id=package.package_id))
            .entries[0]
            .lineage,
            part.lineage,
        )
        self.assertEqual(self.create((part,)).package_id, package.package_id)
        # A deliberately different storage/acquisition shape is an explicit
        # representation revision, never a different source occurrence.
        fragments = (
            a,
            EvidencePart.model_validate(
                b.model_dump()
                | {
                    "ordinal": 1,
                    "component_key": a.component_key,
                    "component_offset": len(a.content),
                }
            ),
        )
        revised = self.create(fragments, package_revision=2)
        for fragment in fragments:
            self.append(revised, fragment)
        self.seal(revised)
        self.assertNotEqual(revised.package_id, package.package_id)
        self.assertEqual(
            self.db.execute(
                "SELECT count(DISTINCT occurrence_hash) FROM memoriesql.evidence_packages"
            ).fetchone(),
            (1,),
        )

    def test_lock_wait_fails_boundedly_without_partial_receipt(self) -> None:
        part = self.part("Fictional bounded wait.")
        package = self.create((part,))
        with psycopg.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        ) as blocker:
            with blocker.transaction():
                blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":evidence-operation:held.append",),
                )
                started = monotonic()
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    self.append(package, part, "held.append")
                self.assertLess(monotonic() - started, 2)
        self.assertEqual(self.status(package).appended_parts, 0)
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.idempotency_receipts WHERE idempotency_key='held.append'"
            ).fetchone(),
            (0,),
        )
        self.append(package, part, "held.append")
        self.seal(package)

    def test_storage_bounds_and_existing_schema_history(self) -> None:
        oversized = "x" * 65537
        with self.assertRaises(ValidationError):
            EvidencePart(
                part_id=uuid.uuid4(),
                ordinal=0,
                component_key="oversized",
                kind="message",
                native=NativeFacts(),
                derivation="identity_utf8",
                lineage=(
                    RawEvidenceSlice(
                        source_range_receipt_id=uuid.uuid4(),
                        byte_start=0,
                        byte_end_exclusive=65537,
                        source_bytes_sha256=digest(oversized.encode()),
                    ),
                ),
                content=oversized,
                content_sha256=digest(oversized.encode()),
            )
        part = self.part("Bounded.")
        package = self.create((part,))
        self.append(package, part)
        self.seal(package)
        with self.assertRaises(ValidationError):
            ReadEvidencePart(
                package_id=package.package_id, part_id=part.part_id, max_characters=1025
            )
        with self.assertRaises(ValidationError):
            PageEvidenceInventory(package_id=package.package_id, limit=5)
        from memoriesql.infrastructure.postgres.migration_runner import (
            discover_migrations,
        )

        history = self.db.execute(
            "SELECT version,sha256 FROM memoriesql.schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(
            history, [(m.version, m.sha256) for m in discover_migrations()]
        )
        self.assertEqual(len(history), 16)
        self.assertEqual(
            self.db.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid='memoriesql.evidence_packages'::regclass"
            ).fetchone(),
            (True,),
        )
        with self.db.transaction():
            self.begin()
            with (
                self.assertRaises(psycopg.errors.InsufficientPrivilege),
                self.db.transaction(),
            ):
                self.db.execute("SELECT content FROM memoriesql.evidence_package_parts")


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    return unittest.TestSuite(
        EvidencePackages(name)
        for name in loader.getTestCaseNames(EvidencePackages)
        if name in EvidencePackages.__dict__
    )
