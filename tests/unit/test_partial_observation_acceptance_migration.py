"""DB-free checks for partial observation acceptance; no SQL execution proof."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from memoriesql.application.authored_output_checks import PARTIAL_ACCEPTANCE
from memoriesql.application.semantic_task_contracts import WITHHELD_REASON_CODES

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
MIGRATION = MIGRATIONS / "0052_partial_observation_acceptance.sql"
SQL = MIGRATION.read_text() if MIGRATION.exists() else ""
TABLE = "semantic_withheld_output_items"
APPLY = "apply_semantic_annotations_schema29"
RECORD = "record_withheld_semantic_output_v1"
READ = "withheld_semantic_output_v1"


def compact(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


def function(sql: str, name: str) -> str:
    """The whole CREATE [OR REPLACE] FUNCTION statement, through its closing $$."""
    start = sql.index(f"FUNCTION memoriesql.{name}(")
    start = sql.rindex("CREATE", 0, start)
    return sql[start : sql.index("$$;", sql.index("AS $$", start) + 5) + 3]


class PartialObservationAcceptanceMigration(unittest.TestCase):
    def test_apply_differs_from_0041_only_by_withheld_coverage(self) -> None:
        before = function(
            (MIGRATIONS / "0041_authored_statement_kinds.sql").read_text(), APPLY
        )
        after = function(SQL, APPLY)
        coverage = after[after.index("    -- Withheld beads") :]
        coverage = coverage[: coverage.index("THEN\n") + 5]
        prior = before[
            before.index(
                "    IF jsonb_array_length(requested_command #> '{payload,annotations}') <>"
            ) :
        ]
        prior = prior[: prior.index("THEN\n") + 5]
        restored = after.replace("    withheld_beads text[];\n", "", 1).replace(
            coverage, prior, 1
        )
        self.assertEqual(restored, before)
        # Every task bead is annotated or withheld, never both, and the counts
        # add up: annotations and withheld beads partition the task's beads.
        self.assertIn(
            "AND withheld.attempt_id = command_attempt_id", compact(coverage)
        )
        self.assertIn("+ cardinality(withheld_beads) <>", compact(coverage))
        self.assertIn("AND requested.value <> ALL(withheld_beads)", compact(coverage))
        self.assertIn(
            "WHERE annotation.value ->> 'bead_id' = ANY(withheld_beads)", compact(coverage)
        )
        # The restated apply is the last statement of the file.
        self.assertEqual(SQL[SQL.index(after) + len(after) :].strip(), "")

    def test_the_table_is_owner_only_immutable_and_fixed_coded(self) -> None:
        text = compact(SQL)
        self.assertIn(f"ALTER TABLE memoriesql.{TABLE} ENABLE ROW LEVEL SECURITY;", text)
        self.assertIn(f"ALTER TABLE memoriesql.{TABLE} FORCE ROW LEVEL SECURITY;", text)
        self.assertIn(
            f"REVOKE ALL ON memoriesql.{TABLE} FROM PUBLIC,memoriesql_application,memoriesql_worker;",
            text,
        )
        self.assertNotRegex(text, rf"GRANT [A-Z, ]+ ON (TABLE )?memoriesql\.{TABLE}")
        self.assertNotIn("CREATE POLICY", text)
        self.assertIn(
            f"BEFORE UPDATE OR DELETE ON memoriesql.{TABLE} FOR EACH ROW "
            "EXECUTE FUNCTION memoriesql.reject_immutable_change();",
            text,
        )
        codes = ",".join(f"'{code}'" for code in WITHHELD_REASON_CODES)
        self.assertIn(f"CHECK(reason_code IN ({codes}))", text)
        self.assertEqual(compact(function(SQL, RECORD)).count(f"IN ({codes})"), 1)

    def test_only_the_recorder_apply_and_the_owner_read_name_the_table(self) -> None:
        coverage = function(SQL, APPLY)
        coverage = coverage[coverage.index("    -- Withheld beads") :]
        coverage = coverage[: coverage.index("THEN\n") + 5]
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            if path == MIGRATION:
                continue
            with self.subTest(migration=path.name):
                text = path.read_text()
                if path.name < MIGRATION.name or APPLY not in text:
                    self.assertNotIn(TABLE, text)
                    continue
                # A later restatement of apply keeps the withheld coverage
                # block exactly, and names the table nowhere else.
                restated = function(text, APPLY)
                self.assertIn(coverage, restated)
                self.assertNotIn(TABLE, text.replace(restated, ""))
        rest = SQL
        for name in (RECORD, READ, APPLY):
            self.assertIn(TABLE, function(SQL, name))
            rest = rest.replace(function(SQL, name), "", 1)
        # Elsewhere only its own definition: table, index, security, trigger.
        statements = [s for s in compact(rest).split(";") if TABLE in s]
        self.assertEqual(
            [s.split()[0:2] for s in statements],
            [["CREATE", "TABLE"], ["CREATE", "INDEX"], ["ALTER", "TABLE"], ["ALTER", "TABLE"],
             ["REVOKE", "ALL"], ["CREATE", "TRIGGER"]],
        )

    def test_the_recorder_is_the_leased_workers_with_applys_fence(self) -> None:
        body = compact(function(SQL, RECORD))
        self.assertIn("SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off", body)
        self.assertIn(
            f"REVOKE ALL ON FUNCTION memoriesql.{RECORD}(uuid,uuid,uuid,bigint,text,text,jsonb) FROM PUBLIC;",
            compact(SQL),
        )
        self.assertEqual(
            re.findall(rf"GRANT EXECUTE ON FUNCTION memoriesql\.{RECORD}\([^)]*\) TO (\w+);", SQL),
            ["memoriesql_worker"],
        )
        # Apply's lock order: outcome authority, relation lineage, then rows.
        outcome = body.index(":semantic_outcome_authority:")
        lineage = body.index(":relation-lineage:")
        task = body.index("SELECT * INTO t FROM memoriesql.semantic_tasks")
        self.assertLess(outcome, lineage)
        self.assertLess(lineage, task)
        for required in (
            "c.principal_kind IS DISTINCT FROM 'service'",
            "t.target_kind IS DISTINCT FROM 'canonical_semantics'",
            "current_context_semantic_task_authorized(t.task_id,'memory.maintain','write')",
            "semantic_task_origin_authorized(t.tenant_id,t.task_id,database_now)",
            "a.claimant_principal_id=c.principal_id",
            "q.status='running'",
            "q.result_attempt_id IS NULL AND q.cancel_requested_at IS NULL FOR UPDATE",
            "a.status='running' FOR UPDATE",
            "v->>'item_kind' IS DISTINCT FROM 'bead'",
        ):
            with self.subTest(required=required):
                self.assertIn(required, body)
        kinds = ",".join(
            f"('{kind}',{revision})" for kind, revision in sorted(PARTIAL_ACCEPTANCE)
        )
        self.assertIn(f"(t.task_kind,t.contract_revision) NOT IN ({kinds})", body)
        # At least one bead is still applied: fewer withheld than task beads.
        self.assertIn(
            "jsonb_array_length(requested_items)<jsonb_array_length(t.input_payload#>'{payload,bead_ids}')",
            body,
        )

    def test_the_owner_read_admits_only_a_human_maintainer(self) -> None:
        body = compact(function(SQL, READ))
        self.assertEqual(
            re.findall(rf"GRANT EXECUTE ON FUNCTION memoriesql\.{READ}\([^)]*\) TO (\w+);", SQL),
            ["memoriesql_application"],
        )
        self.assertIn(
            "IF NOT FOUND OR c.principal_kind IS DISTINCT FROM 'human' OR c.expires_at<=database_now THEN RETURN; END IF;",
            body,
        )
        for required in (
            "current_context_scope_authorized(w.access_scope_id,'memory.maintain','read')",
            "current_context_scope_time_authorized(w.access_scope_id,'read',database_now)",
            "semantic_task_origin_authorized(w.tenant_id,w.task_id,database_now)",
            "w.tenant_id=c.tenant_id AND w.workspace_id=c.workspace_id",
        ):
            with self.subTest(required=required):
                self.assertIn(required, body)

    def test_inventory_pins_the_migration(self) -> None:
        rows = json.loads((ROOT / "contracts/migration-inventory.json").read_text())["migrations"]
        self.assertEqual([int(r["filename"][:4]) for r in rows], list(range(1, 54)))
        self.assertEqual(rows[51]["filename"], MIGRATION.name)
        self.assertEqual(rows[51]["sha256"], hashlib.sha256(MIGRATION.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
