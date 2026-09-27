"""One owned snapshot read, with current authority fenced before snapshot creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


@contextmanager
def relation_projection_frame(
    connection: Connection[Any],
    *,
    credential_sha256: str,
    workspace_id: UUID,
) -> Iterator[Connection[Any]]:
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("relation projection requires transaction ownership")
    key: int | None = None
    try:
        with connection.transaction():
            connection.execute("SET LOCAL lock_timeout='500ms'")
            connection.execute("SET LOCAL statement_timeout='2500ms'")
            connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=credential_sha256,
                requested_workspace_id=workspace_id,
            )
            supported = connection.execute(
                "SELECT to_regprocedure('memoriesql.acquire_relation_read_fence_v1()') IS NOT NULL"
            ).fetchone()
            if supported and supported[0]:
                row = connection.execute(
                    "SELECT memoriesql.acquire_relation_read_fence_v1()"
                ).fetchone()
                if row:
                    key = row[0]
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute("SET LOCAL lock_timeout='500ms'")
            connection.execute("SET LOCAL statement_timeout='2500ms'")
            connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=credential_sha256,
                requested_workspace_id=workspace_id,
            )
            yield connection
    finally:
        if key is not None and not connection.closed:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_unlock_shared(%s)", (key,))


def read_relation_projection(
    connection: Connection[Any],
    *,
    credential_sha256: str,
    workspace_id: UUID,
    query: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with relation_projection_frame(
        connection, credential_sha256=credential_sha256, workspace_id=workspace_id
    ) as frame:
        row = frame.execute(query, (Jsonb(payload),)).fetchone()
        if row is None or row[0] is None:
            raise PermissionError("relation projection unavailable")
        result: dict[str, Any] = row[0]
        return result
