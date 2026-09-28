"""Seen fictional native bounded-path witnesses and private atomic publication."""

from __future__ import annotations

import json
import os
import unittest
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

from memoriesql.application.agent_sql_admission import RecursionBound, admit_query
from memoriesql.application.agent_sql_catalog import SqlCatalog, SqlParameter
from memoriesql.application.agent_sql_witness import compile_bag_witness
from memoriesql.application.investigation_contracts import QueryRequest
from memoriesql.infrastructure.postgres import restricted_query as runtime
from memoriesql.infrastructure.postgres.query_witness import NativeWitnessBuilder
from memoriesql.infrastructure.postgres.relation_sql_population import (
    PreparedRelationPopulation,
)

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_native as native_fixture
    from tests.runtime import test_query_result_commit as result_fixture
else:
    import test_agent_sql_native as native_fixture
    import test_query_result_commit as result_fixture


def path_sql(
    depth: int,
    *,
    seed: str = "memory_v1.observations",
    outer_filter: str | None = None,
) -> str:
    column = "bead_id" if seed.endswith("observations") else "source_bead_id"
    statement = (
        "WITH RECURSIVE walk(node, depth) AS ("
        f"SELECT a.{column}, 0 FROM {seed} a WHERE a.{column}=$1 "
        "UNION ALL SELECT r.target_bead_id, w.depth+1 "
        "FROM walk w JOIN memory_v1.assessed_relations r "
        "ON r.source_bead_id=w.node "
        f"WHERE w.depth < {depth}) CYCLE node SET is_cycle USING path "
        "SELECT node,depth,is_cycle FROM walk"
    )
    return statement + (f" WHERE {outer_filter}" if outer_filter else "")


