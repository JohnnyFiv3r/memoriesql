"""Database-free structure of the work-deadline migration (lock waits excluded)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))
TARGET = next(
    m for m in MIGRATIONS if m.name.endswith("_work_deadlines_after_lock_waits.sql")
)
SQL = TARGET.read_text()
NAMES = (
    "write_evidence_package_v1",
    "read_evidence_package_v1",
    "recover_transcript_fold_v1",
    "materialize_logical_unit_v2",
    "materialize_declared_scope_v1",
    "inspect_stored_bead_v1",
)
START = "lock_at:=clock_timestamp();"
STOP = "waited:=waited+(clock_timestamp()-lock_at);"
# Statements whose time is a wait: keyed locks, row locks and authorization,
# which takes the tenant fence shared and locks the caller's role row.
WAIT = re.compile(r"pg_advisory_xact_lock|FOR UPDATE|FOR SHARE|_authorize\(")
DECLARATIONS = (
    ("\n    waited interval := interval '0';\n    lock_at timestamptz;", ""),
    ("\n    waited interval:=interval '0';\n    lock_at timestamptz;", ""),
    (" waited interval:=interval '0'; lock_at timestamptz;", ""),
)
CHECKS = (
    (
        "clock_timestamp()-started-waited>interval '2 seconds'",
        "clock_timestamp()-started>interval '2 seconds'",
    ),
    (
        "clock_timestamp()-waited>started+interval '2 seconds'",
        "clock_timestamp()>started+interval '2 seconds'",
    ),
)


def statement(text: str, name: str, create: str) -> str:
    start = [
        m.start()
        for m in re.finditer(
            re.escape(create + "FUNCTION memoriesql." + name + "("), text
        )
    ][-1]
    tag = re.search(r"\bAS\s+(\$[a-z_]*\$)", text[start : start + 3000])
    assert tag is not None
    body = start + tag.end()
    return text[start : text.index(";", text.index(tag.group(1), body) + 1) + 1]


def previous(name: str) -> str:
    """The definition the migration replaces: the latest earlier one."""
    found = None
    for migration in MIGRATIONS:
        if migration == TARGET:
            break
        text = migration.read_text()
        for create in ("CREATE ", "CREATE OR REPLACE "):
            if create + "FUNCTION memoriesql." + name + "(" in text:
                found = statement(text, name, create)
    assert found is not None
    return found


def reverted(text: str) -> str:
    """The new definition with every timing addition removed."""
    for old, new in DECLARATIONS:
        text = text.replace(old, new)
    text = re.sub(r"(?m)^[ \t]*" + re.escape(START) + r"\n", "", text)
    text = re.sub(r"(?m)^[ \t]*" + re.escape(STOP) + r"\n", "", text)
    text = text.replace(START + " ", "").replace(" " + STOP, "")
    for new, old in CHECKS:
        text = text.replace(new, old)
    return text


class WorkDeadlineMigration(unittest.TestCase):
    def test_each_function_is_its_previous_text_plus_timing_only(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                before = previous(name)
                after = statement(SQL, name, "CREATE OR REPLACE ")
                self.assertEqual(
                    reverted(after),
                    before.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
                )

    def test_every_bracket_encloses_a_wait(self) -> None:
        for name in NAMES:
            text = statement(SQL, name, "CREATE OR REPLACE ")
            brackets = re.findall(
                re.escape(START) + r"(.*?)" + re.escape(STOP), text, re.S
            )
            with self.subTest(name=name):
                self.assertTrue(brackets)
                self.assertEqual(text.count(START), len(brackets))
                for enclosed in brackets:
                    self.assertRegex(enclosed, WAIT)
                    self.assertNotIn("interval '2 seconds'", enclosed)

    def test_the_deadline_is_kept_and_every_check_excludes_waits(self) -> None:
        for name in NAMES:
            before = previous(name)
            after = statement(SQL, name, "CREATE OR REPLACE ")
            with self.subTest(name=name):
                self.assertEqual(
                    after.count("interval '2 seconds'"),
                    before.count("interval '2 seconds'"),
                )
                self.assertGreater(after.count("interval '2 seconds'"), 0)
                for _, unadjusted in CHECKS:
                    self.assertNotIn(unadjusted, after)

    def test_the_migration_replaces_these_functions_and_nothing_else(self) -> None:
        statements = re.sub(r"(?m)^--.*\n", "", SQL).strip()
        replaced = re.findall(
            r"CREATE OR REPLACE FUNCTION memoriesql\.([a-z0-9_]+)\(", statements
        )
        self.assertEqual(tuple(replaced), NAMES)
        self.assertEqual(
            statements,
            "\n\n".join(statement(SQL, name, "CREATE OR REPLACE ") for name in NAMES),
        )
        # CREATE OR REPLACE keeps owners, grants and comments.
        self.assertNotRegex(SQL, r"(?im)^\s*(GRANT|REVOKE|ALTER|COMMENT|DROP)\b")


if __name__ == "__main__":
    unittest.main()
