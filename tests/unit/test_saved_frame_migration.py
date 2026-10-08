"""Preserve closure's existing security boundary while replacing snapshot replay."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / "migrations/0039_agent_relation_reads.sql").read_text()
NEW = (ROOT / "migrations/0044_saved_query_frame_closure.sql").read_text()


def function(source: str, name: str) -> str:
    match = re.search(
        rf"CREATE(?: OR REPLACE)? FUNCTION memoriesql\.{name}\(.*?END \$\$;",
        source,
        flags=re.S,
    )
    assert match is not None, name
    return match[0]


class SavedFrameMigration(unittest.TestCase):
    def test_existing_closure_guards_are_byte_exact_around_replaced_check(self) -> None:
        original = function(OLD, "check_query_result_closure_v2")
        start = original.index(" current_population:=")
        end = original.index(
            " PERFORM memoriesql.result_preparation_authority_v1();", start
        )
        expected = (
            original[:start]
            + " -- Reauthorize the retained original dependency frame, not a new population.\n"
            + " PERFORM memoriesql.query_saved_records_authorized_v1(saved_records,\n"
            + "  (body#>>'{frame,known_at}')::timestamptz);\n"
            + original[end:]
        ).replace(
            " rebuilt jsonb; current_population jsonb; current_manifest jsonb;",
            " rebuilt jsonb;",
        )
        self.assertEqual(function(NEW, "check_query_result_closure_v2"), expected)

    def test_original_event_representation_and_gates_only_add_id_filter(self) -> None:
        expected = (
            function(OLD, "relation_events_view_v4")
            .replace(
                "relation_events_view_v4(t uuid,k text,id uuid,known timestamptz,read_mode text)",
                "query_saved_events_v1(t uuid,k text,id uuid,known timestamptz,read_mode text,ids uuid[])",
            )
            .replace(") hist ORDER BY", ") hist WHERE event_id=ANY(ids) ORDER BY")
        )
        self.assertEqual(function(NEW, "query_saved_events_v1"), expected)

    def test_forward_migration_adds_no_application_or_public_privilege(self) -> None:
        self.assertNotRegex(NEW, r"(?i)\bGRANT\s")
        self.assertEqual(
            re.findall(r"CREATE(?: OR REPLACE)? FUNCTION memoriesql\.(\w+)", NEW),
            [
                "query_saved_events_v1",
                "query_saved_records_authorized_v1",
                "check_query_result_closure_v2",
            ],
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION memoriesql.query_saved_records_authorized_v1(jsonb,timestamptz) FROM PUBLIC;",
            NEW,
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION memoriesql.query_saved_events_v1(uuid,text,uuid,timestamptz,text,uuid[]) FROM PUBLIC;",
            NEW,
        )


if __name__ == "__main__":
    unittest.main()
