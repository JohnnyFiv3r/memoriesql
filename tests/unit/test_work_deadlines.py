"""The adapters' wall-clock bounds equal work plus every excluded lock wait."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from memoriesql.infrastructure.postgres.work_deadlines import (
    LOCK_TIMEOUT_MS,
    LOCK_WAITS,
    WORK_DEADLINE_MS,
    LockWaits,
    statement_timeout_for,
    statement_timeout_ms,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))
ADAPTERS = ROOT / "src/memoriesql/infrastructure/postgres"
AUTHORIZE = (
    "evidence_package_authorize",
    "source_stable_authorize",
    "revisiting_source_authorize",
)
# Pinned so that changing a lock, a lock timeout or the deadline is reviewed here.
PINNED_MS = {
    "write_evidence_package_v1": 4_000,
    "read_evidence_package_v1": 2_500,
    "recover_transcript_fold_v1": 3_000,
    "materialize_logical_unit_v2": 6_000,
    "materialize_declared_scope_v1": 6_000,
    "inspect_stored_bead_v1": 4_000,
}


def latest(name: str) -> str:
    """The installed definition of memoriesql.`name`: its last CREATE statement."""
    found = None
    for migration in MIGRATIONS:
        text = migration.read_text()
        for match in re.finditer(
            r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\." + name + r"\(", text
        ):
            tag = re.search(
                r"\bAS\s+(\$[a-z_]*\$)", text[match.start() : match.start() + 3000]
            )
            assert tag is not None
            body = match.start() + tag.end()
            found = text[
                match.start() : text.index(";", text.index(tag.group(1), body) + 1) + 1
            ]
    assert found is not None, name
    return found


def capability_rows(statement: str) -> int:
    """Capability rows a `FOR SHARE OF cap` statement can lock."""
    listed = re.search(r"capability_key IN \(([^)]*)\)", statement)
    if listed:
        return len(listed.group(1).split(","))
    return len(re.findall(r"capability_key='[^']+'", statement))


def derived(name: str) -> LockWaits:
    """The waits the definition itself shows, counted from its text."""
    text = latest(name)
    statements = text.split(";")
    role_locks = [s for s in statements if "FOR SHARE OF cap" in s]
    other_rows = [
        s
        for s in statements
        if re.search(r"FOR (UPDATE|SHARE)", s) and s not in role_locks
    ]
    via_helper = 0
    if re.search(
        r"memoriesql\.(revisiting_source_authorize|source_stable_authorize)\(", text
    ):
        helper = latest("revisiting_source_authorize").split(";")
        via_helper = sum(capability_rows(s) for s in helper if "FOR SHARE OF cap" in s)
    return LockWaits(
        fence=int(any("memoriesql." + a + "(" in text for a in AUTHORIZE)),
        role_rows=sum(capability_rows(s) for s in role_locks) + via_helper,
        advisory=text.count("pg_advisory_xact_lock("),
        rows=len(other_rows),
    )


class WorkDeadlines(unittest.TestCase):
    def test_each_bound_is_work_plus_one_lock_timeout_per_wait(self) -> None:
        self.assertEqual(
            {name: statement_timeout_ms(name) for name in LOCK_WAITS}, PINNED_MS
        )
        for name, waits in LOCK_WAITS.items():
            self.assertEqual(
                statement_timeout_ms(name),
                WORK_DEADLINE_MS + waits.total * LOCK_TIMEOUT_MS,
            )

    def test_the_constants_match_the_installed_functions(self) -> None:
        for name in LOCK_WAITS:
            text = latest(name)
            with self.subTest(name=name):
                self.assertRegex(text, rf"SET lock_timeout ?= ?'{LOCK_TIMEOUT_MS}ms'")
                self.assertIn(f"interval '{WORK_DEADLINE_MS // 1000} seconds'", text)
                # The deadline already excludes waits (the migration this bounds).
                self.assertIn("waited", text)

    def test_the_lock_counts_match_the_function_text(self) -> None:
        for name, waits in LOCK_WAITS.items():
            with self.subTest(name=name):
                self.assertEqual(derived(name), waits)

    def test_every_listed_function_takes_its_bound_from_this_table(self) -> None:
        for name in LOCK_WAITS:
            callers = [
                path
                for path in ADAPTERS.glob("*.py")
                if "memoriesql." + name + "(" in path.read_text()
            ]
            with self.subTest(name=name):
                self.assertEqual(len(callers), 1)
                source = callers[0].read_text()
                self.assertTrue(
                    "statement_timeout_for(query)" in source
                    or 'statement_timeout_ms("' + name + '")' in source
                )
                self.assertEqual(
                    statement_timeout_for("SELECT memoriesql." + name + "(%s)"),
                    PINNED_MS[name],
                )

    def test_other_queries_keep_their_own_bound(self) -> None:
        self.assertIsNone(
            statement_timeout_for("SELECT memoriesql.inspect_logical_event_v1(%s)")
        )
        self.assertIsNone(
            statement_timeout_for(
                "SELECT memoriesql.read_evidence_package_v1(%s), memoriesql.write_evidence_package_v1(%s)"
            )
        )


if __name__ == "__main__":
    unittest.main()
