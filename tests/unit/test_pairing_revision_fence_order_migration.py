"""Database-free structure of migration 0048 (pairing revision fence order)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = (ROOT / "migrations/0006_force_resource_authorization.sql").read_text()
SQL = (ROOT / "migrations/0048_pairing_revision_fence_order.sql").read_text()

NAME = "FUNCTION memoriesql.revise_pairing_grant(\n"
FENCE = """    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            context_record.tenant_id::text || ':semantic_outcome_authority:',
            0
        )
    );
"""
# The statement that locks the grant's row; the fence must come before it.
ROW_LOCK = "    SELECT pairing_grant_root.* INTO STRICT pairing_grant_record\n"


def kernel(text: str, create: str) -> str:
    start = text.index(create + NAME)
    return text[start : text.index("END;\n$$;\n", start) + len("END;\n$$;\n")]


def added() -> str:
    """The lines migration 0048 adds to migration 0006's function."""
    before, after = kernel(ORIGINAL, "CREATE ").split(ROW_LOCK)
    replaced = kernel(SQL, "CREATE OR REPLACE ")
    before = before.replace("CREATE " + NAME, "CREATE OR REPLACE " + NAME, 1)
    assert replaced.startswith(before) and replaced.endswith(ROW_LOCK + after)
    return replaced[len(before) : len(replaced) - len(ROW_LOCK + after)]


class PairingRevisionFenceOrderMigration(unittest.TestCase):
    def test_only_the_fence_is_added_before_the_grant_row_lock(self) -> None:
        # Every check, refusal, the revision insert and the returned revision
        # keep migration 0006's text, including the header that restates
        # SECURITY DEFINER and the search path.
        self.assertEqual(kernel(ORIGINAL, "CREATE ").count(ROW_LOCK), 1)
        text = added()
        self.assertTrue(text.endswith(FENCE), text)
        self.assertEqual(re.sub(r"(?m)^    --.*\n", "", text), FENCE)

    def test_the_fence_is_the_one_the_revision_trigger_takes(self) -> None:
        # The same key, exclusively, as fence_semantic_outcome_authority_mutation
        # takes on every pairing_grant_revisions insert (migration 0008).
        trigger = (ROOT / "migrations/0008_create_semantic_task_queue.sql").read_text()
        body = trigger[
            trigger.index(
                "CREATE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation()"
            ) :
        ]
        self.assertIn(
            "    PERFORM pg_catalog.pg_advisory_xact_lock(\n"
            "        pg_catalog.hashtextextended(\n"
            "            affected_tenant_id::text || ':semantic_outcome_authority:',\n"
            "            0\n",
            body[: body.index("$$;\n")],
        )
        self.assertNotIn("pg_advisory_xact_lock_shared", added())

    def test_the_migration_replaces_one_function_and_nothing_else(self) -> None:
        statements = re.sub(r"(?m)^--.*\n", "", SQL).strip()
        self.assertEqual(statements, kernel(SQL, "CREATE OR REPLACE ").strip())
        # CREATE OR REPLACE keeps the function's owner, grants and comment.
        self.assertNotRegex(SQL, r"(?im)^\s*(GRANT|REVOKE|ALTER|COMMENT|DROP)\b")
        self.assertEqual(SQL.count("SECURITY DEFINER"), 1)


if __name__ == "__main__":
    unittest.main()
