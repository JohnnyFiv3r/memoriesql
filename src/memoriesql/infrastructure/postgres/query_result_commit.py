"""Private atomic result construction/publication; no result disclosure API.

Commit in a fresh trusted authority frame while the original source frame is
still held. The database pins that original backend epoch and settled invocation.
An acknowledgement is returned only after commit. Uncertain responses require
owned recovery; neither recovery nor replay dispatches the original SELECT.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.pq import TransactionStatus

from memoriesql.application.investigation_contracts import (
    QueryRequest,
    encode_result_scalar,
    request_fingerprint,
    result_json_bytes,
)
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.relation_sql_population import (
    PreparedRelationPopulation,
)
from memoriesql.infrastructure.postgres.restricted_query import NativeQueryExecution
from memoriesql.infrastructure.postgres.result_preparation import (
    PreparationReceipt,
    PreparedContent,
)


@dataclass(frozen=True, slots=True)
class InternalResultCandidate:
    """Private sealed partitions, not an available wire Result or evidence."""

    result_id: UUID
    receipt_ref: UUID
    invocation_ref: UUID
    fingerprint: str
    content_digest: str
    content: PreparedContent

    @classmethod
    def construct(
        cls,
        request: QueryRequest,
        population: PreparedRelationPopulation,
        execution: NativeQueryExecution,
        *,
        policy_hash: str,
    ) -> InternalResultCandidate:
        witness = execution.witnesses
        if (
            execution.deadline_monotonic is None
            or time.monotonic() >= execution.deadline_monotonic
        ):
            raise ValueError("internal result work exhausted")
        if (
            execution.outcome != "complete"
            or execution.settlement_state != "settled"
            or witness is None
            or execution.query_metadata is None
            or execution.invocation_ref is None
            or execution.authority_profile_sha256 is None
            or request.inputs
            or request.parents
            or request.candidate_profile_ref
            or request.scope.source_refs
            or datetime.fromisoformat(request.scope.known_at) != population.known_at
            or request.catalog_hash != population.catalog_hash
            or request.intent not in {"discover", "enumerate"}
        ):
            raise ValueError("internal result qualification unavailable")
        expected_query = {
            "sql": request.sql,
            "parameters": [p.model_dump(mode="json") for p in request.parameters],
            "recursion": (
                request.recursion.model_dump(mode="json")
                if request.recursion else None
            ),
        }
        actual = json.loads(execution.query_metadata)
        if any(actual[k] != value for k, value in expected_query.items()) or len(
            witness.row_provenance
        ) != len(execution.rows):
            raise ValueError("query execution binding mismatch")
        witness_graph = json.loads(witness.bytes)
        if request.recursion:
            bound = request.recursion
            if (
                actual["derivation_program"].get("coverage")
                != {"explicit_depth": bound.max_depth}
                or not any(
                    stage["operation"] == "recursion"
                    and stage.get("phase") == "visit"
                    and stage.get("cte") == bound.cte
                    and stage.get("max_depth") == bound.max_depth
                    and stage.get("reachable")
                    for stage in witness_graph["stages"]
                )
            ):
                raise ValueError("recursive result qualification unavailable")
        fingerprint = request_fingerprint(request)
        result_id, receipt = uuid4(), uuid4()
        rows = [
            {
                "row_ref": str(i),
                "values": [encode_result_scalar(v) for v in row],
                "provenance_ref": str(ref),
            }
            for i, (row, ref) in enumerate(
                zip(execution.rows, witness.row_provenance, strict=True), 1
            )
        ]
        body = {
            "serializer": "result-json-v1",
            "provenance_revision": "native-bag-v1",
            "result_id": str(result_id),
            "query_ref": str(uuid4()),
            "lineage_ref": str(uuid4()),
            "result_schema": [
                {
                    "name": c.name,
                    "pg_type": c.type.pg_type,
                    "ref_type": c.type.reference_kind,
                    "nullable": c.type.nullable,
                }
                for c in execution.columns
            ],
            "rows": rows,
            "query": request.model_dump(mode="json", exclude_unset=True),
            "query_fingerprint": fingerprint,
            "admitted_program": actual["derivation_program"],
            "catalog_hash": population.catalog_hash,
            "policy_hash": policy_hash,
            "authority_profile_sha256": execution.authority_profile_sha256,
            "witness_sha256": hashlib.sha256(witness.bytes).hexdigest(),
            "parents": [],
            "frame": {
                "frame_ref": str(witness.frame_ref),
                "known_at": encode_result_scalar(population.known_at),
                "snapshot_at": encode_result_scalar(population.snapshot_at),
                "view": request.scope.view,
                "snapshot_digest": population.dependency_manifest_sha256,
                "projection_manifest_sha256": population.dependency_manifest_sha256,
                "lifecycle_projection_version": 1,
            },
            "coverage": {
                "query_result": "complete",
                "population_basis": "limited_query"
                if any(
                    s["operation"] == "limit"
                    for s in witness_graph["stages"]
                )
                else "authorized_logical_scope",
                "qualification": "internal_assessed_bag_only",
                "semantic_quality": "unqualified",
                **(
                    {"explicit_depth": request.recursion.max_depth}
                    if request.recursion else {}
                ),
            },
        }
        dependencies = {
            # Lifecycle records have their own exact numeric encoding. Preserve
            # bytes rather than round-trip accepted values through JSON floats.
            "manifest_base64": base64.b64encode(population.dependency_manifest).decode(
                "ascii"
            ),
            "records_base64": base64.b64encode(population.dependency_records).decode(
                "ascii"
            ),
            "logical_rows": {
                name: [
                    [encode_result_scalar(v) for v in row]
                    for row in population.rows[name]
                ]
                for name in sorted(population.rows)
            },
            "evidence_bindings": {
                str(ref): {
                    "statement_id": str(pin.statement_id),
                    "source_unit_id": str(pin.source_unit_id),
                    "content_sha256": pin.content_sha256,
                }
                for ref, pin in population.evidence_bindings.items()
            },
        }
        content = PreparedContent(
            result_json_bytes(body), witness.bytes, result_json_bytes(dependencies)
        )
        content.validate()
        if content.encoded_bytes + 8192 > request.max_result_bytes:
            raise ValueError("internal result storage exhausted")
        if time.monotonic() >= execution.deadline_monotonic:
            raise ValueError("internal result work exhausted")
        return cls(
            result_id,
            receipt,
            execution.invocation_ref,
            fingerprint,
            hashlib.sha256(content.content).hexdigest(),
            content,
        )


class PostgresQueryResultCommit:
    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self.connection, self.credential, self.workspace = (
            connection,
            credential_sha256,
            workspace_id,
        )

    def commit(
        self, ownership: PreparationReceipt, candidate: InternalResultCandidate
    ) -> dict[str, Any]:
        candidate.content.validate()
        return self._call(
            "SELECT memoriesql.commit_query_result_v1(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                ownership.operation_ref,
                ownership.ownership_ref,
                candidate.invocation_ref,
                candidate.result_id,
                candidate.receipt_ref,
                candidate.fingerprint,
                candidate.content.content,
                candidate.content.witnesses,
                candidate.content.dependencies,
                candidate.content.artifact_sha256,
                candidate.content_digest,
            ),
        )

    def recover(self, ownership: PreparationReceipt) -> dict[str, Any]:
        """Private creation receipt only; no bytes, disclosure or execution grant."""
        return self._call(
            "SELECT memoriesql.recover_query_result_v1(%s,%s)",
            (ownership.operation_ref, ownership.ownership_ref),
        )

    def _call(self, statement: str, values: tuple[Any, ...]) -> dict[str, Any]:
        if self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("result commit requires transaction ownership")
        with relation_projection_frame(
            self.connection,
            credential_sha256=self.credential,
            workspace_id=self.workspace,
        ) as frame:
            if statement.startswith("SELECT memoriesql.commit_query_result_v1"):
                limit = frame.execute(
                    "SELECT memoriesql.query_result_commit_budget_v1(%s,%s,%s)",
                    values[:3],
                ).fetchone()
                if limit is None:
                    raise PermissionError("internal result unavailable")
                frame.execute(
                    "SELECT set_config('statement_timeout',%s,true)",
                    (str(int(limit[0])) + "ms",),
                )
            row = frame.execute(statement, values).fetchone()
            if row is None or row[0] is None:
                raise PermissionError("internal result unavailable")
            result: dict[str, Any] = row[0]
        return result
