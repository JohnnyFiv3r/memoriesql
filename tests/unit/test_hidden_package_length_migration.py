"""DB-free pins for the hidden package length closure; no SQL execution proof."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
# Found by name, not number: the forward number is the next free one at landing.
FOUND = sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_hidden_package_length.sql"))
MIGRATION = FOUND[0] if FOUND else MIGRATIONS / "hidden_package_length.sql"
SQL = MIGRATION.read_text() if MIGRATION.exists() else ""
ORIGINAL = (MIGRATIONS / "0037_observation_sql_population.sql").read_text()
FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.([a-z0-9_]+)\((.*?)\)"
    r"(.*?)AS \$\$(.*?)\$\$;",
    re.DOTALL,
)
NORMALIZED = (
    "p.sealed_receipt_id IS NOT NULL "
    "AND m.package_pin->>'inventory_sha256'=p.inventory_hash "
    "AND count(pp.part_id)=(m.package_pin->>'required_parts')::integer "
    "AND bool_and(pp.inventory->>'derivation'='producer_normalized')"
)
TEXT_AGGREGATE = (
    "'text',CASE WHEN " + NORMALIZED + " "
    "THEN string_agg(pp.content,'' ORDER BY pp.ordinal) END,"
)


def compact(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


def functions(sql: str) -> dict[str, tuple[str, str, str]]:
    return {m[1]: (m[2], m[3], m[4]) for m in FUNCTION.finditer(sql)}


def latest() -> dict[str, tuple[str, str]]:
    """Each function's installed definition: its last migration and body."""
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for name, (_, _, body) in functions(path.read_text()).items():
            found[name] = (path.name, body)
    return found


