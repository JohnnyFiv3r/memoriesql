"""DB-free pins for Core Memory engine record intake (0055 and its 0056 privacy
fix); no SQL execution proof.

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
    EngineRecordPage,
    EngineRecordRetryable,
    ReadEngineRecords,
)

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "migrations/0055_engine_record_intake.sql").read_text()
PRIVACY = (ROOT / "migrations/0056_engine_intake_privacy.sql").read_text()
REPLACED = (
    "engine_accept_record_v1",
    "accept_engine_records_v1",
    "read_engine_records_v1",
)
UNAVAILABLE = (
    "'the engine scope is not enrolled, or this credential may not deliver to "
    "and read it'"
)
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
        for bad in (
            {"outcome": "maybe"},
            {"outcome": "rejected", "reason": ""},
            # Every deliverer may read its scope, so a conflict always shows it.
            {"outcome": "conflict"},
            {"outcome": "conflict", "stored": None},
        ):
            with self.assertRaises(ValidationError):
                adapter.validate_python(bad)

    def test_reads_are_bounded(self) -> None:
        ReadEngineRecords(engine_scope="acme", limit=1024)
        ReadEngineRecords(engine_scope="acme", after="chunk:" + "0" * 36)
        for bad in (
            {"engine_scope": " "},
            {"engine_scope": "acme", "limit": 1025},
            {"engine_scope": "acme", "after": 0},
            {"engine_scope": "acme", "after": " "},
            {"engine_scope": "acme", "after": "x" * 129},
        ):
            with self.assertRaises(ValidationError):
                ReadEngineRecords.model_validate(bad)

    def test_cursors_are_opaque_strings(self) -> None:
        for cursor in (None, "chunk:" + "0" * 36):
            self.assertEqual(
                EngineRecordPage.model_validate({"records": [], "next": cursor}).next,
                cursor,
            )
        # A page always carries its cursor, and a cursor is never a number.
        bad_pages: tuple[dict[str, Any], ...] = (
            {"records": [], "next": 7},
            {"records": []},
        )
        for bad in bad_pages:
            with self.assertRaises(ValidationError):
                EngineRecordPage.model_validate(bad)

    def test_every_engine_key_names_its_scope(self) -> None:
        for table in TABLES:
            block = SQL[SQL.index(f"CREATE TABLE memoriesql.{table} (") :]
            block = block[: block.index("\n);")]
            (key,) = re.findall(r"PRIMARY KEY \(([^)]*)\)", block)
            self.assertTrue(key.startswith("tenant_id, source_object_id, "), table)
        references = re.findall(r"REFERENCES memoriesql\.engine_\w+ \(([^)]*)\)", SQL)
        self.assertEqual(len(references), 7)
        for reference in references:
            self.assertTrue(
                reference.startswith("tenant_id, source_object_id, "), reference
            )
        self.assertIn(
            "ON memoriesql.engine_type_decisions (tenant_id, source_object_id, chunk_id)",
            SQL,
        )

    def test_every_intake_lookup_and_receipt_is_scoped(self) -> None:
        lines = SQL.splitlines()
        for index, line in enumerate(lines):
            match = re.search(r"\b(\w+)\.tenant_id = (authority|NEW)\.tenant_id$", line)
            if match and match.group(1) not in {"source", "credential"}:
                self.assertIn("source_object_id", lines[index + 1], line)
        self.assertEqual(
            SQL.count(
                "source.source_object_id::text || ':' || (item ->> 'record_key')"
            ),
            2,
        )
        self.assertNotIn("belongs to another engine scope", SQL)


def body(name: str) -> str:
    start = PRIVACY.index(f"FUNCTION memoriesql.{name}(")
    return PRIVACY[start : PRIVACY.index("\n$$;\n", start)]


class EngineIntakePrivacyMigration(unittest.TestCase):
    def test_one_revoked_helper_and_three_replacements_widen_nothing(self) -> None:
        self.assertEqual(
            re.findall(r"^CREATE FUNCTION memoriesql\.(\w+)\(", PRIVACY, re.M),
            ["engine_deliverable_source_v1"],
        )
        self.assertEqual(
            re.findall(
                r"^CREATE OR REPLACE FUNCTION memoriesql\.(\w+)\(", PRIVACY, re.M
            ),
            list(REPLACED),
        )
        for name in ("engine_deliverable_source_v1", *REPLACED):
            self.assertRegex(
                PRIVACY,
                rf"REVOKE ALL ON FUNCTION memoriesql\.{name}\([^;]*FROM PUBLIC;",
            )
        # CREATE OR REPLACE keeps only the owner and grants, so every function
        # states its definer, search path and 0055's volatility again.
        for name, volatility in (
            ("engine_deliverable_source_v1", "VOLATILE"),
            ("engine_accept_record_v1", "VOLATILE"),
            ("accept_engine_records_v1", "VOLATILE"),
            ("read_engine_records_v1", "STABLE"),
        ):
            self.assertRegex(
                body(name),
                rf"\n(LANGUAGE \w+) {volatility} SECURITY DEFINER\n"
                r"SET search_path = pg_catalog, memoriesql\n",
            )
        for definer in re.finditer(r"SECURITY DEFINER\n(.*)\n", PRIVACY):
            self.assertEqual(
                definer.group(1), "SET search_path = pg_catalog, memoriesql"
            )
        for widening in ("GRANT ", "CREATE TABLE", "ALTER TABLE", "INSERT INTO"):
            self.assertNotIn(widening, PRIVACY)
        self.assertIn("SET lock_timeout = '500ms'", body("accept_engine_records_v1"))

    def test_one_predicate_decides_both_the_locks_and_each_answer(self) -> None:
        helper = body("engine_deliverable_source_v1")
        for check in (
            "'engine.records.deliver', 'write'",
            "'write', instant.checked_at",
            "'source.read', 'read'",
            "'read', instant.checked_at",
        ):
            self.assertEqual(helper.count(check), 1, check)
        # Both windows are judged at one instant.
        self.assertEqual(helper.count("clock_timestamp()"), 1)
        batch = body("accept_engine_records_v1")
        record = body("engine_accept_record_v1")
        for caller in (batch, record):
            self.assertEqual(
                caller.count("memoriesql.engine_deliverable_source_v1("), 1
            )
            self.assertNotIn("current_context_source_authorized", caller)
            self.assertNotIn("engine_scope_source_v1", caller)
        # The fence and credential check still come before any scope is resolved.
        self.assertLess(
            batch.index("semantic_outcome_authority"),
            batch.index("authentication_credentials"),
        )
        self.assertLess(
            batch.index("authentication_credentials"),
            batch.index("engine_deliverable_source_v1"),
        )
        # One branch, one constant reason, before the lock and every lookup.
        self.assertEqual(PRIVACY.count(UNAVAILABLE), 1)
        self.assertEqual(record.count("engine_retryable("), 1)
        answer = record.index(UNAVAILABLE)
        for later in (
            "engine_lock_scope_v1(",
            "FROM memoriesql.engine_records",
            "engine_accept_chunk_v1(",
            "engine_accept_type_decision_v1(",
        ):
            self.assertLess(answer, record.index(later), later)
        for gone in ("is not enrolled in this workspace", "may not deliver to engine"):
            self.assertNotIn(gone, PRIVACY)

    def test_a_conflict_always_shows_the_record_its_reader_may_read(self) -> None:
        record = body("engine_accept_record_v1")
        self.assertEqual(
            record.count(
                "jsonb_build_object('outcome', 'conflict', 'stored', existing.record)"
            ),
            1,
        )
        self.assertNotIn("'outcome', 'conflict')", PRIVACY)

    def test_reads_return_no_global_sequence(self) -> None:
        read = body("read_engine_records_v1")
        returned = read[read.index("RETURN jsonb_build_object(") :]
        self.assertNotIn("seq", returned)
        self.assertIn("'next', COALESCE(last_key, after_key)", returned)
        for message in re.findall(r"RAISE EXCEPTION ('[^']*')", read):
            self.assertNotIn("seq", message)
        self.assertNotRegex(read, r"(max|min|count)\([^)]*record_seq")
        # The cursor is examined only once the caller may read the scope.
        self.assertLess(
            read.rindex("USING ERRCODE = '42501'"), read.index("request -> 'after'")
        )


if __name__ == "__main__":
    unittest.main()
