"""Database-free checks of the query/reuse reply mechanics; no execution claim."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import sqlglot

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import (
    OBSERVATION_TABLES,
    POLICY_HASH,
    PREPARED_RELATIONS,
    RELATION_TABLES,
    EvidenceIndex,
    order_basis,
    remaining_after,
    reply,
    stable_reply_bytes,
    wire_coverage,
    wire_frame,
)
from memoriesql.application.investigation_contracts import result_json_bytes

ROOT = Path(__file__).resolve().parents[2]


class AgentSqlResultsContract(unittest.TestCase):
    def test_database_pins_the_same_baseline_policy_hash(self) -> None:
        sql = (ROOT / "migrations/0038_query_result_access.sql").read_text()
        self.assertIn("'" + POLICY_HASH + "'", sql)
        self.assertEqual(
            POLICY_HASH,
            "b19f408584b5d6aac158c0d122be3c132cda9b6d14f6e6809ad05510b15b18d1",
        )

    def test_prepared_relations_are_catalog_relations(self) -> None:
        catalog = SqlCatalog.installed()
        self.assertEqual(len(PREPARED_RELATIONS), 14)
        self.assertTrue(PREPARED_RELATIONS <= set(catalog.relations))
        unprepared = {
            name.split(".", 1)[1]
            for name in set(catalog.relations) - PREPARED_RELATIONS
            if name.startswith("memory_v1.")
        }
        self.assertEqual(
            unprepared,
            {
                "entities",
                "entity_aliases",
                "entity_mentions",
                "mention_entities",
                "observation_topics",
                "topics",
            },
        )

    def test_order_basis_reports_projected_keys_then_witness_ties(self) -> None:
        tree = sqlglot.parse_one(
            "SELECT o.bead_id AS b, o.title FROM memory_v1.observations o "
            "ORDER BY o.bead_id DESC, o.title, o.recorded_at, o.summary",
            read="postgres",
        ).dump()
        basis = order_basis(tree, ["b", "title"])
        self.assertEqual(
            basis,
            {
                "columns": [
                    {"column": "b", "direction": "desc", "nulls": "first"},
                    {"column": "title", "direction": "asc", "nulls": "last"},
                ],
                "tie_basis": "witness_ordinal",
            },
        )
        unordered = sqlglot.parse_one("SELECT a FROM t", read="postgres").dump()
        self.assertEqual(order_basis(unordered, ["a"])["columns"], [])

    def test_lifecycle_frame_fields_only_with_relation_projection(self) -> None:
        common = {
            "frame_ref": "f",
            "known_at": "k",
            "snapshot_at": "s",
            "view": "historical",
            "snapshot_digest": "d" * 64,
            "relation_manifest_sha256": "r" * 64,
        }
        plain = wire_frame(relations=sorted(OBSERVATION_TABLES), **common)
        self.assertIsNone(plain["lifecycle_projection_version"])
        self.assertIsNone(plain["projection_manifest_sha256"])
        related = wire_frame(relations=sorted(RELATION_TABLES)[:1], **common)
        self.assertEqual(related["lifecycle_projection_version"], 1)
        self.assertEqual(related["projection_manifest_sha256"], "r" * 64)
        coverage = wire_coverage(
            relations=["memory_v1.source_units"], limited=True, recursion=True
        )
        self.assertEqual(coverage["population_basis"], "limited_query")
        self.assertEqual(coverage["truncation"], "explicit_limit")
        self.assertEqual(len(coverage["gaps"]), 3)

    def test_evidence_refs_come_only_from_sealed_population_pins(self) -> None:
        catalog = SqlCatalog.installed()
        statements = catalog.relations["memory_v1.statements"]
        values = {
            "statement_id": "11111111-1111-4111-8111-111111111111",
            "bead_id": "22222222-2222-4222-8222-222222222222",
            "bead_version_id": "33333333-3333-4333-8333-333333333333",
            "sequence": "1",
            "kind": "observation",
            "text": "fictional",
            "supersedes_statement_id": None,
            "correction_reason": None,
            "recorded_at": "2026-09-28T00:00:00.000000Z",
        }
        index = EvidenceIndex(
            {"memory_v1.statements": [[values[c.name] for c in statements.columns]]},
            {
                "44444444-4444-4444-8444-444444444444": {
                    "statement_id": values["statement_id"],
                    "source_unit_id": "55555555-5555-4555-8555-555555555555",
                    "content_sha256": "a" * 64,
                }
            },
        )
        schema: list[dict[str, str | None]] = [
            {"name": "s", "ref_type": "statement_ref"},
            {"name": "b", "ref_type": "bead_ref"},
            {"name": "e", "ref_type": "evidence_ref_ref"},
            {"name": "x", "ref_type": "bead_ref"},
            {"name": "t", "ref_type": None},
        ]
        refs = index.refs(
            schema,
            [
                values["statement_id"],
                values["bead_id"],
                "44444444-4444-4444-8444-444444444444",
                "66666666-6666-4666-8666-666666666666",
                "text",
            ],
        )
        self.assertEqual(
            sorted(ref["kind"] for ref in refs), ["observation", "source", "statement"]
        )
        self.assertNotIn("66666666-6666-4666-8666-666666666666", json.dumps(refs))

    def test_reply_size_converges_to_its_own_transport_charge(self) -> None:
        before = {
            "accesses": 128,
            "db_ms": 300000,
            "transport_bytes": 16777216,
            "allocation_bytes": 134217728,
            "run_expires_at": "2026-09-28T00:30:00.000000Z",
        }
        data, transport = stable_reply_bytes(
            lambda transport, remaining: reply(
                run_ref=None,
                step_key=None,
                outcome="unavailable",
                receipt_ref=None,
                access_receipt_ref=None,
                remaining=remaining,
                error={"code": "unavailable"},
            ),
            charge={"db_ms": 7},
            remaining_before=before,
        )
        self.assertEqual(len(data), transport)
        decoded = json.loads(data)
        self.assertEqual(decoded["remaining"]["transport_bytes"], 16777216 - transport)
        self.assertEqual(decoded["remaining"]["accesses"], 127)
        self.assertEqual(result_json_bytes(decoded), data)
        self.assertEqual(
            remaining_after(before, db_ms=5, transport_bytes=9)["db_ms"], 299995
        )
        with self.assertRaises(ValueError):
            reply(
                run_ref=None,
                step_key=None,
                outcome="unavailable",
                receipt_ref=None,
                access_receipt_ref=None,
                remaining=None,
                extra={"result": {}},
            )


if __name__ == "__main__":
    unittest.main()
