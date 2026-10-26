"""DB-free pins for Core Memory engine record intake; no SQL execution proof.

The runtime proof is tests/runtime/test_engine_intake.py.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from memoriesql.application.engine_intake import (
    AcceptEngineRecords,
    EngineRecordConflict,
    EngineRecordOutcome,
    EngineRecordRetryable,
    ReadEngineRecords,
)

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "migrations/0054_engine_record_intake.sql").read_text()
TABLES = (
    "engine_events",
    "engine_records",
    "engine_chunks",
    "engine_event_completions",
    "engine_type_decisions",
)
PUBLIC_FUNCTIONS = (
    "accept_engine_records_v1(jsonb)",
    "read_engine_records_v1(jsonb)",
    "engine_vocabulary_v1()",
)


class EngineIntakeMigration(unittest.TestCase):
    def test_tables_are_immutable_forced_rls_and_ungranted(self) -> None:
        created = re.findall(r"CREATE TABLE memoriesql\.(\w+)", SQL)
        self.assertEqual(sorted(created), sorted(TABLES))
        for table in TABLES:
            self.assertIn(
                f"ALTER TABLE memoriesql.{table} ENABLE ROW LEVEL SECURITY;", SQL
            )
            self.assertIn(
                f"ALTER TABLE memoriesql.{table} FORCE ROW LEVEL SECURITY;", SQL
            )
            self.assertRegex(
                SQL,
                rf"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE "
                rf"ON memoriesql\.{table}\nFOR EACH ROW EXECUTE FUNCTION "
                r"memoriesql\.reject_immutable_change\(\);",
            )
        self.assertNotRegex(
            SQL,
            r"GRANT (SELECT|INSERT|UPDATE|DELETE|ALL)\b[^;]* ON memoriesql\.engine_",
        )

    def test_only_the_three_entry_points_are_executable_by_the_application(
        self,
    ) -> None:
        functions = re.findall(r"CREATE FUNCTION memoriesql\.(\w+)\(", SQL)
        for name in functions:
            self.assertRegex(SQL, rf"REVOKE ALL ON FUNCTION memoriesql\.{name}\(")
        grants = re.findall(
            r"GRANT EXECUTE ON FUNCTION memoriesql\.(.+?) TO (\w+);", SQL
        )
        self.assertEqual(
            sorted(grants),
            sorted((name, "memoriesql_application") for name in PUBLIC_FUNCTIONS),
        )
        for definer in re.finditer(r"SECURITY DEFINER\n(.*)\n", SQL):
            self.assertEqual(
                definer.group(1), "SET search_path = pg_catalog, memoriesql"
            )

    def test_the_slice_authors_and_audits_nothing_new(self) -> None:
        for forbidden in (
            "application_authorize_resource(",
            "authorization_audit_events",
            "semantic_tasks",
            "bead_versions",
            "INSERT INTO memoriesql.beads",
            "INSERT INTO memoriesql.source_units",
        ):
            self.assertNotIn(forbidden, SQL)

    def test_delivery_is_a_new_capability_for_owner_and_service_roles(self) -> None:
        self.assertIn("'engine.records.deliver'", SQL)
        self.assertIn("('personal_owner', 'engine.records.deliver')", SQL)
        self.assertIn("('background_service', 'engine.records.deliver')", SQL)
        self.assertNotIn("('paired_agent', 'engine.records.deliver')", SQL)


class EngineIntakeContracts(unittest.TestCase):
    def test_batches_are_bounded(self) -> None:
        record: dict[str, Any] = {"record_key": "chunk:x"}
        AcceptEngineRecords(records=(record,) * 256)
        empty: tuple[dict[str, Any], ...] = ()
        for records in (empty, (record,) * 257):
            with self.assertRaises(ValidationError):
                AcceptEngineRecords(records=records)
        with self.assertRaises(ValidationError):
            AcceptEngineRecords.model_validate(
                {
                    "contract_version": 1,
                    "expected_schema_version": 53,
                    "records": [record],
                }
            )

    def test_outcomes_are_a_closed_union(self) -> None:
        adapter: TypeAdapter[object] = TypeAdapter(EngineRecordOutcome)
        self.assertEqual(
            adapter.validate_python({"outcome": "conflict", "stored": {"a": 1}}),
            EngineRecordConflict(outcome="conflict", stored={"a": 1}),
        )
        self.assertIsInstance(
            adapter.validate_python(
                {"outcome": "retryable", "retry_after_ms": 60000, "reason": "later"}
            ),
            EngineRecordRetryable,
        )
        for bad in ({"outcome": "maybe"}, {"outcome": "rejected", "reason": ""}):
            with self.assertRaises(ValidationError):
                adapter.validate_python(bad)

    def test_reads_are_bounded(self) -> None:
        ReadEngineRecords(engine_scope="acme", limit=1024)
        for bad in ({"engine_scope": " "}, {"engine_scope": "acme", "limit": 1025}):
            with self.assertRaises(ValidationError):
                ReadEngineRecords.model_validate(bad)


if __name__ == "__main__":
    unittest.main()