class HiddenPackageLengthMigration(unittest.TestCase):
    def test_one_forward_resource_replaces_only_the_two_unit_functions(
        self,
    ) -> None:
        self.assertEqual(len(FOUND), 1)
        self.assertGreater(MIGRATION.name, "0046_agent_sql_result_helpers.sql")
        self.assertEqual(
            re.findall(r"CREATE (OR REPLACE )?FUNCTION memoriesql\.(\w+)", SQL),
            [
                ("", "query_unit_package_record_v1"),
                ("OR REPLACE ", "query_unit_package_v1"),
                ("OR REPLACE ", "query_unit_records_v1"),
            ],
        )
        outside = compact(FUNCTION.sub("", SQL))
        self.assertEqual(
            outside,
            "REVOKE ALL ON FUNCTION "
            "memoriesql.query_unit_package_record_v1(uuid,uuid) FROM PUBLIC;",
        )
        # Renumbering at landing renames the file and touches no SQL.
        self.assertNotIn(MIGRATION.name[:4], SQL)

    def test_replacements_keep_their_installed_signatures(self) -> None:
        before, after = functions(ORIGINAL), functions(SQL)
        for name in ("query_unit_package_v1", "query_unit_records_v1"):
            with self.subTest(function=name):
                self.assertEqual(
                    tuple(map(compact, before[name][:2])),
                    tuple(map(compact, after[name][:2])),
                )
        self.assertEqual(
            tuple(map(compact, before["query_unit_package_v1"][:2])),
            tuple(map(compact, after["query_unit_package_record_v1"][:2])),
        )

    def test_provenance_record_is_0037s_package_value_without_text(self) -> None:
        original = compact(functions(ORIGINAL)["query_unit_package_v1"][2])
        self.assertEqual(original.count(TEXT_AGGREGATE + " "), 1)
        # 0037 serves text exactly when its package is normalized.
        self.assertEqual(original.count("'normalized'," + NORMALIZED + ","), 1)
        record = compact(functions(SQL)["query_unit_package_record_v1"][2])
        self.assertEqual(record, original.replace(TEXT_AGGREGATE + " ", ""))
        self.assertNotIn("content,", record)
        self.assertNotIn("string_agg", record)

    def test_package_text_is_read_only_on_the_normalized_branch(self) -> None:
        # An aggregate inside CASE runs for every group; this sub-select runs
        # only when its branch is taken, so raw parts are never concatenated.
        self.assertEqual(
            compact(functions(SQL)["query_unit_package_v1"][2]),
            "SELECT q.pkg||jsonb_build_object('text',"
            "CASE WHEN (q.pkg->>'normalized')::boolean "
            "THEN (SELECT string_agg(pp.content,'' ORDER BY pp.ordinal) "
            "FROM memoriesql.evidence_package_parts pp "
            "WHERE pp.tenant_id=t AND pp.package_id=(q.pkg->>'package_id')::uuid) "
            "END) "
            "FROM memoriesql.query_unit_package_record_v1(t,id) AS q(pkg) "
            "WHERE q.pkg IS NOT NULL",
        )

    def test_unit_package_records_hold_only_a_fixed_width_digest(self) -> None:
        original = compact(functions(ORIGINAL)["query_unit_records_v1"][2])
        replaced = compact(functions(SQL)["query_unit_records_v1"][2])
        # The digest is of exactly the row 0037 stored, so every frame still
        # binds the full provenance record.
        edits = (
            (
                "pkg:=memoriesql.query_unit_package_v1(t,id);",
                "pkg:=memoriesql.query_unit_package_record_v1(t,id);",
            ),
            (
                "'row',(pkg-'text')||jsonb_build_object('source_unit_id',id)))",
                "'row',jsonb_build_object('content_sha256', "
                "memoriesql.lifecycle_hash_v1(pkg||"
                "jsonb_build_object('source_unit_id',id)))))",
            ),
        )
        for old, new in edits:
            self.assertEqual(original.count(old), 1, old)
            original = original.replace(old, new)
        self.assertEqual(replaced, original)
        # Authorization still precedes every package read.
        self.assertLess(
            replaced.index("'source.read','read')"),
            replaced.index("query_unit_package_record_v1"),
        )

    def test_every_installed_unit_package_record_is_fixed_width(self) -> None:
        installed = latest()
        # This closure or a later forward one (sealed unit facts) installs them.
        self.assertGreaterEqual(installed["query_unit_records_v1"][0], MIGRATION.name)
        self.assertGreaterEqual(installed["query_unit_package_v1"][0], MIGRATION.name)
        self.assertEqual(
            {name for name, (_, body) in installed.items() if "'unit_package'" in body},
            {
                "query_unit_records_v1",
                # 0046 helper frames: a {kind,id,content_sha256} pin and its read.
                "prepare_query_inspection_v1",
                "read_query_helper_artifact_v1",
            },
        )
        # The full record is only ever hashed, or gates the population's text.
        builders = {
            name
            for name, (_, body) in installed.items()
            if "query_unit_package_record_v1(" in body
        }
        self.assertTrue(builders)
        for name in builders - {"query_unit_package_v1"}:
            with self.subTest(builder=name):
                self.assertIn("lifecycle_hash_v1(", installed[name][1])
        # Only the population asks for package text, and only when normalized.
        readers = {
            name
            for name, (_, body) in installed.items()
            if "query_unit_package_v1(" in body
        }
        self.assertEqual(readers, {"prepare_query_sql_population_v2"})
        population = compact(installed["prepare_query_sql_population_v2"][1])
        self.assertEqual(population.count("pkg->>'text'"), 1)
        self.assertIn(
            "CASE WHEN (pkg->>'normalized')::boolean THEN pkg->>'text' END",
            population,
        )

    def test_digest_records_keep_the_saved_frame_shape(self) -> None:
        # 0044 refuses any saved record other than {kind,id,row} with an object
        # row; the digest record is exactly that shape.
        installed = compact(latest()["query_saved_records_authorized_v1"][1])
        for check in (
            "v-ARRAY['kind','id','row']<>'{}'::jsonb",
            "jsonb_typeof(v->'row') IS DISTINCT FROM 'object'",
            "EXCEPT SELECT value FROM jsonb_array_elements(rebuilt)",
        ):
            self.assertIn(check, installed)
        self.assertIn(
            "jsonb_build_object( 'kind','unit_package','id',id::text,'row',"
            "jsonb_build_object('content_sha256',",
            compact(functions(SQL)["query_unit_records_v1"][2]),
        )

    def test_inventory_appends_only_this_migration(self) -> None:
        inventory = json.loads(
            (ROOT / "contracts/migration-inventory.json").read_text()
        )
        rows = inventory["migrations"]
        self.assertEqual(inventory["default_policy"], "deny")
        self.assertEqual(
            [int(row["filename"][:4]) for row in rows], list(range(1, len(rows) + 1))
        )
        # Later forward migrations may follow; this one keeps its own slot.
        self.assertEqual(
            rows[int(MIGRATION.name[:4]) - 1],
            {
                "filename": MIGRATION.name,
                "sha256": hashlib.sha256(MIGRATION.read_bytes()).hexdigest(),
            },
        )
        self.assertEqual(rows[45]["filename"], "0046_agent_sql_result_helpers.sql")
        for row in rows:
            data = (MIGRATIONS / row["filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])


if __name__ == "__main__":
    unittest.main()
