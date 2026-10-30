"""Short, authenticated transactions for Core Memory engine record intake."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import (
    DeadlockDetected,
    InsufficientPrivilege,
    LockNotAvailable,
    QueryCanceled,
)
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.application.engine_intake import (
    AcceptEngineRecords,
    EngineRecordPage,
    EngineRecordResults,
    EngineVocabulary,
    ReadEngineRecords,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.transient_retry import run_owned


class EngineIntakeBusy(RuntimeError):
    """A lock wait, deadlock or cancelled statement stopped the batch.

    Nothing was written. Records are keyed, so resending the same batch is
    safe. The psycopg error is always the `__cause__`.
    """


class PostgresEngineIntake:
    """Database authority is obtained from a credential, never caller IDs."""

    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential_sha256 = credential_sha256
        self._workspace_id = workspace_id

    def _begin(self) -> None:
        self._connection.execute("SET LOCAL statement_timeout='10000ms'")
        self._connection.execute("SET LOCAL lock_timeout='500ms'")
        self._connection.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(self._connection).begin_context(
            credential_sha256=self._credential_sha256,
            requested_workspace_id=self._workspace_id,
        )

    def _call(self, query: str, params: tuple[Any, ...], unavailable: str) -> Any:
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("engine record intake requires transaction ownership")
        try:

            def attempt() -> Any:
                with self._connection.transaction():
                    self._begin()
                    row = self._connection.execute(query, params).fetchone()
                    if row is None:
                        raise RuntimeError(f"{unavailable}: no result")
                    return row[0]

            return run_owned(attempt)
        except InsufficientPrivilege as error:
            raise PermissionError(unavailable) from error
        except (QueryCanceled, LockNotAvailable, DeadlockDetected) as error:
            raise EngineIntakeBusy(f"{unavailable}: busy") from error

    def accept(self, request: AcceptEngineRecords) -> EngineRecordResults:
        command = AcceptEngineRecords.model_validate(request.model_dump(mode="json"))
        results = EngineRecordResults.model_validate(
            self._call(
                "SELECT memoriesql.accept_engine_records_v1(%s)",
                (Jsonb(command.model_dump(mode="json")),),
                "engine record intake is unavailable",
            )
        )
        if len(results.results) != len(command.records):
            raise RuntimeError(
                "engine record intake answered the wrong number of records"
            )
        return results

    def read(self, request: ReadEngineRecords) -> EngineRecordPage:
        command = ReadEngineRecords.model_validate(request.model_dump(mode="json"))
        return EngineRecordPage.model_validate(
            self._call(
                "SELECT memoriesql.read_engine_records_v1(%s)",
                (Jsonb(command.model_dump(mode="json")),),
                "engine records are unavailable",
            )
        )

    def vocabulary(self) -> EngineVocabulary:
        return EngineVocabulary.model_validate(
            self._call(
                "SELECT memoriesql.engine_vocabulary_v1()",
                (),
                "the engine vocabulary is unavailable",
            )
        )
