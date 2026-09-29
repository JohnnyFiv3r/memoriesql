"""`doctor --check-database` against real login shapes on a migrated database."""

from __future__ import annotations

import io
import json
import os
import unittest
import uuid
from contextlib import redirect_stdout
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.cli import main as cli_main
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class CliDoctorDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.admin = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "pr06_doctor_" + uuid.uuid4().hex
        self.suffix = uuid.uuid4().hex[:12]
        with psycopg.connect(self.admin, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.addCleanup(self.cleanup)
        self.url = make_conninfo(self.admin, dbname=self.database)
        with psycopg.connect(self.url, autocommit=True) as db:
            migrate(
                db,
                expected_current_version=0,
                target_version=discover_migrations()[-1].version,
            )
            for name, inherit in (("member", True), ("noinherit", False)):
                role = sql.Identifier(f"pr06_{name}_{self.suffix}")
                db.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD 'fictional-doctor-pass' "
                        + ("INHERIT" if inherit else "NOINHERIT")
                    ).format(role)
                )
                db.execute(sql.SQL("GRANT memoriesql_application TO {}").format(role))

    def cleanup(self) -> None:
        with psycopg.connect(self.admin, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )
            for name in ("member", "noinherit"):
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(
                        sql.Identifier(f"pr06_{name}_{self.suffix}")
                    )
                )

    def doctor(self, url: str) -> tuple[int, dict[str, Any]]:
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"MEMORIESQL_DATABASE_URL": url}, clear=True),
            redirect_stdout(output),
        ):
            status = cli_main(["doctor", "--check-database", "--json"])
        return status, json.loads(output.getvalue())

    def login(self, name: str) -> str:
        return make_conninfo(
            self.url,
            user=f"pr06_{name}_{self.suffix}",
            password="fictional-doctor-pass",
        )

    def test_member_is_ready_and_noinherit_member_is_named_not_ready(self) -> None:
        status, superuser = self.doctor(self.url)
        self.assertEqual(status, 0, superuser)
        self.assertTrue(superuser["database"]["schema_compatible"])
        self.assertTrue(superuser["database"]["login"]["superuser"])
        status, member = self.doctor(self.login("member"))
        self.assertEqual(status, 0, member)
        self.assertTrue(member["database"]["login"]["application_ready"])
        status, noinherit = self.doctor(self.login("noinherit"))
        self.assertEqual((status, noinherit["reason"]), (3, "login_role_not_ready"))
        self.assertFalse(noinherit["database"]["login"]["prologue_builtins_executable"])
        self.assertNotIn("fictional-doctor-pass", json.dumps(noinherit))


if __name__ == "__main__":
    unittest.main()
