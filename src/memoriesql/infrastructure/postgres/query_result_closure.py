"""Private current-authority verdict for one immutable standalone result.

This returns no saved bytes, facts, metadata or delivery receipt. It is a
prerequisite for a future metered disclosure operation, not that operation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus

from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)


class PostgresQueryResultClosure:
    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def check_standalone(self, result_id: UUID, content_digest: str) -> None:
        """Refuse loss of original owner, context, bytes or any protected pin."""
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("result closure requires transaction ownership")
        if (
            type(result_id) is not UUID
            or type(content_digest) is not str
            or len(content_digest) != 64
            or any(c not in "0123456789abcdef" for c in content_digest)
        ):
            raise ValueError("invalid result pin")
        with relation_projection_frame(
            self._connection,
            credential_sha256=self._credential,
            workspace_id=self._workspace,
        ) as frame:
            row = frame.execute(
                "SELECT memoriesql.check_query_result_closure_v1(%s,%s)",
                (result_id, content_digest),
            ).fetchone()
            if row != (True,):
                raise PermissionError("query result unavailable")
