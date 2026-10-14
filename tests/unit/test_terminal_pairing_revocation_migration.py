"""Database-free structure of migration 0042 (terminal pairing revocation)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "migrations/0042_terminal_pairing_revocation.sql").read_text()
# The statements, without the explanatory comments.
CODE = "".join(line.split("--", 1)[0] + "\n" for line in SQL.splitlines())
FUNCTION = "memoriesql.reject_pairing_grant_revival()"


def trigger_body() -> str:
    match = re.search(
        re.escape("CREATE FUNCTION " + FUNCTION) + r" RETURNS trigger\n(.*?)\$\$;\n",
        CODE,
        re.S,
    )
    assert match is not None
    return match.group(1)


def guard() -> str:
    match = re.search(r"^DO \$\$\n(.*?)^\$\$;\n", CODE, re.S | re.M)
    assert match is not None
    return match.group(1)


class TerminalPairingRevocationMigration(unittest.TestCase):
    def test_it_adds_one_ungranted_trigger_and_restates_nothing(self) -> None:
        # One trigger function and its trigger; nothing older is restated,
        # granted, altered, commented or dropped.
        self.assertEqual(
            re.findall(r"CREATE (?:OR REPLACE )?FUNCTION ([\w.]+)\(", CODE),
            ["memoriesql.reject_pairing_grant_revival"],
        )
        self.assertEqual(
            re.findall(r"CREATE TRIGGER (\w+)", CODE),
            ["pairing_grant_revisions_terminal"],
        )
        self.assertIn(
            "CREATE TRIGGER pairing_grant_revisions_terminal\n"
            "BEFORE INSERT ON memoriesql.pairing_grant_revisions\n"
            "FOR EACH ROW EXECUTE FUNCTION " + FUNCTION + ";\n",
            CODE,
        )
        self.assertIn("REVOKE ALL ON FUNCTION " + FUNCTION + " FROM PUBLIC;", CODE)
        for absent in (
            "GRANT ",
            "ALTER ",
            "COMMENT ",
            "DROP ",
            "OR REPLACE",
            "SECURITY DEFINER",
            "UPDATE ",
            "DELETE ",
            "INSERT INTO",
        ):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, CODE)

    def test_any_revoked_revision_ends_the_grant_whatever_is_asked(self) -> None:
        body = trigger_body()
        self.assertIn("SET search_path = pg_catalog, memoriesql\n", body)
        for condition in (
            "revision.tenant_id = NEW.tenant_id",
            "revision.pairing_grant_id = NEW.pairing_grant_id",
            "revision.status = 'revoked'",
        ):
            self.assertIn(condition, body)
        self.assertIn("USING ERRCODE = '23514'", body)
        # The requested status does not matter: a second revocation is refused
        # as firmly as a reactivation.
        self.assertNotIn("NEW.status", body)
        # It reads no membership, so the reviewed list of membership readers
        # (test_local_client_pairing) is unchanged.
        self.assertNotIn("workspace_memberships", SQL)

    def test_installation_waits_for_a_grant_reactivated_by_hand(self) -> None:
        body = guard()
        self.assertIn("revoked.status = 'revoked'", body)
        self.assertIn("ORDER BY latest.revision DESC", body)
        self.assertIn(") = 'active'", body)
        self.assertIn("USING ERRCODE = '55000'", body)
        # The guard runs before the trigger exists.
        self.assertLess(CODE.index("DO $$"), CODE.index("CREATE FUNCTION"))


if __name__ == "__main__":
    unittest.main()
