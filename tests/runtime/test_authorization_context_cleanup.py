"""Authorization context cleanup against disposable PostgreSQL (migration 0040).

Each race is first reproduced on schema 39, then shown gone on schema 40, in
the same fictional database. Nothing here measures workload or timing.
"""

from __future__ import annotations

import hashlib
import os
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import errors, sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.source_enrollment import (
    EnrollExactSource,
    GrantExactSource,
    RevokeExactSource,
)
from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
    _authentication_context_refused,
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
START = "SELECT principal_id FROM memoriesql.begin_authorization_context(%s,%s)"
CURRENT = "SELECT count(*) FROM memoriesql.current_authorization_context()"
KERNEL = "'memoriesql.begin_authorization_context(text,uuid)'::regprocedure"
COLLECTOR = "'memoriesql.collect_authorization_contexts_v1()'::regprocedure"
RC, RR = "READ COMMITTED", "REPEATABLE READ"


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AuthorizationContextCleanup(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "pr05_context_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.url = make_conninfo(self.admin_url, dbname=self.database)
        self.db = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.cleanup_database)
        migrate(self.db, expected_current_version=0, target_version=39)
        (
            self.tenant,
            user,
            identity,
            principal,
            self.workspace,
            scope,
            membership,
            credential,
            session,
        ) = (uuid.uuid4() for _ in range(9))
        self.secret = hashlib.sha256(OWNER_CREDENTIAL.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
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
                    scope,
                    membership,
                    credential,
                    session,
                    str(identity),
                    self.secret,
                    now,
                    now + timedelta(hours=1),
                ),
            )

    def cleanup_database(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def upgrade(self) -> None:
        migrate(self.db, expected_current_version=39, target_version=40)

    def connect(self) -> psycopg.Connection[Any]:
        connection = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(connection.close)
        return connection

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(statement, values).fetchone()
        assert row is not None
        return row[0]

    def begin(self, connection: psycopg.Connection[Any], isolation: str = RC) -> None:
        """Open the transaction an adapter would: application role, 500 ms locks."""
        connection.execute("BEGIN ISOLATION LEVEL " + isolation)
        connection.execute("SET LOCAL lock_timeout='500ms'")
        connection.execute("SET LOCAL ROLE memoriesql_application")

    def start(self, connection: psycopg.Connection[Any], isolation: str = RC) -> None:
        """Open a transaction and start the owner's context in it; leave it open."""
        self.begin(connection, isolation)
        connection.execute(START, (self.secret, self.workspace))

    def committed(
        self, connection: psycopg.Connection[Any], isolation: str = RC
    ) -> int:
        """Start a context, commit, and return the backend that now owns a row."""
        self.start(connection, isolation)
        connection.execute("COMMIT")
        return connection.info.backend_pid

    def contexts(self, pid: int) -> int:
        return int(
            self.scalar(
                "SELECT count(*) FROM memoriesql.authorization_contexts "
                "WHERE backend_pid=%s",
                (pid,),
            )
        )

    def expire(self, pid: int) -> None:
        self.db.execute(
            "UPDATE memoriesql.authorization_contexts SET expires_at=created_at "
            "WHERE backend_pid=%s",
            (pid,),
        )

    def ended_backend_context(self) -> int:
        """One committed context row whose backend has ended; returns its pid."""
        with psycopg.connect(self.url, autocommit=True) as gone:
            pid = self.committed(gone)
        for _ in range(100):
            if not self.scalar(
                "SELECT count(*) FROM pg_stat_activity WHERE pid=%s", (pid,)
            ):
                break
            time.sleep(0.05)
        else:
            self.fail("backend did not end")
        self.assertEqual(self.contexts(pid), 1)
        return pid

    def assert_waited_on_cleanup(self, refused: BaseException) -> None:
        assert isinstance(refused, errors.LockNotAvailable)
        origin = refused.diag.context or ""
        self.assertIn("while deleting tuple", origin)
        self.assertIn('in relation "authorization_contexts"', origin)
        self.assertIn("begin_authorization_context(text,uuid)", origin)

    def test_context_start_no_longer_waits_on_another_sessions_cleanup(self) -> None:
        holder, waiter = self.connect(), self.connect()
        ended = self.ended_backend_context()
        # Schema 39: the holder's open transaction is removing the ended
        # backend's row, so every other context start waits on that row.
        self.start(holder)
        with self.assertRaises(errors.LockNotAvailable) as refused:
            self.start(waiter)
        self.assert_waited_on_cleanup(refused.exception)
        waiter.execute("ROLLBACK")
        holder.execute("ROLLBACK")
        self.assertEqual(self.contexts(ended), 1)

        self.upgrade()
        # Schema 40: the same holder, and the waiter starts at once. It could
        # not have waited and succeeded: the holder never lets go.
        self.start(holder)
        self.start(waiter)
        self.assertEqual(waiter.execute(CURRENT).fetchone(), (1,))
        waiter.execute("COMMIT")
        # The skipped row is the holder's to remove when it commits.
        self.assertEqual(self.contexts(ended), 1)
        holder.execute("COMMIT")
        self.assertEqual(self.contexts(ended), 0)

    def test_a_backends_own_expired_row_no_longer_makes_it_wait(self) -> None:
        holder, returning = self.connect(), self.connect()

        def contended() -> None:
            """The holder's open transaction is removing the returning backend's
            earlier row, which has expired."""
            self.expire(self.committed(returning))
            self.start(holder)

        # Schema 39: the returning backend deletes its own rows inline and waits.
        contended()
        with self.assertRaises(errors.LockNotAvailable) as refused:
            self.start(returning)
        self.assert_waited_on_cleanup(refused.exception)
        returning.execute("ROLLBACK")
        holder.execute("ROLLBACK")

        self.upgrade()
        # Schema 40: only rows of the current transaction are deleted inline;
        # the backend's earlier rows are collected, so a locked one is skipped.
        contended()
        self.start(returning)
        self.assertEqual(returning.execute(CURRENT).fetchone(), (1,))
        returning.execute("COMMIT")
        holder.execute("COMMIT")
        self.assertEqual(self.contexts(returning.info.backend_pid), 1)

    def test_snapshot_context_start_no_longer_fails_on_concurrent_cleanup(
        self,
    ) -> None:
        reader, other = self.connect(), self.connect()

        def overtaken() -> None:
            """The reader's snapshot predates another session's cleanup."""
            ended = self.ended_backend_context()
            self.begin(reader, RR)
            reader.execute("SELECT 1")
            self.committed(other)
            self.assertEqual(self.contexts(ended), 0)

        # Schema 39: the reader's DELETE meets a row removed after its snapshot.
        overtaken()
        with self.assertRaises(errors.SerializationFailure):
            reader.execute(START, (self.secret, self.workspace))
        reader.execute("ROLLBACK")

        self.upgrade()
        # Schema 40: the collection meets the same row, is undone, and the
        # context starts. The reader's own earlier row shows nothing was
        # collected by that attempt.
        earlier = self.committed(reader)
        overtaken()
        reader.execute(START, (self.secret, self.workspace))
        self.assertEqual(reader.execute(CURRENT).fetchone(), (1,))
        reader.execute("COMMIT")
        self.assertEqual(self.contexts(earlier), 2)
        # With nothing removed behind its snapshot, such a start collects too.
        ended = self.ended_backend_context()
        self.committed(reader, RR)
        self.assertEqual((self.contexts(ended), self.contexts(earlier)), (0, 1))

    def test_a_serializable_start_is_undone_and_starts_like_repeatable_read(
        self,
    ) -> None:
        # SERIALIZABLE takes the same snapshot branch: the collection attempt
        # meets a row removed after the snapshot, is undone, and the context
        # starts and commits.
        self.upgrade()
        reader, other = self.connect(), self.connect()
        earlier = self.committed(reader)
        ended = self.ended_backend_context()
        self.begin(reader, "SERIALIZABLE")
        reader.execute("SELECT 1")
        self.committed(other)
        self.assertEqual(self.contexts(ended), 0)
        reader.execute(START, (self.secret, self.workspace))
        self.assertEqual(reader.execute(CURRENT).fetchone(), (1,))
        reader.execute("COMMIT")
        self.assertEqual(self.contexts(earlier), 2)

    def test_a_second_context_in_one_transaction_revokes_the_first(self) -> None:
        self.upgrade()
        named = "SELECT current_setting('memoriesql.authorization_context_id')"
        for isolation in (RC, RR):
            with self.subTest(isolation=isolation):
                session = self.connect()
                self.start(session, isolation)
                first = session.execute(named).fetchone()
                session.execute(START, (self.secret, self.workspace))
                second = session.execute(named).fetchone()
                assert first is not None and second is not None
                self.assertNotEqual(first, second)
                self.assertEqual(session.execute(CURRENT).fetchone(), (1,))
                # Naming the earlier context again resumes nothing.
                session.execute(
                    "SELECT set_config('memoriesql.authorization_context_id',%s,true)",
                    first,
                )
                self.assertEqual(session.execute(CURRENT).fetchone(), (0,))
                session.execute("COMMIT")
                self.assertEqual(self.contexts(session.info.backend_pid), 1)

    def test_a_start_collects_only_rows_nothing_can_use(self) -> None:
        self.upgrade()
        live, collector = self.connect(), self.connect()
        resident = self.committed(live)
        own = self.committed(collector)
        ended = self.ended_backend_context()
        self.assertEqual(
            (self.contexts(ended), self.contexts(resident), self.contexts(own)),
            (1, 1, 1),
        )
        # It collects the ended backend's row and its own backend's earlier
        # row, and keeps a live backend's unexpired row.
        self.committed(collector)
        self.assertEqual(
            (self.contexts(ended), self.contexts(resident), self.contexts(own)),
            (0, 1, 1),
        )
        # An expired row is collected even though its backend is still live.
        self.expire(resident)
        self.committed(collector)
        self.assertEqual((self.contexts(resident), self.contexts(own)), (0, 1))

    def test_upgrade_keeps_the_kernel_functions_identity_and_refusal(self) -> None:
        identity = (
            "SELECT p.proowner,p.proacl::text,p.prosecdef,p.provolatile,"
            "p.proisstrict,p.proparallel,p.proleakproof,p.procost,p.prorows,"
            "p.proretset,p.prokind,p.proconfig::text,"
            "pg_get_function_identity_arguments(p.oid),"
            "pg_get_function_result(p.oid),l.lanname,obj_description(p.oid,'pg_proc') "
            "FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid="
        )
        body = "SELECT prosrc FROM pg_proc WHERE oid=" + KERNEL
        before = self.db.execute(identity + KERNEL).fetchone()
        text = self.scalar(body)
        self.upgrade()
        self.assertEqual(self.db.execute(identity + KERNEL).fetchone(), before)
        self.assertNotEqual(self.scalar(body), text)
        # The private collector runs with its caller's rights and is granted to
        # nobody: neither product role can call it.
        self.assertEqual(
            self.db.execute(
                "SELECT p.prosecdef,"
                "has_function_privilege('memoriesql_application',p.oid,'EXECUTE'),"
                "has_function_privilege('memoriesql_worker',p.oid,'EXECUTE'),"
                "has_function_privilege('public',p.oid,'EXECUTE') "
                "FROM pg_proc p WHERE p.oid=" + COLLECTOR
            ).fetchone(),
            (False, False, False, False),
        )
        # The worker still recognizes a refused credential as the kernel's own.
        session = self.connect()
        self.begin(session)
        with self.assertRaises(errors.InvalidAuthorizationSpecification) as refused:
            session.execute(START, ("0" * 64, self.workspace))
        self.assertTrue(_authentication_context_refused(refused.exception))

    def test_source_commands_report_a_lock_timeout_as_busy_and_retry_cleanly(
        self,
    ) -> None:
        self.upgrade()
        source = uuid.uuid4()
        now = datetime.now(UTC)
        enroll = EnrollExactSource(
            request_id=source,
            source_system="fictional-orchard",
            object_kind="transcript",
            external_object_id="orchard.plot.seven",
            source_schema_version=1,
            exact_source_confirmed=True,
        )
        grant = GrantExactSource(
            request_id=uuid.uuid4(),
            source_object_id=source,
            target_principal_id=uuid.uuid4(),
            permission_keys=("read",),
            valid_from=now,
            expires_at=now + timedelta(minutes=30),
        )
        revoke = RevokeExactSource(
            request_id=uuid.uuid4(),
            source_object_id=source,
            reason="fictional orchard closed",
        )
        authority = PostgresSourceEnrollment(
            self.connect(), credential_sha256=self.secret, workspace_id=self.workspace
        )
        fence = "hashtextextended(%s::text||':semantic_outcome_authority:',0)"
        written = (
            "SELECT (SELECT count(*) FROM memoriesql.source_objects),"
            "(SELECT count(*) FROM memoriesql.source_revocation_receipts)"
        )

        def reading(command: Any, request: Any) -> None:
            """A relation read holds the tenant's authority fence shared for its
            whole frame; every source command takes it exclusively within 500 ms."""
            before = self.db.execute(written).fetchone()
            with self.connect() as reader:
                reader.execute(
                    "SELECT pg_advisory_lock_shared(" + fence + ")", (self.tenant,)
                )
                with self.assertRaises(SourceAuthorityBusy) as refused:
                    command(request)
            # The psycopg error stays on the cause; callers read its SQLSTATE.
            cause = refused.exception.__cause__
            assert isinstance(cause, errors.LockNotAvailable)
            self.assertEqual(cause.sqlstate, "55P03")
            self.assertEqual(self.db.execute(written).fetchone(), before)

        # Nothing was written, so the identical request enrolls, then replays.
        reading(authority.enroll, enroll)
        self.assertFalse(authority.enroll(enroll).replayed)
        self.assertTrue(authority.enroll(enroll).replayed)
        reading(authority.grant, grant)
        reading(authority.revoke, revoke)
        self.assertFalse(authority.revoke(revoke).replayed)
        self.assertTrue(authority.revoke(revoke).replayed)


if __name__ == "__main__":
    unittest.main()
