"""DB-free checks for owner re-approval of supervised tasks; no SQL execution proof."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
MIGRATION = MIGRATIONS / "0051_supervised_reapproval.sql"
SQL = MIGRATION.read_text() if MIGRATION.exists() else ""
HEAD = "memoriesql.supervised_qualification_head"


def compact(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


def definition(sql: str, name: str) -> str:
    """The whole CREATE [OR REPLACE] FUNCTION statement of one plpgsql function."""
    match = re.search(
        rf"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.{name}\(.*?\nEND; \$\$;",
        sql,
        re.DOTALL,
    )
    assert match, name
    return match.group(0)


def latest(name: str, before: str) -> str:
    """The definition installed by the newest migration earlier than `before`."""
    found = None
    for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if path.name >= before:
            break
        text = path.read_text()
        if re.search(rf"FUNCTION memoriesql\.{name}\(", text):
            found = definition(text, name)
    assert found, name
    return found


class SupervisedReapprovalMigration(unittest.TestCase):
    def test_readers_differ_from_their_prior_definitions_only_by_the_chain_head(
        self,
    ) -> None:
        select = (
            " SELECT * INTO q FROM memoriesql.model_supervised_qualifications "
            "WHERE tenant_id=t AND task_id=task;\n"
        )
        for name in ("supervised_task_open", "supervised_acceptance_valid"):
            with self.subTest(name=name):
                before = latest(name, MIGRATION.name)
                after = definition(SQL, name)
                self.assertEqual(
                    after.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
                    .replace(f" SELECT * INTO q FROM {HEAD}(t,task);\n", select, 1),
                    before,
                )

    def test_dispatch_changes_only_by_the_head_and_earlier_approvals(self) -> None:
        before = latest("consume_supervised_dispatch", MIGRATION.name)
        after = definition(SQL, "consume_supervised_dispatch")
        start = after.index(" -- Intents under an approval this one superseded")
        end = after.index(" THEN RAISE EXCEPTION 'supervised_prior_dispatch_outside_approval'")
        restored = (
            after[:start]
            + " IF EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents "
            "WHERE tenant_id=q.tenant_id AND task_id=q.task_id AND "
            "supervised_qualification_id IS DISTINCT FROM q.qualification_id)\n"
            + after[end:]
        ).replace(
            f" SELECT * INTO q FROM {HEAD}((i->>'tenant_id')::uuid,"
            "(i->>'semantic_task_id')::uuid);\n",
            " SELECT * INTO q FROM memoriesql.model_supervised_qualifications\n"
            " WHERE tenant_id=(i->>'tenant_id')::uuid AND "
            "task_id=(i->>'semantic_task_id')::uuid;\n",
            1,
        )
        self.assertEqual(restored, before)
        # An earlier intent is inside approval only under a revoked approval of the
        # same task; an unsupervised intent stays outside approval.
        clause = compact(after[start:end])
        for condition in (
            "earlier.task_id=r.task_id",
            "earlier.qualification_id=r.supervised_qualification_id",
            "earlier.status='revoked'",
            "r.supervised_qualification_id IS DISTINCT FROM q.qualification_id",
        ):
            self.assertIn(condition, clause)
        # The allowance still counts every intent of the current approval.
        self.assertIn(
            "WHERE tenant_id=q.tenant_id AND supervised_qualification_id=q.qualification_id",
            compact(after),
        )

    def test_the_head_is_the_approval_no_other_supersedes(self) -> None:
        head = compact(SQL[SQL.index("CREATE FUNCTION " + HEAD) : SQL.index("$$;", SQL.index(HEAD)) + 3])
        self.assertIn("RETURNS SETOF memoriesql.model_supervised_qualifications", head)
        self.assertIn("ROWS 1", head)
        self.assertIn(
            "NOT EXISTS(SELECT 1 FROM memoriesql.model_supervised_qualifications later "
            "WHERE later.tenant_id=q.tenant_id AND "
            "later.supersedes_qualification_id=q.qualification_id)",
            head,
        )
        statement = compact(SQL)
        for clause in (
            "DROP CONSTRAINT model_supervised_qualifications_tenant_id_task_id_key",
            "ADD COLUMN supersedes_qualification_id uuid",
            "UNIQUE(tenant_id,supersedes_qualification_id)",
            "CHECK(supersedes_qualification_id IS DISTINCT FROM qualification_id)",
            "CREATE UNIQUE INDEX model_supervised_qualifications_one_active ON "
            "memoriesql.model_supervised_qualifications(tenant_id,task_id) "
            "WHERE status='active'",
        ):
            self.assertIn(clause, statement)

    def test_a_replacement_needs_a_revoked_head_and_a_waiting_task(self) -> None:
        guard = compact(definition(SQL, "guard_supervised_reapproval"))
        for condition in (
            "NEW.supersedes_qualification_id IS DISTINCT FROM prior.qualification_id",
            "prior.status<>'revoked'",
            "NEW.status<>'active'",
            "NEW.workspace_id IS DISTINCT FROM prior.workspace_id",
            "NEW.access_scope_id IS DISTINCT FROM prior.access_scope_id",
            "NEW.origin_principal_id IS DISTINCT FROM prior.origin_principal_id",
            "t.status IN ('queued','retry_wait','policy_paused'))",
            "a.status IN ('claimed','running')",
        ):
            self.assertIn(condition, guard)
        # Every recorded outcome sets result_attempt_id, a retry or a pause
        # included, so a waiting task is known by its status alone.
        self.assertNotIn("result_attempt_id", guard)
        self.assertIn(
            "CREATE TRIGGER supervised_reapproval_guard BEFORE INSERT ON "
            "memoriesql.model_supervised_qualifications",
            compact(SQL),
        )
        # Trigger names order: the outcome-authority fence takes the tenant lock first.
        self.assertLess("supervised_qualification_authority_fence", "supervised_reapproval_guard")
        # The first approval of a task names nothing.
        self.assertIn(
            "IF NOT FOUND THEN IF NEW.supersedes_qualification_id IS NOT NULL THEN "
            "RAISE EXCEPTION 'supervised_reapproval_unavailable'",
            guard,
        )

    def test_no_runtime_role_gains_anything(self) -> None:
        self.assertNotIn("GRANT ", SQL)
        for name in ("supervised_qualification_head(uuid,uuid)", "guard_supervised_reapproval()"):
            self.assertIn(f"REVOKE ALL ON FUNCTION memoriesql.{name} FROM PUBLIC;", SQL)
        # Approvals stay immutable apart from revocation (0026's guard is not replaced).
        self.assertNotIn("guard_supervised_qualification", SQL)

    def test_inventory_pins_the_migration(self) -> None:
        rows = json.loads((ROOT / "contracts/migration-inventory.json").read_text())["migrations"]
        self.assertEqual([int(r["filename"][:4]) for r in rows], list(range(1, 53)))
        self.assertEqual(rows[50]["filename"], MIGRATION.name)
        self.assertEqual(rows[50]["sha256"], hashlib.sha256(MIGRATION.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
