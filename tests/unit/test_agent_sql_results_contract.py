"""Database-free checks of the query/reuse reply mechanics; no execution claim."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import sqlglot

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import (
    AGENT_RELATION_TABLES,
    OBSERVATION_TABLES,
    POLICY_HASH,
    PREPARED_RELATIONS,
    RELATION_HISTORY_FACET,
    RELATION_HISTORY_TABLES,
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
from memoriesql.contracts import load_catalog

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

    def test_installed_stage_says_what_executes_and_claims_no_delivery(self) -> None:
        # Agents read this text from the installed catalog, so it must match
        # what executes. The approved packet forbids registering the contract
        # as delivered, so every "delivered" is negated.
        catalog = load_catalog("sql-recall-operations")
        logical = catalog["logical_catalog"]
        assert isinstance(logical, dict)
        stage, note = logical["admission_stage"], catalog["note"]
        assert isinstance(stage, str) and isinstance(note, str)
        self.assertTrue(stage.startswith("trusted_executor_preview;"))
        self.assertIn(
            "query and reuse_result execute through the trusted executor", stage
        )
        for text in (stage, note):
            self.assertIn(
                "inspect, hydrate_source and checkpoints are not delivered", text
            )
            self.assertNotIn("not yet delivered", text)
            self.assertEqual(
                re.findall(r"\bdelivered\b", text),
                re.findall(r"(?<=\bnot )delivered\b", text),
            )
            self.assertNotRegex(text, r"(?<!un)released")

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
        self.assertEqual(
            coverage["gaps"],
            [
                {"facet": "source_units.occurrence_ref", "reason": "unsupported"},
                {"facet": "source_units.trust_label", "reason": "unsupported"},
                {
                    "facet": "source_units.search_text",
                    "reason": "normalized_text_not_exact_source",
                },
            ],
        )
        labelled = wire_coverage(
            relations=["memory_v1.source_units"],
            limited=False,
            recursion=False,
            source_text_labels=("normalized_projection:fictional.v1",),
        )
        self.assertEqual(
            labelled["gaps"][-1],
            {
                "facet": "source_units.search_text",
                "reason": "normalized_projection:fictional.v1",
            },
        )
        # Missing caller capabilities are disclosed per family, never as absence.
        withheld = wire_coverage(
            relations=["memory_v1.assessed_relations", "memory_v1.observations"],
            limited=False,
            recursion=False,
            relation_raw_authority=False,
            source_read_authority=False,
        )
        self.assertEqual(
            withheld["gaps"],
            [
                {"facet": "relation_tables", "reason": "source_raw_read_required"},
                {"facet": "observation_tables", "reason": "source_read_required"},
            ],
        )
        self.assertEqual(
            wire_coverage(
                relations=["memory_v1.observations"],
                limited=False,
                recursion=False,
                relation_raw_authority=False,
            )["gaps"],
            [],
        )

    def test_agent_relation_reads_follow_am5(self) -> None:
        # Six relation tables are readable under AM-5; history and pair
        # coverage stay owner-only.
        self.assertEqual(
            RELATION_HISTORY_TABLES,
            {
                "memory_v1.relation_events",
                "memory_v1.relation_event_evidence",
                "memory_v1.relation_pairs",
            },
        )
        self.assertEqual(len(AGENT_RELATION_TABLES), 6)
        self.assertEqual(
            AGENT_RELATION_TABLES | RELATION_HISTORY_TABLES, RELATION_TABLES
        )
        self.assertFalse(AGENT_RELATION_TABLES & RELATION_HISTORY_TABLES)

        def gaps(
            relations: list[str],
            relation_read_mode: str | None = None,
            source_read_authority: bool = True,
        ) -> list[dict[str, str]]:
            coverage = wire_coverage(
                relations=relations,
                limited=False,
                recursion=False,
                relation_raw_authority=False,
                source_read_authority=source_read_authority,
                relation_read_mode=relation_read_mode,
            )
            return list(coverage["gaps"])

        readable = ["memory_v1.assessed_relations", "memory_v1.relation_statements"]
        history = ["memory_v1.assessed_relations", "memory_v1.relation_events"]
        owner_only = {"facet": RELATION_HISTORY_FACET, "reason": "owner_only"}
        # An authorized agent: readable tables carry no gap; history is owner-only.
        self.assertEqual(gaps(readable, relation_read_mode="agent"), [])
        self.assertEqual(gaps(history, relation_read_mode="agent"), [owner_only])
        self.assertEqual(
            gaps(["memory_v1.relation_pairs"], relation_read_mode="agent"), [owner_only]
        )
        # Without source.read every relation table is withheld and says so;
        # history stays owner-only either way (source.read would not reveal it).
        self.assertEqual(
            gaps(readable, relation_read_mode="agent", source_read_authority=False),
            [{"facet": "relation_tables", "reason": "source_read_required"}],
        )
        self.assertEqual(
            gaps(history, relation_read_mode="agent", source_read_authority=False),
            [
                {"facet": "relation_tables", "reason": "source_read_required"},
                owner_only,
            ],
        )
        # The raw-authority gap survives only for frames without a read mode.
        self.assertEqual(
            gaps(readable),
            [{"facet": "relation_tables", "reason": "source_raw_read_required"}],
        )
        # Owner mode discloses no relation gap.
        self.assertEqual(
            wire_coverage(
                relations=history,
                limited=False,
                recursion=False,
                relation_read_mode="owner",
            )["gaps"],
            [],
        )

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
