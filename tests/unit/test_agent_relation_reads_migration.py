"""Database-free structure of migration 0039 (AM-5 agent relation reads)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "migrations/0039_agent_relation_reads.sql").read_text()

# Owner decision 6, completed (Codex P1 on #78). The reply's content_digest is
# the SHA-256 of the sealed body, so a revision-2 body holds only what the
# caller may see: its protected frame digests and witness hash are bound in the
# internal dependency partition. Result commit and the disclosure-time closure
# check read them there. Each restatement is its installed text with exactly
# these edits; revision-1 bodies, which reach no reply, are checked as before.
PROTECTED_KEYS_ABSENT = (
    "{0}->'witness_sha256' IS NOT NULL OR {0}#>'{{frame,snapshot_digest}}' IS NOT NULL\n"
    "{1}OR {0}#>'{{frame,projection_manifest_sha256}}' IS NOT NULL"
)
RESTATED = {
    "commit_query_result_v1": (
        "0034_query_result_commit.sql",
        (
            (
                "  OR data#>>'{frame,projection_manifest_sha256}' IS DISTINCT FROM"
                " i.projection_manifest_sha256\n"
                "  OR data->>'witness_sha256' IS DISTINCT FROM"
                " encode(sha256(witnesses),'hex')\n",
                "  OR (data->'population_revision' IS DISTINCT FROM '2'::jsonb AND (\n"
                "   data#>>'{frame,projection_manifest_sha256}' IS DISTINCT FROM"
                " i.projection_manifest_sha256\n"
                "   OR data->>'witness_sha256' IS DISTINCT FROM"
                " encode(sha256(witnesses),'hex')))\n"
                "  OR (data->'population_revision'='2'::jsonb AND (\n"
                "   " + PROTECTED_KEYS_ABSENT.format("data", "   ") + "\n"
                "   OR deps#>>'{protected_frame,snapshot_digest}' IS DISTINCT FROM"
                " i.projection_manifest_sha256\n"
                "   OR deps#>>'{protected_frame,projection_manifest_sha256}' IS DISTINCT FROM"
                " i.projection_manifest_sha256\n"
                "   OR deps->>'witness_sha256' IS DISTINCT FROM"
                " encode(sha256(witnesses),'hex')))\n",
            ),
        ),
    ),
    "check_query_result_closure_v2": (
        "0037_observation_sql_population.sql",
        (
            (
                "  OR body->>'witness_sha256' IS DISTINCT FROM"
                " encode(sha256(a.witness_bytes),'hex')\n",
                "  OR " + PROTECTED_KEYS_ABSENT.format("body", "  ") + "\n"
                "  OR deps->>'witness_sha256' IS DISTINCT FROM"
                " encode(sha256(a.witness_bytes),'hex')\n",
            ),
            (
                "IS DISTINCT FROM body#>>'{frame,projection_manifest_sha256}'\n"
                "  OR body#>>'{frame,snapshot_digest}' IS DISTINCT FROM"
                " body#>>'{frame,projection_manifest_sha256}' THEN\n",
                "IS DISTINCT FROM deps#>>'{protected_frame,projection_manifest_sha256}'\n"
                "  OR deps#>>'{protected_frame,snapshot_digest}' IS DISTINCT FROM"
                " deps#>>'{protected_frame,projection_manifest_sha256}' THEN\n",
            ),
        ),
    ),
}


def statement(text: str, create: str, name: str) -> str:
    match = re.search(
        re.escape(f"{create} memoriesql.{name}(") + r".*?\$\$;", text, re.S
    )
    assert match is not None, name
    return match.group(0)


def restated(name: str) -> str:
    """The installed statement of `name` with exactly its listed edits."""
    source, edits = RESTATED[name]
    text = statement(
        (ROOT / "migrations" / source).read_text(), "CREATE FUNCTION", name
    )
    for old, new in edits:
        if text.count(old) != 1:
            raise AssertionError(f"{name}: edit does not apply once")
        text = text.replace(old, new)
    return text.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


# Every earlier kernel name delegates to one mode-threaded body.
THREADED = {
    "relation_closure_authorized_v1": "relation_closure_authorized_v2",
    "derivation_bead_authorize_v1": "derivation_bead_authorize_v2",
    "qualified_bead_roots_v1": "qualified_bead_roots_v2",
    "qualified_unit_roots_v1": "qualified_unit_roots_v2",
    "relation_evidence_view_v3": "relation_evidence_view_v4",
    "relation_events_view_v3": "relation_events_view_v4",
    "relation_unit_records_v1": "relation_unit_records_v2",
    "relation_bead_records_v1": "relation_bead_records_v2",
    "relation_assertion_records_v1": "relation_assertion_records_v2",
    "relation_assertion_row_v1": "relation_assertion_row_v2",
    "prepare_relation_sql_population_v1": "prepare_relation_sql_population_v2",
}


def body(name: str, *, replaced: bool) -> str:
    create = "CREATE OR REPLACE FUNCTION" if replaced else "CREATE FUNCTION"
    match = re.search(
        re.escape(f"{create} memoriesql.{name}(") + r".*?AS \$\$(.*?)\$\$;", SQL, re.S
    )
    assert match is not None, name
    return match.group(1)


class AgentRelationReadsMigration(unittest.TestCase):
    def test_no_privilege_or_reader_object_is_added(self) -> None:
        # The reviewed query reader's profile cannot change: nothing is granted
        # and nothing in the reader's schema is touched.
        self.assertNotRegex(SQL, r"(?im)^\s*GRANT\b")
        # Only the restated result commit reads the reader schema's invocation
        # records, exactly as its installed text does.
        rest = SQL
        for name in RESTATED:
            rest = rest.replace(statement(SQL, "CREATE OR REPLACE FUNCTION", name), "")
        self.assertNotIn("memoriesql_query", rest)
        created = re.findall(r"^CREATE FUNCTION memoriesql\.([a-z_0-9]+)\(", SQL, re.M)
        self.assertEqual(
            sorted(created),
            sorted(
                [
                    *THREADED.values(),
                    "relation_read_source_authorized_v1",
                    # Owner decision 7 of 2026-10-06: a relation's task population.
                    "relation_task_population_records_v1",
                ]
            ),
        )
        for name in created:
            self.assertIn(f"REVOKE ALL ON FUNCTION memoriesql.{name}(", SQL)

    def test_a_relation_needs_its_whole_task_population(self) -> None:
        # Owner decision 7 of 2026-10-06: the family block discloses a relation,
        # with its author's text, only with its task's whole pinned population,
        # in both modes, and in owner mode with each retiring task's too. Any
        # failure is a 42501 inside the block, so the relation is withheld whole.
        # The helper checks every pin exactly as pair coverage does.
        population = body("prepare_relation_sql_population_v2", replaced=False)
        block = population.split(
            "EXCEPTION WHEN insufficient_privilege THEN\n            withheld"
        )[0]
        self.assertIn(
            "memoriesql.relation_task_population_records_v1(\n"
            "                c.tenant_id,(SELECT ar.task_id",
            block,
        )
        self.assertIn(
            "IF read_mode='owner' THEN\n                FOR retirement IN", block
        )
        helper = body("relation_task_population_records_v1", replaced=False)
        self.assertIn(
            "pin.authored_at>known OR NOT "
            "memoriesql.current_context_bead_version_authorized(",
            helper,
        )
        self.assertIn(
            "memoriesql.relation_bead_records_v2(t,pin.bead_id,known,read_mode)", helper
        )
        self.assertEqual(helper.count("ERRCODE='42501'"), 2)

    def test_earlier_names_delegate_in_owner_mode(self) -> None:
        for old, new in THREADED.items():
            text = body(old, replaced=True)
            self.assertRegex(text, rf"memoriesql\.{new}\([^;]*,'owner'\)")
            self.assertLessEqual(len(text.strip().splitlines()), 3, old)

    def test_raw_revisiting_remains_only_on_owner_branches(self) -> None:
        helper = body("relation_read_source_authorized_v1", replaced=False)
        self.assertIn(
            "IF read_mode='owner' THEN\n  PERFORM memoriesql.revisiting_source_authorize(source);",
            helper,
        )
        roots = body("qualified_bead_roots_v2", replaced=False)
        self.assertIn(
            " IF read_mode='owner' THEN\n  FOREACH next_bead IN ARRAY roots LOOP "
            "PERFORM memoriesql.revisiting_source_authorize(next_bead); END LOOP;",
            roots,
        )
        self.assertEqual(SQL.count("revisiting_source_authorize("), 2)
        # The agent gate is source.read on the exact event of the source object.
        self.assertIn("'source.read','read'", helper)
        self.assertIn("ev.source_object_id=source", helper)

    def test_result_digests_bind_protected_values_only_internally(self) -> None:
        for name in RESTATED:
            self.assertEqual(
                statement(SQL, "CREATE OR REPLACE FUNCTION", name), restated(name)
            )

    def test_agent_mode_never_governs_and_withholds_history(self) -> None:
        closure = body("relation_closure_authorized_v2", replaced=False)
        self.assertIn(
            "IF govern AND read_mode IS DISTINCT FROM 'owner' THEN RETURN false; END IF;",
            closure,
        )
        population = body("prepare_relation_sql_population_v2", replaced=False)
        # History stays owner-only, and an agent's head_token hashes no event
        # (the owner's decision of 2026-10-05).
        self.assertIn(
            "IF read_mode='agent' THEN\n"
            "                assertion := assertion || jsonb_build_object('events','[]'::jsonb,\n"
            "                    'head_token',memoriesql.lifecycle_hash_v1(\n"
            "                        (item->'head_manifest') || jsonb_build_object('events','[]'::jsonb)));",
            population,
        )
        self.assertIn("WHERE read_mode='owner' AND a.tenant_id=c.tenant_id", population)
        # A family is disclosed only with every assessed relation its records
        # name (review P1), in both read modes (owner decision 5b).
        self.assertIn("WHERE NOT n.id::uuid=ANY(COALESCE(disclosed,'{}'))", population)
        self.assertIn(
            "        FROM jsonb_array_elements(families) f;\n    LOOP\n        SELECT array_agg(",
            population,
        )
        # Owner decision 5b: a task that recorded a relation withheld from the
        # reader discloses none of its pair coverage.
        self.assertIn("withheld := withheld || r.relation_id;", population)
        self.assertIn(
            "IF r.relation_id=ANY(withheld) THEN\n"
            "                    RAISE EXCEPTION 'pair_unavailable' USING ERRCODE='42501';",
            population,
        )
        self.assertNotIn("IF read_mode='agent' THEN\n        LOOP", population)
        # Owner mode for raw-read holders, the agent mode for paired agents
        # only (AM-5), and the earlier gate with no mode for anyone else.
        entry = body("prepare_query_sql_population_v2", replaced=True)
        self.assertIn(
            "relation_mode := CASE\n"
            "        WHEN memoriesql.current_context_has_capability('source.raw.read')"
            " THEN 'owner'\n"
            "        WHEN c.principal_kind='agent' AND c.pairing_grant_id IS NOT NULL"
            " THEN 'agent'\n"
            "    END;",
            entry,
        )
        self.assertIn("COALESCE(relation_mode,'owner')", entry)
        self.assertIn("'relation_read_mode',relation_mode,", entry)


if __name__ == "__main__":
    unittest.main()
