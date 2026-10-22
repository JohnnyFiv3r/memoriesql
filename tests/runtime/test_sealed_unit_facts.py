"""Fictional sealed unit facts regressions; execute only in an assigned DB slot.

The hidden-length worlds rerun with the facts installed: the pre-closure
control, the three-world invariance, the TOAST check, the digests and the
transition. On top, query paths must read no block of any part relation for a
hidden package (exact, against the closure's own record build), every
materialization's facts must equal the closure's digest, results saved under
the closure must still reuse, and a new materialization must record its facts
once and immutably. No model or provider is called.
"""

from __future__ import annotations

import os
import unittest
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_helpers_runtime as helpers
    from tests.runtime import test_hidden_package_length as hidden
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_agent_sql_helpers_runtime as helpers
    import test_hidden_package_length as hidden
    from test_postgres_runtime import migrate

PARTS_RELATIONS = (
    "SELECT array_agg(rel) FROM ("
    "SELECT 'memoriesql.evidence_package_parts'::regclass::oid rel "
    "UNION SELECT reltoastrelid FROM pg_class "
    "WHERE oid='memoriesql.evidence_package_parts'::regclass "
    "UNION SELECT indexrelid FROM pg_index WHERE indrelid IN ("
    "'memoriesql.evidence_package_parts'::regclass,(SELECT reltoastrelid FROM pg_class "
    "WHERE oid='memoriesql.evidence_package_parts'::regclass))) r"
)


