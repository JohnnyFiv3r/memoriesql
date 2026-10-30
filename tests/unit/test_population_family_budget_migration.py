"""DB-free pins for the population family budget fix; no SQL execution proof.

The runtime proof is tests/runtime/test_population_family_budget.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
(MIGRATION,) = sorted(
    MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_population_family_budget.sql")
)
SQL = MIGRATION.read_text()
ORIGINAL = (MIGRATIONS / "0039_agent_relation_reads.sql").read_text()
NAME = "prepare_relation_sql_population_v2"
ACL = (
    "REVOKE ALL ON FUNCTION memoriesql.prepare_relation_sql_population_v2"
    "(timestamptz,integer,text) FROM PUBLIC;"
)
WITHHOLD = "withheld := withheld || r.relation_id;\n                CONTINUE;"


def body(sql: str, name: str = NAME) -> str:
    start = re.search(
        r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\." + name + r"\(", sql
    )
    assert start is not None, name
    return sql[start.start() : sql.index("END $$;", start.start()) + len("END $$;")]


def bodies(sql: str) -> dict[str, str]:
    """Every function's body (plpgsql or SQL) in sql, last definition winning."""
    found: dict[str, str] = {}
    for start in re.finditer(
        r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.(\w+)\(", sql
    ):
        opened = sql.index("AS $$", start.start()) + len("AS $$")
        found[start.group(1)] = sql[opened : sql.index("$$", opened)]
    return found


def defined(sql: str) -> list[str]:
    return re.findall(r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.(\w+)\(", sql)


class PopulationFamilyBudgetMigration(unittest.TestCase):
    def test_it_redefines_only_the_relation_population_with_its_acl(self) -> None:
        self.assertEqual(defined(SQL), [NAME])
        self.assertIn(ACL, SQL)
        self.assertNotIn("GRANT", SQL)
        # No other migration since 0039 redefined it, so 0039's is the base.
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            if 39 < int(path.name[:4]) < int(MIGRATION.name[:4]):
                self.assertNotIn(NAME, defined(path.read_text()), path.name)

    def test_the_body_is_0039s_with_only_the_budget_arm(self) -> None:
        expected = body(ORIGINAL).replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
        expected = expected.replace(
            "    discovered uuid[]; withheld uuid[] := '{}'; retirement record;\n",
            "    discovered uuid[]; withheld uuid[] := '{}'; retirement record;\n"
            "    budget_message text;\n",
            1,
        )
        old_arm = (
            "        EXCEPTION WHEN insufficient_privilege THEN\n"
            "            withheld := withheld || r.relation_id;\n"
            "            CONTINUE;\n"
            "        END;\n"
        )
        self.assertEqual(expected.count(old_arm), 1)
        new = body(SQL)
        start = new.index("        EXCEPTION\n            WHEN insufficient_privilege")
        stop = new.index("        END;\n", start) + len("        END;\n")
        self.assertEqual(new[:start] + old_arm + new[stop:], expected)

    def test_only_root_budget_is_caught_and_withheld_as_unreadable(self) -> None:
        new = body(SQL)
        start = new.index("        EXCEPTION\n            WHEN insufficient_privilege")
        handler = new[start : new.index("        END;\n", start)]
        # Both arms withhold the family through the same statements.
        self.assertEqual(handler.count(WITHHOLD), 2)
        self.assertEqual(handler.count("WHEN program_limit_exceeded THEN"), 1)
        self.assertIn(
            "GET STACKED DIAGNOSTICS budget_message = MESSAGE_TEXT;\n"
            "                IF budget_message IS DISTINCT FROM 'root_budget' "
            "THEN RAISE; END IF;",
            handler,
        )
        self.assertNotIn("OTHERS", handler)
        # The population's own byte budget still fails the population, as before.
        self.assertEqual(
            new.count("RAISE EXCEPTION 'population_budget_exhausted'"),
            body(ORIGINAL).count("RAISE EXCEPTION 'population_budget_exhausted'"),
        )
        # Only this family block gains a budget arm; the pair-coverage family
        # (which cannot reach root_budget) keeps its single privilege arm.
        self.assertEqual(new.count("WHEN program_limit_exceeded"), 1)
        self.assertEqual(
            new.count("EXCEPTION WHEN insufficient_privilege THEN CONTINUE;"),
            body(ORIGINAL).count(
                "EXCEPTION WHEN insufficient_privilege THEN CONTINUE;"
            ),
        )

    def test_root_budget_has_one_source_reached_only_through_relation_families(
        self,
    ) -> None:
        stream: dict[str, str] = {}
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            stream.update(bodies(path.read_text()))
        raisers = sorted(
            n for n, b in stream.items() if "RAISE EXCEPTION 'root_budget'" in b
        )
        self.assertEqual(raisers, ["qualified_bead_roots_v2"])

        def reaches(entry: str) -> bool:
            seen, todo = set(), [entry]
            while todo:
                name = todo.pop()
                if name in seen or name not in stream:
                    continue
                seen.add(name)
                todo.extend(re.findall(r"memoriesql\.(\w+)\(", stream[name]))
            return "qualified_bead_roots_v2" in seen

        # The family block's two reaching calls; the pair-coverage and bead
        # families' calls cannot reach it.
        for name in ("relation_assertion_row_v2", "relation_evidence_view_v4"):
            self.assertTrue(reaches(name), name)
        for name in (
            "relation_assertion_records_v2",
            "relation_bead_records_v2",
            "relation_task_population_records_v1",
            "query_bead_records_v1",
        ):
            self.assertFalse(reaches(name), name)

    def test_the_migration_is_inventoried_contiguously(self) -> None:
        rows = json.loads((ROOT / "contracts/migration-inventory.json").read_text())[
            "migrations"
        ]
        self.assertEqual(
            [int(row["filename"][:4]) for row in rows], list(range(1, len(rows) + 1))
        )
        self.assertIn(
            {
                "filename": MIGRATION.name,
                "sha256": hashlib.sha256(MIGRATION.read_bytes()).hexdigest(),
            },
            rows,
        )


if __name__ == "__main__":
    unittest.main()