def native_population(
    fixture: type[native_fixture.AgentSqlNative],
    rows: dict[str, tuple[tuple[Any, ...], ...]],
) -> PreparedRelationPopulation:
    now = datetime.now(UTC)
    return PreparedRelationPopulation(
        known_at=now,
        snapshot_at=now,
        catalog_hash=fixture.catalog.hash,
        schemas=fixture.catalog.relations,
        rows=rows,
        evidence_bindings={},
        dependency_records=b"[]",
        dependency_manifest=b"[]",
        dependency_manifest_sha256="0" * 64,
        preparation_bytes=0,
    )


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class QueryRecursiveNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        native_fixture.AgentSqlNative.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        native_fixture.AgentSqlNative.tearDownClass()

    def evaluate(
        self,
        depth: int,
        anchor: UUID,
        *,
        seed: str = "memory_v1.observations",
        outer_filter: str | None = None,
    ) -> tuple[dict[str, Any], tuple[tuple[Any, ...], ...], list[Any]]:
        fixture = native_fixture.AgentSqlNative
        sql = path_sql(depth, seed=seed, outer_filter=outer_filter)
        query = admit_query(
            sql,
            (SqlParameter(1, "bead_ref", str(anchor)),),
            admitted_anchors=frozenset({("bead_ref", str(anchor))}),
            recursion=RecursionBound("walk", "depth", "node", depth),
        )
        plan = compile_bag_witness(query, fixture.catalog.relations)
        with fixture.db.cursor() as cursor:
            cursor.execute(query.sql, query.parameters)
            plain = tuple(cursor.fetchall())
            cursor.execute(plan.sql, query.parameters)
            transported = cursor.fetchall()
            rows = {}
            for name in ("memory_v1.observations", "memory_v1.assessed_relations"):
                cursor.execute("SELECT * FROM " + name)
                rows[name] = tuple(cursor.fetchall())
        visible = tuple(
            row[:-1] for row in transported if row[-1][0] is not None
        )
        self.assertEqual(Counter(visible), Counter(plain))
        population = native_population(fixture, rows)
        builder = NativeWitnessBuilder(plan, population, uuid4())
        for row in transported:
            trace = row[-1]
            builder.add(
                trace[1] if trace[0] is None else trace,
                published=trace[0] is not None,
            )
        graph: dict[str, Any] = json.loads(builder.seal().bytes)
        return graph, plain, transported

    def test_cycles_diamonds_all_depths_and_complete_path_ledger(self) -> None:
        fixture = native_fixture.AgentSqlNative
        for depth in range(1, 9):
            with self.subTest(depth=depth):
                graph, rows, _ = self.evaluate(depth, native_fixture.identity(1))
                expected: list[tuple[UUID, ...]] = []

                def walk(node: int, path: tuple[int, ...]) -> None:
                    route = (*path, node)
                    expected.append(tuple(native_fixture.identity(n) for n in route))
                    if node in path or len(route) - 1 == depth:
                        return
                    for source, target in fixture.edges:
                        if source == node and target != 5:
                            walk(target, route)

                walk(1, ())
                visits = [
                    node for node in graph["nodes"]
                    if node["operation"] == "recursion" and "cycle" in node
                ]
                actual = [tuple(UUID(n) for n in v["path_nodes"]) for v in visits]
                self.assertEqual(Counter(actual), Counter(expected))
                self.assertEqual(len(rows), len(graph["row_provenance"]))
                self.assertEqual(len(expected), len(graph["tested_nodes"]))
                self.assertTrue(all(v["explicit_depth"] == depth for v in visits))
                self.assertTrue(all(v["depth"] <= depth for v in visits))
                self.assertEqual(
                    sum(v["cycle"] for v in visits),
                    sum(route[-1] in route[:-1] for route in expected),
                )

    def test_high_degree_and_empty_seed_retain_complete_population(self) -> None:
        fixture = native_fixture.AgentSqlNative
        for offset in range(20):
            fixture.insert(
                "memory_v1.assessed_relations",
                700 + offset,
                {
                    "source_bead_id": native_fixture.identity(1),
                    "target_bead_id": native_fixture.identity(700 + offset),
                    "support_eligible": True,
                    "roots_status": "qualified",
                },
            )
        graph, rows, _ = self.evaluate(1, native_fixture.identity(1))
        self.assertEqual(len(rows), 23)
        self.assertEqual(len(graph["tested_nodes"]), 23)
        empty, no_rows, _ = self.evaluate(8, native_fixture.identity(999))
        self.assertEqual(no_rows, ())
        self.assertEqual(empty["row_provenance"], [])
        self.assertEqual(empty["tested_nodes"], [])
        self.assertEqual(
            set(empty["searched_relations"]),
            {"memory_v1.observations", "memory_v1.assessed_relations"},
        )
        self.assertTrue(empty["members"])

    def test_duplicate_anchor_rows_preserve_path_multiplicity(self) -> None:
        graph, rows, _ = self.evaluate(
            1,
            native_fixture.identity(1),
            seed="memory_v1.assessed_relations",
        )
        self.assertEqual(len(rows), 9)
        self.assertEqual(len(graph["row_provenance"]), 9)
        self.assertEqual(len(graph["tested_nodes"]), 9)
        visits = [
            n for n in graph["nodes"]
            if n["operation"] == "recursion" and "cycle" in n
        ]
        self.assertEqual(len(visits), 9)
        self.assertEqual(
            Counter(tuple(n["path_nodes"]) for n in visits)[
                (str(native_fixture.identity(1)),)
            ],
            3,
        )

    def test_outer_filter_cannot_erase_unselected_native_paths(self) -> None:
        _, all_rows, _ = self.evaluate(3, native_fixture.identity(1))
        graph, rows, _ = self.evaluate(
            3, native_fixture.identity(1), outer_filter="is_cycle"
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row[2] is True for row in rows))
        self.assertEqual(len(graph["row_provenance"]), 2)
        self.assertEqual(len(graph["tested_nodes"]), len(all_rows))
        visits = [
            n for n in graph["nodes"]
            if n["operation"] == "recursion" and "cycle" in n
        ]
        self.assertEqual(len(visits), len(all_rows))
        self.assertEqual(sum(n["cycle"] for n in visits), 2)

    def test_native_cycle_tamper_is_refused(self) -> None:
        graph, _, transported = self.evaluate(3, native_fixture.identity(1))
        self.assertTrue(any(n.get("cycle") for n in graph["nodes"]))
        fixture = native_fixture.AgentSqlNative
        query = admit_query(
            path_sql(3),
            (SqlParameter(1, "bead_ref", str(native_fixture.identity(1))),),
            admitted_anchors=frozenset(
                {("bead_ref", str(native_fixture.identity(1)))}
            ),
            recursion=RecursionBound("walk", "depth", "node", 3),
        )
        plan = compile_bag_witness(query, fixture.catalog.relations)
        rows = {}
        with fixture.db.cursor() as cursor:
            for name in ("memory_v1.observations", "memory_v1.assessed_relations"):
                cursor.execute("SELECT * FROM " + name)
                rows[name] = tuple(cursor.fetchall())
        builder = NativeWitnessBuilder(
            plan,
            native_population(fixture, rows),
            uuid4(),
        )
        cycling = next(
            row[-1][1] for row in transported
            if row[-1][0] is None and row[-1][1][-1] is True
        )
        altered = cycling.copy()
        altered[-1] = False
        with self.assertRaisesRegex(ValueError, "cycle/path mismatch"):
            builder.add(altered, published=False)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class QueryRecursiveCommit(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = result_fixture.QueryResultCommit()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def request(self, anchor: UUID, depth: int = 2) -> QueryRequest:
        request = self.harness.request(
            path_sql(depth, seed="memory_v1.assessed_relations"),
            parameters=[{"position": 1, "type": "bead_ref", "value": str(anchor)}],
        )
        return QueryRequest.model_validate(
            request.model_dump(mode="json")
            | {
                "recursion": {
                    "cte": "walk", "depth_column": "depth",
                    "node_column": "node", "max_depth": depth,
                }
            }
        )

    def execute(self, request: QueryRequest, owner: Any, population: Any, **kw: Any) -> Any:
        assert request.recursion is not None
        return self.harness.executor.execute(
            self.harness.db,
            population,
            owner,
            request.sql,
            tuple(
                p.sql_parameter(SqlCatalog.installed())
                for p in request.parameters
            ),
            recursion=RecursionBound("walk", "depth", "node", request.recursion.max_depth),
            **kw,
        )

    def test_relation_aware_native_values_atomic_replay_and_restart(self) -> None:
        h = self.harness
        source, target, _ = h.fixture.assertion()
        request = self.request(source)
        plain_request = result_fixture.replace_request_step(request)
        owner, plain_owner = h.reserve_request(request), h.reserve_request(plain_request)
        with h.population(request) as population:
            plain = self.execute(plain_request, plain_owner, population)
            result = self.execute(request, owner, population, collect_bag_witnesses=True)
            self.assertEqual((plain.outcome, result.outcome), ("complete", "complete"))
            self.assertEqual(result.rows, plain.rows)
            self.assertEqual(Counter(result.rows), Counter(((source, 0, False), (target, 1, False))))
            graph = json.loads(result.witnesses.bytes)
            self.assertEqual(len(graph["tested_nodes"]), 2)
            self.assertEqual(len(graph["row_provenance"]), 2)
            self.assertEqual(
                {n["relation"] for n in graph["members"] if n["member_ref"]},
                {"memory_v1.assessed_relations"},
            )
            candidate = h.candidate(request, population, result)
            changed = QueryRequest.model_validate(
                request.model_dump(mode="json")
                | {"recursion": {
                    "cte": "walk", "depth_column": "depth",
                    "node_column": "node", "max_depth": 1,
                }}
            )
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                h.candidate(changed, population, result)
            self.assertEqual(
                json.loads(candidate.content.content)["coverage"]["explicit_depth"], 2
            )
            with h.fixture.connection() as connection:
                receipt = h.adapter(connection).commit(owner, candidate)
                self.assertEqual(receipt["state"], "committed")
                self.assertTrue(h.adapter(connection).commit(owner, candidate)["replayed"])
            with h.fixture.connection() as connection:
                self.assertEqual(
                    h.adapter(connection).recover(owner)["content_digest"],
                    candidate.content_digest,
                )

    def test_disputed_edge_is_not_current_path_support_but_old_frame_replays(self) -> None:
        h = self.harness
        source, target, relation = h.fixture.assertion(key="derived_from")
        before = self.request(source)
        first_owner = h.reserve_request(before)
        with h.population(before) as population:
            initial = self.execute(
                before, first_owner, population, collect_bag_witnesses=True
            )
            self.assertEqual(initial.outcome, "complete")
            self.assertEqual(
                Counter(initial.rows),
                Counter(((source, 0, False), (target, 1, False))),
            )
        h.fixture.governance.record_event(
            h.fixture.lifecycle_command(source, relation, "dispute", "path-dispute")
        )
        current = self.request(source)
        current_owner = h.reserve_request(current)
        with h.population(current) as population:
            result = self.execute(
                current, current_owner, population, collect_bag_witnesses=True
            )
            self.assertEqual(result.outcome, "complete")
            self.assertEqual(result.rows, ((source, 0, False),))
            self.assertEqual(len(json.loads(result.witnesses.bytes)["tested_nodes"]), 1)
        historical = result_fixture.replace_request_step(before)
        old_owner = h.reserve_request(historical)
        with h.population(historical) as population:
            replay = self.execute(
                historical, old_owner, population, collect_bag_witnesses=True
            )
            self.assertEqual(replay.outcome, "complete")
            self.assertEqual(Counter(replay.rows), Counter(initial.rows))

    def test_cancelled_or_exhausted_path_has_no_partial_result(self) -> None:
        h = self.harness
        source, _, _ = h.fixture.assertion()
        request = self.request(source)
        owner = h.reserve_request(request)
        with h.population(request) as population:
            exhausted = self.execute(
                request, owner, population,
                collect_bag_witnesses=True, max_output_bytes=8192,
            )
            self.assertEqual(exhausted.outcome, "budget_exhausted")
            self.assertEqual(exhausted.rows, ())
            self.assertIsNone(exhausted.witnesses)
            self.assertIsNone(exhausted.invocation_ref)

        bounded = self.request(source)
        bounded_owner = h.reserve_request(bounded)
        with h.population(bounded) as population:
            overflow = self.execute(
                bounded, bounded_owner, population,
                collect_bag_witnesses=True, max_output_bytes=36864,
            )
            self.assertEqual(overflow.outcome, "budget_exhausted")
            self.assertEqual(overflow.rows, ())
            self.assertIsNone(overflow.witnesses)
            self.assertIsNotNone(overflow.invocation_ref)
            self.assertEqual(overflow.settlement_state, "settled")

        retry = self.request(source)
        retry_owner = h.reserve_request(retry)
        original = NativeWitnessBuilder.add

        def stop(builder: NativeWitnessBuilder, trace: Any, *, published: bool = True) -> Any:
            value = original(builder, trace, published=published)
            if published:
                raise runtime._StopQuery("cancelled")
            return value

        with h.population(retry) as population, patch.object(
            NativeWitnessBuilder, "add", stop
        ):
            cancelled = self.execute(
                retry, retry_owner, population, collect_bag_witnesses=True
            )
            self.assertEqual(cancelled.outcome, "cancelled")
            self.assertEqual(cancelled.rows, ())
            self.assertIsNone(cancelled.witnesses)
            self.assertEqual(cancelled.settlement_state, "settled")

    def test_revoked_source_cannot_publish_path_or_protected_metadata(self) -> None:
        h = self.harness
        source, _, _ = h.fixture.assertion()
        h.db.execute(
            "UPDATE memoriesql.protected_resources "
            "SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source'"
        )
        request = self.request(source)
        owner = h.reserve_request(request)
        with h.population(request) as population:
            self.assertTrue(all(not rows for rows in population.rows.values()))
            self.assertEqual(population.dependency_records, b"[]")
            result = self.execute(
                request, owner, population, collect_bag_witnesses=True
            )
            self.assertNotEqual(result.outcome, "complete")
            self.assertEqual(result.rows, ())
            self.assertIsNone(result.witnesses)
            self.assertIsNone(result.invocation_ref)


if __name__ == "__main__":
    unittest.main()
