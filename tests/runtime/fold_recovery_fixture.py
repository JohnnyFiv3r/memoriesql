"""Public-authored fictional storage fixtures; no producer qualification policy.

Raw capture uses its public command. Fold rows are administrative test fixtures
for every stored outcome kind; the exact-turn fixture does not claim admission
through the historical provider-profile allowlist.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from memoriesql.application.capture.contracts import (
    CaptureSurface,
    KernelCaptureBinding,
)
from memoriesql.application.capture.host_protocol import FileIdentity
from memoriesql.application.capture.source_range import (
    SourceRangePolicyReferences,
    build_capture_source_range_command,
)
from memoriesql.application.fold_recovery import FoldOutcomeKey
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.source_range import (
    PostgresAuthorizedSourceRangeSession,
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def insert(db: Any, table: str, values: dict[str, Any]) -> None:
    db.execute(
        sql.SQL("INSERT INTO memoriesql.{} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values),
        ),
        tuple(Jsonb(v) if isinstance(v, dict) else v for v in values.values()),
    )


def seed(
    owner: Any,
    revision: int = 1,
    *,
    db: Any = None,
    kinds: tuple[str, ...] = ("exact_turn", "transcript_span", "policy_disposition"),
    payload: bytes | None = None,
    windows: int = 3,
) -> list[FoldOutcomeKey]:
    """Seed acknowledged immutable facts, plus an unacknowledged physical tail."""
    connection = owner.db if db is None else db
    identity = FileIdentity(platform="fictional", device=7, inode=revision)
    raw = PostgresAuthorizedSourceRangeSession(
        connection, credential_sha256=owner.secret_hash, workspace_id=owner.workspace
    )
    content = payload or ('{"text":"orchard 🌳 café"}\n'.encode())
    retained = content + b'{"pending":'
    offset = 0
    ranges = []
    for ordinal in range(windows):
        end = (
            len(retained)
            if ordinal == windows - 1
            else (ordinal + 1) * len(content) // windows
        )
        part = retained[offset:end]
        command = build_capture_source_range_command(
            payload=part,
            binding=KernelCaptureBinding(
                tenant_id=owner.tenant,
                workspace_id=owner.workspace,
                access_scope_id=owner.scope,
                source_object_id=owner.source,
                expected_source_object_schema_version=1,
            ),
            capability_id="orchard.capture",
            connector_id="orchard.synthetic",
            observed_connector_version="1",
            capture_surface=CaptureSurface.SYNTHETIC,
            source_revision_key=f"orchard.revision.{revision}",
            file_identity=identity,
            observed_source_format_version="orchard.v1",
            byte_start=offset,
            checkpoint_key=f"orchard.raw.{revision}",
            expected_checkpoint_sequence=ordinal,
            policies=SourceRangePolicyReferences(
                capture_policy_id="orchard.capture",
                retention_policy_ref="orchard.retention",
            ),
            chunk_size=4096,
        )
        receipt = raw.capture(command, recorded_at=owner.now)
        ranges.append(
            dict(
                source_range_receipt_id=receipt.source_range_receipt_id,
                receipt_byte_start=offset,
                receipt_byte_end_exclusive=end,
                receipt_payload_sha256=digest(part),
                byte_start=offset,
                byte_end_exclusive=min(end, len(content)),
                source_bytes_sha256=digest(retained[offset : min(end, len(content))]),
            )
        )
        offset = end
    if not kinds:
        return []
    common = dict(
        tenant_id=owner.tenant,
        workspace_id=owner.workspace,
        access_scope_id=owner.scope,
    )
    receipt_id = uuid.uuid4()
    ledger = uuid.uuid4()
    envelope = canonical_json_bytes(
        dict(
            envelope_kind="conversation_event",
            source_type="transcript",
            native_id="orchard.turn",
            parent_native_id="orchard.parent",
            ordered_messages=[{"role": "user", "text": "normalized 🌳"}],
            uncertainty={"time": "unknown"},
            padding="é" * 70000,
        )
    )
    with connection.transaction():
        insert(
            connection,
            "idempotency_receipts",
            common
            | dict(
                idempotency_receipt_id=ledger,
                operation_kind="transcript_fold",
                idempotency_key=str(ledger),
                request_hash=digest(content),
                status="succeeded",
                response_receipt={"fictional": True},
                attempt_count=1,
                created_at=owner.now,
                updated_at=owner.now,
                completed_at=owner.now,
            ),
        )
        insert(
            connection,
            "transcript_fold_receipts",
            common
            | dict(
                transcript_fold_receipt_id=receipt_id,
                idempotency_receipt_id=ledger,
                source_object_id=owner.source,
                source_revision_key=f"orchard.revision.{revision}",
                file_identity_key=identity.stable_key,
                file_identity=identity.model_dump(),
                capability_id="orchard.capture",
                connector_id="orchard.synthetic",
                adapter_profile_version="orchard.fictional.v1",
                observed_source_format_version="orchard.v1",
                byte_start=0,
                byte_end_exclusive=len(content),
                start_record_index=0,
                end_record_index=1,
                complete_record_count=1,
                source_bytes_sha256=digest(content),
                outcomes_sha256=digest(b"fictional stored outcomes"),
                outcome_count=len(kinds),
                exact_turn_count=kinds.count("exact_turn"),
                transcript_span_count=kinds.count("transcript_span"),
                policy_disposition_count=kinds.count("policy_disposition"),
                checkpoint_key=f"orchard.fold.{revision}",
                checkpoint_sequence=1,
                folded_by_principal_id=owner.principal,
                folded_at=owner.now,
            ),
        )
        for ordinal, kind in enumerate(kinds):
            key = common | dict(
                transcript_fold_receipt_id=receipt_id,
                outcome_ordinal=ordinal,
                source_object_id=owner.source,
            )
            insert(
                connection,
                "transcript_fold_outcomes",
                key
                | dict(
                    outcome_kind=kind,
                    byte_start=0,
                    byte_end_exclusive=len(content),
                    start_record_index=0,
                    end_record_index=1,
                    source_bytes_sha256=digest(content),
                    exact_envelope_sha256=digest(envelope)
                    if kind == "exact_turn"
                    else None,
                    disposition_code="orchard.outside_scope"
                    if kind == "policy_disposition"
                    else None,
                    scope_reason="off_date" if kind == "policy_disposition" else None,
                    adapter_profile_version="orchard.fictional.v1",
                    folded_at=owner.now,
                ),
            )
            if kind == "exact_turn":
                insert(
                    connection,
                    "transcript_fold_exact_turns",
                    key
                    | dict(
                        envelope_canonical_json=envelope.decode(),
                        envelope_sha256=digest(envelope),
                        folded_at=owner.now,
                    ),
                )
            for i, r in enumerate(ranges):
                insert(
                    connection,
                    "transcript_fold_outcome_ranges",
                    key | dict(lineage_ordinal=i) | r,
                )
    return [
        FoldOutcomeKey(
            source_object_id=owner.source, fold_receipt_id=receipt_id, outcome_ordinal=i
        )
        for i in range(len(kinds))
    ]


def service(owner: Any, capabilities: list[str] | None = None) -> tuple[str, uuid.UUID]:
    # Explicit fictional role policy. Production policy/provisioning is deferred.
    owner.db.execute(
        "INSERT INTO memoriesql.role_capabilities(role_key,capability_key) VALUES('background_service','source.raw.read') ON CONFLICT DO NOTHING"
    )
    principal, pairing, grant, credential = [uuid.uuid4() for _ in range(4)]
    secret = digest(str(credential).encode())
    with owner.db.transaction():
        owner.begin()
        owner.db.execute(
            "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,'service','background_service',%s,%s,%s,%s,%s)",
            (
                principal,
                pairing,
                grant,
                credential,
                capabilities if capabilities is not None else ["source.raw.read"],
                [owner.scope],
                secret,
                owner.now,
                owner.now + timedelta(hours=1),
            ),
        )
    return secret, grant
