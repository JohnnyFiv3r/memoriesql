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
    statement_timeout_ms: int = 2500,
    query_access: bool = False,
) -> Iterator[Connection[Any]]:
    """`statement_timeout_ms` may carry an admitted operation's remaining budget
    (at most its 30 s reservation); the default keeps the canonical 2.5 s.

    `query_access` takes the workspace's query-access lock before the snapshot,
    for calls that check a workspace cap (starting a run, admission, close,
    recovery). Their snapshot then sees everything the previous lock holder
    committed. A snapshot taken before the lock could not."""
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("relation projection requires transaction ownership")
    if type(statement_timeout_ms) is not int or not 1 <= statement_timeout_ms <= 30000:
        raise ValueError("invalid statement timeout")
    timeout = f"SET LOCAL statement_timeout='{statement_timeout_ms}ms'"
    key: int | None = None
    access: int | None = None
    try:
        with connection.transaction():
            connection.execute("SET LOCAL lock_timeout='500ms'")
            connection.execute(timeout)
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
            if query_access:
                held = connection.execute(
                    "SELECT memoriesql.acquire_query_access_lock_v1()"
                ).fetchone()
                if held:
                    access = held[0]
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute("SET LOCAL lock_timeout='500ms'")
            connection.execute(timeout)
            connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=credential_sha256,
                requested_workspace_id=workspace_id,
            )
            yield connection
    finally:
        if access is not None and not connection.closed:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_unlock(%s)", (access,))
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
