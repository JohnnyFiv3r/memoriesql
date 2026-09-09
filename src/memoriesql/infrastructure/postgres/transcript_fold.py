from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.application.capture.transcript_fold import (
    CommitTranscriptFoldCommand,
    CommitTranscriptFoldReceipt,
    DurableTranscriptRead,
    DurableTranscriptReadRequest,
    TranscriptFoldInboxItem,
)
from memoriesql.infrastructure.postgres.authorization import (
    PostgresAuthorizationPort,
)


class PostgresTranscriptFold:
    """Schema-14 fold port over immutable schema-13 source ranges."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def read(self, request: DurableTranscriptReadRequest) -> DurableTranscriptRead:
        row = self._connection.execute(
            "SELECT memoriesql.read_transcript_fold_window(%s, %s, %s, %s, %s, %s, %s)",
            (
                request.source_object_id,
                request.source_revision_key,
                request.file_identity_key,
                request.byte_start,
                request.max_bytes,
                uuid4(),
                request.confirmation,
            ),
        ).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("Postgres did not return a transcript fold window")
        return DurableTranscriptRead.model_validate(row[0])

    def commit(
        self,
        command: CommitTranscriptFoldCommand,
        *,
        recorded_at: datetime,
    ) -> CommitTranscriptFoldReceipt:
        row = self._connection.execute(
            "SELECT memoriesql.commit_transcript_fold(%s, %s)",
            (
                Jsonb(command.model_dump(mode="json", warnings="error")),
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return a transcript fold receipt")
        return CommitTranscriptFoldReceipt.model_validate(row[0])

    def list_inbox(
        self,
        *,
        source_object_id: UUID,
    ) -> tuple[TranscriptFoldInboxItem, ...]:
        rows = self._connection.execute(
            "SELECT value FROM memoriesql.list_transcript_fold_inbox(%s) AS value",
            (source_object_id,),
        ).fetchall()
        return tuple(TranscriptFoldInboxItem.model_validate(row[0]) for row in rows)


class PostgresAuthorizedTranscriptFoldSession:
    """Own each authorized read/fold transaction and return only after commit."""

    def __init__(
        self,
        connection: Connection[Any],
        *,
        credential_sha256: str,
        workspace_id: UUID,
    ) -> None:
        if not credential_sha256:
            raise ValueError("transcript fold session requires a credential hash")
        self._connection = connection
        self._credential_sha256 = credential_sha256
        self._workspace_id = workspace_id

    def read(self, request: DurableTranscriptReadRequest) -> DurableTranscriptRead:
        self._require_idle()
        with self._connection.transaction():
            self._begin_context()
            result = PostgresTranscriptFold(self._connection).read(request)
        self._require_idle()
        return result

    def commit(
        self,
        command: CommitTranscriptFoldCommand,
        *,
        recorded_at: datetime,
    ) -> CommitTranscriptFoldReceipt:
        self._require_idle()
        with self._connection.transaction():
            self._begin_context()
            receipt = PostgresTranscriptFold(self._connection).commit(
                command,
                recorded_at=recorded_at,
            )
        self._require_idle()
        return receipt

    def list_inbox(
        self,
        *,
        source_object_id: UUID,
    ) -> tuple[TranscriptFoldInboxItem, ...]:
        self._require_idle()
        with self._connection.transaction():
            self._begin_context()
            items = PostgresTranscriptFold(self._connection).list_inbox(
                source_object_id=source_object_id
            )
        self._require_idle()
        return items

    def _begin_context(self) -> None:
        self._connection.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(self._connection).begin_context(
            credential_sha256=self._credential_sha256,
            requested_workspace_id=self._workspace_id,
        )

    def _require_idle(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("transcript fold session requires transaction ownership")
