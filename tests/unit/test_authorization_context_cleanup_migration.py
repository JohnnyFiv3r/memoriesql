"""Database-free structure of migration 0040 (authorization context cleanup)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = (ROOT / "migrations/0006_force_resource_authorization.sql").read_text()
SQL = (ROOT / "migrations/0040_authorization_context_cleanup.sql").read_text()

NAME = "FUNCTION memoriesql.begin_authorization_context(\n"
COLLECT = "memoriesql.collect_authorization_contexts_v1()"
# The one statement migration 0006 used to remove every stale context row.
WAITING_DELETE = """    DELETE FROM memoriesql.authorization_contexts AS old_context
     WHERE old_context.backend_pid = pg_backend_pid()
        OR old_context.expires_at <= statement_timestamp()
        OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_stat_activity AS activity
            WHERE activity.pid = old_context.backend_pid
        );
"""


def kernel(text: str, create: str) -> str:
    start = text.index(create + NAME)
    return text[start : text.index("END;\n$$;\n", start) + len("END;\n$$;\n")]


def cleanup() -> str:
    """The statements that replace the waiting DELETE."""
    before, after = kernel(ORIGINAL, "CREATE ").split(WAITING_DELETE)
    replaced = kernel(SQL, "CREATE OR REPLACE ")
    before = before.replace("CREATE " + NAME, "CREATE OR REPLACE " + NAME, 1)
    assert replaced.startswith(before) and replaced.endswith(after)
    return replaced[len(before) : len(replaced) - len(after)]


def collector() -> str:
    match = re.search(
        re.escape("CREATE FUNCTION " + COLLECT) + r" RETURNS void\n(.*?)\$\$;\n",
        SQL,
        re.S,
    )
    assert match is not None
    return match.group(1)


class AuthorizationContextCleanupMigration(unittest.TestCase):
    def test_only_the_stale_context_delete_changes_in_the_kernel(self) -> None:
        # Every credential, membership and pairing check, every refusal, the
        # inserted context and the returned row keep migration 0006's text,
        # including the header that restates SECURITY DEFINER and the search
        # path. The refusals keep their place in this one function, where the
        # worker recognizes them.
        self.assertEqual(kernel(ORIGINAL, "CREATE ").count(WAITING_DELETE), 1)
        self.assertNotIn(WAITING_DELETE, SQL)
        self.assertTrue(cleanup().strip())

    def test_the_migration_adds_one_private_helper_and_nothing_else(self) -> None:
        statements = re.sub(r"(?m)^--.*\n", "", SQL).strip()
        helper = (
            "CREATE FUNCTION "
            + COLLECT
            + " RETURNS void\n"
            + collector()
            + "$$;\nREVOKE ALL ON FUNCTION "
            + COLLECT
            + " FROM PUBLIC;"
        )
        self.assertEqual(
            statements, helper + "\n\n" + kernel(SQL, "CREATE OR REPLACE ").strip()
        )
        # CREATE OR REPLACE keeps the kernel's owner, grants and comment. The
        # helper is granted to nobody and runs only inside the kernel.
        self.assertNotRegex(SQL, r"(?im)^\s*(GRANT|ALTER|COMMENT|DROP)\b")
        self.assertEqual(SQL.count("SECURITY DEFINER"), 1)
        self.assertNotIn("SECURITY DEFINER", collector())

    def test_this_transactions_earlier_context_is_revoked_at_once(self) -> None:
        self.assertTrue(
            cleanup().startswith(
                "    -- A second context in one transaction revokes the first. "
                "Those rows were\n"
                "    -- written by this transaction, so no other session can see or "
                "lock them.\n"
                "    DELETE FROM memoriesql.authorization_contexts AS own_context\n"
                "     WHERE own_context.backend_pid = pg_backend_pid()\n"
                "       AND own_context.transaction_id = txid_current();\n"
            )
        )
        # It is the kernel's only DELETE; the rest is collection.
        self.assertEqual(cleanup().count("DELETE FROM"), 1)

    def test_collection_never_waits_and_never_fails_the_caller(self) -> None:
        text = cleanup()
        # Read committed collects directly. Snapshot isolation undoes an attempt
        # that met a row removed after its snapshot, and nothing else.
        self.assertIn(
            "    IF current_setting('transaction_isolation', true)\n"
            "       IN ('read committed', 'read uncommitted') THEN\n"
            "        PERFORM " + COLLECT + ";\n"
            "    ELSE\n",
            text,
        )
        self.assertIn(
            "        BEGIN\n"
            "            PERFORM " + COLLECT + ";\n"
            "        EXCEPTION WHEN serialization_failure THEN\n"
            "            NULL;\n"
            "        END;\n"
            "    END IF;\n",
            text,
        )
        self.assertEqual(text.count("EXCEPTION"), 1)
        # The collection locks what it can and deletes exactly those rows.
        body = collector()
        self.assertIn("FOR UPDATE OF stale_context SKIP LOCKED", body)
        self.assertIn(
            "    DELETE FROM memoriesql.authorization_contexts AS old_context\n"
            "     USING collectable\n"
            "     WHERE old_context.context_id = collectable.context_id\n",
            body,
        )
        # It keeps migration 0006's three reasons a row is removable.
        for reason in (
            "stale_context.backend_pid = pg_backend_pid()",
            "stale_context.expires_at <= statement_timestamp()",
            "WHERE activity.pid = stale_context.backend_pid",
        ):
            self.assertIn(reason, body)


if __name__ == "__main__":
    unittest.main()
