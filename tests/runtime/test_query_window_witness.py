"""Seen fictional native ranking and neighbor witnesses; no public disclosure."""

from __future__ import annotations

import json
import os
import unittest
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from psycopg import sql

from memoriesql.infrastructure.postgres import restricted_query as runtime
from memoriesql.infrastructure.postgres.query_witness import NativeWitnessBuilder

if TYPE_CHECKING:
    from tests.runtime import test_query_result_commit as fixtures
else:
    import test_query_result_commit as fixtures


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class QueryWindowWitness(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = fixtures.QueryResultCommit()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.fixture = self.harness.fixture

    def compare(
        self,
        statement: str,
        *,
        commit: bool = False,
        parameters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        h = self.harness
        request = h.request(statement, parameters=parameters)
        plain_request = fixtures.replace_request_step(request)
        owner, plain_owner = (
            h.reserve_request(request),
            h.reserve_request(plain_request),
        )
        try:
            with h.population(request) as population:
                plain = h.execute_request(
                    plain_request, plain_owner, population, witnesses=False
                )
                result = h.execute_request(request, owner, population)
                self.assertEqual(plain.outcome, "complete", plain)
                self.assertEqual(result.outcome, "complete", result)
                self.assertEqual(result.columns, plain.columns)
                self.assertEqual(result.rows, plain.rows)
                self.assertIsNotNone(result.witnesses)
                graph: dict[str, Any] = json.loads(result.witnesses.bytes)
                self.assertEqual(len(graph["row_provenance"]), len(result.rows))
                if commit:
                    candidate = h.candidate(request, population, result)
                    with self.fixture.connection() as control:
                        receipt = h.adapter(control).commit(owner, candidate)
                        self.assertEqual(receipt["state"], "committed")
                        self.assertEqual(
                            h.adapter(control).commit(owner, candidate)["result_id"],
                            receipt["result_id"],
                        )
                    with self.fixture.connection() as control:
                        self.assertEqual(
                            h.adapter(control).recover(owner)["content_digest"],
                            candidate.content_digest,
                        )
                return graph
        finally:
            for pin in (owner, plain_owner):
                h.store.discard(
                    operation_ref=pin.operation_ref, ownership_ref=pin.ownership_ref
                )

    def test_partition_order_and_row_number_match_native_values(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            """SELECT s.statement_id,r.state,
            row_number() OVER(PARTITION BY r.state ORDER BY r.relation_id,s.relation_id,s.role,s.statement_id) AS position
            FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s
                ON r.relation_id=s.relation_id ORDER BY statement_id""",
            commit=True,
        )
        partition = next(
            n for n in graph["nodes"] if n["operation"] == "window_partition"
        )
        rows = [n for n in graph["nodes"] if n["operation"] == "window"]
        self.assertEqual(len(partition["ordered_input_refs"]), 2)
        self.assertEqual(sorted(n["ordinal"] for n in rows), [1, 2])
        self.assertEqual({n["partition_ref"] for n in rows}, {partition["node_ref"]})
        self.assertEqual(sum(partition["input_multiplicities"]), 2)

    def test_rank_and_dense_rank_keep_native_peer_spans(self) -> None:
        self.fixture.assertion()
        for function in ("rank", "dense_rank"):
            with self.subTest(function=function):
                graph = self.compare(
                    "SELECT s.statement_id,r.state,"
                    + function
                    + "() OVER(PARTITION BY r.state ORDER BY r.state) AS place "
                    + "FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s "
                    + "ON r.relation_id=s.relation_id ORDER BY statement_id"
                )
                partition = next(
                    n for n in graph["nodes"] if n["operation"] == "window_partition"
                )
                self.assertEqual(
                    partition["peer_spans"],
                    [{"rank": 1, "start_ordinal": 1, "end_ordinal": 2}],
                )
                self.assertTrue(
                    all(
                        n["peer_span"] == [1, 2]
                        for n in graph["nodes"]
                        if n["operation"] == "window"
                    )
                )

    def test_distinct_native_partitions_do_not_share_members(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT s.statement_id,s.role,"
            "rank() OVER(PARTITION BY s.role ORDER BY s.statement_id) AS place "
            "FROM memory_v1.relation_statements s ORDER BY statement_id",
            commit=True,
        )
        partitions = [
            n for n in graph["nodes"] if n["operation"] == "window_partition"
        ]
        self.assertEqual(len(partitions), 2)
        self.assertEqual(
            sorted(len(n["ordered_input_refs"]) for n in partitions), [1, 1]
        )
        self.assertEqual(
            len(
                {
                    n["partition_ref"]
                    for n in graph["nodes"]
                    if n["operation"] == "window"
                }
            ),
            2,
        )

    def test_lag_lead_and_missing_neighbors_preserve_source_identity(self) -> None:
        self.fixture.assertion()
        for function in ("lag", "lead"):
            with self.subTest(function=function):
                graph = self.compare(
                    "SELECT s.statement_id,"
                    + function
                    + "(s.statement_id,1,s.statement_id) OVER(ORDER BY r.relation_id,s.relation_id,s.role,s.statement_id) AS adjacent "
                    + "FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s "
                    + "ON r.relation_id=s.relation_id ORDER BY statement_id",
                    commit=True,
                )
                rows = [n for n in graph["nodes"] if n["operation"] == "window"]
                self.assertEqual(len(rows), 2)
                self.assertEqual(sum(n["neighbor_exists"] for n in rows), 1)
                self.assertEqual(sum("neighbor_ref" in n for n in rows), 1)
                self.assertEqual({n["ordinal"] for n in rows}, {1, 2})

    def test_zero_and_maximum_neighbor_offsets_use_exact_native_semantics(self) -> None:
        self.fixture.assertion()
        for function, offset, expected in (("lag", 0, 2), ("lead", 64, 0)):
            with self.subTest(function=function, offset=offset):
                graph = self.compare(
                    "SELECT s.statement_id,"
                    + function
                    + f"(s.statement_id,{offset}) OVER(ORDER BY s.relation_id,s.role,s.statement_id) AS adjacent "
                    + "FROM memory_v1.relation_statements s ORDER BY statement_id"
                )
                rows = [n for n in graph["nodes"] if n["operation"] == "window"]
                self.assertEqual(sum(n["neighbor_exists"] for n in rows), expected)
                self.assertEqual(sum("neighbor_ref" in n for n in rows), expected)

    def test_grouped_cte_and_window_compose_without_duplicate_native_work(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            """WITH grouped AS (
                SELECT r.relation_id,count(*) AS n FROM memory_v1.assessed_relations r
                GROUP BY r.relation_id)
            SELECT relation_id,n,row_number() OVER(ORDER BY relation_id) AS position
            FROM grouped ORDER BY relation_id""",
            commit=True,
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "group"]), 1
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window"]), 1
        )
        self.assertEqual(len(graph["row_provenance"]), 1)

    def test_windowed_set_branches_keep_both_native_populations(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT relation_id,rank() OVER(ORDER BY state) AS place "
            "FROM memory_v1.assessed_relations "
            "UNION ALL "
            "SELECT relation_id,rank() OVER(ORDER BY state) AS place "
            "FROM memory_v1.assessed_relations ORDER BY relation_id",
            commit=True,
        )
        self.assertEqual(len(graph["row_provenance"]), 2)
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window_partition"]),
            2,
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "set"]), 2
        )

    def test_aggregate_rows_frames_share_partition_membership(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT s.statement_id,"
            "count(*) OVER(ORDER BY s.statement_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n,"
            "sum(r.author_confidence) FILTER(WHERE r.support_eligible) "
            "OVER(ORDER BY s.statement_id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS total,"
            "avg(r.author_confidence) OVER(ORDER BY s.statement_id ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS mean "
            "FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s "
            "ON r.relation_id=s.relation_id ORDER BY s.statement_id",
            commit=True,
        )
        partitions = [
            n for n in graph["nodes"] if n["operation"] == "window_partition"
        ]
        windows = [n for n in graph["nodes"] if n["operation"] == "window"]
        self.assertEqual(len(partitions), 1)
        self.assertEqual(len(partitions[0]["ordered_input_refs"]), 2)
        self.assertEqual(len(windows), 6)
        self.assertEqual(
            sorted(len(n["frame_input_refs"]) for n in windows),
            [1, 1, 2, 2, 2, 2],
        )
        self.assertEqual(
            sorted(tuple(n["frame_span"]) for n in windows),
            [(1, 1), (1, 1), (1, 2), (1, 2), (1, 2), (1, 2)],
        )

    def test_multiple_window_orders_and_unprojected_outer_sort(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT s.statement_id,s.role,"
            "rank() OVER(PARTITION BY s.role ORDER BY s.statement_id) AS place,"
            "dense_rank() OVER(ORDER BY s.role) AS role_place "
            "FROM memory_v1.relation_statements s ORDER BY lower(s.role),s.statement_id",
            commit=True,
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window_partition"]),
            3,
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window"]), 4,
        )

    def test_outer_sort_window_without_projection_keeps_its_witness(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "ORDER BY row_number() OVER(ORDER BY r.relation_id) DESC",
            commit=True,
        )
        self.assertEqual(len(graph["row_provenance"]), 1)
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window"]), 1
        )

    def test_same_select_group_aggregates_feed_native_windows(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT s.role,count(*) AS n,"
            "sum(count(*)) OVER(ORDER BY s.role ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running,"
            "rank() OVER(ORDER BY count(*) DESC) AS place "
            "FROM memory_v1.relation_statements s GROUP BY s.role ORDER BY lower(s.role)",
            commit=True,
        )
        self.assertEqual(len(graph["row_provenance"]), 2)
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "group"]), 2
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window"]), 4
        )

    def test_having_rejects_groups_before_window_membership(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT s.role,count(*) AS n,rank() OVER(ORDER BY s.role) AS place "
            "FROM memory_v1.relation_statements s GROUP BY s.role "
            "HAVING s.role=$1 ORDER BY s.role",
            parameters=[{"position": 1, "type": "text", "value": "source"}],
        )
        self.assertEqual(len(graph["row_provenance"]), 1)
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "group"]), 2
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window"]), 1
        )

    def test_implicit_group_feeds_full_frame(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT count(*) AS n,"
            "sum(count(*)) OVER(ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS total "
            "FROM memory_v1.relation_statements s"
        )
        self.assertEqual(len(graph["row_provenance"]), 1)
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "group"]), 1
        )
        self.assertEqual(
            len([n for n in graph["nodes"] if n["operation"] == "window"]), 1
        )

    def test_empty_rows_frame_and_tied_peer_membership_are_native(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT s.statement_id,"
            "count(*) OVER(ORDER BY s.statement_id ROWS BETWEEN 2 FOLLOWING AND 3 FOLLOWING) AS empty_count,"
            "count(*) OVER(ORDER BY s.relation_id ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS self_count "
            "FROM memory_v1.relation_statements s ORDER BY s.statement_id"
        )
        windows = [n for n in graph["nodes"] if n["operation"] == "window"]
        self.assertEqual(len(windows), 4)
        self.assertEqual(sum(n.get("frame_empty", False) for n in windows), 2)
        self.assertEqual(
            sorted(len(n.get("frame_input_refs", [])) for n in windows),
            [0, 0, 1, 1],
        )

    def test_empty_window_keeps_protected_source_and_predicate(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT r.relation_id,row_number() OVER(ORDER BY r.relation_id) AS position "
            "FROM memory_v1.assessed_relations r WHERE NOT r.support_eligible"
        )
        self.assertEqual(graph["row_provenance"], [])
        self.assertGreater(len(graph["members"]), 0)
        self.assertTrue(any(s["operation"] == "filter" for s in graph["stages"]))
        self.assertFalse(
            any(n["operation"] == "window_partition" for n in graph["nodes"])
        )

    def test_previously_pending_window_compositions_publish_exact_witnesses(self) -> None:
        self.fixture.assertion()
        for statement in (
            "SELECT r.relation_id,count(*) OVER(ORDER BY r.relation_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n FROM memory_v1.assessed_relations r",
            "SELECT r.relation_id,rank() OVER(ORDER BY r.state) AS place,row_number() OVER(ORDER BY r.relation_id) AS position FROM memory_v1.assessed_relations r",
            "SELECT r.relation_id,rank() OVER(ORDER BY r.state) AS place FROM memory_v1.assessed_relations r ORDER BY r.state",
        ):
            with self.subTest(statement=statement):
                graph = self.compare(statement)
                self.assertTrue(
                    any(node["operation"] == "window" for node in graph["nodes"])
                )

    def test_missing_lag_privilege_refuses_without_result(self) -> None:
        self.fixture.assertion()
        h = self.harness
        h.db.execute(
            sql.SQL(
                "REVOKE EXECUTE ON FUNCTION pg_catalog.lag(anycompatible,integer,anycompatible) FROM {}"
            ).format(sql.Identifier(h.runtime.reader))
        )
        request = h.request(
            "SELECT s.statement_id,lag(s.statement_id,1,s.statement_id) OVER(ORDER BY r.relation_id,s.relation_id,s.role,s.statement_id) AS adjacent "
            "FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s "
            "ON r.relation_id=s.relation_id"
        )
        owner = h.reserve_request(request)
        with h.population(request) as population:
            result = h.execute_request(request, owner, population)
            self.assertEqual(result.outcome, "unavailable")
            self.assertEqual(result.rows, ())
            self.assertIsNone(result.witnesses)
        h.store.discard(
            operation_ref=owner.operation_ref, ownership_ref=owner.ownership_ref
        )

    def test_cancelled_partition_binding_settles_without_partial_result(self) -> None:
        self.fixture.assertion()
        h = self.harness
        request = h.request(
            "SELECT r.relation_id,row_number() OVER(ORDER BY r.relation_id) AS position "
            "FROM memory_v1.assessed_relations r"
        )
        owner = h.reserve_request(request)
        original = NativeWitnessBuilder.add

        def stop(
            builder: NativeWitnessBuilder, trace: Any, *, published: bool = True
        ) -> Any:
            value = original(builder, trace, published=published)
            if not published:
                raise runtime._StopQuery("cancelled")
            return value

        with (
            h.population(request) as population,
            patch.object(NativeWitnessBuilder, "add", stop),
        ):
            result = h.execute_request(request, owner, population)
            self.assertEqual(result.outcome, "cancelled")
            self.assertEqual(result.rows, ())
            self.assertIsNone(result.witnesses)
            self.assertEqual(result.settlement_state, "settled")
        h.store.discard(
            operation_ref=owner.operation_ref, ownership_ref=owner.ownership_ref
        )
