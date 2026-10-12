"""Database-free: a busy database stops a source command as `SourceAuthorityBusy`.

A lock timeout (55P03) and a cancelled statement (57014) both say that nothing
was written and that the same request may simply be retried. The psycopg error
stays the cause, so a caller can still read its SQLSTATE.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from psycopg import errors
from psycopg.pq import TransactionStatus

from memoriesql.application.source_enrollment import (
    EnrollExactSource,
    GrantExactSource,
    RevokeExactSource,
)
from memoriesql.infrastructure.postgres.source_enrollment import (
    PostgresSourceEnrollment,
    SourceAuthorityBusy,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def commands() -> dict[str, Any]:
    return {
        "enroll": EnrollExactSource(
            request_id=uuid4(),
            source_system="fictional-orchard",
            object_kind="transcript",
            external_object_id="orchard/busy.jsonl",
            source_schema_version=1,
            exact_source_confirmed=True,
        ),
        "grant": GrantExactSource(
            request_id=uuid4(),
            source_object_id=uuid4(),
            target_principal_id=uuid4(),
            permission_keys=("read",),
            valid_from=NOW,
            expires_at=NOW + timedelta(hours=1),
        ),
        "revoke": RevokeExactSource(
            request_id=uuid4(),
            source_object_id=uuid4(),
            reason="fictional revocation",
        ),
    }


class SourceCommandsBusy(unittest.TestCase):
    def test_a_lock_or_statement_timeout_is_busy_and_keeps_its_cause(self) -> None:
        for error in (
            errors.LockNotAvailable("fictional lock timeout"),
            errors.QueryCanceled("fictional statement timeout"),
        ):
            for name, command in commands().items():
                with self.subTest(sqlstate=error.sqlstate, command=name):
                    connection = MagicMock()
                    connection.info.transaction_status = TransactionStatus.IDLE
                    connection.execute.side_effect = error
                    adapter = PostgresSourceEnrollment(
                        connection,
                        credential_sha256="0" * 64,
                        workspace_id=UUID(int=1),
                    )
                    with self.assertRaises(SourceAuthorityBusy) as busy:
                        getattr(adapter, name)(command)
                    self.assertIs(busy.exception.__cause__, error)


if __name__ == "__main__":
    unittest.main()
