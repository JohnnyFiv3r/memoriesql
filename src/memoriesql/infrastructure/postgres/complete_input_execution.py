"""Short authorized transactions for explicit activation, reader v2 and attestation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.application.complete_input_execution import (
    READER_RESPONSE_BYTES,
    ActivateCompleteInput,
    CompleteEvidenceBatch,
    CompleteInputActivationReceipt,
    EvidenceExecutionWindow,
    ReadCompleteEvidence,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


class PostgresCompleteInput:
    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self.connection = connection
        self.credential = credential_sha256
        self.workspace = workspace_id

    def activate(
        self, request: ActivateCompleteInput
    ) -> CompleteInputActivationReceipt:
        request = ActivateCompleteInput.model_validate(request.model_dump(mode="json"))
        return CompleteInputActivationReceipt.model_validate(
            self._call(
                "SELECT memoriesql.activate_complete_input_v1(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def read(self, request: ReadCompleteEvidence) -> CompleteEvidenceBatch:
        request = ReadCompleteEvidence.model_validate(request.model_dump(mode="json"))
        return CompleteEvidenceBatch.model_validate(
            self._call(
                "SELECT memoriesql.read_complete_evidence_v2(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def _call(self, query: str, parameters: tuple[Any, ...], *, role: str) -> Any:
        if self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("complete input session requires transaction ownership")
        with self.connection.transaction():
            self.connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self.connection.execute("SET LOCAL statement_timeout='2s'")
            self.connection.execute("SET LOCAL lock_timeout='500ms'")
            if role == "memoriesql_worker":
                self.connection.execute("SET LOCAL ROLE memoriesql_worker")
            else:
                self.connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self.connection).begin_context(
                credential_sha256=self.credential, requested_workspace_id=self.workspace
            )
            row = self.connection.execute(query, parameters).fetchone()
            if row is None or row[0] is None:
                raise PermissionError("complete input unavailable")
            if len(canonical_json_bytes(row[0])) > READER_RESPONSE_BYTES:
                raise ValueError("complete input response bound")
            return row[0]


class PostgresEvidenceExposureRecorder:
    """Use a separately trusted attestor credential, never the input or model.

    Provisioning and review of this identity is deliberately external. Reads and
    ordinary worker credentials cannot certify dispatch. Started writes are owned
    through cancellation; the caller's cleanup cannot abandon a database thread.
    """

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Connection[Any]],
        credential_sha256: str,
        workspace_id: UUID,
    ) -> None:
        self.connection_factory = connection_factory
        self.credential = credential_sha256
        self.workspace = workspace_id

    async def record_received(
        self,
        *,
        request_id: UUID,
        request_payload_hash: str,
        window: EvidenceExecutionWindow,
    ) -> None:
        payload = EvidenceExecutionWindow.model_validate(
            window.model_dump(mode="json")
        ).model_dump(mode="json")

        def write() -> None:
            with self.connection_factory() as connection:
                PostgresCompleteInput(
                    connection,
                    credential_sha256=self.credential,
                    workspace_id=self.workspace,
                )._call(
                    "SELECT memoriesql.record_complete_input_exposure_v1(%s,%s,%s)",
                    (request_id, request_payload_hash, Jsonb(payload)),
                    role="memoriesql_worker",
                )

        operation = asyncio.create_task(asyncio.to_thread(write))
        cancelled: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(operation)
                break
            except asyncio.CancelledError as error:
                cancelled = error
                continue
        if cancelled is not None:
            raise cancelled
