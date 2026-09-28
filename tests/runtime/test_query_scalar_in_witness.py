"""Seen fictional native scalar and IN membership witnesses."""

from __future__ import annotations

import json
import os
import unittest
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from memoriesql.infrastructure.postgres import restricted_query as runtime
from memoriesql.infrastructure.postgres.query_witness import NativeWitnessBuilder

if TYPE_CHECKING:
    from tests.runtime import test_query_result_commit as fixtures
else:
    import test_query_result_commit as fixtures


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class QueryScalarInWitness(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = fixtures.QueryResultCommit()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.fixture = self.harness.fixture

    def compare(
        self,
        statement: str,
        *,
        parameters: list[dict[str, Any]] | None = None,
        commit: bool = False,
    ) -> tuple[dict[str, Any], tuple[tuple[Any, ...], ...]]:
        h = self.harness
        request = h.request(statement, parameters=parameters)
        plain_request = fixtures.replace_request_step(request)
        owner, plain_owner = h.reserve_request(request), h.reserve_request(plain_request)
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
                return graph, result.rows
        finally:
            for pin in (owner, plain_owner):
                h.store.discard(
                    operation_ref=pin.operation_ref,
                    ownership_ref=pin.ownership_ref,
                )

    def test_correlated_scalar_singleton_and_atomic_replay(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "SELECT s.statement_id, "
            "(SELECT r.state FROM memory_v1.assessed_relations r "
            "WHERE r.relation_id=s.relation_id) AS state "
            "FROM memory_v1.relation_statements s ORDER BY s.statement_id",
            commit=True,
        )
        self.assertEqual(len(rows), 2)
        scalar = [
            n for n in graph["nodes"]
            if n["operation"] == "project" and "scalar_count" in n
        ]
        self.assertEqual(len(scalar), 2)
        self.assertEqual([n["scalar_count"] for n in scalar], [1, 1])
        self.assertTrue(all(len(n["scalar_input_refs"]) == 1 for n in scalar))

    def test_empty_scalar_is_null_with_protected_search_population(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "SELECT (SELECT s.text FROM memory_v1.relation_statements s "
            "WHERE s.role=$1) AS missing FROM memory_v1.assessed_relations r",
            parameters=[{"position": 1, "type": "text", "value": "absent-role"}],
            commit=True,
        )
        self.assertEqual(rows, ((None,),))
        scalar = [n for n in graph["nodes"] if "scalar_count" in n]
        self.assertEqual(len(scalar), 1)
        self.assertEqual(scalar[0]["scalar_count"], 0)
        self.assertFalse(scalar[0]["scalar_present"])
        self.assertIn("memory_v1.relation_statements", graph["searched_relations"])
        self.assertEqual(
            sum(
                member["relation"] == "memory_v1.relation_statements"
                for member in graph["members"]
            ),
            2,
        )

    def test_scalar_cardinality_error_is_native_and_atomic(self) -> None:
        self.fixture.assertion()
        h = self.harness
        statement = (
            "SELECT (SELECT s.statement_id FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id) AS sid "
            "FROM memory_v1.assessed_relations r"
        )
        for witnessed in (False, True):
            with self.subTest(witnessed=witnessed):
                request = h.request(statement)
                owner = h.reserve_request(request)
                try:
                    with h.population(request) as population:
                        result = h.execute_request(
                            request, owner, population, witnesses=witnessed
                        )
                        self.assertEqual(result.outcome, "execution_error")
                        self.assertEqual(result.rows, ())
                        self.assertIsNone(result.witnesses)
                        self.assertEqual(result.settlement_state, "settled")
                finally:
                    h.store.discard(
                        operation_ref=owner.operation_ref,
                        ownership_ref=owner.ownership_ref,
                    )

    def test_in_bag_retains_matches_exclusions_and_null_truth(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "SELECT r.relation_id, "
            "r.relation_id IN (SELECT s.relation_id "
            "FROM memory_v1.relation_statements s) AS present, "
            "r.state IN (SELECT s.support_reason "
            "FROM memory_v1.assessed_relations s) AS unknown "
            "FROM memory_v1.assessed_relations r",
            commit=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], (True, None))
        membership = [
            n for n in graph["nodes"]
            if n["operation"] == "exists" and "in_truth" in n
        ]
        self.assertEqual(len(membership), 2)
        self.assertEqual(sorted(n["match_count"] for n in membership), [0, 2])
        self.assertEqual(sorted(n["unknown_count"] for n in membership), [0, 1])
        self.assertTrue(all(n["searched_input_refs"] for n in membership))

    def test_null_left_empty_set_and_negation_keep_native_truth(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "SELECT r.support_reason IN "
            "(SELECT s.state FROM memory_v1.assessed_relations s) AS unknown, "
            "r.support_reason IN "
            "(SELECT s.state FROM memory_v1.assessed_relations s "
            "WHERE s.state=$1) AS empty_false, "
            "r.state NOT IN "
            "(SELECT s.support_reason FROM memory_v1.assessed_relations s) "
            "AS not_unknown FROM memory_v1.assessed_relations r",
            parameters=[{"position": 1, "type": "text", "value": "absent-state"}],
        )
        self.assertEqual(rows, ((None, False, None),))
        membership = [n for n in graph["nodes"] if "in_truth" in n]
        self.assertEqual(len(membership), 3)
        self.assertEqual(sorted(n["match_count"] for n in membership), [0, 0, 0])
        self.assertEqual(sorted(n["unknown_count"] for n in membership), [0, 1, 1])

    def test_correlated_in_retains_each_inner_occurrence(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "SELECT s.statement_id FROM memory_v1.relation_statements s "
            "WHERE s.role IN (SELECT t.role "
            "FROM memory_v1.relation_statements t "
            "WHERE t.relation_id=s.relation_id "
            "AND t.statement_id=s.statement_id) ORDER BY s.statement_id",
            commit=True,
        )
        self.assertEqual(len(rows), 2)
        membership = [n for n in graph["nodes"] if "in_truth" in n]
        self.assertEqual(len(membership), 2)
        self.assertTrue(all(n["in_truth"] is True for n in membership))
        self.assertTrue(all(len(n["searched_input_refs"]) == 1 for n in membership))

    def test_not_in_unknown_returns_no_rows_without_erasing_search_basis(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE r.state NOT IN "
            "(SELECT s.support_reason FROM memory_v1.assessed_relations s)"
        )
        self.assertEqual(rows, ())
        self.assertEqual(graph["row_provenance"], [])
        self.assertTrue(graph["tested_nodes"])
        self.assertTrue(
            any(
                node.get("predicate_truth") is None
                for node in graph["nodes"]
                if node["operation"] == "filter"
            )
        )
        self.assertTrue(any(node.get("in_truth") is None for node in graph["nodes"]))
        self.assertIn("memory_v1.assessed_relations", graph["searched_relations"])
        self.assertEqual(
            sum(
                member["relation"] == "memory_v1.assessed_relations"
                for member in graph["members"]
            ),
            1,
        )

    def test_in_filters_compose_with_cte_and_union_all(self) -> None:
        self.fixture.assertion()
        arm = (
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE r.relation_id IN (SELECT s.relation_id "
            "FROM memory_v1.relation_statements s WHERE s.role=$1)"
        )
        graph, rows = self.compare(
            "WITH chosen AS (" + arm + ") SELECT c.relation_id FROM chosen c "
            "UNION ALL " + arm,
            parameters=[{"position": 1, "type": "text", "value": "source"}],
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(n["operation"] == "set" for n in graph["nodes"]))
        self.assertTrue(any("in_truth" in n for n in graph["nodes"]))

    def test_unused_in_cte_does_not_run_or_retain_its_search(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "WITH unused AS (SELECT s.role "
            "FROM memory_v1.relation_statements s "
            "WHERE s.role IN (SELECT t.role "
            "FROM memory_v1.relation_statements t)) "
            "SELECT r.relation_id FROM memory_v1.assessed_relations r"
        )
        self.assertEqual(len(rows), 1)
        self.assertNotIn("memory_v1.relation_statements", graph["searched_relations"])
        self.assertFalse(any("in_truth" in n for n in graph["nodes"]))

    def test_empty_left_set_does_not_force_in_ledger_on_right(self) -> None:
        self.fixture.assertion()
        graph, rows = self.compare(
            "SELECT r.state FROM memory_v1.assessed_relations r "
            "WHERE r.state=$1 INTERSECT "
            "SELECT s.state FROM memory_v1.assessed_relations s "
            "WHERE s.state IN (SELECT t.state "
            "FROM memory_v1.assessed_relations t)",
            parameters=[{"position": 1, "type": "text", "value": "absent-state"}],
        )
        self.assertEqual(rows, ())
        summaries = [n for n in graph["nodes"] if n["operation"] == "set_population"]
        self.assertEqual(len(summaries), 1)
        self.assertFalse(summaries[0]["right_evaluated"])
        self.assertFalse(any("in_truth" in n for n in graph["nodes"]))

    def test_unqualified_subquery_forms_refuse_before_reader_dispatch(self) -> None:
        self.fixture.assertion()
        statements = (
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE r.state=(SELECT s.state FROM memory_v1.assessed_relations s)",
            "SELECT (SELECT count(*) FROM memory_v1.relation_statements s) "
            "AS count_value "
            "FROM memory_v1.assessed_relations r",
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE r.state IN (SELECT s.state FROM memory_v1.assessed_relations s "
            "LIMIT 1)",
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE lower(r.state) IN "
            "(SELECT s.state FROM memory_v1.assessed_relations s)",
            "SELECT r.state,count(*) AS n "
            "FROM memory_v1.assessed_relations r "
            "WHERE r.state IN "
            "(SELECT s.state FROM memory_v1.assessed_relations s) "
            "GROUP BY r.state",
            "SELECT CASE WHEN r.support_eligible THEN "
            "(SELECT s.state FROM memory_v1.assessed_relations s "
            "WHERE s.relation_id=r.relation_id) ELSE r.state END AS state "
            "FROM memory_v1.assessed_relations r",
        )
        h = self.harness
        for statement in statements:
            with self.subTest(statement=statement):
                request = h.request(statement)
                owner = h.reserve_request(request)
                try:
                    with h.population(request) as population:
                        result = h.execute_request(request, owner, population)
                        self.assertEqual(result.outcome, "unsupported_query")
                        self.assertEqual(result.error, "witness_qualification_pending")
                        self.assertEqual(result.rows, ())
                        self.assertIsNone(result.witnesses)
                        self.assertIsNone(result.invocation_ref)
                finally:
                    h.store.discard(
                        operation_ref=owner.operation_ref,
                        ownership_ref=owner.ownership_ref,
                    )

    def test_cancelled_membership_binding_settles_without_partial_result(self) -> None:
        self.fixture.assertion()
        h = self.harness
        request = h.request(
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE r.relation_id IN "
            "(SELECT s.relation_id FROM memory_v1.relation_statements s)"
        )
        owner = h.reserve_request(request)
        original = NativeWitnessBuilder.add

        def stop(
            builder: NativeWitnessBuilder, trace: Any, *, published: bool = True
        ) -> Any:
            value = original(builder, trace, published=published)
            if published:
                raise runtime._StopQuery("cancelled")
            return value

        with h.population(request) as population, patch.object(
            NativeWitnessBuilder, "add", stop
        ):
            result = h.execute_request(request, owner, population)
            self.assertEqual(result.outcome, "cancelled")
            self.assertEqual(result.rows, ())
            self.assertIsNone(result.witnesses)
            self.assertEqual(result.settlement_state, "settled")
        h.store.discard(
            operation_ref=owner.operation_ref,
            ownership_ref=owner.ownership_ref,
        )


if __name__ == "__main__":
    unittest.main()
