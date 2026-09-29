"""Installed one-time personal-local initialization against disposable PostgreSQL."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.authorization import LocalCredential
from memoriesql.application.personal_local_initialization import (
    InitializePersonalLocal,
)
from memoriesql.cli import main as cli_main
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations
from memoriesql.infrastructure.postgres.personal_local_initialization import (
    PostgresPersonalLocalInitialization,
    initialization_identities,
)

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class PersonalLocalInitialization(unittest.TestCase):
    def setUp(self) -> None:
        self.admin = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "pr06_init_" + uuid.uuid4().hex
        with psycopg.connect(self.admin, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.addCleanup(self.cleanup)
        self.url = make_conninfo(self.admin, dbname=self.database)
        self.db = psycopg.connect(self.url, autocommit=True)
        migrate(
            self.db,
            expected_current_version=0,
            target_version=discover_migrations()[-1].version,
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        now = self.db.execute("SELECT clock_timestamp()").fetchone()
        assert now is not None
        self.request: dict[str, object] = {
            "request_id": str(uuid.uuid4()),
            "owner_display_name": "Fictional owner",
            "expires_at": (now[0] + timedelta(hours=2)).isoformat(),
            "exact_initialization_confirmed": True,
        }

    def cleanup(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def init(
        self, request: dict[str, object], secret: Path, url: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        path = self.root / f"init-{uuid.uuid4()}.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        output = io.StringIO()
        environment = {"MEMORIESQL_DATABASE_URL": url or self.url}
        with (
            patch.dict(os.environ, environment, clear=True),
            redirect_stdout(output),
        ):
            status = cli_main(
                [
                    "init",
                    "--request-file",
                    str(path),
                    "--secret-file",
                    str(secret),
                    "--json",
                ]
            )
        return status, json.loads(output.getvalue())

    def authenticates(self, secret: str, workspace: uuid.UUID) -> bool:
        with psycopg.connect(self.url) as connection:
            try:
                with connection.transaction():
                    connection.execute("SET LOCAL ROLE memoriesql_application")
                    context = PostgresAuthorizationPort(connection).begin_context(
                        credential_sha256=LocalCredential(secret).sha256(),
                        requested_workspace_id=workspace,
                    )
                    return context.principal_kind.value == "human"
            except (PermissionError, psycopg.Error):
                return False

    def principals(self) -> int:
        row = self.db.execute("SELECT count(*) FROM memoriesql.principals").fetchone()
        assert row is not None
        return int(row[0])

    def test_initialize_replay_and_second_owner_refusal(self) -> None:
        first = self.root / "owner.secret"
        status, created = self.init(self.request, first)
        self.assertEqual(status, 0, created)
        receipt = created["receipt"]
        self.assertFalse(receipt["replayed"])
        ids = initialization_identities(uuid.UUID(str(self.request["request_id"])))
        self.assertEqual(receipt["workspace_id"], str(ids["workspace"]))
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
        secret = first.read_text(encoding="ascii")
        self.assertNotIn(secret, json.dumps(created))
        workspace = uuid.UUID(receipt["workspace_id"])
        self.assertTrue(self.authenticates(secret, workspace))
        principals = self.principals()

        status, stale = self.init(self.request, first)
        self.assertEqual(
            (status, stale), (3, {"outcome": "failed", "reason": "stale_secret_file"})
        )
        self.assertEqual(first.read_text(encoding="ascii"), secret)

        replay_file = self.root / "replay.secret"
        status, replay = self.init(self.request, replay_file)
        self.assertEqual(status, 0, replay)
        self.assertTrue(replay["receipt"]["replayed"])
        self.assertFalse(replay["secret_file_written"])
        self.assertFalse(replay_file.exists())
        self.assertEqual(replay["receipt"]["credential_id"], receipt["credential_id"])
        self.assertTrue(self.authenticates(secret, workspace))

        changed = self.request | {"owner_display_name": "Another fictional owner"}
        other = self.request | {"request_id": str(uuid.uuid4())}
        for label, request in (("changed replay", changed), ("second owner", other)):
            with self.subTest(label):
                refused_file = self.root / f"refused-{uuid.uuid4()}.secret"
                status, refused = self.init(request, refused_file)
                self.assertEqual(
                    (status, refused),
                    (2, {"outcome": "unavailable", "reason": "already_initialized"}),
                )
                self.assertFalse(refused_file.exists())
        self.assertEqual(self.principals(), principals)

    def test_crash_before_and_after_commit_recover_without_a_second_owner(self) -> None:
        # Crash after the secret file was written but before commit leaves a
        # stale file; the operator removes it and the same request succeeds.
        leftover = self.root / "leftover.secret"
        leftover.write_text("interrupted before commit", encoding="ascii")
        status, stale = self.init(self.request, leftover)
        self.assertEqual((status, stale["reason"]), (3, "stale_secret_file"))
        self.assertEqual(self.principals(), 0)
        leftover.unlink()

        # Crash after commit, before the receipt: the adapter commits, the
        # response is lost, and the first secret file stays authoritative.
        secret = LocalCredential.generate()
        with psycopg.connect(self.url, autocommit=True) as connection:
            PostgresPersonalLocalInitialization(connection).initialize(
                InitializePersonalLocal.model_validate(self.request),
                session_secret_sha256=secret.sha256(),
            )
        status, replay = self.init(self.request, self.root / "after-crash.secret")
        self.assertEqual(status, 0, replay)
        self.assertTrue(replay["receipt"]["replayed"])
        self.assertFalse((self.root / "after-crash.secret").exists())
        self.assertEqual(self.principals(), 1)
        workspace = uuid.UUID(replay["receipt"]["workspace_id"])
        with psycopg.connect(self.url) as connection:
            with connection.transaction():
                connection.execute("SET LOCAL ROLE memoriesql_application")
                context = PostgresAuthorizationPort(connection).begin_context(
                    credential_sha256=secret.sha256(), requested_workspace_id=workspace
                )
        self.assertEqual(context.workspace_id, workspace)

    def operator_login(self) -> str:
        """A non-superuser login that can only assume the application role."""
        role = "pr06_init_operator_" + uuid.uuid4().hex[:12]
        password = "fictional-init-operator-password"
        self.db.execute(
            sql.SQL("CREATE ROLE {} LOGIN INHERIT PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        self.db.execute(
            sql.SQL("GRANT memoriesql_application TO {} WITH INHERIT TRUE, SET TRUE").format(
                sql.Identifier(role)
            )
        )

        def drop() -> None:
            with psycopg.connect(self.admin, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE usename=%s",
                    (role,),
                )
            self.db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            self.db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

        self.addCleanup(drop)
        return make_conninfo(self.url, user=role, password=password)

    def test_least_privileged_operator_replay_is_unverifiable_not_refused(self) -> None:
        operator = self.operator_login()
        first = self.root / "operator-owner.secret"
        status, created = self.init(self.request, first, operator)
        self.assertEqual(status, 0, created)
        self.assertFalse(created["receipt"]["replayed"])
        secret = first.read_text(encoding="ascii")
        workspace = uuid.UUID(created["receipt"]["workspace_id"])

        # This login cannot read the bootstrap records a replay must match, so
        # it must not claim that a different owner initialized the database.
        replay_file = self.root / "operator-replay.secret"
        status, unverifiable = self.init(self.request, replay_file, operator)
        self.assertEqual(
            (status, unverifiable),
            (
                2,
                {
                    "outcome": "unavailable",
                    "reason": "initialization_replay_unverifiable",
                },
            ),
        )
        self.assertFalse(replay_file.exists())
        self.assertEqual(first.read_text(encoding="ascii"), secret)
        self.assertTrue(self.authenticates(secret, workspace))

        # Granting it SELECT only shows rows through forced row security, which
        # hides them: an empty read is still no evidence of another owner.
        for table in ("workspaces", "users", "authentication_credentials"):
            self.db.execute(
                sql.SQL("GRANT SELECT ON memoriesql.{} TO {}").format(
                    sql.Identifier(table),
                    sql.Identifier(str(psycopg.conninfo.conninfo_to_dict(operator)["user"])),
                )
            )
        filtered_file = self.root / "operator-filtered.secret"
        status, filtered = self.init(self.request, filtered_file, operator)
        self.assertEqual(
            (status, filtered),
            (
                2,
                {
                    "outcome": "unavailable",
                    "reason": "initialization_replay_unverifiable",
                },
            ),
        )
        self.assertFalse(filtered_file.exists())

        # A login that bypasses row security confirms the same replay.
        status, confirmed = self.init(self.request, self.root / "admin-replay.secret")
        self.assertEqual(status, 0, confirmed)
        self.assertTrue(confirmed["receipt"]["replayed"])
        self.assertEqual(self.principals(), 1)

    def test_lost_connection_during_replay_verification_is_unverifiable(self) -> None:
        first = self.root / "owner.secret"
        status, created = self.init(self.request, first)
        self.assertEqual(status, 0, created)
        secret = first.read_text(encoding="ascii")
        workspace = uuid.UUID(created["receipt"]["workspace_id"])
        admin_url = self.url
        connect = psycopg.connect

        class CutBeforeVerification:
            """A real connection whose server session ends before the replay read."""

            def __init__(self, connection: Any) -> None:
                self.connection = connection

            def __getattr__(self, name: str) -> Any:
                return getattr(self.connection, name)

            def __enter__(self) -> Any:
                self.connection.__enter__()
                return self

            def __exit__(self, *exc: Any) -> Any:
                return self.connection.__exit__(*exc)

            def execute(self, query: Any, params: Any = None) -> Any:
                if isinstance(query, str) and query.startswith("SELECT u.display_name"):
                    pid = self.connection.info.backend_pid
                    with connect(admin_url, autocommit=True) as admin:
                        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
                        for _ in range(500):
                            if not admin.execute(
                                "SELECT 1 FROM pg_stat_activity WHERE pid=%s", (pid,)
                            ).fetchone():
                                break
                            time.sleep(0.01)
                return self.connection.execute(query, params)

        def cut(url: str, **kwargs: Any) -> Any:
            return CutBeforeVerification(connect(url, **kwargs))

        replay_file = self.root / "cut-replay.secret"
        with patch("memoriesql.cli.psycopg.connect", side_effect=cut):
            status, cut_off = self.init(self.request, replay_file)
        # The bootstrap had already refused this run, so nothing it sent can
        # have committed: the lost read leaves the replay unverifiable, and the
        # new file, which cannot be the credential, is removed.
        self.assertEqual(
            (status, cut_off),
            (
                2,
                {
                    "outcome": "unavailable",
                    "reason": "initialization_replay_unverifiable",
                },
            ),
        )
        self.assertFalse(replay_file.exists())
        self.assertEqual(self.principals(), 1)
        self.assertTrue(self.authenticates(secret, workspace))


if __name__ == "__main__":
    unittest.main()
