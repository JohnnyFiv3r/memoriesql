"""Short authorized materialization transactions; no authoring execution."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from memoriesql.application.evidence_packages import (
    LOCK_TIMEOUT_MS,
    OPERATION_TIMEOUT_MS,
)
from memoriesql.application.logical_unit_materialization import (
    MATERIALIZATION_COMMAND_MAX_BYTES,
    InspectLogicalEvent,
    LogicalEventProgress,
    LogicalUnitMaterializationReceipt,
    MaterializeLogicalUnit,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


class PostgresLogicalUnitMaterialization:
    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def materialize(
        self, command: MaterializeLogicalUnit
    ) -> LogicalUnitMaterializationReceipt:
        return self._call(
            command,
            "SELECT memoriesql.materialize_logical_unit_v1(%s)",
            LogicalUnitMaterializationReceipt,
        )

    def inspect_event(self, request: InspectLogicalEvent) -> LogicalEventProgress:
        return self._call(
            request,
            "SELECT memoriesql.inspect_logical_event_v1(%s)",
            LogicalEventProgress,
        )

    def _call[ResultT: BaseModel](
        self, request: BaseModel, query: str, response_type: type[ResultT]
    ) -> ResultT:
        payload = (
            type(request)
            .model_validate(request.model_dump(mode="json"))
            .model_dump(mode="json")
        )
        if len(canonical_json_bytes(payload)) > MATERIALIZATION_COMMAND_MAX_BYTES:
            raise ValueError("materialization command exceeds byte bound")
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("materialization requires transaction ownership")
        with self._connection.transaction():
            # Authorization fences must observe revocations committed during waits.
            # Do not inherit a caller-configured repeatable-read snapshot.
            self._connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self._connection.execute(
                "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
                (str(OPERATION_TIMEOUT_MS), str(LOCK_TIMEOUT_MS)),
            )
            self._connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self._connection).begin_context(
                credential_sha256=self._credential,
                requested_workspace_id=self._workspace,
            )
            row = self._connection.execute(query, (Jsonb(payload),)).fetchone()
            if row is None or row[0] is None:
                raise PermissionError("materialization unavailable")
            if len(canonical_json_bytes(row[0])) > MATERIALIZATION_COMMAND_MAX_BYTES:
                raise ValueError("materialization response exceeds byte bound")
            result = response_type.model_validate(row[0])
        return result
