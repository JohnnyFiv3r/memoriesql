"""The public executable exposes only explicit, authorized core read paths."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from memoriesql.cli import main

BEAD = UUID(int=11)
WORKSPACE = UUID(int=12)
LOCAL_ENV = {
    "MEMORIESQL_DATABASE_URL": "postgresql://fictional.example/fictional",
    "MEMORIESQL_LOCAL_CREDENTIAL": "fictional-local-credential-with-ample-length",
    "MEMORIESQL_WORKSPACE_ID": str(WORKSPACE),
}


def invoke(arguments: list[str]) -> tuple[int, dict[str, object]]:
    output = io.StringIO()
    with redirect_stdout(output):
        status = main(arguments)
    return status, json.loads(output.getvalue())


class CoreCLIReadTests(unittest.TestCase):
    def test_doctor_and_capabilities_never_connect_or_discover_private_code(
        self,
    ) -> None:
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
        ):
            status, doctor = invoke(["doctor", "--json"])
            self.assertEqual(status, 0)
            self.assertTrue(doctor["configuration_only"])
            self.assertTrue(doctor["local_read_identity_configured"])
            self.assertFalse(doctor["database_contacted"])
            self.assertNotIn(LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"], str(doctor))
            self.assertNotIn(LOCAL_ENV["MEMORIESQL_DATABASE_URL"], str(doctor))
            status, capabilities = invoke(["capabilities", "--json"])
        self.assertEqual(status, 0)
        commands = capabilities["core_commands"]
        self.assertIsInstance(commands, list)
        assert isinstance(commands, list)
        self.assertIn("inspect", commands)
        unavailable = capabilities["unavailable"]
        self.assertIsInstance(unavailable, dict)
        assert isinstance(unavailable, dict)
        self.assertEqual(unavailable["query"], "pr05_query_result_not_released")

    def test_read_without_identity_is_unavailable_before_database_contact(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
        ):
            for command in ("inspect", "relations"):
                status, result = invoke([command, str(BEAD), "--json"])
                self.assertEqual(status, 2)
                self.assertEqual(
                    result,
                    {
                        "outcome": "unavailable",
                        "reason": "local_read_identity_required",
                    },
                )

    def test_inspect_dispatches_typed_request_with_derived_credential(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        reader = MagicMock()
        reader.inspect.return_value = {"outcome": "unavailable"}
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", return_value=connection) as connect,
            patch(
                "memoriesql.cli.PostgresStoredBeadInspection", return_value=reader
            ) as reader_type,
        ):
            status, result = invoke(["inspect", str(BEAD), "--json"])
        self.assertEqual(status, 2)
        self.assertEqual(result, {"outcome": "unavailable"})
        connect.assert_called_once_with(
            LOCAL_ENV["MEMORIESQL_DATABASE_URL"], autocommit=True
        )
        self.assertEqual(reader.inspect.call_args.args[0].bead_id, BEAD)
        self.assertEqual(reader_type.call_args.kwargs["workspace_id"], WORKSPACE)
        self.assertEqual(len(reader_type.call_args.kwargs["credential_sha256"]), 64)
        self.assertNotEqual(
            reader_type.call_args.kwargs["credential_sha256"],
            LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"],
        )

    def test_invalid_source_selection_never_contacts_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            path.write_text('{"package_id":"invalid"}', encoding="utf-8")
            with (
                patch.dict(os.environ, LOCAL_ENV),
                patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
            ):
                status, result = invoke(
                    ["source", str(BEAD), "--selection-file", str(path), "--json"]
                )
        self.assertEqual(status, 3)
        self.assertEqual(
            result, {"outcome": "failed", "reason": "invalid_source_selection"}
        )

    def test_historical_relation_cutoff_requires_explicit_offset(self) -> None:
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as rejected,
        ):
            main(["relations", str(BEAD), "--known-at", "2026-09-28T08:00:00"])
        self.assertEqual(rejected.exception.code, 2)

    def test_bad_connection_is_failed_without_leaking_secret_or_dsn(self) -> None:
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch(
                "memoriesql.cli.psycopg.connect",
                side_effect=RuntimeError(LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"]),
            ),
        ):
            status, result = invoke(["inspect", str(BEAD), "--json"])
        self.assertEqual(status, 3)
        self.assertEqual(result, {"outcome": "failed", "reason": "core_read_failed"})
        self.assertNotIn(LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"], str(result))


if __name__ == "__main__":
    unittest.main()
