"""Short, currently authorized evidence-package transactions over installed SQL."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from memoriesql.application.evidence_packages import (
    COMMAND_MAX_JSON_BYTES,
    LOCK_TIMEOUT_MS,
    OPERATION_TIMEOUT_MS,
    RESPONSE_MAX_JSON_BYTES,
    AppendEvidencePart,
    CreateEvidencePackage,
    EvidencePartPage,
    InspectEvidencePackage,
    InventoryPage,
    PackageReceipt,
    PackageStatus,
    PageEvidenceInventory,
    ReadEvidencePart,
    SealEvidencePackage,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


class PostgresEvidencePackages:
    """Own each transaction; no raw handles or arbitrary locators reach readers.

    The credential is trusted caller composition, never a package/read payload.
    A server read is not authoring exposure or semantic acceptance evidence.
    """

    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def write(
        self, command: CreateEvidencePackage | AppendEvidencePart | SealEvidencePackage
    ) -> PackageReceipt:
        return self._call(command, "write", PackageReceipt)

    def inspect(self, request: InspectEvidencePackage) -> PackageStatus:
        return self._call(request, "read", PackageStatus)

    def inventory(self, request: PageEvidenceInventory) -> InventoryPage:
        return self._call(request, "read", InventoryPage)

    def read(self, request: ReadEvidencePart) -> EvidencePartPage:
        return self._call(request, "read", EvidencePartPage)

    def _call[ResultT: BaseModel](
        self, request: BaseModel, operation: str, response_type: type[ResultT]
    ) -> ResultT:
        # Revalidate even model_copy/model_construct objects at this trust boundary.
        payload = (
            type(request)
            .model_validate(request.model_dump(mode="json"))
            .model_dump(mode="json")
        )
        if len(canonical_json_bytes(payload)) > COMMAND_MAX_JSON_BYTES:
            raise ValueError("evidence command exceeds byte bound")
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError(
                "evidence package session requires transaction ownership"
            )
        query = {
            "write": "SELECT memoriesql.write_evidence_package_v1(%s)",
            "read": "SELECT memoriesql.read_evidence_package_v1(%s)",
        }[operation]
        with self._connection.transaction():
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
                raise PermissionError("evidence unavailable")
            if len(canonical_json_bytes(row[0])) > RESPONSE_MAX_JSON_BYTES:
                raise ValueError("evidence response exceeds byte bound")
            result = response_type.model_validate(row[0])
        return result
