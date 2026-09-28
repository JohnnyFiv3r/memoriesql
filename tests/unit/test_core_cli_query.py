"""The query CLI prints closed replies and names operator maintenance plainly."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from memoriesql.cli import main
from memoriesql.query_client import ADMISSION_RULES, schema_description

OVERDUE = b'{"contract_version":1,"error":{"code":"settlement"},"outcome":"budget_exhausted"}'


class RefusingHost:
    def start_run(self) -> bytes:
        return OVERDUE

    def handle(self, request: bytes) -> bytes:  # pragma: no cover - never reached
        raise AssertionError("a refused run admits no request")


class QueryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.sql = self.root / "query.sql"
        self.sql.write_text("SELECT o.bead_id FROM memory_v1.observations o", "utf-8")
        self.environment = {
            "MEMORIESQL_DATABASE_URL": "postgresql://fictional.example/fictional",
            "MEMORIESQL_LOCAL_CREDENTIAL": "fictional-local-credential-with-ample-length",
            "MEMORIESQL_WORKSPACE_ID": str(UUID(int=61)),
            "MEMORIESQL_STATE_DIR": str(self.root / "state"),
        }

    def query(self, *extra: str) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch("memoriesql.cli._results_transport", return_value=RefusingHost()),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            status = main(
                [
                    "query",
                    "--file",
                    str(self.sql),
                    "--intent",
                    "enumerate",
                    "--view",
                    "resolved",
                    *extra,
                ]
            )
        return status, output.getvalue(), errors.getvalue()

    def test_overdue_cleanup_refusal_is_named_as_host_maintenance(self) -> None:
        status, output, errors = self.query()
        self.assertEqual(status, 3)
        self.assertEqual(json.loads(output)["outcome"], "budget_exhausted")
        self.assertIn("expired-result cleanup", errors)
        status, output, errors = self.query("--json")
        self.assertEqual((status, output.encode()), (3, OVERDUE + b"\n"))
        self.assertEqual(errors, "", "machine output stays the exact reply")

    def test_schema_labels_source_text_and_text_inequality(self) -> None:
        schema = schema_description()
        self.assertIn("normalized text of an authorized source unit", schema["source_text"])
        self.assertIn("never raw bytes", schema["source_text"])
        self.assertTrue(any("Text inequality" in rule for rule in ADMISSION_RULES))


if __name__ == "__main__":
    unittest.main()
