from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from scripts.project_engine_correction import project

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "engine_201_correction.jsonl"
)


def fixture() -> list[dict[str, Any]]:
    return [json.loads(line) for line in FIXTURE.read_bytes().splitlines()]


def run(rows: list[dict[str, Any]], *, handoff: bool = False) -> dict[str, Any]:
    data = b"\n".join(json.dumps(row).encode() for row in rows)
    return project(data, workspace="w1", scope="acme", handoff=handoff)


class EngineCorrectionProjectionTests(unittest.TestCase):
    def test_tagged_export_and_duplicate_delivery(self) -> None:
        data = FIXTURE.read_bytes()
        first = project(data, workspace="w1", scope="acme")
        self.assertEqual(first, project(data + data, workspace="w1", scope="acme"))
        self.assertEqual(first, run(list(reversed(fixture()))))
        self.assertEqual("unassessed", first["currentness"])
        old, old_chunk, new, new_chunk, edge = fixture()
        self.assertEqual(
            {(r["event_id"], r["chunk_id"], r["text"]) for r in first["chunks"]},
            {
                (old["event_id"], old_chunk["chunk_id"], old_chunk["text"]),
                (new["event_id"], new_chunk["chunk_id"], new_chunk["text"]),
            },
        )
        self.assertEqual(
            [
                {
                    "record_key": edge["record_key"],
                    "arrow": "repl",
                    "from_bead": new_chunk["chunk_id"],
                    "to_bead": old_chunk["chunk_id"],
                }
            ],
            first["corrections"],
        )

    def test_erasure_dominates_stale_replay_in_any_order(self) -> None:
        rows = fixture()
        erased = rows[2]["event_id"]
        erasure = {
            "contract": "core_memory.record.v2",
            "record_kind": "erasure",
            "workspace": "w1",
            "scope": "acme",
            "event_id": erased,
            "record_key": f"erasure:{erased}",
            "seq": 110,
            "erased_at": "2026-11-20T16:14:00Z",
        }
        for mixed in (rows + [erasure] + rows, [erasure] + rows):
            projection = run(mixed)
            self.assertEqual(
                [rows[1]["text"]], [r["text"] for r in projection["chunks"]]
            )
            self.assertEqual([], projection["corrections"])
            self.assertEqual("unassessed", projection["currentness"])

    def test_only_direct_committed_edges_with_both_endpoints_are_projected(
        self,
    ) -> None:
        rows = fixture()
        edge = rows[-1]
        for arrow, edge_type in (("withdraws", "leadsto"), ("!contradicts", "near")):
            with self.subTest(arrow=arrow):
                committed = {
                    **edge,
                    "arrow": arrow,
                    "edge_type": edge_type,
                    "record_key": edge["record_key"].replace(":repl:", f":{arrow}:"),
                }
                self.assertEqual(
                    arrow, run([*rows[:-1], committed])["corrections"][0]["arrow"]
                )
        self.assertEqual([], run(rows[2:])["corrections"])
        verdict = {
            "contract": edge["contract"],
            "record_key": "verdict:w1:100",
            "record_kind": "verdict",
            "workspace": "w1",
            "read_no": 1,
            "earlier_scope": "acme",
            "later_scope": "acme",
            "arrow": "repl",
        }
        self.assertEqual([], run([*rows[:-1], verdict])["corrections"])

    def test_refuses_damaged_text_incomplete_events_and_conflicting_keys(self) -> None:
        rows = fixture()
        for changed in (
            [rows[0]],
            [rows[1]],
            [{**rows[0], "content_hash": "f" * 64}, rows[1]],
            [rows[0], {**rows[1], "text": "different"}],
            [rows[0], {**rows[1], "char_end": rows[1]["char_end"] + 1}],
            [rows[0], {**rows[1], "ordinal": False}],
            [*rows, {**rows[-1], "seq": 102}],
            [{**rows[0], "event_id": "not-an-id"}, rows[1]],
            [{**rows[0], "contract": "core_memory.record.v1"}],
        ):
            with self.assertRaises(ValueError):
                run(changed)

    def test_refuses_mixed_scope_and_workspace_without_returning_text(self) -> None:
        rows = fixture()
        for index, patch in (
            (0, {"workspace": "other"}),
            (1, {"scope": "other"}),
            (0, {"key": {"scope": "other"}}),
            (4, {"scope": "acme"}),
        ):
            changed = rows.copy()
            changed[index] = {**rows[index], **patch}
            with self.assertRaises(ValueError):
                run(changed)
        for kind, pair in (
            ("verdict", {"earlier_scope": "other", "later_scope": "acme"}),
            ("name_link", {"a_scope": "acme", "b_scope": "other"}),
        ):
            with self.assertRaises(ValueError):
                run(
                    [
                        *rows,
                        {
                            "contract": rows[0]["contract"],
                            "record_kind": kind,
                            "record_key": f"{kind}:w1:120",
                            "workspace": "w1",
                            **pair,
                        },
                    ]
                )

    def test_unsupported_kind_and_ambiguous_json_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            run([{**fixture()[0], "record_kind": "future"}])
        with self.assertRaises(ValueError):
            project(b'{"contract":"a","contract":"b"}', workspace="w1", scope="acme")

    def test_tool_arguments_and_edges_to_tool_calls_are_withheld(self) -> None:
        rows = fixture()
        rows[0]["content_kind"] = "tool_call"
        projection = run(rows)
        self.assertNotIn(
            rows[1]["text"], [chunk["text"] for chunk in projection["chunks"]]
        )
        self.assertEqual([], projection["corrections"])

    def test_handoff_keeps_producer_time_source_identity_and_receipt_coordinates(
        self,
    ) -> None:
        old, old_chunk, new, new_chunk, edge = fixture()
        old["occurred_at"] = {
            "at": old["time_at"],
            "precision": "instant",
            "raw": "unparsed source text",
        }
        rows = [new_chunk, edge, old_chunk, old, new]
        self.assertNotIn("handoff", run(rows))
        handoff = run(rows, handoff=True)["handoff"]
        self.assertEqual(handoff["producer_contract"], "core_memory.record.v2")
        self.assertEqual(
            [r["seq"] for r in handoff["events"]], [old["seq"], new["seq"]]
        )
        first, second = handoff["events"]
        self.assertEqual(first["key"], old["key"])
        self.assertEqual(second["key"], new["key"])
        self.assertTrue(second["engine_assigned_revision"])
        for field in ("record_key", "captured_at", "time_at", "time_basis"):
            self.assertEqual(old[field], first[field])
        self.assertEqual(
            first["occurred_at"], {"at": old["time_at"], "precision": "instant"}
        )
        self.assertNotIn("actor", first)
        self.assertEqual(
            [r["record_key"] for r in handoff["chunks"]],
            [old_chunk["record_key"], new_chunk["record_key"]],
        )
        for field in ("content_hash", "ordinal", "byte_end", "char_end"):
            self.assertEqual(old_chunk[field], handoff["chunks"][0][field])
        self.assertNotIn("text", handoff["chunks"][0])
        self.assertEqual(handoff["corrections"][0]["seq"], edge["seq"])
        self.assertEqual(handoff["corrections"][0]["created_at"], edge["created_at"])
        self.assertEqual(handoff["erasures"], [])

    def test_handoff_withholds_erased_provenance_and_retains_only_tombstone(
        self,
    ) -> None:
        old, old_chunk, new, new_chunk, edge = fixture()
        tombstone = {
            "contract": old["contract"],
            "scope": "acme",
            "workspace": "w1",
            "record_kind": "erasure",
            "record_key": "erasure:" + new["event_id"],
            "event_id": new["event_id"],
            "seq": edge["seq"] + 1,
            "erased_at": "2026-11-20T01:02:03.456Z",
        }
        rows = [tombstone, edge, old, new, new_chunk, old_chunk]
        handoff = run(rows, handoff=True)["handoff"]
        self.assertEqual([old["event_id"]], [r["event_id"] for r in handoff["events"]])
        self.assertEqual(
            [old_chunk["chunk_id"]], [r["chunk_id"] for r in handoff["chunks"]]
        )
        self.assertEqual([], handoff["corrections"])
        self.assertEqual(
            [{k: tombstone[k] for k in ("record_key", "event_id", "seq", "erased_at")}],
            handoff["erasures"],
        )
        self.assertNotIn(new_chunk["content_hash"], json.dumps(handoff))
        self.assertNotIn(new["key"]["revision"], json.dumps(handoff))

    def test_handoff_refuses_ambiguous_positions_and_malformed_source_provenance(
        self,
    ) -> None:
        old, old_chunk, new, new_chunk, edge = fixture()
        for invalid in (
            {"seq": False},
            {"seq": old["seq"]},
            {"seq": 2**64},
            {"time_basis": "unknown"},
            {"captured_at": None},
            {"key": {"scope": "acme", "source_ns": "missing-id"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                run([old, old_chunk, {**new, **invalid}, new_chunk, edge], handoff=True)
