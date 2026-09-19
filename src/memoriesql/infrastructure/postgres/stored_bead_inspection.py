"""Fresh, short authorization transactions for bounded stored-result reads."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import InsufficientPrivilege, LockNotAvailable, QueryCanceled
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.application.stored_bead_inspection import (
    InspectStoredBead,
    ReadStoredBeadEvidence,
    StoredBeadEvidence,
    StoredBeadInspection,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


class PostgresStoredBeadInspection:
    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def inspect(self, request: InspectStoredBead) -> StoredBeadInspection:
        return self._call(request, "inspect", StoredBeadInspection)

    def read(self, request: ReadStoredBeadEvidence) -> StoredBeadEvidence:
        return self._call(request, "evidence", StoredBeadEvidence)

    def _call[ResultT: BaseModel](
        self, request: BaseModel, operation: str, result_type: type[ResultT]
    ) -> ResultT:
        payload = type(request).model_validate(request.model_dump(mode="json"))
        query = {
            "inspect": "SELECT memoriesql.inspect_stored_bead_v1(%s)",
            "evidence": "SELECT memoriesql.read_stored_bead_evidence_v1(%s)",
        }[operation]
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("stored inspection requires transaction ownership")
        try:
            return self._transaction(payload, query, result_type)
        except InsufficientPrivilege:
            return result_type.model_validate({"outcome": "unavailable"})
        except (QueryCanceled, LockNotAvailable):
            return result_type.model_validate({"outcome": "budget_exhausted"})

    def _transaction[ResultT: BaseModel](
        self, payload: BaseModel, query: str, result_type: type[ResultT]
    ) -> ResultT:
        with self._connection.transaction():
            self._connection.execute(
                "SELECT set_config('statement_timeout','2500',true), "
                "set_config('lock_timeout','500',true)"
            )
            self._connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self._connection).begin_context(
                credential_sha256=self._credential,
                requested_workspace_id=self._workspace,
            )
            row = self._connection.execute(
                query,
                (Jsonb(payload.model_dump(mode="json")),),
            ).fetchone()
            if row is None or row[0] is None:
                raise PermissionError("stored result unavailable")
            if len(canonical_json_bytes(row[0])) > 262144:
                raise ValueError("stored inspection response exceeds bound")
            result = result_type.model_validate(row[0])
        return result
