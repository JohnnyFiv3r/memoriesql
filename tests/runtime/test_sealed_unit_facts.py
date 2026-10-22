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

import hashlib
import os
import time
import unittest
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg

from memoriesql.application.stored_bead_inspection import InspectStoredBead
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_helpers_runtime as helpers
    from tests.runtime import test_hidden_package_length as hidden
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_agent_sql_helpers_runtime as helpers
    import test_hidden_package_length as hidden
    from test_postgres_runtime import migrate

RELATIONS = (
    "SELECT array_agg(rel) FROM ("
    "SELECT %s::regclass::oid rel "
    "UNION SELECT reltoastrelid FROM pg_class WHERE oid=%s::regclass "
    "UNION SELECT indexrelid FROM pg_index WHERE indrelid IN ("
    "%s::regclass,(SELECT reltoastrelid FROM pg_class WHERE oid=%s::regclass))"
    ") r WHERE rel<>0"
)
PARTS = "memoriesql.evidence_package_parts"
FACTS = "memoriesql.logical_unit_query_facts"


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

    def relations(self, table: str) -> list[int]:
        """The table, its TOAST table and every index of either."""
        found: list[int] = self.scalar(RELATIONS, (table,) * 4)
        self.assertGreaterEqual(len(found), 3)
        return found

    def settled_blocks(self, relations: list[int]) -> int:
        """Blocks every finished backend fetched from these relations, once flushed.

        Service connections close after each request, and a backend flushes its
        statistics as it exits; this connection flushes on request.
        """
        deadline = time.monotonic() + 15
        while self.scalar(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
            "AND pid<>pg_backend_pid() AND backend_type='client backend'"
        ):
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.db.execute("SELECT pg_stat_force_next_flush()")
        previous = None
        while True:
            self.db.execute("SELECT pg_stat_clear_snapshot()")
            current = int(
                self.scalar(
                    "SELECT sum(pg_stat_get_blocks_fetched(r)) FROM unnest(%s::oid[]) r",
                    (relations,),
                )
            )
            if current == previous:
                return current
            previous = current
            time.sleep(0.5)

    def materialized(
        self,
        world: hidden.World,
        key: str,
        *,
        raw: str,
        parts: tuple[tuple[str, str], ...],
    ) -> UUID:
        """A unit materialized now from one capture of `raw`, as ordered parts.

        Each part is (derivation, content); a producer_normalized part holds its
        content, an identity_utf8 part its own raw slice. Every lineage is an
        exact sub-slice of the one capture.
        """
        original, create, append = (
            self.fixture.part,
            self.fixture.create,
            self.fixture.append,
        )
        fragments: dict[UUID, tuple[Any, ...]] = {}

        def digest(data: bytes) -> str:
            return hashlib.sha256(data).hexdigest()

        def captured(content: str, *args: Any, **kwargs: Any) -> Any:
            return original(raw, *args, **kwargs)

        def fragmented(declared: tuple[Any, ...], **updates: Any) -> Any:
            (part,) = declared
            (whole,) = part.lineage
            size = -(-len(raw) // len(parts))
            built, characters = [], 0
            for ordinal, (derivation, content) in enumerate(parts):
                data = raw.encode()[ordinal * size : (ordinal + 1) * size]
                self.assertTrue(data)
                body = data.decode() if derivation == "identity_utf8" else content
                built.append(
                    type(part).model_validate(
                        part.model_dump()
                        | {
                            "part_id": uuid4(),
                            "ordinal": ordinal,
                            "component_offset": characters,
                            "derivation": derivation,
                            "content": body,
                            "content_sha256": digest(body.encode()),
                            "lineage": [
                                whole.model_dump()
                                | {
                                    "byte_start": whole.byte_start + ordinal * size,
                                    "byte_end_exclusive": whole.byte_start
                                    + ordinal * size
                                    + len(data),
                                    "source_bytes_sha256": digest(data),
                                }
                            ],
                        }
                    )
                )
                characters += len(body)
            fragments[part.part_id] = tuple(built)
            return create(fragments[part.part_id], **updates)

        def appended(package: Any, part: Any, key: str | None = None) -> Any:
            for piece in fragments[part.part_id]:
                result = append(package, piece)
            return result

        captured.__module__ = original.__module__
        with (
            self.r.in_scope(world.scope),
            patch.object(self.fixture, "part", captured),
            patch.object(self.fixture, "create", fragmented),
            patch.object(self.fixture, "append", appended),
        ):
            bound: Any = self.fixture.unit(key, key, source=world.source)
        unit: UUID = self.scalar(
            "SELECT source_unit_id FROM memoriesql.logical_unit_materializations "
            "WHERE bead_id=%s",
            (bound.bead_id,),
        )
        return unit

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

    def test_query_paths_read_no_part_block_for_any_unit(self) -> None:
        self.upgrade()
        relations = self.relations(PARTS)
        tenant = self.fixture.tenant
        # Every unit of the 1-part and the 64-part worlds, normalized ones too.
        for world in self.worlds[1:]:
            units = [
                self.unit(b) for b in (world.ledger, world.normalized, world.identity)
            ]
            with self.subTest(world=world.identity), self.db.transaction():
                self.fixture.begin()
                self.db.execute("RESET ROLE")
                before = self.blocks(relations)
                texts = []
                for unit in units:
                    self.db.execute(
                        "SELECT memoriesql.query_unit_records_v1(%s,%s,clock_timestamp())",
                        (tenant, unit),
                    )
                    row = self.db.execute(
                        "SELECT memoriesql.query_unit_package_v1(%s,%s)->>'text'",
                        (tenant, unit),
                    ).fetchone()
                    assert row is not None
                    texts.append(row[0])
                self.assertEqual(texts, [self.LEDGER, self.TEXT, None])
                self.assertEqual(self.blocks(relations), before)
                # The closure built the full record from every part row each time.
                self.db.execute(
                    "SELECT memoriesql.query_unit_package_record_v1(%s,%s)",
                    (tenant, units[-1]),
                )
                self.assertGreater(self.blocks(relations), before)

    def test_operations_read_no_part_block_end_to_end(self) -> None:
        """A non-raw agent's whole operations read no part block, after a control."""
        # Settled counters must not include maintenance reads of the parts.
        for table in (PARTS,):
            self.db.execute(
                f"ALTER TABLE {table} SET (autovacuum_enabled=false,"
                "toast.autovacuum_enabled=false)"
            )
        relations = self.relations(PARTS)
        world = self.worlds[2]
        migrate(
            self.db,
            expected_current_version=hidden.closure() - 1,
            target_version=hidden.closure(),
        )
        before = self.settled_blocks(relations)
        run = self.r.start(world.agent)
        _, control = self.r.query(run, hidden.QUERY, page_size=50, secret=world.agent)
        self.assertEqual(control["outcome"], "available", control)
        self.r.close(run, secret=world.agent)
        # Under the closure alone the population still built each record from parts.
        self.assertGreater(self.settled_blocks(relations), before)
        migrate(
            self.db, expected_current_version=hidden.closure(), target_version=facts()
        )
        # The test's own hydration lookup of a part id is answered from a read
        # made before any window, so a window counts only the operations.
        known = {
            case.normalized: self.scalar(
                "SELECT pp.part_id FROM memoriesql.logical_unit_materializations m "
                "JOIN memoriesql.evidence_package_parts pp "
                "ON pp.tenant_id=m.tenant_id AND pp.package_id=m.package_id "
                "WHERE m.bead_id=%s",
                (case.normalized,),
            )
            for case in self.worlds
        }
        lookup = self.scalar

        def scalar(statement: str, values: tuple[Any, ...] = ()) -> Any:
            if "evidence_package_parts" in statement:
                return known[values[0]]
            return lookup(statement, values)

        windows: list[tuple[int, int]] = []
        for case in self.worlds:
            with (
                self.subTest(world=case.identity),
                patch.object(self, "scalar", scalar),
            ):
                before = self.settled_blocks(relations)
                exact, _ = self.measure(case)
                run = self.r.start(case.agent)
                request = {
                    "contract_version": 1,
                    "run_ref": run["run_ref"],
                    "step_key": str(uuid4()),
                    "kind": "inspect",
                    "observation_refs": [
                        self.target(b)
                        for b in (case.ledger, case.normalized, case.identity)
                    ],
                    "relation_refs": [],
                    "result": None,
                    "provenance_ref": None,
                    "facets": ["content"],
                    "view": "historical",
                    "known_at": run["default_known_at"],
                    "cursor": None,
                }
                first = self.r.send(request, secret=case.agent)
                replay = self.r.send(request, secret=case.agent)
                self.assertEqual(first["outcome"], "available", first)
                self.assertEqual(replay["receipt_ref"], first["receipt_ref"])
                self.r.close(run, secret=case.agent)
                inspected = PostgresStoredBeadInspection(
                    self.db,
                    credential_sha256=case.agent,
                    workspace_id=self.fixture.workspace,
                ).inspect(InspectStoredBead(bead_id=case.identity))
                # Stored-bead inspection is raw-gated (0043): a non-raw agent is
                # refused at revisiting authority before any record is built.
                self.assertEqual(
                    (inspected.outcome, inspected.bead), ("unavailable", None)
                )
                self.assertTrue(exact)
                after = self.settled_blocks(relations)
                windows.append((before, after))
                self.assertEqual(after, before)
        # Every world's window was measured: a refused or empty step can never
        # leave a world's zero-block equality unrun.
        self.assertEqual(len(windows), len(self.worlds))
        for before, after in windows:
            self.assertIsInstance(before, int)
            self.assertIsInstance(after, int)

    def test_normalized_text_costs_only_its_visible_length(self) -> None:
        self.upgrade()
        world = self.worlds[0]
        text = "Fictional normalized orchard ledger line. " * 8
        base, extra = divmod(len(text), 64)
        bounds = [i * base + min(i, extra) for i in range(65)]
        chunks = tuple(text[bounds[i] : bounds[i + 1]] for i in range(64))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(chunks))
        one = self.materialized(
            world,
            "w1-one",
            raw=hidden.hidden("w1-one", 640),
            parts=(("producer_normalized", text),),
        )
        many = self.materialized(
            world,
            "w1-many",
            raw=hidden.hidden("w1-many", 640),
            parts=tuple(("producer_normalized", chunk) for chunk in chunks),
        )
        mixed = self.materialized(
            world,
            "w1-mixed",
            raw=hidden.hidden("w1-mixed", 640),
            parts=(("producer_normalized", chunks[0]), ("identity_utf8", "")),
        )
        self.assertEqual(
            [
                self.scalar(
                    "SELECT count(*) FROM memoriesql.logical_unit_materializations m "
                    "JOIN memoriesql.evidence_package_parts pp "
                    "ON pp.tenant_id=m.tenant_id AND pp.package_id=m.package_id "
                    "WHERE m.source_unit_id=%s",
                    (unit,),
                )
                for unit in (one, many, mixed)
            ],
            [1, 64, 2],
        )
        parts, sealed = self.relations(PARTS), self.relations(FACTS)
        reads = []
        for unit in (one, many, mixed):
            with self.subTest(unit=unit), self.db.transaction():
                before = self.blocks(parts), self.blocks(sealed)
                row = self.db.execute(
                    "SELECT memoriesql.query_unit_package_v1(%s,%s)",
                    (self.fixture.tenant, unit),
                ).fetchone()
                assert row is not None
                self.assertEqual(self.blocks(parts), before[0])
                reads.append(
                    (
                        row[0]["normalized"],
                        row[0]["text"],
                        self.blocks(sealed) - before[1],
                    )
                )
        self.assertEqual(reads[0], reads[1])
        self.assertEqual(reads[0][:2], (True, text))
        self.assertEqual(reads[2][:2], (False, None))
        # The trigger sealed each from the one definition, the closure's values.
        for unit in (one, many, mixed):
            sealed_rows = self.db.execute(
                "SELECT f.record_sha256=x.record_sha256,"
                "f.normalized IS NOT DISTINCT FROM x.normalized,"
                "f.normalized_text IS NOT DISTINCT FROM x.normalized_text "
                "FROM memoriesql.logical_unit_query_facts f, "
                "memoriesql.unit_query_facts_v1(f.tenant_id,f.source_unit_id) x "
                "WHERE f.source_unit_id=%s",
                (unit,),
            ).fetchall()
            self.assertEqual(sealed_rows, [(True, True, True)])

    def test_the_application_role_can_neither_read_nor_write_facts(self) -> None:
        self.upgrade()
        unit = self.unit(self.worlds[0].identity)
        for statement in (
            "SELECT count(*) FROM memoriesql.logical_unit_query_facts",
            "INSERT INTO memoriesql.logical_unit_query_facts SELECT * FROM "
            "memoriesql.logical_unit_query_facts WHERE source_unit_id=%s",
        ):
            with (
                self.subTest(statement=statement[:6]),
                self.assertRaises(psycopg.errors.InsufficientPrivilege),
                self.db.transaction(),
            ):
                self.db.execute("SET LOCAL ROLE memoriesql_application")
                self.db.execute(statement, (unit,) if "%s" in statement else ())

    def test_every_materialization_has_the_closure_digest_once(self) -> None:
        self.upgrade()
        self.original_package_function()
        text = self.db.execute(
            "SELECT count(*),count(*) FILTER (WHERE f.normalized_text IS NOT DISTINCT FROM "
            "pg_temp.original_unit_package(f.tenant_id,f.source_unit_id)->>'text') "
            "FROM memoriesql.logical_unit_query_facts f"
        ).fetchone()
        assert text is not None
        self.assertEqual(text[0], text[1])
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
