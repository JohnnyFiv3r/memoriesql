from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.application.capture.source_range import (
    CaptureSourceRangeCommand,
    CaptureSourceRangeReceipt,
    SourceRangeInspection,
    SourceRangeRawDetail,
)
from memoriesql.infrastructure.postgres.authorization import (
    PostgresAuthorizationPort,
)


class PostgresSourceRangeCapture:
    """The schema-13 raw-evidence port; no parsing or ledger fold occurs here."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def capture(
        self,
        command: CaptureSourceRangeCommand,
        *,
        recorded_at: datetime,
    ) -> CaptureSourceRangeReceipt:
        row = self._connection.execute(
            "SELECT memoriesql.capture_source_range(%s, %s)",
            (
                Jsonb(command.model_dump(mode="json", warnings="error")),
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return a source-range receipt")
        return CaptureSourceRangeReceipt.model_validate(row[0])

    def inspect(
        self,
        *,
        source_range_receipt_id: UUID,
        diagnostic_context: dict[str, object] | None = None,
        request_id: UUID | None = None,
    ) -> SourceRangeInspection | None:
        row = self._connection.execute(
            "SELECT memoriesql.inspect_captured_source_range(%s, %s, %s)",
            (
                source_range_receipt_id,
                request_id or uuid4(),
                Jsonb(diagnostic_context or {}),
            ),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return SourceRangeInspection.model_validate(row[0])

    def read_confirmed_local_bytes(
        self,
        *,
        source_range_receipt_id: UUID,
        request_id: UUID | None = None,
        confirmation: str,
    ) -> tuple[SourceRangeRawDetail, ...]:
        rows = self._connection.execute(
            "SELECT value FROM memoriesql.read_captured_source_range_bytes"
            "(%s, %s, %s) AS value",
            (
                source_range_receipt_id,
                request_id or uuid4(),
                confirmation,
            ),
        ).fetchall()
        return tuple(SourceRangeRawDetail.model_validate(row[0]) for row in rows)


class PostgresAuthorizedSourceRangeSession:
    """Commit each authorized raw-range operation before returning its receipt."""

    def __init__(
        self,
        connection: Connection[Any],
        *,
        credential_sha256: str,
        workspace_id: UUID,
    ) -> None:
        if not credential_sha256:
            raise ValueError("source-range session requires a credential hash")
        self._connection = connection
        self._credential_sha256 = credential_sha256
        self._workspace_id = workspace_id

    def capture(
        self,
        command: CaptureSourceRangeCommand,
        *,
        recorded_at: datetime,
    ) -> CaptureSourceRangeReceipt:
        self._require_idle()
        with self._connection.transaction():
            self._begin_context()
            receipt = PostgresSourceRangeCapture(self._connection).capture(
                command,
                recorded_at=recorded_at,
            )
        self._require_idle()
        return receipt

    def inspect(
        self,
        *,
        source_range_receipt_id: UUID,
        diagnostic_context: dict[str, object] | None = None,
        request_id: UUID | None = None,
    ) -> SourceRangeInspection | None:
        self._require_idle()
        with self._connection.transaction():
            self._begin_context()
            inspection = PostgresSourceRangeCapture(self._connection).inspect(
                source_range_receipt_id=source_range_receipt_id,
                diagnostic_context=diagnostic_context,
                request_id=request_id,
            )
        self._require_idle()
        return inspection

    def read_confirmed_local_bytes(
        self,
        *,
        source_range_receipt_id: UUID,
        request_id: UUID | None = None,
        confirmation: str,
    ) -> tuple[SourceRangeRawDetail, ...]:
        self._require_idle()
        with self._connection.transaction():
            self._begin_context()
            details = PostgresSourceRangeCapture(
                self._connection
            ).read_confirmed_local_bytes(
                source_range_receipt_id=source_range_receipt_id,
                request_id=request_id,
                confirmation=confirmation,
            )
        self._require_idle()
        return details

    def _begin_context(self) -> None:
        self._connection.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(self._connection).begin_context(
            credential_sha256=self._credential_sha256,
            requested_workspace_id=self._workspace_id,
        )

    def _require_idle(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("source-range session requires transaction ownership")
