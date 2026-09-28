"""Seen fictional native EXISTS witnesses; no public result disclosure."""

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
class QueryExistsWitness(unittest.TestCase):
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
    ) -> dict[str, Any]:
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
                return graph
        finally:
            for pin in (owner, plain_owner):
                h.store.discard(
                    operation_ref=pin.operation_ref,
                    ownership_ref=pin.ownership_ref,
                )

    def test_correlated_exists_commits_native_match_bag(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id) ORDER BY r.relation_id",
            commit=True,
        )
        matches = [n for n in graph["nodes"] if n["operation"] == "exists"]
        self.assertEqual(len(graph["row_provenance"]), 1)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_count"], 2)
        self.assertEqual(len(matches[0]["match_refs"]), 2)
        self.assertTrue(matches[0]["exists_truth"])
        self.assertIn("memory_v1.relation_statements", graph["searched_relations"])

    def test_not_exists_and_empty_exists_keep_protected_population(self) -> None:
        self.fixture.assertion()
        predicate = (
            "(SELECT s.statement_id FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id AND s.role=$1)"
        )
        parameters = [{"position": 1, "type": "text", "value": "absent-role"}]
        for operator, rows in (("NOT EXISTS", 1), ("EXISTS", 0)):
            with self.subTest(operator=operator):
                graph = self.compare(
                    "SELECT r.relation_id FROM memory_v1.assessed_relations r "
                    f"WHERE {operator} {predicate} ORDER BY r.relation_id",
                    parameters=parameters,
                    commit=bool(rows),
                )
                self.assertEqual(len(graph["row_provenance"]), rows)
                self.assertIn("memory_v1.relation_statements", graph["searched_relations"])
                self.assertEqual(
                    len(
                        [
                            member
                            for member in graph["members"]
                            if member["relation"] == "memory_v1.relation_statements"
                        ]
                    ),
                    2,
                )
                matches = [n for n in graph["nodes"] if n["operation"] == "exists"]
                if rows:
                    self.assertEqual([n["match_count"] for n in matches], [0])

    def test_multiple_exists_compose_without_extra_reader_dispatch(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT s.statement_id FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id) "
            "AND NOT EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id AND s.role=$1)",
            parameters=[{"position": 1, "type": "text", "value": "absent-role"}],
        )
        matches = [n for n in graph["nodes"] if n["operation"] == "exists"]
        self.assertEqual(len(graph["row_provenance"]), 1)
        self.assertEqual(sorted(n["match_count"] for n in matches), [0, 2])

    def test_uncorrelated_and_per_row_correlated_searches(self) -> None:
        self.fixture.assertion()
        for statement, rows, counts in (
            (
                "SELECT r.relation_id FROM memory_v1.assessed_relations r "
                "WHERE EXISTS (SELECT s.statement_id "
                "FROM memory_v1.relation_statements s)",
                1,
                [2],
            ),
            (
                "SELECT s.statement_id FROM memory_v1.relation_statements s "
                "WHERE EXISTS (SELECT r.relation_id "
                "FROM memory_v1.assessed_relations r "
                "WHERE r.relation_id=s.relation_id) ORDER BY s.statement_id",
                2,
                [1, 1],
            ),
            (
                "WITH roots AS (SELECT relation_id "
                "FROM memory_v1.assessed_relations) "
                "SELECT x.relation_id FROM roots x "
                "WHERE EXISTS (SELECT s.statement_id "
                "FROM memory_v1.relation_statements s "
                "WHERE s.relation_id=x.relation_id)",
                1,
                [2],
            ),
        ):
            with self.subTest(statement=statement):
                graph = self.compare(statement)
                self.assertEqual(len(graph["row_provenance"]), rows)
                self.assertEqual(
                    sorted(
                        n["match_count"]
                        for n in graph["nodes"]
                        if n["operation"] == "exists"
                    ),
                    counts,
                )

    def test_exists_composes_with_distinct_and_set_branches(self) -> None:
        self.fixture.assertion()
        arm = (
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id)"
        )
        graph = self.compare(arm.replace("SELECT", "SELECT DISTINCT", 1))
        self.assertEqual(len(graph["row_provenance"]), 1)
        self.assertTrue(any(n["operation"] == "collapse" for n in graph["nodes"]))
        graph = self.compare(arm + " UNION ALL " + arm)
        self.assertEqual(len(graph["row_provenance"]), 2)
        self.assertTrue(any(n["operation"] == "set" for n in graph["nodes"]))

    def test_unqualified_exists_shapes_stay_unavailable(self) -> None:
        self.fixture.assertion()
        statements = (
            "SELECT r.relation_id, EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id) AS found "
            "FROM memory_v1.assessed_relations r",
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT count(*) FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id)",
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id LIMIT 0)",
            "SELECT r.relation_id,count(*) FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id) GROUP BY r.relation_id",
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id<>r.relation_id)",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                request = self.harness.request(statement)
                owner = self.harness.reserve_request(request)
                try:
                    with self.harness.population(request) as population:
                        result = self.harness.execute_request(request, owner, population)
                        self.assertEqual(result.outcome, "unsupported_query")
                        self.assertEqual(result.error, "witness_qualification_pending")
                        self.assertEqual(result.rows, ())
                        self.assertIsNone(result.witnesses)
                finally:
                    self.harness.store.discard(
                        operation_ref=owner.operation_ref,
                        ownership_ref=owner.ownership_ref,
                    )

    def test_cancelled_exists_binding_settles_without_partial_result(self) -> None:
        self.fixture.assertion()
        h = self.harness
        request = h.request(
            "SELECT r.relation_id FROM memory_v1.assessed_relations r "
            "WHERE EXISTS (SELECT s.statement_id "
            "FROM memory_v1.relation_statements s "
            "WHERE s.relation_id=r.relation_id)"
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
