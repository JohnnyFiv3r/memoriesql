from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from memoriesql.application.canonical_transactions import (
    AcceptSourceEventCommand,
    AcceptSourceEventReceipt,
    ApplySemanticAnnotationsCommand,
    ApplySemanticAnnotationsReceipt,
)


class PostgresCanonicalTransactions:
    """Typed adapter over the two PR-02 PostgreSQL transaction commands."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def accept_source_event(
        self,
        command: AcceptSourceEventCommand,
        *,
        recorded_at: datetime,
    ) -> AcceptSourceEventReceipt:
        row = self._connection.execute(
            """
            SELECT *
            FROM memoriesql.accept_source_event(%s, %s)
            """,
            (
                Jsonb(command.model_dump(mode="json", warnings="error")),
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return a source acceptance receipt")
        return AcceptSourceEventReceipt(
            event_id=UUID(str(row[0])),
            source_unit_ids=tuple(UUID(str(value)) for value in row[1]),
            bead_ids=tuple(UUID(str(value)) for value in row[2]),
            semantic_task_id=UUID(str(row[3])),
            idempotency_receipt_id=UUID(str(row[4])),
            checkpoint_sequence=int(row[5]) if row[5] is not None else None,
            status=cast(Literal["accepted", "already_exists"], str(row[6])),
            replayed=bool(row[7]),
        )

    def apply_semantic_annotations(
        self,
        command: ApplySemanticAnnotationsCommand,
        *,
        worker_id: str,
        worker_instance_id: str,
        recorded_at: datetime,
    ) -> ApplySemanticAnnotationsReceipt:
        row = self._connection.execute(
            """
            SELECT *
            FROM memoriesql.apply_semantic_annotations(%s, %s, %s, %s)
            """,
            (
                Jsonb(command.model_dump(mode="json", warnings="error")),
                worker_id,
                worker_instance_id,
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return a semantic apply receipt")
        return ApplySemanticAnnotationsReceipt(
            task_id=UUID(str(row[0])),
            attempt_id=UUID(str(row[1])),
            idempotency_receipt_id=UUID(str(row[2])),
            statement_ids=tuple(UUID(str(value)) for value in row[3]),
            bead_version_ids=tuple(UUID(str(value)) for value in row[4]),
            task_status=cast(Literal["succeeded"], str(row[5])),
            replayed=bool(row[6]),
        )
