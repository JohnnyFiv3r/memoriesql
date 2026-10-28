"""Database-free gate/preservation checks; not proof of installed SQL or drain."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "0045_legacy7_execution_unavailable.sql"


def latest_function(name: str) -> str:
    definitions: list[str] = []
    pattern = re.compile(
        rf"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.{name}\(.*?\$\$;",
        re.DOTALL,
    )
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        definitions.extend(pattern.findall(migration.read_text()))
    if not definitions:
        raise AssertionError(f"missing function: {name}")
    return definitions[-1]


class Legacy7AvailabilityMigration(unittest.TestCase):
    def test_activation_refuses_before_replay_or_mutation(self) -> None:
        function = latest_function("activate_complete_input_v6")
        self.assertIn("invalid_complete_input_activation", function)
        self.assertIn("complete_input_activation_denied", function)
        self.assertIn("complete_input_authorize", function)
        # A closed activation endpoint must not retain a reachable success path.
        self.assertNotIn("RETURN old.response_receipt", function)
        self.assertNotIn("INSERT INTO", function)
        self.assertNotIn("enqueue_semantic_task", function)
        self.assertIn(
            "RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' "
            "USING ERRCODE='42501'",
            function,
        )

    def test_only_revision7_task_gate_changes(self) -> None:
        original = (ROOT / "migrations/0041_authored_statement_kinds.sql").read_text()
        baseline = re.search(
            r"CREATE OR REPLACE FUNCTION memoriesql.source_revisiting_authorize"
            r"\(.*?\$\$;",
            original,
            re.DOTALL,
        )
        assert baseline is not None
        gate = (
            " IF e.execution_contract_revision=7 THEN\n"
            "  RAISE EXCEPTION 'source_revisiting_qualification_required' "
            "USING ERRCODE='42501'; END IF;\n"
        )
        current = latest_function("source_revisiting_authorize")
        self.assertEqual(current.count(gate), 1)
        self.assertEqual(current.replace(gate, ""), baseline.group())
        self.assertLess(current.index("complete_input_authorize"), current.index(gate))

    def test_forward_resource_and_inventory_are_exact(self) -> None:
        inventory = json.loads(
            (ROOT / "contracts/migration-inventory.json").read_text()
        )
        rows = inventory["migrations"]
        self.assertEqual(rows[44]["filename"], NAME)
        self.assertEqual(len(rows), 59)
        for row in rows:
            data = (ROOT / "migrations" / row["filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
        source = (ROOT / "migrations" / NAME).read_text()
        functions = re.findall(r"CREATE OR REPLACE FUNCTION memoriesql\.(\w+)", source)
        self.assertEqual(
            functions, ["activate_complete_input_v6", "source_revisiting_authorize"]
        )
        self.assertNotIn("pg_advisory", source)
        self.assertNotIn("LOCK TABLE", source)
        self.assertNotIn("GRANT ", source)
        self.assertNotIn("COMMIT", source)

    def test_historical_schema41_is_byte_exact(self) -> None:
        self.assertEqual(
            hashlib.sha256(
                (ROOT / "migrations/0041_authored_statement_kinds.sql").read_bytes()
            ).hexdigest(),
            "3a7c5eca413aa8c573f62a446c957d2b048fc066d8bce1c1f8af2cff72fd623a",
        )
