"""DB-free pins for engine identity within its engine scope; no SQL execution proof.

The runtime proof is tests/runtime/test_engine_scope_identity.py.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "migrations/0055_engine_scope_identity.sql").read_text()
INTAKE = (ROOT / "migrations/0054_engine_record_intake.sql").read_text()
TABLES = (
    "engine_events",
    "engine_records",
    "engine_chunks",
    "engine_event_completions",
    "engine_type_decisions",
)
BACKFILLED = ("engine_chunks", "engine_event_completions", "engine_type_decisions")
REPLACED = (
    "guard_engine_type_upgrade",
    "engine_accept_chunk_v1",
    "engine_accept_type_decision_v1",
    "engine_accept_record_v1",
)


def signature(sql: str, name: str) -> str:
    match = re.search(
        rf"FUNCTION memoriesql\.{name}\((.*?)\)\nRETURNS (\w+)", sql, re.DOTALL
    )
    assert match is not None, name
    return match.group(1).strip() + " -> " + match.group(2)


class EngineScopeIdentityMigration(unittest.TestCase):
    def test_every_engine_key_names_its_scope(self) -> None:
        for table in TABLES:
            self.assertRegex(
                SQL,
                rf"ADD CONSTRAINT {table}_pkey\n\s+PRIMARY KEY "
                r"\(tenant_id, source_object_id, ",
            )
        for reference in re.findall(
            r"REFERENCES memoriesql\.engine_\w+ \(([^)]*)\)", SQL
        ):
            self.assertTrue(
                reference.startswith("tenant_id, source_object_id, "), reference
            )
        self.assertIn(
            "ON memoriesql.engine_type_decisions (tenant_id, source_object_id, chunk_id)\n"
            "    WHERE status <> 'pending';",
            SQL,
        )

    def test_the_copy_drops_each_guard_only_for_itself(self) -> None:
        trigger = (
            "CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE "
            "ON memoriesql.{table}\nFOR EACH ROW EXECUTE FUNCTION "
            "memoriesql.reject_immutable_change();"
        )
        restored_before = 0
        for table in BACKFILLED:
            dropped = SQL.index(
                f"DROP TRIGGER {table}_immutable ON memoriesql.{table};"
            )
            copied = SQL.index(f"UPDATE memoriesql.{table} AS")
            restored = SQL.index(trigger.format(table=table))
            # Each guard is restored before the next one is dropped.
            self.assertLess(restored_before, dropped)
            self.assertLess(dropped, copied)
            self.assertLess(copied, restored)
            restored_before = restored
            # The restored guard is the one 0054 created.
            self.assertIn(trigger.format(table=table), INTAKE)
            self.assertIn(
                f"ALTER TABLE memoriesql.{table} "
                "ALTER COLUMN source_object_id SET NOT NULL;",
                SQL,
            )
        self.assertEqual(SQL.count("DROP TRIGGER"), len(BACKFILLED))
        self.assertEqual(SQL.count("UPDATE memoriesql.engine_"), len(BACKFILLED))
        self.assertNotIn("DISABLE TRIGGER", SQL)

    def test_only_the_intake_functions_change_and_keep_their_grants(self) -> None:
        replaced = re.findall(r"CREATE OR REPLACE FUNCTION memoriesql\.(\w+)\(", SQL)
        self.assertEqual(replaced, list(REPLACED))
        self.assertNotRegex(SQL, r"CREATE FUNCTION")
        for name in REPLACED:
            self.assertEqual(signature(SQL, name), signature(INTAKE, name))
        self.assertNotRegex(SQL, r"\b(GRANT|REVOKE)\b")
        self.assertNotRegex(SQL, r"\bDROP (TABLE|FUNCTION|POLICY)\b")

    def test_every_lookup_and_receipt_is_scoped(self) -> None:
        functions = SQL[SQL.index("CREATE OR REPLACE FUNCTION") :]
        lines = functions.splitlines()
        for index, line in enumerate(lines):
            if re.search(r"\b\w+\.tenant_id = (authority|NEW)\.tenant_id$", line):
                self.assertIn("source_object_id", lines[index + 1], line)
        self.assertNotIn("engine_record.accept.v1", functions)
        self.assertEqual(functions.count("'engine_record.accept.v2',"), 2)
        self.assertEqual(
            functions.count(
                "source.source_object_id::text || ':' || (item ->> 'record_key')"
            ),
            2,
        )
        self.assertNotIn("belongs to another engine scope", functions)

    def test_the_v1_contract_keeps_its_schema_54_pin(self) -> None:
        # Requests and answers keep their shapes, so the entry points and
        # their expected_schema_version 54 pin stay exactly as 0054 wrote them.
        self.assertIn(
            "request -> 'expected_schema_version' IS DISTINCT FROM '54'::jsonb", INTAKE
        )
        for entry_point in ("accept_engine_records_v1", "read_engine_records_v1"):
            self.assertNotIn(entry_point, SQL)


if __name__ == "__main__":
    unittest.main()
