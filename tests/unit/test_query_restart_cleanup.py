from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg

from scripts import prove_query_result_restart as script

PUBLIC = "pr05_results_" + "a" * 32
INNER = "pr05_query_" + "b" * 32


class AbruptExit(BaseException):
    pass


class QueryRestartCleanupTests(unittest.TestCase):
    def prepare(self, session: Path) -> dict[str, Any]:
        case = MagicMock()
        case.fixture.assertion = MagicMock()
        case.reader = PUBLIC
        case.h.runtime.reader = INNER
        case.db.info.dbname = "fictional_restart_database"
        case.fixture.secret_hash = "c" * 64
        case.fixture.workspace = "00000000-0000-0000-0000-000000000001"
        case.request_only.return_value = ({"kind": "query"}, None)
        fixture_module = types.ModuleType("test_agent_sql_results")
        fixture_module.AgentSqlResults = MagicMock(return_value=case)  # type: ignore[attr-defined]
        fixture_module.READER_PASSWORD = "cpu-only-fictional"  # type: ignore[attr-defined]
        model_module = types.ModuleType(
            "memoriesql.infrastructure.postgres.agent_sql_results"
        )
        model = type("FictionalResults", (), {})
        model_module.PostgresAgentSqlResults = model  # type: ignore[attr-defined]

        def dispatch(_: bytes) -> None:
            model._disclose()  # type: ignore[attr-defined]

        case.service.return_value.handle.side_effect = dispatch
        with (
            patch.dict(
                sys.modules,
                {
                    fixture_module.__name__: fixture_module,
                    model_module.__name__: model_module,
                },
            ),
            patch.object(script, "SESSION", session),
            patch.object(sys, "argv", ["prove_query_result_restart.py", "prepare"]),
            patch.object(os, "_exit", side_effect=AbruptExit),
            patch.object(sys, "path", list(sys.path)),
            patch("builtins.print"),
            self.assertRaises(AbruptExit),
        ):
            script.main()
        case.setUp.assert_called_once()
        case.service.return_value.handle.assert_called_once()
        context: dict[str, Any] = json.loads(session.read_text())
        return context

    def cleanup(self, session: Path, db: MagicMock) -> None:
        with (
            patch.object(script, "SESSION", session),
            patch.object(sys, "argv", ["prove_query_result_restart.py", "cleanup"]),
            patch.object(sys, "path", list(sys.path)),
            patch.dict(os.environ, {"N1_TEST_DATABASE_URL": "cpu-only"}),
            patch.object(psycopg, "connect") as connect,
            patch("builtins.print"),
        ):
            connect.return_value.__enter__.return_value = db
            script.main()

    def test_abrupt_prepare_retains_both_exact_owned_readers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            context = self.prepare(session)
            self.assertEqual(context["reader"], PUBLIC)
            self.assertEqual(context.get("inner_reader"), INNER)
            self.assertEqual(context["request"], {"kind": "query"})

    def test_credential_session_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            self.prepare(session)
            self.assertEqual(stat.S_IMODE(session.stat().st_mode), 0o600)

    def test_cleanup_drops_only_recorded_roles_after_owned_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            session.write_text(
                json.dumps(
                    {
                        "database": "fictional_restart_database",
                        "reader": PUBLIC,
                        "inner_reader": INNER,
                        "unrelated_role": "must_not_be_touched",
                    }
                )
            )
            db = MagicMock()
            self.cleanup(session, db)
            statements = [
                call.args[0].as_string() for call in db.execute.call_args_list
            ]
            self.assertEqual(
                statements,
                [
                    'DROP DATABASE "fictional_restart_database" WITH (FORCE)',
                    f'DROP ROLE IF EXISTS "{PUBLIC}"',
                    f'DROP ROLE IF EXISTS "{INNER}"',
                ],
            )
            self.assertFalse(session.exists())

    def test_inner_drop_failure_is_visible_and_session_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            session.write_text(
                json.dumps(
                    {
                        "database": "fictional_restart_database",
                        "reader": PUBLIC,
                        "inner_reader": INNER,
                        "unrelated_role": "must_not_be_touched",
                    }
                )
            )
            db = MagicMock()
            db.execute.side_effect = [
                None,
                None,
                RuntimeError("fictional cleanup fault"),
            ]
            with self.assertRaisesRegex(RuntimeError, "fictional cleanup fault"):
                self.cleanup(session, db)
            self.assertTrue(session.exists())

    def test_missing_inner_identity_refuses_before_database_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            session.write_text(
                json.dumps({"database": "fictional_restart_database", "reader": PUBLIC})
            )
            db = MagicMock()
            with self.assertRaises(KeyError):
                self.cleanup(session, db)
            db.execute.assert_not_called()
            self.assertTrue(session.exists())


if __name__ == "__main__":
    unittest.main()
