from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from memoriesql.application.capture.contracts import (
    CaptureDeliveryDetail,
    CaptureEnvelope,
    CaptureInboxItem,
    CaptureStatusEvent,
    CaptureSubmissionCommand,
    CaptureSubmissionReceipt,
    ConnectorCursor,
    KernelCaptureBinding,
    ParserCoverageStatus,
    compile_capture_submission,
)


class PostgresCaptureTransactions:
    """PostgreSQL adapter for the one PR-02A envelope transaction and reads."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def submit(
        self,
        command: CaptureSubmissionCommand,
        *,
        recorded_at: datetime,
    ) -> CaptureSubmissionReceipt:
        row = self._connection.execute(
            "SELECT memoriesql.submit_capture_envelope(%s, %s)",
            (
                Jsonb(command.model_dump(mode="json", warnings="error")),
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return a capture receipt")
        return CaptureSubmissionReceipt.model_validate(row[0])

    def list_inbox(self, *, source_object_id: UUID) -> tuple[CaptureInboxItem, ...]:
        rows = self._connection.execute(
            "SELECT value FROM memoriesql.read_capture_inbox(%s) AS value",
            (source_object_id,),
        ).fetchall()
        return tuple(CaptureInboxItem.model_validate(row[0]) for row in rows)

    def read_status_history(
        self, *, delivery_id: UUID
    ) -> tuple[CaptureStatusEvent, ...]:
        rows = self._connection.execute(
            "SELECT value FROM memoriesql.read_capture_status_history(%s) AS value",
            (delivery_id,),
        ).fetchall()
        return tuple(CaptureStatusEvent.model_validate(row[0]) for row in rows)

    def read_detail(self, *, delivery_id: UUID) -> CaptureDeliveryDetail | None:
        row = self._connection.execute(
            "SELECT memoriesql.read_capture_delivery_detail(%s)",
            (delivery_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return CaptureDeliveryDetail.model_validate(row[0])


class PostgresCaptureSession:
    """Kernel-bound port: adapters cannot provide workspace or scope authority."""

    def __init__(
        self,
        connection: Connection[Any],
        *,
        binding: KernelCaptureBinding,
    ) -> None:
        self._connection = connection
        self._binding = binding
        self._transactions = PostgresCaptureTransactions(connection)

    def submit(
        self,
        envelope: CaptureEnvelope,
        *,
        idempotency_key: str,
        cursor: ConnectorCursor | None,
        recorded_at: datetime,
    ) -> CaptureSubmissionReceipt:
        semantic_task_id: UUID | None = None
        if (
            envelope.parser_receipt.status != ParserCoverageStatus.ERROR
            and envelope.event_kind != "source_deleted"
        ):
            semantic_task_id = UUID(int=envelope.event_id.int ^ 1)
        command = compile_capture_submission(
            envelope,
            binding=self._binding,
            idempotency_key=idempotency_key,
            semantic_task_id=semantic_task_id,
            cursor=cursor,
        )
        return self._transactions.submit(command, recorded_at=recorded_at)

    def list_inbox(self) -> tuple[CaptureInboxItem, ...]:
        return self._transactions.list_inbox(
            source_object_id=self._binding.source_object_id
        )

    def read_status_history(
        self, *, delivery_id: UUID
    ) -> tuple[CaptureStatusEvent, ...]:
        return self._transactions.read_status_history(delivery_id=delivery_id)

    def read_detail(self, *, delivery_id: UUID) -> CaptureDeliveryDetail | None:
        return self._transactions.read_detail(delivery_id=delivery_id)
