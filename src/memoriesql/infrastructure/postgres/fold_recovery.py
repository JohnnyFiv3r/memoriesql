"""Current-authority recovery transactions over the canonical retained evidence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from memoriesql.application.fold_recovery import (
    REQUEST_BYTES,
    RESPONSE_BYTES,
    DiscoverFoldOutcomes,
    FoldDiscoveryPage,
    FoldEvidencePage,
    FoldLineagePage,
    FoldOutcomeDetail,
    InspectFoldOutcome,
    PageFoldLineage,
    ReadFoldEvidence,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


class PostgresFoldRecovery:
    """Explicit caller credentials; no grants, filesystem reads or semantic writes."""

    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def discover(self, request: DiscoverFoldOutcomes) -> FoldDiscoveryPage:
        return self._call(request, FoldDiscoveryPage)

    def inspect(self, request: InspectFoldOutcome) -> FoldOutcomeDetail:
        return self._call(request, FoldOutcomeDetail)

    def lineage(self, request: PageFoldLineage) -> FoldLineagePage:
        return self._call(request, FoldLineagePage)

    def read(self, request: ReadFoldEvidence) -> FoldEvidencePage:
        return self._call(request, FoldEvidencePage)

    def _call[T: BaseModel](self, request: BaseModel, result_type: type[T]) -> T:
        payload = (
            type(request)
            .model_validate(request.model_dump(mode="json"))
            .model_dump(mode="json")
        )
        if len(canonical_json_bytes(payload)) > REQUEST_BYTES:
            raise ValueError("fold recovery request exceeds bound")
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("fold recovery requires transaction ownership")
        with self._connection.transaction():
            self._connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self._connection.execute("SET LOCAL statement_timeout = '2s'")
            self._connection.execute("SET LOCAL lock_timeout = '500ms'")
            self._connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self._connection).begin_context(
                credential_sha256=self._credential,
                requested_workspace_id=self._workspace,
            )
            row = self._connection.execute(
                "SELECT memoriesql.recover_transcript_fold_v1(%s)", (Jsonb(payload),)
            ).fetchone()
            if row is None or row[0] is None:
                raise PermissionError("fold recovery unavailable")
            if len(canonical_json_bytes(row[0])) > RESPONSE_BYTES:
                raise ValueError("fold recovery response exceeds bound")
            result = result_type.model_validate(row[0])
        return result