def facts() -> int:
    return hidden.installed("sealed_unit_query_facts")[0]


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class SealedUnitFacts(hidden.HiddenPackageLength):
    def upgrade(self) -> None:
        migrate(
            self.db,
            expected_current_version=hidden.closure() - 1,
            target_version=facts(),
        )

    def blocks(self, relations: list[int]) -> int:
        """Blocks this transaction fetched from the part heap, TOAST and indexes."""
        row = self.db.execute(
            "SELECT sum(pg_stat_get_xact_blocks_fetched(r)) FROM unnest(%s::oid[]) r",
            (relations,),
        ).fetchone()
        assert row is not None
        return int(row[0])

    def test_package_values_are_unchanged_for_every_package(self) -> None:
        # Facts keep every key of the package value but the unread parts list.
        self.upgrade()
        self.original_package_function()
        tenant = self.fixture.tenant
        for world in self.worlds:
            for bead in (world.ledger, world.normalized, world.identity):
                with self.subTest(bead=bead):
                    unit = self.unit(bead)
                    original = self.scalar(
                        "SELECT pg_temp.original_unit_package(%s,%s)", (tenant, unit)
                    )
                    self.assertIsNotNone(original)
                    self.assertEqual(
                        self.scalar(
                            "SELECT memoriesql.query_unit_package_v1(%s,%s)",
                            (tenant, unit),
                        ),
                        {k: v for k, v in original.items() if k != "parts"},
                    )
                    self.assertEqual(
                        self.scalar(
                            "SELECT memoriesql.query_unit_package_record_v1(%s,%s)",
                            (tenant, unit),
                        ),
                        {k: v for k, v in original.items() if k != "text"},
                    )

    def test_query_paths_read_no_part_block_of_a_hidden_package(self) -> None:
        self.upgrade()
        relations = self.scalar(PARTS_RELATIONS)
        self.assertGreaterEqual(len(relations), 4)
        tenant = self.fixture.tenant
        # The 1-part and the 64-part hidden identity packages.
        for world in self.worlds[1:]:
            units = [
                self.unit(b) for b in (world.ledger, world.normalized, world.identity)
            ]
            identity = units[-1]
            with self.subTest(world=world.identity), self.db.transaction():
                self.fixture.begin()
                self.db.execute("RESET ROLE")
                before = self.blocks(relations)
                for unit in units:
                    self.db.execute(
                        "SELECT memoriesql.query_unit_records_v1(%s,%s,clock_timestamp())",
                        (tenant, unit),
                    )
                package = self.db.execute(
                    "SELECT memoriesql.query_unit_package_v1(%s,%s)", (tenant, identity)
                ).fetchone()
                assert package is not None
                self.assertEqual(
                    (package[0]["normalized"], package[0]["text"]), (False, None)
                )
                self.assertEqual(self.blocks(relations), before)
                # The closure built the full record from every part row each time.
                self.db.execute(
                    "SELECT memoriesql.query_unit_package_record_v1(%s,%s)",
                    (tenant, identity),
                )
                self.assertGreater(self.blocks(relations), before)

    def test_every_materialization_has_the_closure_digest_once(self) -> None:
        self.upgrade()
        rows = self.db.execute(
            "SELECT m.source_unit_id,f.package_id=m.package_id,f.record_sha256,"
            "memoriesql.lifecycle_hash_v1(memoriesql.query_unit_package_record_v1("
            "m.tenant_id,m.source_unit_id)||jsonb_build_object('source_unit_id',"
            "m.source_unit_id)),f.normalized IS NOT DISTINCT FROM (memoriesql."
            "query_unit_package_record_v1(m.tenant_id,m.source_unit_id)->>'normalized')"
            "::boolean FROM memoriesql.logical_unit_materializations m "
            "LEFT JOIN memoriesql.logical_unit_query_facts f "
            "ON f.tenant_id=m.tenant_id AND f.source_unit_id=m.source_unit_id"
        ).fetchall()
        self.assertGreaterEqual(len(rows), 9)
        for unit, same_package, sealed, closure, same_gate in rows:
            with self.subTest(unit=unit):
                self.assertIsNotNone(sealed)
                self.assertTrue(same_package)
                self.assertEqual(sealed, closure)
                self.assertTrue(same_gate)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.logical_unit_query_facts"),
            len(rows),
        )

    def test_results_saved_under_the_closure_still_reuse(self) -> None:
        world = self.worlds[2]
        migrate(
            self.db,
            expected_current_version=hidden.closure() - 1,
            target_version=hidden.closure(),
        )
        run = self.r.start(world.agent)
        _, saved = self.r.query(run, hidden.QUERY, page_size=50, secret=world.agent)
        self.assertEqual(saved["outcome"], "available", saved)
        self.assertEqual(saved["result"]["total_rows"], "3")
        migrate(
            self.db, expected_current_version=hidden.closure(), target_version=facts()
        )
        # Same digests: no second invalidation of saved results.
        again = self.r.reuse(run, saved["result"], page_size=50, secret=world.agent)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["page"]["rows"], saved["page"]["rows"])

    def test_a_new_materialization_records_its_facts_once_and_immutably(self) -> None:
        self.upgrade()
        world = self.worlds[0]
        with self.r.in_scope(world.scope):
            bound: Any = self.fixture.unit(
                "Fictional late harvest note.", "w1-late", source=world.source
            )
        unit: UUID = self.scalar(
            "SELECT source_unit_id FROM memoriesql.logical_unit_materializations "
            "WHERE bead_id=%s",
            (bound.bead_id,),
        )
        row = self.db.execute(
            "SELECT f.record_sha256,(SELECT x.record_sha256 FROM "
            "memoriesql.unit_query_facts_v1(f.tenant_id,f.source_unit_id) x),f.normalized "
            "FROM memoriesql.logical_unit_query_facts f WHERE f.source_unit_id=%s",
            (unit,),
        ).fetchall()
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0][0], row[0][1])
        self.assertFalse(row[0][2])
        for statement in (
            "UPDATE memoriesql.logical_unit_query_facts SET normalized=true "
            "WHERE source_unit_id=%s",
            "DELETE FROM memoriesql.logical_unit_query_facts WHERE source_unit_id=%s",
        ):
            with (
                self.subTest(statement=statement[:6]),
                self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState),
                self.db.transaction(),
            ):
                self.db.execute(statement, (unit,))
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.logical_unit_query_facts "
                "WHERE source_unit_id=%s",
                (unit,),
            ),
            1,
        )


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class HelpersWithFacts(helpers.AgentSqlHelperRuntime):
    """Every installed helper regression, rerun with the sealed facts installed."""

    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=46, target_version=facts())


if __name__ == "__main__":
    unittest.main()
