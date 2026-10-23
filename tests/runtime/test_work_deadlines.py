"""Work deadlines against disposable PostgreSQL: lock waits no longer count.

The evidence writer bounds its own work by 2 s. Here a holder in another
session makes two of its lock acquisitions wait 0.3 s each, and a test-only
trigger sleeps 1.6 s while it works, after every lock. On the schema before the
fix the waits count, so the budget is exceeded. After it they do not, so the
same write succeeds. Each wait stays well under the 500 ms lock timeout and
the work well under 2 s, so neither outcome depends on machine speed.
"""

from __future__ import annotations

import threading
import time
import unittest
import uuid
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import errors
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from memoriesql.application.evidence_packages import AppendEvidencePart
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime import test_evidence_packages as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_evidence_packages as fixtures
    from test_postgres_runtime import migrate

FIX = next(
    migration.version
    for migration in discover_migrations()
    if migration.path.name.endswith("_work_deadlines_after_lock_waits.sql")
)
HOLD_SECONDS = 0.3
WORK_SECONDS = 1.6
FENCE = "hashtextextended(%s::text || ':semantic_outcome_authority:', 0)"
OPERATION = "hashtextextended(%s::text || ':evidence-operation:' || %s, 0)"
KEY = "((classid::bigint << 32) | objid::bigint)"


class WorkDeadlines(fixtures.EvidencePackages):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=16, target_version=FIX - 1)
        self.db.execute(
            "CREATE FUNCTION public.fictional_slow_work() RETURNS trigger"
            " LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_catalog.pg_sleep("
            + str(WORK_SECONDS)
            + "); RETURN NEW; END $$"
        )
        self.db.execute(
            "CREATE TRIGGER fictional_slow_work BEFORE INSERT"
            " ON memoriesql.evidence_package_parts FOR EACH ROW"
            " WHEN (NEW.ordinal = 1) EXECUTE FUNCTION public.fictional_slow_work()"
        )

    def connect(self) -> psycopg.Connection[Any]:
        connection = psycopg.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        )
        self.addCleanup(connection.close)
        return connection

    def contended_append(self, package: Any, part: Any) -> Any:
        """Append `part` in SQL while two of the writer's lock waits are held."""
        command = AppendEvidencePart(
            idempotency_key="append." + str(uuid.uuid4()),
            package_id=package.package_id,
            part=part,
        )
        writer, holder, observer = self.connect(), self.connect(), self.connect()
        pid = writer.info.backend_pid
        fence = observer.execute("SELECT " + FENCE, (self.tenant,)).fetchone()[0]  # type: ignore[index]
        operation = observer.execute(
            "SELECT " + OPERATION, (self.tenant, command.idempotency_key)
        ).fetchone()[0]  # type: ignore[index]
        holder.execute("SELECT pg_advisory_lock(%s)", (fence,))
        holder.execute("SELECT pg_advisory_lock(%s)", (operation,))
        failures: list[BaseException] = []

        def release_after_waits() -> None:
            try:
                for key in (fence, operation):
                    deadline = time.monotonic() + 30
                    while not observer.execute(
                        "SELECT count(*) FROM pg_locks WHERE pid=%s AND NOT granted"
                        " AND locktype='advisory' AND " + KEY + "=%s",
                        (pid, key),
                    ).fetchone()[0]:  # type: ignore[index]
                        if time.monotonic() > deadline:
                            raise AssertionError("the writer never waited")
                        time.sleep(0.005)
                    time.sleep(HOLD_SECONDS)
                    holder.execute("SELECT pg_advisory_unlock(%s)", (key,))
            except BaseException as error:  # surfaced by the test thread
                failures.append(error)
                holder.execute("SELECT pg_advisory_unlock_all()")

        watcher = threading.Thread(target=release_after_waits)
        watcher.start()
        try:
            with writer.transaction():
                # A standalone SQL caller: its own interruption bound is long,
                # so only the function's work deadline can end the call.
                writer.execute("SET LOCAL statement_timeout='10s'")
                writer.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(writer).begin_context(
                    credential_sha256=self.secret_hash,
                    requested_workspace_id=self.workspace,
                )
                return writer.execute(
                    "SELECT memoriesql.write_evidence_package_v1(%s)",
                    (Jsonb(command.model_dump(mode="json")),),
                ).fetchone()[0]  # type: ignore[index]
        finally:
            watcher.join()
            if failures:
                raise failures[0]

    def test_lock_waits_no_longer_spend_the_work_budget(self) -> None:
        first, second = (
            self.part("Fictional first part."),
            self.part("Fictional second part.", 1),
        )
        package = self.create((first, second))
        self.append(package, first)
        # Before the fix: 0.6 s of waits plus 1.6 s of work exceed 2 s.
        with self.assertRaises(errors.QueryCanceled) as expired:
            self.contended_append(package, second)
        self.assertIn("evidence_work_timeout", str(expired.exception))
        self.assertEqual(self.status(package).appended_parts, 1)

        migrate(self.db, expected_current_version=FIX - 1, target_version=FIX)
        # After it: the same waits are excluded, and 1.6 s of work fits.
        receipt = self.contended_append(package, second)
        self.assertFalse(receipt["replayed"])
        self.assertEqual(self.status(package).appended_parts, 2)


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        WorkDeadlines(name)
        for name in WorkDeadlines.__dict__
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
