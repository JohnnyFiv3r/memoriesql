"""DB-free pins for sealed unit query facts; no SQL execution proof."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"


def named(stem: str) -> Path:
    # Found by name, not number: forward numbers are assigned at landing.
    found = sorted(MIGRATIONS.glob(f"[0-9][0-9][0-9][0-9]_{stem}.sql"))
    return found[0] if found else MIGRATIONS / f"{stem}.sql"


MIGRATION = named("sealed_unit_query_facts")
CLOSURE = named("hidden_package_length")
SQL = MIGRATION.read_text() if MIGRATION.exists() else ""
CLOSURE_SQL = CLOSURE.read_text() if CLOSURE.exists() else ""
ORIGINAL = (MIGRATIONS / "0037_observation_sql_population.sql").read_text()
FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.([a-z0-9_]+)\((.*?)\)"
    r"(.*?)AS \$\$(.*?)\$\$;",
    re.DOTALL,
)
KEY = re.compile(r"'([a-z_0-9]+)',")


def compact(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


def functions(sql: str) -> dict[str, tuple[str, str, str]]:
    return {m[1]: (m[2], m[3], m[4]) for m in FUNCTION.finditer(sql)}


def latest() -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for name, (_, _, body) in functions(path.read_text()).items():
            found[name] = (path.name, body)
    return found


def built_keys(body: str) -> list[str]:
    """Top-level keys of the package value a query_unit_package_v1 body builds."""
    start = body.index("jsonb_build_object(") + len("jsonb_build_object(")
    arguments, depth, quoted, current = [], 0, False, ""
    for char in body[start:]:
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            if depth == 0:
                break
            depth -= 1
        if not quoted and depth == 0 and char == ",":
            arguments.append(current.strip())
            current = ""
            continue
        current += char
    arguments.append(current.strip())
    keys = arguments[0::2]
    assert all(KEY.fullmatch(key + ",") for key in keys), keys
    return [key.strip("'") for key in keys]


class SealedUnitFactsMigration(unittest.TestCase):
    def test_one_forward_resource_after_the_hidden_length_closure(self) -> None:
        self.assertTrue(MIGRATION.exists() and CLOSURE.exists())
        self.assertGreater(MIGRATION.name, CLOSURE.name)
        self.assertEqual(
            re.findall(r"CREATE (OR REPLACE )?FUNCTION memoriesql\.(\w+)", SQL),
            [
                ("", "unit_query_facts_v1"),
                ("", "record_unit_query_facts"),
                ("OR REPLACE ", "query_unit_records_v1"),
                ("OR REPLACE ", "query_unit_package_v1"),
            ],
        )
        self.assertEqual(
            re.findall(r"CREATE TABLE memoriesql\.(\w+)", SQL),
            ["logical_unit_query_facts"],
        )
        self.assertNotIn(MIGRATION.name[:4], SQL)
        self.assertNotIn(CLOSURE.name[:4], SQL)

    def test_facts_are_protected_and_insert_only(self) -> None:
        text = compact(SQL)
        for statement in (
            "ALTER TABLE memoriesql.logical_unit_query_facts ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE memoriesql.logical_unit_query_facts FORCE ROW LEVEL SECURITY;",
            "REVOKE ALL ON memoriesql.logical_unit_query_facts FROM PUBLIC,memoriesql_application;",
            "CREATE TRIGGER unit_query_facts_immutable BEFORE UPDATE OR DELETE ON "
            "memoriesql.logical_unit_query_facts FOR EACH ROW EXECUTE FUNCTION "
            "memoriesql.guard_materialization_identity();",
            "CREATE TRIGGER unit_query_facts_record AFTER INSERT ON "
            "memoriesql.logical_unit_materializations FOR EACH ROW EXECUTE FUNCTION "
            "memoriesql.record_unit_query_facts();",
            "PRIMARY KEY(tenant_id,source_unit_id)",
            "record_sha256 text NOT NULL CHECK(record_sha256 ~ '^[a-f0-9]{64}$')",
        ):
            with self.subTest(statement=statement[:60]):
                self.assertIn(statement, text)
        for name in ("unit_query_facts_v1", "record_unit_query_facts"):
            self.assertIn(f"REVOKE ALL ON FUNCTION memoriesql.{name}(", text)
        # The materialization it describes is itself insert-only (0017).
        self.assertIn(
            "CREATE TRIGGER logical_unit_binding_immutable BEFORE UPDATE OR DELETE ON "
            "memoriesql.logical_unit_materializations",
            (MIGRATIONS / "0017_logical_unit_materialization.sql").read_text(),
        )

    def test_facts_are_exactly_the_closure_digest_and_text_gate(self) -> None:
        facts = compact(functions(SQL)["unit_query_facts_v1"][2])
        closure = functions(CLOSURE_SQL)
        records = compact(closure["query_unit_records_v1"][2])
        package = compact(closure["query_unit_package_v1"][2])
        digest = (
            "memoriesql.lifecycle_hash_v1(pkg||jsonb_build_object('source_unit_id',id))"
        )
        self.assertIn(digest, records)
        self.assertIn(digest.replace("(pkg||", "(q.pkg||"), facts)
        gate = "(q.pkg->>'normalized')::boolean"
        self.assertIn("CASE WHEN " + gate, package)
        self.assertIn(gate, facts)
        self.assertIn(
            "FROM memoriesql.query_unit_package_record_v1(t,id) AS q(pkg)", facts
        )
        self.assertIn("WHERE q.pkg IS NOT NULL", facts)

    def test_trigger_and_backfill_share_the_one_definition(self) -> None:
        trigger = compact(functions(SQL)["record_unit_query_facts"][2])
        self.assertEqual(
            trigger,
            "BEGIN INSERT INTO memoriesql.logical_unit_query_facts SELECT f.* FROM "
            "memoriesql.unit_query_facts_v1(NEW.tenant_id,NEW.source_unit_id) f "
            "WHERE f.source_unit_id IS NOT NULL; RETURN NULL; END",
        )
        outside = compact(FUNCTION.sub("", SQL))
        backfill = (
            "SET LOCAL row_security = off; INSERT INTO memoriesql.logical_unit_query_facts "
            "SELECT f.* FROM memoriesql.logical_unit_materializations m CROSS JOIN LATERAL "
            "memoriesql.unit_query_facts_v1(m.tenant_id,m.source_unit_id) f "
            "WHERE f.source_unit_id IS NOT NULL; SET LOCAL row_security = on;"
        )
        self.assertEqual(outside.count(backfill), 1)
        # The trigger exists before the backfill, so no materialization is missed.
        self.assertLess(
            outside.index("CREATE TRIGGER unit_query_facts_record"),
            outside.index(backfill),
        )

    def test_query_paths_read_no_part_row_except_normalized_text(self) -> None:
        installed = latest()
        for name in ("query_unit_records_v1", "query_unit_package_v1"):
            self.assertEqual(installed[name][0], MIGRATION.name)
        records = compact(installed["query_unit_records_v1"][1])
        self.assertNotIn("evidence_package_parts", records)
        self.assertNotIn("query_unit_package_record_v1", records)
        package = compact(installed["query_unit_package_v1"][1])
        self.assertNotIn("query_unit_package_record_v1", package)
        self.assertEqual(package.count("evidence_package_parts"), 1)
        self.assertIn(
            "'text',CASE WHEN f.normalized THEN (SELECT string_agg(pp.content,'' "
            "ORDER BY pp.ordinal) FROM memoriesql.evidence_package_parts pp "
            "WHERE pp.tenant_id=t AND pp.package_id=p.package_id) END)",
            package,
        )
        self.assertIn(
            "FROM memoriesql.logical_unit_query_facts f JOIN "
            "memoriesql.logical_unit_materializations m ON m.tenant_id=f.tenant_id "
            "AND m.source_unit_id=f.source_unit_id JOIN memoriesql.evidence_packages p "
            "ON p.tenant_id=m.tenant_id AND p.package_id=m.package_id "
            "WHERE f.tenant_id=t AND f.source_unit_id=id",
            package,
        )
        # Only the facts definition, the trigger and the backfill build the record.
        self.assertEqual(
            {
                n
                for n, (_, body) in installed.items()
                if "query_unit_package_record_v1(" in body
            },
            {"unit_query_facts_v1"},
        )

    def test_records_differ_from_the_closure_only_in_the_digest_source(self) -> None:
        closure = compact(functions(CLOSURE_SQL)["query_unit_records_v1"][2])
        facts = compact(functions(SQL)["query_unit_records_v1"][2])
        edits = (
            ("DECLARE u record; pkg jsonb;", "DECLARE u record; digest text;"),
            (
                "pkg:=memoriesql.query_unit_package_record_v1(t,id);",
                "SELECT f.record_sha256 INTO digest FROM memoriesql.logical_unit_query_facts f "
                "WHERE f.tenant_id=t AND f.source_unit_id=id;",
            ),
            ("CASE WHEN pkg IS NULL THEN", "CASE WHEN digest IS NULL THEN"),
            (
                "'row',jsonb_build_object('content_sha256', "
                "memoriesql.lifecycle_hash_v1(pkg||jsonb_build_object('source_unit_id',id)))))",
                "'row',jsonb_build_object('content_sha256',digest)))",
            ),
        )
        for old, new in edits:
            self.assertEqual(closure.count(old), 1, old)
            closure = closure.replace(old, new)
        self.assertEqual(facts, closure)
        before, after = functions(CLOSURE_SQL), functions(SQL)
        for name in ("query_unit_records_v1", "query_unit_package_v1"):
            with self.subTest(function=name):
                self.assertEqual(
                    tuple(map(compact, before[name][:2])),
                    tuple(map(compact, after[name][:2])),
                )

    def test_package_value_keeps_every_key_but_the_unread_parts(self) -> None:
        original = built_keys(compact(functions(ORIGINAL)["query_unit_package_v1"][2]))
        self.assertIn("parts", original)
        facts = built_keys(compact(functions(SQL)["query_unit_package_v1"][2]))
        self.assertEqual(sorted(facts), sorted(k for k in original if k != "parts"))
        # No installed definition reads 'parts' from a package value.
        for name, (_, body) in latest().items():
            with self.subTest(function=name):
                text = compact(body)
                for read in ("pkg->'parts'", "pkg#>'{parts", "pkg->>'parts'"):
                    self.assertNotIn(read, text)

    def test_inventory_slot_is_exact(self) -> None:
        rows = json.loads((ROOT / "contracts/migration-inventory.json").read_text())[
            "migrations"
        ]
        self.assertEqual(
            [int(row["filename"][:4]) for row in rows], list(range(1, len(rows) + 1))
        )
        self.assertEqual(
            rows[int(MIGRATION.name[:4]) - 1],
            {
                "filename": MIGRATION.name,
                "sha256": hashlib.sha256(MIGRATION.read_bytes()).hexdigest(),
            },
        )


if __name__ == "__main__":
    unittest.main()
