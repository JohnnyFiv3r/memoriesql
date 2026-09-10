from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from memoriesql.application.canonical_transactions import (
    AcceptSourceEventCommand,
    AcceptSourceEventReceipt,
    ApplySemanticAnnotationsCommand,
    ApplySemanticAnnotationsReceipt,
)
from memoriesql.application.observation_commands import (
    AcceptSourceEventV2Command,
    AuthorInitialObservationsCommand,
    CorrectObservationCommand,
    ObservationReceiptV2,
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
        return self._accept_source_event(command, recorded_at=recorded_at)

    def accept_source_event_v2(
        self,
        command: AcceptSourceEventV2Command,
        *,
        recorded_at: datetime,
    ) -> AcceptSourceEventReceipt:
        return self._accept_source_event(command, recorded_at=recorded_at)

    def _accept_source_event(
        self,
        command: AcceptSourceEventCommand | AcceptSourceEventV2Command,
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

    def author_initial_observations(
        self,
        command: AuthorInitialObservationsCommand,
        *,
        worker_id: str,
        worker_instance_id: str,
        recorded_at: datetime,
    ) -> ObservationReceiptV2:
        return self._apply_observations_v2(
            "author_initial_observations_v2",
            command,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
            recorded_at=recorded_at,
        )

    def correct_observation(
        self,
        command: CorrectObservationCommand,
        *,
        worker_id: str,
        worker_instance_id: str,
        recorded_at: datetime,
    ) -> ObservationReceiptV2:
        return self._apply_observations_v2(
            "correct_observation_v2",
            command,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
            recorded_at=recorded_at,
        )

    def _apply_observations_v2(
        self,
        function: str,
        command: AuthorInitialObservationsCommand | CorrectObservationCommand,
        *,
        worker_id: str,
        worker_instance_id: str,
        recorded_at: datetime,
    ) -> ObservationReceiptV2:
        row = self._connection.execute(
            sql.SQL("SELECT memoriesql.{}(%s, %s, %s, %s)").format(
                sql.Identifier(function)
            ),
            (
                Jsonb(command.model_dump(mode="json", warnings="error")),
                worker_id,
                worker_instance_id,
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return an observation receipt")
        return ObservationReceiptV2.model_validate(row[0])
