"""Bounded retry of owned transactions against disposable PostgreSQL.

A lock holder in another session stands in for an in-flight query frame or
authority change. The retry's backoff pause is replaced by a hook that can
release the holder, so each case waits for a lock state, never for a time.
"""

from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg
from psycopg import errors, sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.local_client_pairing import (
    PairLocalClient,
    RevokeLocalClient,
)
from memoriesql.application.source_enrollment import EnrollExactSource
from memoriesql.infrastructure.postgres import transient_retry
from memoriesql.infrastructure.postgres.local_client_pairing import (
    PairingRevisionConflict,
    PostgresLocalClientPairing,
)
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.source_enrollment import (
    PostgresSourceEnrollment,
    SourceAuthorityBusy,
)

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate

OWNER_CREDENTIAL = "fictional orchard owner session credential"
FENCE = "hashtextextended(%s::text || ':semantic_outcome_authority:', 0)"
QUERY_ACCESS = "hashtextextended(%s::text || ':query-access:' || %s::text, 0)"


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class TransientRetry(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "transient_retry_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.url = make_conninfo(self.admin_url, dbname=self.database)
        self.db = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.cleanup_database)
        migrate(
            self.db,
            expected_current_version=0,
            target_version=discover_migrations()[-1].version,
        )
        (
            self.tenant,
            user,
            identity,
            principal,
            self.workspace,
            self.scope,
            membership,
            credential,
            session,
        ) = (uuid.uuid4() for _ in range(9))
        self.owner = hashlib.sha256(OWNER_CREDENTIAL.encode()).hexdigest()
        self.now = self.scalar("SELECT clock_timestamp()")
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.bootstrap_personal_local(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "'memoriesql.local',%s,'Fictional orchard',%s,%s,%s)",
                (
                    self.tenant,
                    user,
                    identity,
                    principal,
                    self.workspace,
                    self.scope,
                    membership,
                    credential,
                    session,
                    str(identity),
                    self.owner,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        self.holder = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.holder.close)
        self.pauses: list[float] = []

    def cleanup_database(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(statement, values).fetchone()
        assert row is not None
        return row[0]

    def hold(self, lock: str, key: str, *values: Any) -> None:
        """The holder takes a session-level advisory lock and keeps it."""
        self.holder.execute(f"SELECT {lock}({key})", values)

    def pausing(self, on_pause: Callable[[], None] | None = None) -> Any:
        """Record each backoff pause, running `on_pause` at the first one."""

        def pause(seconds: float) -> None:
            self.pauses.append(seconds)
            if on_pause is not None and len(self.pauses) == 1:
                on_pause()

        return patch.object(transient_retry, "pause", pause)

    def advisory_locks(self, pid: int) -> list[tuple[str, bool]]:
        return [
            (str(row[0]), bool(row[1]))
            for row in self.db.execute(
                "SELECT mode, granted FROM pg_locks"
                " WHERE pid=%s AND locktype='advisory' ORDER BY mode",
                (pid,),
            ).fetchall()
        ]

    def enrollment(self) -> tuple[PostgresSourceEnrollment, EnrollExactSource]:
        connection = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(connection.close)
        return PostgresSourceEnrollment(
            connection, credential_sha256=self.owner, workspace_id=self.workspace
        ), EnrollExactSource(
            request_id=uuid.uuid4(),
            source_system="fictional-orchard",
            object_kind="transcript",
            external_object_id="orchard/retry-" + uuid.uuid4().hex + ".jsonl",
            source_schema_version=1,
            exact_source_confirmed=True,
        )

    def sources(self) -> int:
        return int(self.scalar("SELECT count(*) FROM memoriesql.source_objects"))

    def test_an_owner_command_outlasts_a_brief_query_frame(self) -> None:
        # A query frame holds the tenant fence shared; enrollment needs it
        # exclusively, times out at 500 ms, and runs again once it is free.
        enrollment, request = self.enrollment()
        self.hold("pg_advisory_lock_shared", FENCE, self.tenant)

        def release() -> None:
            self.hold("pg_advisory_unlock_shared", FENCE, self.tenant)

        with self.pausing(release):
            receipt = enrollment.enroll(request)
        self.assertFalse(receipt.replayed)
        self.assertEqual(len(self.pauses), 1)
        self.assertEqual(self.sources(), 1)

    def test_a_holder_that_stays_exhausts_the_bounded_attempts(self) -> None:
        enrollment, request = self.enrollment()
        self.hold("pg_advisory_lock_shared", FENCE, self.tenant)
        with self.pausing(), self.assertRaises(SourceAuthorityBusy) as busy:
            enrollment.enroll(request)
        # Mapped exactly as before, after four attempts and nothing written.
        self.assertIsInstance(busy.exception.__cause__, errors.LockNotAvailable)
        self.assertEqual(len(self.pauses), transient_retry.ATTEMPTS - 1)
        self.assertEqual(self.sources(), 0)

    def test_a_stale_revision_is_a_conflict_at_once(self) -> None:
        connection = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(connection.close)
        pairing = PostgresLocalClientPairing(
            connection, credential_sha256=self.owner, workspace_id=self.workspace
        )
        paired = pairing.pair(
            PairLocalClient(
                request_id=uuid.uuid4(),
                capabilities=("memory.query",),
                access_scope_ids=(self.scope,),
                expires_at=self.now + timedelta(hours=1),
                exact_pairing_confirmed=True,
            ),
            client_secret_sha256=hashlib.sha256(b"fictional orchard agent").hexdigest(),
        )
        with self.pausing(), self.assertRaises(PairingRevisionConflict):
            pairing.revoke(
                RevokeLocalClient(
                    pairing_grant_id=paired.pairing_grant_id,
                    expected_revision=2,
                    capabilities=paired.capabilities,
                    access_scope_ids=paired.access_scope_ids,
                    exact_revocation_confirmed=True,
                )
            )
        # A kernel-raised 40001 is a real conflict: one attempt, no pause.
        self.assertEqual(self.pauses, [])

    def test_the_read_frame_releases_its_session_locks_between_attempts(
        self,
    ) -> None:
        # The frame takes the tenant fence shared, then the workspace's
        # query-access lock, both at session level. A held query-access lock
        # times out the second; the fence the attempt already took must be
        # released before the frame tries again.
        connection = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(connection.close)
        reader = connection.info.backend_pid
        self.hold("pg_advisory_lock", QUERY_ACCESS, self.tenant, self.workspace)
        between: list[list[tuple[str, bool]]] = []

        def release() -> None:
            between.append(self.advisory_locks(reader))
            self.hold("pg_advisory_unlock", QUERY_ACCESS, self.tenant, self.workspace)

        with (
            self.pausing(release),
            relation_projection_frame(
                connection,
                credential_sha256=self.owner,
                workspace_id=self.workspace,
                query_access=True,
                retry=True,
            ) as frame,
        ):
            held = self.advisory_locks(reader)
            self.assertEqual(frame.execute("SELECT 1").fetchone(), (1,))
        self.assertEqual(between, [[]])
        self.assertEqual(held, [("ExclusiveLock", True), ("ShareLock", True)])
        self.assertEqual(self.advisory_locks(reader), [])
        self.assertEqual(len(self.pauses), 1)

    def test_a_frame_without_retry_still_fails_at_once(self) -> None:
        connection = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(connection.close)
        self.hold("pg_advisory_lock", QUERY_ACCESS, self.tenant, self.workspace)
        with self.pausing(), self.assertRaises(errors.LockNotAvailable):
            with relation_projection_frame(
                connection,
                credential_sha256=self.owner,
                workspace_id=self.workspace,
                query_access=True,
            ):
                self.fail("the frame opened behind a held query-access lock")
        self.assertEqual(self.pauses, [])
        self.assertEqual(self.advisory_locks(connection.info.backend_pid), [])


if __name__ == "__main__":
    unittest.main()
