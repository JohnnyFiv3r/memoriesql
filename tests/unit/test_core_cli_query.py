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

from memoriesql.application.agent_sql_admission import admit_query
from memoriesql.application.agent_sql_catalog import SqlAdmissionError, SqlParameter
from memoriesql.cli import main
from memoriesql.infrastructure.postgres.agent_sql_results import _admission_error
from memoriesql.query_client import ADMISSION_RULES, query_request, schema_description

OVERDUE = b'{"contract_version":1,"error":{"code":"settlement"},"outcome":"budget_exhausted"}'
ANCHOR = "11111111-1111-4111-8111-111111111111"
# A bounded walk over relation endpoints, as the results contract would admit it
# with a recursion declaration.
RECURSIVE_SQL = """WITH RECURSIVE walk(node, depth) AS (
  SELECT o.bead_id, 0 FROM memory_v1.observations o WHERE o.bead_id = $1
  UNION ALL
  SELECT r.target_bead_id, w.depth + 1
  FROM walk w JOIN memory_v1.assessed_relations r ON r.source_bead_id = w.node
  WHERE w.depth < 3
) CYCLE node SET is_cycle USING path
SELECT node, depth, is_cycle FROM walk"""
NO_RECURSION = (
    "`memoriesql query` sends no recursion declaration in this release, so a "
    "query using `WITH RECURSIVE` never runs through it: it replies "
    'invalid_request with error {"code": "binding"}, without a feature or a '
    "position."
)


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

    def test_schema_labels_source_text_and_the_operator_closure(self) -> None:
        schema = schema_description()
        self.assertIn("normalized text of an authorized source unit", schema["source_text"])
        self.assertIn("never raw bytes", schema["source_text"])
        rules = " ".join(ADMISSION_RULES)
        for fact in (
            '"feature": "reference_type"',
            '"feature": "unreviewed_operator"',
            '{"code": "unavailable"}',
            "1 to 64 values on every type",
            "9 to 64 values",
            "2 to 8 values on text, identifiers",
            "`flag <> $1`",
            "`<>` between two booleans",
            "0-based character offset",
            "`IN ($1,$2)`",
            "NOT LIKE and NOT ILIKE",
            '"feature": "comparison_grouping"',
        ):
            self.assertIn(fact, rules)
        self.assertNotIn("Text inequality", rules)
        self.assertNotIn("nine or more values", rules)

    def test_schema_advertises_no_recursion_the_cli_cannot_send(self) -> None:
        # The CLI's request carries no recursion declaration, so the schema must
        # not offer recursive queries; it says what such a query replies.
        rules = schema_description()["admission_rules"]
        self.assertIn(NO_RECURSION, rules)
        joined = " ".join(rules)
        for advertised in (
            "may expand over relation endpoints",
            "declared recursion object",
            "correction_recursion",
        ):
            self.assertNotIn(advertised, joined)
        run = {
            "run_ref": str(UUID(int=1)),
            "default_known_at": "2026-10-07T00:00:00.000000Z",
        }
        request = json.loads(
            query_request(
                run,
                sql=RECURSIVE_SQL,
                parameters=[],
                intent="discover",
                view="resolved",
                known_at=None,
                page_size=20,
            )
        )
        self.assertNotIn("recursion", request)
        # Without the declaration the reviewed admission refuses the walk, and
        # the executor's reply is the one the rule states.
        with self.assertRaises(SqlAdmissionError) as refused:
            admit_query(
                RECURSIVE_SQL,
                (SqlParameter(1, "bead_ref", ANCHOR),),
                admitted_anchors=frozenset({("bead_ref", ANCHOR)}),
            )
        self.assertEqual(refused.exception.code, "invalid_request")
        self.assertIsNone(refused.exception.position)
        self.assertEqual(
            _admission_error("invalid_request", refused.exception.construct),
            ("binding", None),
        )


if __name__ == "__main__":
    unittest.main()
