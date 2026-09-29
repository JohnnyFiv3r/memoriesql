"""`doctor --check-database` is read-only and names configuration problems."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from typing import Any
from unittest.mock import MagicMock, patch

from psycopg import OperationalError
from psycopg.errors import InsufficientPrivilege

from memoriesql.cli import main

DATABASE = {"MEMORIESQL_DATABASE_URL": "postgresql://fictional.example/fictional"}
INSTALLED = 38


def invoke(environment: dict[str, str], connection: Any) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with (
        patch.dict(os.environ, environment, clear=True),
        patch("memoriesql.cli.psycopg.connect", return_value=connection)
        if not isinstance(connection, Exception)
        else patch("memoriesql.cli.psycopg.connect", side_effect=connection),
        patch("memoriesql.cli._installed_schema_version", return_value=INSTALLED),
        redirect_stdout(output),
    ):
        status = main(["doctor", "--check-database", "--json"])
    return status, json.loads(output.getvalue())


def connection(
    login: tuple[Any, ...] | Exception,
    schema: int | None | Exception,
    *,
    present: bool = True,
) -> MagicMock:
    db = MagicMock()
    db.__enter__.return_value = db

    def execute(statement: str, *_: Any) -> MagicMock:
        cursor = MagicMock()
        if "pg_has_role" in statement:
            if isinstance(login, Exception):
                raise login
            cursor.fetchone.return_value = login
        elif "to_regclass" in statement:
            cursor.fetchone.return_value = (present,)
        elif "schema_migrations" in statement:
            if isinstance(schema, Exception):
                raise schema
            cursor.fetchone.return_value = (schema,)
        else:
            cursor.fetchone.return_value = (1,)
        return cursor

    db.execute.side_effect = execute
    return db


class DoctorDatabaseTests(unittest.TestCase):
    def test_default_doctor_never_connects(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(os.environ, DATABASE, clear=True),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
            redirect_stdout(output),
        ):
            self.assertEqual(main(["doctor", "--json"]), 0)
        self.assertFalse(json.loads(output.getvalue())["database_contacted"])

    def test_unconfigured_and_unreachable_are_distinct_and_sanitized(self) -> None:
        status, missing = invoke({}, AssertionError())
        self.assertEqual((status, missing["reason"]), (2, "database_not_configured"))
        status, down = invoke(DATABASE, OperationalError("secret@fictional.example"))
        self.assertEqual((status, down["reason"]), (3, "database_unreachable"))
        self.assertNotIn("secret", json.dumps(down))

    def test_ready_member_login_and_schema_compatibility(self) -> None:
        ready = (False, True, True, True, True, True, 180004)
        status, report = invoke(DATABASE, connection(ready, INSTALLED))
        self.assertEqual(status, 0, report)
        self.assertTrue(report["database"]["login"]["application_ready"])
        self.assertTrue(report["database"]["schema_compatible"])
        status, behind = invoke(DATABASE, connection(ready, INSTALLED - 1))
        self.assertEqual((status, behind["reason"]), (3, "schema_version_mismatch"))
        status, empty = invoke(DATABASE, connection(ready, None, present=False))
        self.assertEqual((status, empty["reason"]), (3, "schema_not_installed"))
        status, blank = invoke(DATABASE, connection(ready, None))
        self.assertEqual((status, blank["reason"]), (3, "schema_version_unknown"))
        # An application login cannot read the owner-only history: reported as
        # unverified, never as a verified match and never as a login failure.
        status, member = invoke(
            DATABASE, connection(ready, InsufficientPrivilege("owner only"))
        )
        self.assertEqual(status, 0, member)
        self.assertFalse(member["database"]["schema_version_readable"])
        self.assertEqual(member["database"]["schema_compatibility"], "unverified")
        self.assertEqual(member["database_check"], "login_ready_schema_unverified")
        self.assertIn("migration administrator", member["notice"])

    def test_unreadable_version_never_reports_compatible(self) -> None:
        ready = (False, True, True, True, True, True, 180004)
        for error in (InsufficientPrivilege("owner only"), OperationalError("gone")):
            with self.subTest(error=type(error).__name__):
                status, report = invoke(DATABASE, connection(ready, error))
                database = report["database"]
                self.assertIsNot(database["schema_compatible"], True)
                self.assertNotEqual(database["schema_compatibility"], "compatible")
                self.assertNotEqual(report.get("database_check"), "passed")
        status, verified = invoke(DATABASE, connection(ready, INSTALLED))
        self.assertEqual(verified["database"]["schema_compatibility"], "compatible")
        self.assertEqual(verified["database_check"], "passed")

    def test_builtin_denied_or_noinherit_login_is_not_ready(self) -> None:
        status, denied = invoke(
            DATABASE, connection(InsufficientPrivilege("denied"), INSTALLED)
        )
        self.assertEqual((status, denied["reason"]), (3, "login_role_not_ready"))
        self.assertFalse(denied["database"]["login"]["prologue_builtins_executable"])
        noinherit = (False, True, True, False, False, False, 180004)
        status, report = invoke(DATABASE, connection(noinherit, INSTALLED))
        self.assertEqual((status, report["reason"]), (3, "login_role_not_ready"))


if __name__ == "__main__":
    unittest.main()
