"""Seen fictional native aggregate/set values and independently inspected lineage."""

from __future__ import annotations

import json
import os
import unittest
from collections import Counter
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
class QueryCompositionWitness(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = fixtures.QueryResultCommit()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.fixture = self.harness.fixture

    def compare_failure(
        self, statement: str, parameters: list[dict[str, Any]] | None = None
    ) -> None:
        h = self.harness
        request = h.request(statement, parameters=parameters)
        owners = [
            h.reserve_request(request),
            h.reserve_request(fixtures.replace_request_step(request)),
        ]
        with h.population(request) as population:
            plain = h.execute_request(request, owners[1], population, witnesses=False)
            witnessed = h.execute_request(request, owners[0], population)
            self.assertEqual(plain.outcome, "execution_error", plain)
            self.assertEqual(witnessed.outcome, plain.outcome, witnessed)
            self.assertEqual(witnessed.rows, ())
            self.assertIsNone(witnessed.witnesses)
            self.assertEqual(witnessed.settlement_state, "settled")
        for pin in owners:
            h.store.discard(
                operation_ref=pin.operation_ref, ownership_ref=pin.ownership_ref
            )

    def compare(
        self,
        statement: str,
        *,
        parameters: list[dict[str, Any]] | None = None,
        expected: tuple[Any, ...] | None = None,
        commit: bool = False,
    ) -> dict[str, Any]:
        h = self.harness
        request = h.request(statement, parameters=parameters)
        original = fixtures.replace_request_step(request)
        owner, plain_owner = h.reserve_request(request), h.reserve_request(original)
        try:
            with h.population(request) as population:
                plain = h.execute_request(
                    original, plain_owner, population, witnesses=False
                )
                result = h.execute_request(request, owner, population)
                self.assertEqual(plain.outcome, "complete", plain)
                self.assertEqual(result.outcome, "complete", result)
                self.assertEqual(result.columns, plain.columns)
                self.assertEqual(Counter(result.rows), Counter(plain.rows))
                if (
                    "ORDER BY" in statement.upper()
                    and " OVER " not in statement.upper()
                ):
                    self.assertEqual(result.rows, plain.rows)
                if expected is not None:
                    self.assertEqual(result.rows, expected)
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

    def test_joined_tuple_distinct_bead_and_root_counts_are_separate(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            """WITH pins AS (
            SELECT r.relation_id,r.source_bead_id,r.independent_root_count,s.statement_id
            FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s ON r.relation_id=s.relation_id),
            copies AS (SELECT relation_id,source_bead_id,independent_root_count,statement_id FROM pins
                UNION ALL SELECT relation_id,source_bead_id,independent_root_count,statement_id FROM pins)
            SELECT count(*) AS tuples,count(DISTINCT source_bead_id) AS beads,
                min(independent_root_count) AS roots FROM copies""",
            commit=True,
        )
        groups = [n for n in graph["nodes"] if n["operation"] == "group"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(sum(groups[0]["input_multiplicities"]), 4)
        distinct = groups[0]["aggregate_inputs"][1]
        self.assertEqual(len(distinct), 4)
        self.assertEqual(len({e["equality_class"] for e in distinct}), 1)
        self.assertEqual(groups[0]["native_text_values"][:2], ["4", "1"])
        self.assertEqual(groups[0]["having_truth"], True)

    def test_having_rejected_groups_and_filter_truth_remain_tested(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            """SELECT r.state AS state,count(*) FILTER(WHERE r.support_eligible) AS n
            FROM memory_v1.assessed_relations r GROUP BY r.state HAVING count(*)>$1 ORDER BY state""",
            parameters=[{"position": 1, "type": "int8", "value": "9"}],
            expected=(),
        )
        self.assertEqual(graph["row_provenance"], [])
        tested = {n["node_ref"]: n for n in graph["nodes"]}
        groups = [
            tested[r]
            for r in graph["tested_nodes"]
            if tested[r]["operation"] == "group"
        ]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["having_truth"], False)
        self.assertEqual(groups[0]["native_text_values"], [None, None])
        self.assertEqual(groups[0]["native_group_key_values"], ["active"])
        self.assertEqual(groups[0]["native_aggregate_values"], ["1", "1"])
        self.assertEqual(sum(groups[0]["input_multiplicities"]), 1)
        self.assertEqual(groups[0]["aggregate_inputs"][0][0]["filter_truth"], True)

    def test_empty_and_all_null_aggregates_keep_native_null_semantics(self) -> None:
        self.fixture.assertion()
        for predicate, expected in (
            ("WHERE NOT r.support_eligible", ((0, 0, 0, None, None, None, None),)),
            ("", ((1, 0, 0, None, None, None, None),)),
        ):
            with self.subTest(predicate=predicate):
                graph = self.compare(
                    "SELECT count(*) AS tuples,count(r.qualification) AS nonnull,count(DISTINCT r.qualification) AS distinct_values,sum(CASE WHEN NOT r.support_eligible THEN $1 END) AS total,avg(CASE WHEN NOT r.support_eligible THEN $1 END) AS mean,min(r.qualification) AS smallest,max(r.qualification) AS largest FROM memory_v1.assessed_relations r "
                    + predicate,
                    parameters=[{"position": 1, "type": "numeric", "value": "1.25"}],
                    expected=expected,
                    commit=True,
                )
                groups = [n for n in graph["nodes"] if n["operation"] == "group"]
                self.assertEqual(len(groups), 1)
                self.assertEqual(sum(groups[0]["input_multiplicities"]), expected[0][0])
                if not predicate:
                    self.assertEqual(
                        groups[0]["aggregate_inputs"][1][0]["argument_nonnull"], False
                    )
                    self.assertEqual(
                        groups[0]["aggregate_inputs"][5][0]["extremum_winner"], False
                    )

    def test_numeric_promotions_extrema_and_composed_group_facts(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            """WITH pins AS (SELECT r.source_bead_id,CASE WHEN s.bead_id=r.source_bead_id THEN $1 ELSE $2 END AS value FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s ON r.relation_id=s.relation_id),
            copies AS (SELECT source_bead_id,value FROM pins UNION ALL SELECT source_bead_id,value FROM pins),
            grouped AS (SELECT source_bead_id,count(*) AS n,sum(value) AS total,min(value) AS lo,max(value) AS hi FROM copies GROUP BY source_bead_id)
            SELECT sum(n) AS tuples,avg(n) AS mean,sum(total) AS total,min(lo) AS lo,max(hi) AS hi FROM grouped""",
            parameters=[
                {"position": 1, "type": "int8", "value": "1"},
                {"position": 2, "type": "int8", "value": "2"},
            ],
            commit=True,
        )
        groups = [n for n in graph["nodes"] if n["operation"] == "group"]
        self.assertEqual(len(groups), 2)
        inner = next(n for n in groups if sum(n["input_multiplicities"]) == 4)
        self.assertEqual(inner["native_text_values"][1], "4")
        self.assertEqual(
            sum(e["extremum_winner"] for e in inner["aggregate_inputs"][2]), 2
        )
        self.assertEqual(
            sum(e["extremum_winner"] for e in inner["aggregate_inputs"][3]), 2
        )
        self.compare(
            "SELECT sum(r.author_confidence) AS total,avg(r.author_confidence) AS mean,min(r.source_bead_id) AS bead,min(r.support_eligible) AS all_eligible,max(r.support_eligible) AS any_eligible FROM memory_v1.assessed_relations r",
            commit=True,
        )

    def test_set_null_equality_empty_branches_and_all_multiplicities(self) -> None:
        self.fixture.assertion()
        for operator in ("UNION", "INTERSECT", "INTERSECT ALL", "EXCEPT", "EXCEPT ALL"):
            with self.subTest(operator=operator):
                graph = self.compare(
                    """WITH pins AS (SELECT r.qualification AS value FROM memory_v1.assessed_relations r),
                    copies AS (SELECT value FROM pins UNION ALL SELECT value FROM pins UNION ALL SELECT value FROM pins)
                    SELECT value FROM copies """
                    + operator
                    + " SELECT value FROM pins ORDER BY value NULLS FIRST"
                )
                classes = [n for n in graph["nodes"] if n["operation"] == "set_class"]
                self.assertEqual(len(classes), 1)
                self.assertEqual(classes[0]["native_text_values"], [None])
                self.assertEqual(classes[0]["left_multiplicity"], 3)
                self.assertEqual(classes[0]["right_multiplicity"], 1)
                want = (
                    0 if operator == "EXCEPT" else 2 if operator == "EXCEPT ALL" else 1
                )
                self.assertEqual(classes[0]["output_multiplicity"], want)
                self.assertEqual(len(graph["row_provenance"]), want)
        graph = self.compare(
            "SELECT r.state FROM memory_v1.assessed_relations r WHERE NOT r.support_eligible EXCEPT ALL SELECT s.state FROM memory_v1.assessed_relations s WHERE NOT s.support_eligible",
            expected=(),
        )
        summaries = [n for n in graph["nodes"] if n["operation"] == "set_population"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["left_multiplicity"], 0)
        self.assertEqual(summaries[0]["output_multiplicity"], 0)
        self.assertGreater(len(graph["members"]), 0)

    def test_filtered_unknown_truth_and_qualified_native_type_matrix(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT count(*) FILTER(WHERE r.qualification=$1) AS unknown_count,count(DISTINCT r.source_bead_id) FILTER(WHERE NOT r.support_eligible) AS excluded,min(r.recorded_at) AS first,max(r.recorded_at) AS last FROM memory_v1.assessed_relations r",
            parameters=[{"position": 1, "type": "text", "value": "fictional"}],
            commit=True,
        )
        group = next(n for n in graph["nodes"] if n["operation"] == "group")
        self.assertEqual(group["aggregate_inputs"][0][0]["filter_truth"], None)
        self.assertEqual(group["aggregate_inputs"][1][0]["filter_truth"], False)
        cases = [
            "SELECT lower(r.state) AS k,count(*) AS n FROM memory_v1.assessed_relations r GROUP BY lower(r.state) ORDER BY k",
            "SELECT date_trunc('day',r.recorded_at,'UTC') AS day,count(*) AS n FROM memory_v1.assessed_relations r GROUP BY date_trunc('day',r.recorded_at,'UTC') ORDER BY day",
            "SELECT count(*) AS n FROM memory_v1.assessed_relations r GROUP BY r.state ORDER BY count(*) DESC LIMIT 1 OFFSET 0",
            "WITH grouped(n) AS (SELECT count(*) FROM memory_v1.assessed_relations) SELECT n FROM grouped ORDER BY n",
            'WITH "quoted%" AS (SELECT DISTINCT r.state AS "v%" FROM memory_v1.assessed_relations r) SELECT "v%" FROM "quoted%" ORDER BY "v%"',
            "SELECT DISTINCT r.state AS k,count(*) AS n FROM memory_v1.assessed_relations r GROUP BY r.state ORDER BY k,n",
        ]
        for statement in cases:
            with self.subTest(sql=statement):
                self.compare(statement)

    def test_distinct_and_set_classes_preserve_collapsed_and_negative_inputs(
        self,
    ) -> None:
        self.fixture.assertion()
        graph = self.compare(
            """WITH copies AS (
            SELECT r.state FROM memory_v1.assessed_relations r UNION ALL
            SELECT r.state FROM memory_v1.assessed_relations r)
            SELECT DISTINCT state FROM copies ORDER BY state""",
            expected=(("active",),),
        )
        collapse = [n for n in graph["nodes"] if n["operation"] == "collapse"]
        self.assertEqual(len(collapse), 1)
        self.assertEqual(sum(collapse[0]["input_multiplicities"]), 2)
        for operator in ("UNION", "INTERSECT", "INTERSECT ALL", "EXCEPT", "EXCEPT ALL"):
            with self.subTest(operator=operator):
                graph = self.compare(
                    """WITH pins AS (SELECT r.state FROM memory_v1.assessed_relations r),
                    copies AS (SELECT state FROM pins UNION ALL SELECT state FROM pins)
                    SELECT state FROM copies """
                    + operator
                    + " SELECT state FROM pins ORDER BY state",
                    commit=True,
                )
                classes = [n for n in graph["nodes"] if n["operation"] == "set_class"]
                self.assertEqual(len(classes), 1)
                self.assertEqual(classes[0]["left_multiplicity"], 2)
                self.assertEqual(classes[0]["right_multiplicity"], 1)
                expected_count = 0 if operator == "EXCEPT" else 1
                self.assertEqual(classes[0]["output_multiplicity"], expected_count)
                self.assertEqual(len(graph["row_provenance"]), expected_count)
                self.assertEqual(len(classes[0]["right_input_refs"]), 1)

    def test_filter_and_having_preserve_short_circuit_and_native_errors(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "SELECT sum($1 / (r.author_confidence-r.author_confidence)) FILTER(WHERE NOT r.support_eligible) AS n FROM memory_v1.assessed_relations r",
            parameters=[{"position": 1, "type": "numeric", "value": "1.25"}],
            expected=((None,),),
        )
        group = next(n for n in graph["nodes"] if n["operation"] == "group")
        self.assertFalse(group["aggregate_inputs"][0][0]["argument_evaluated"])
        graph = self.compare(
            "SELECT $1/count(*) AS quotient FROM memory_v1.assessed_relations r WHERE NOT r.support_eligible HAVING count(*)>$2",
            parameters=[
                {"position": 1, "type": "int8", "value": "1"},
                {"position": 2, "type": "int8", "value": "0"},
            ],
            expected=(),
        )
        group = next(n for n in graph["nodes"] if n["operation"] == "group")
        self.assertFalse(group["projection_evaluated"])
        self.assertEqual(group["native_aggregate_values"], ["0"])
        self.compare_failure(
            "SELECT sum($1 / (r.author_confidence-r.author_confidence)) AS n FROM memory_v1.assessed_relations r",
            parameters=[{"position": 1, "type": "numeric", "value": "1.25"}],
        )
        self.compare_failure(
            "SELECT r.state,r.author_run_ref,count(*) AS n FROM memory_v1.assessed_relations r GROUP BY r.state"
        )
        self.compare_failure(
            "SELECT DISTINCT r.state FROM memory_v1.assessed_relations r ORDER BY r.author_run_ref"
        )

    def test_sets_feed_groups_without_double_counting_class_multiplicity(self) -> None:
        self.fixture.assertion()
        graph = self.compare(
            """WITH pins AS (SELECT r.source_bead_id,s.statement_id FROM memory_v1.assessed_relations r JOIN memory_v1.relation_statements s ON r.relation_id=s.relation_id),
            two AS (SELECT source_bead_id,statement_id FROM pins UNION ALL SELECT source_bead_id,statement_id FROM pins),
            three AS (SELECT source_bead_id,statement_id FROM two UNION ALL SELECT source_bead_id,statement_id FROM pins),
            compared AS (SELECT source_bead_id,statement_id FROM three INTERSECT ALL SELECT source_bead_id,statement_id FROM two)
            SELECT source_bead_id,count(*) AS tuples,count(DISTINCT statement_id) AS identities FROM compared GROUP BY source_bead_id ORDER BY source_bead_id""",
            commit=True,
        )
        classes = [n for n in graph["nodes"] if n["operation"] == "set_class"]
        self.assertEqual(len(classes), 2)
        self.assertEqual([n["output_multiplicity"] for n in classes], [2, 2])
        group = next(n for n in graph["nodes"] if n["operation"] == "group")
        self.assertEqual(group["native_text_values"][1:], ["4", "2"])
        self.assertEqual(sum(group["input_multiplicities"]), 4)

    def test_c_collation_numeric_equality_and_fictional_skew(self) -> None:
        self.fixture.assertion()
        for datatype, left, right, want in (
            ("text", "A", "a", 2),
            ("numeric", "1.0", "1.00", 1),
        ):
            with self.subTest(datatype=datatype):
                graph = self.compare(
                    "SELECT $1 AS value FROM memory_v1.assessed_relations r UNION SELECT $2 AS value FROM memory_v1.assessed_relations r ORDER BY value",
                    parameters=[
                        {"position": 1, "type": datatype, "value": left},
                        {"position": 2, "type": datatype, "value": right},
                    ],
                )
                classes = [n for n in graph["nodes"] if n["operation"] == "set_class"]
                self.assertEqual(len(classes), want)
                self.assertEqual(sum(n["output_multiplicity"] for n in classes), want)
        declarations = [
            "pins AS (SELECT s.relation_id,s.statement_id FROM memory_v1.relation_statements s)"
        ]
        prior = "pins"
        for depth in range(1, 8):
            name = "d" + str(depth)
            declarations.append(
                name
                + " AS (SELECT relation_id,statement_id FROM "
                + prior
                + " UNION ALL SELECT relation_id,statement_id FROM "
                + prior
                + ")"
            )
            prior = name
        graph = self.compare(
            "WITH "
            + ",".join(declarations)
            + " SELECT relation_id,count(*) AS tuples,count(DISTINCT statement_id) AS identities FROM "
            + prior
            + " GROUP BY relation_id ORDER BY relation_id",
            commit=True,
        )
        group = next(n for n in graph["nodes"] if n["operation"] == "group")
        self.assertEqual(group["native_text_values"][1:], ["256", "2"])
        self.assertEqual(sum(group["input_multiplicities"]), 256)

    def test_missing_helper_privilege_refuses_before_any_result_or_fallback(
        self,
    ) -> None:
        self.fixture.assertion()
        h = self.harness
        h.db.execute(
            sql.SQL(
                "REVOKE EXECUTE ON FUNCTION pg_catalog.jsonb_agg(anyelement) FROM {}"
            ).format(sql.Identifier(h.runtime.reader))
        )
        request = h.request("SELECT count(*) AS n FROM memory_v1.assessed_relations")
        owner = h.reserve_request(request)
        with h.population(request) as population:
            result = h.execute_request(request, owner, population)
            self.assertEqual(result.outcome, "unavailable", result)
            self.assertEqual(result.rows, ())
            self.assertIsNone(result.witnesses)
        self.assertEqual(
            h.scalar("SELECT count(*) FROM memoriesql.query_result_creations"), 0
        )

    def test_empty_left_set_skips_unevaluated_right_without_inventing_count(
        self,
    ) -> None:
        self.fixture.assertion()
        for operator in ("INTERSECT", "INTERSECT ALL", "EXCEPT", "EXCEPT ALL"):
            for right in (
                "SELECT $1/(r.author_confidence-r.author_confidence) AS n FROM memory_v1.assessed_relations r",
                "SELECT sum($1/(r.author_confidence-r.author_confidence)) AS n FROM memory_v1.assessed_relations r",
            ):
                with self.subTest(operator=operator, right=right):
                    graph = self.compare(
                        "SELECT r.author_confidence AS n FROM memory_v1.assessed_relations r WHERE NOT r.support_eligible "
                        + operator
                        + " "
                        + right,
                        parameters=[
                            {"position": 1, "type": "numeric", "value": "1.25"}
                        ],
                        expected=(),
                    )
                    summary = next(
                        n for n in graph["nodes"] if n["operation"] == "set_population"
                    )
                    self.assertFalse(summary["right_evaluated"])
                    self.assertIsNone(summary["right_multiplicity"])
                    self.assertEqual(summary["output_multiplicity"], 0)
                    self.assertGreater(len(graph["members"]), 0)
                    self.assertFalse(
                        any(n["operation"] == "group" for n in graph["nodes"])
                    )

    def test_unused_cte_is_not_evaluated_but_reused_cte_keeps_dependencies(
        self,
    ) -> None:
        self.fixture.assertion()
        graph = self.compare(
            "WITH unused AS (SELECT sum($1/(r.author_confidence-r.author_confidence)) AS bad FROM memory_v1.assessed_relations r) SELECT r.state FROM memory_v1.assessed_relations r ORDER BY state",
            parameters=[{"position": 1, "type": "numeric", "value": "1.25"}],
            expected=(("active",),),
        )
        self.assertFalse(any(n["operation"] == "group" for n in graph["nodes"]))
        self.assertTrue(
            any(
                s["operation"] == "group" and not s["reachable"]
                for s in graph["stages"]
            )
        )

    def test_cancellation_and_exhaustion_during_ledger_binding_suppress_everything(
        self,
    ) -> None:
        self.fixture.assertion()
        h = self.harness
        original = NativeWitnessBuilder.add
        for outcome in ("cancelled", "budget_exhausted"):
            with self.subTest(outcome=outcome):
                request = h.request(
                    "SELECT r.state,count(*) AS n FROM memory_v1.assessed_relations r GROUP BY r.state"
                )
                owner = h.reserve_request(request)
                seen = []

                def stop(
                    builder: NativeWitnessBuilder, trace: Any, *, published: bool = True
                ) -> Any:
                    value = original(builder, trace, published=published)
                    if not published:
                        seen.append(value)
                        raise runtime._StopQuery(outcome)
                    return value

                with (
                    h.population(request) as population,
                    patch.object(NativeWitnessBuilder, "add", stop),
                ):
                    result = h.execute_request(request, owner, population)
                    self.assertEqual(result.outcome, outcome, result)
                    self.assertEqual(result.rows, ())
                    self.assertIsNone(result.witnesses)
                    self.assertEqual(result.settlement_state, "settled")
                self.assertEqual(len(seen), 1)
                self.assertEqual(
                    h.scalar("SELECT count(*) FROM memoriesql.query_result_creations"),
                    0,
                )
                h.store.discard(
                    operation_ref=owner.operation_ref, ownership_ref=owner.ownership_ref
                )
