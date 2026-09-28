"""Seen fictional admission fixtures; these never access the evaluation holdout."""

from __future__ import annotations

import logging
import unittest
from uuid import UUID

from memoriesql.application.agent_sql_admission import RecursionBound, admit_query
from memoriesql.application.agent_sql_catalog import (
    SqlAdmissionError,
    SqlCatalog,
    SqlColumn,
    SqlParameter,
    SqlRelation,
    SqlType,
)
from memoriesql.application.agent_sql_witness import compile_bag_witness

ANCHOR = "11111111-1111-4111-8111-111111111111"
ANCHORS = frozenset({("bead_ref", ANCHOR)})


def recursive_sql(depth: int = 3) -> str:
    return f"""WITH RECURSIVE walk(node, depth) AS (
      SELECT o.bead_id, 0 FROM memory_v1.observations o WHERE o.bead_id = $1
      UNION ALL
      SELECT r.target_bead_id, w.depth + 1
      FROM walk w JOIN memory_v1.assessed_relations r ON r.source_bead_id = w.node
      WHERE w.depth < {depth}
    ) CYCLE node SET is_cycle USING path
    SELECT node, depth, is_cycle FROM walk"""


class AgentSqlAdmissionTests(unittest.TestCase):
    def test_registry_catalog_preserves_distinct_reference_types(self) -> None:
        catalog = SqlCatalog.installed()
        self.assertEqual(len(catalog.relations), 21)
        self.assertEqual(catalog.collation, "C")
        observations = catalog.relations["memory_v1.observations"]
        self.assertEqual(observations.column("bead_id").type.reference_kind, "bead_ref")
        self.assertEqual(
            observations.column("bead_version_id").type.reference_kind,
            "bead_version_ref",
        )
        relation = catalog.relations["memory_v1.relation_statements"]
        self.assertEqual(relation.column("text").type.pg_type, "text")
        self.assertEqual(
            relation.unique_keys, (("relation_id", "role", "statement_id"),)
        )

    def test_bound_values_are_not_in_emitted_sql_or_derivation_program(self) -> None:
        private_value = "fixture marker; DROP TABLE forbidden"
        result = admit_query(
            "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE o.summary ILIKE $1",
            (SqlParameter(1, "text", private_value),),
        )
        self.assertNotIn(private_value, result.sql)
        self.assertNotIn(private_value, repr(result.derivation_program))
        self.assertEqual(result.parameters, {"p1": private_value})
        self.assertIn("CAST(%(p1)s AS TEXT)", result.sql)

    def test_byte_identity_and_composite_source_key_are_preserved(self) -> None:
        catalog = SqlCatalog.installed()
        for name in (
            "source_units",
            "statement_sources",
            "relation_evidence",
            "relation_event_evidence",
        ):
            self.assertEqual(
                catalog.relations["memory_v1." + name]
                .column("content_sha256")
                .type.pg_type,
                "text",
            )
        self.assertEqual(
            catalog.relations["memory_v1.source_units"].unique_keys,
            (("source_unit_id", "content_sha256"),),
        )
        with self.assertRaises(SqlAdmissionError):
            admit_query(
                "SELECT row_number() OVER (ORDER BY u.source_unit_id) AS n FROM memory_v1.source_units u"
            )
        admit_query(
            "SELECT row_number() OVER (ORDER BY u.source_unit_id,u.content_sha256) AS n FROM memory_v1.source_units u"
        )

    def test_parameter_only_text_uses_catalog_collation(self) -> None:
        result = admit_query(
            "SELECT $1 ILIKE $2 AS matched",
            (SqlParameter(1, "text", "İ"), SqlParameter(2, "text", "i")),
        )
        self.assertEqual(result.sql.count('COLLATE "pg_catalog"."C"'), 2)

    def test_distinct_on_and_illegal_aggregate_window_phases_are_refused(self) -> None:
        for query in (
            "SELECT DISTINCT ON (o.bead_id) o.title AS title FROM memory_v1.observations o",
            "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE count(*)=$1",
            "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE row_number() OVER (ORDER BY o.bead_id,o.bead_version_id)=$1",
            "SELECT count(*) AS n FROM memory_v1.observations o GROUP BY count(*)",
            "SELECT count(*) AS n FROM memory_v1.observations o GROUP BY row_number() OVER (ORDER BY o.bead_id,o.bead_version_id)",
            "SELECT count(*) AS n FROM memory_v1.observations o HAVING row_number() OVER (ORDER BY o.bead_id,o.bead_version_id)=$1",
            "SELECT sum(count(*)) AS n FROM memory_v1.observations o",
            "SELECT sum(row_number() OVER (ORDER BY o.bead_id,o.bead_version_id)) AS n FROM memory_v1.observations o",
            "SELECT lag(lag(o.summary,1) OVER (ORDER BY o.bead_id,o.bead_version_id),1) OVER (ORDER BY o.bead_id,o.bead_version_id) AS n FROM memory_v1.observations o",
        ):
            with self.subTest(query=query), self.assertRaises(SqlAdmissionError):
                admit_query(
                    query, (SqlParameter(1, "int8", "1"),) if "$1" in query else ()
                )

    def test_qualified_columns_group_alias_and_percent_tokens(self) -> None:
        result = admit_query(
            'SELECT memory_v1.observations.bead_id AS "%(p1)s%%" FROM memory_v1.observations WHERE observations.summary=$1',
            (SqlParameter(1, "text", "fictional"),),
        )
        self.assertEqual(result.columns[0].name, "%(p1)s%%")
        self.assertIn('"%%(p1)s%%%%"', result.sql)
        result = admit_query(
            "SELECT lower(o.title) AS label, count(*) AS n FROM memory_v1.observations o GROUP BY label"
        )
        self.assertIn("GROUP BY LOWER(o.title)", result.sql)
        with self.assertRaises(SqlAdmissionError):
            admit_query(
                "SELECT memory_v1.observations.bead_id FROM memory_v1.observations o"
            )

    def test_identity_parameters_need_trusted_admission_and_matching_kind(self) -> None:
        query = (
            "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE o.bead_id=$1"
        )
        with self.assertRaises(SqlAdmissionError) as missing:
            admit_query(query, (SqlParameter(1, "bead_ref", ANCHOR),))
        self.assertEqual(missing.exception.code, "unavailable")
        with self.assertRaises(SqlAdmissionError):
            admit_query(query, (SqlParameter(1, "uuid", ANCHOR),))
        result = admit_query(
            query, (SqlParameter(1, "bead_ref", ANCHOR),), admitted_anchors=ANCHORS
        )
        self.assertEqual(result.columns[0].type.reference_kind, "bead_ref")

    def test_sets_preserve_schema_reference_kinds_and_bag_shape(self) -> None:
        left = "SELECT o.bead_id AS id FROM memory_v1.observations o"
        right = "SELECT s.bead_id AS id FROM memory_v1.statements s"
        for operation in (
            "UNION",
            "UNION ALL",
            "INTERSECT",
            "INTERSECT ALL",
            "EXCEPT",
            "EXCEPT ALL",
        ):
            with self.subTest(operation=operation):
                result = admit_query(left + " " + operation + " " + right)
                self.assertEqual(result.columns[0].type.reference_kind, "bead_ref")
                self.assertIn(operation, result.sql)
        with self.assertRaises(SqlAdmissionError):
            admit_query(
                left + " UNION SELECT s.statement_id AS id FROM memory_v1.statements s"
            )

    def test_saved_input_composes_without_a_physical_table_name(self) -> None:
        saved = SqlRelation(
            (
                SqlColumn("id", SqlType("uuid", "bead_ref")),
                SqlColumn("n", SqlType("numeric")),
            ),
            (("id",),),
        )
        result = admit_query(
            "SELECT q.id AS id, sum(q.n) AS total FROM input.prior q GROUP BY q.id",
            inputs={"prior": saved},
        )
        self.assertEqual(result.relations, ("input.prior",))
        self.assertEqual(result.columns[1].type.pg_type, "numeric")
        with self.assertRaises(SqlAdmissionError):
            admit_query("SELECT q.id FROM input.guessed q", inputs={"prior": saved})

    def test_ctes_subqueries_filters_and_case_compose(self) -> None:
        query = """WITH counts AS (
            SELECT s.bead_id AS id, count(*) FILTER (WHERE s.text ILIKE $1) AS n
            FROM memory_v1.statements s GROUP BY s.bead_id
          ) SELECT o.bead_id AS id, CASE WHEN o.title IS NULL THEN $2 ELSE o.title END AS title
          FROM memory_v1.observations o JOIN counts c ON c.id=o.bead_id
          WHERE EXISTS (SELECT s.statement_id AS sid FROM memory_v1.statements s
                        WHERE s.bead_id=o.bead_id AND s.text ILIKE $1)
          ORDER BY o.bead_id"""
        result = admit_query(
            query,
            (
                SqlParameter(1, "text", "%fictional%"),
                SqlParameter(2, "text", "untitled"),
            ),
        )
        self.assertEqual([c.name for c in result.columns], ["id", "title"])
        self.assertEqual(
            result.relations, ("memory_v1.observations", "memory_v1.statements")
        )

    def test_windows_require_explicit_rows_and_visible_total_order(self) -> None:
        for expression in (
            "row_number() OVER (ORDER BY o.bead_id, o.bead_version_id)",
            "rank() OVER (ORDER BY o.title)",
            "dense_rank() OVER (ORDER BY o.title)",
            "count(*) OVER (ORDER BY o.bead_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "lag(o.summary, 1, $1) OVER (ORDER BY o.bead_id, o.bead_version_id)",
        ):
            with self.subTest(expression=expression):
                parameters = (
                    (SqlParameter(1, "text", "no prior row"),)
                    if "$1" in expression
                    else ()
                )
                admit_query(
                    "SELECT " + expression + " AS value FROM memory_v1.observations o",
                    parameters,
                )
        for expression in (
            "row_number() OVER (ORDER BY o.title)",
            "lag(o.summary, 1) OVER (ORDER BY o.title)",
            "count(*) OVER (ORDER BY o.bead_id)",
            "count(*) OVER (ORDER BY o.bead_id RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "count(*) OVER (ORDER BY o.bead_id ROWS BETWEEN 4097 PRECEDING AND CURRENT ROW)",
        ):
            with (
                self.subTest(expression=expression),
                self.assertRaises(SqlAdmissionError),
            ):
                admit_query(
                    "SELECT " + expression + " AS value FROM memory_v1.observations o"
                )

    def test_array_membership_is_typed_bounded_and_not_in_expansion(self) -> None:
        query = "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE o.bead_id=ANY($1::uuid[])"
        for values in ([], [ANCHOR]):
            result = admit_query(
                query,
                (SqlParameter(1, "bead_ref[]", values),),
                admitted_anchors=ANCHORS,
            )
            self.assertEqual(
                result.parameters["p1"], [] if not values else [UUID(ANCHOR)]
            )
        for invalid_values in ([None], [ANCHOR] * 65):
            with self.assertRaises(SqlAdmissionError):
                admit_query(
                    query,
                    (SqlParameter(1, "bead_ref[]", invalid_values),),
                    admitted_anchors=ANCHORS,
                )
        with self.assertRaises(SqlAdmissionError):
            admit_query(
                query.replace("=ANY($1::uuid[])", "IN ($1)"),
                (SqlParameter(1, "bead_ref[]", [ANCHOR]),),
                admitted_anchors=ANCHORS,
            )

    def test_recursion_requires_real_anchor_depth_cycle_and_edge_progress(self) -> None:
        parameter = (SqlParameter(1, "bead_ref", ANCHOR),)
        for depth in range(1, 9):
            result = admit_query(
                recursive_sql(depth),
                parameter,
                admitted_anchors=ANCHORS,
                recursion=RecursionBound("walk", "depth", "node", depth),
            )
            self.assertGreaterEqual(
                result.sql.count(f"w.depth < CAST({depth} AS BIGINT)"), 2
            )
            self.assertIn("r.support_eligible = TRUE", result.sql)
            self.assertIn("r.roots_status = 'qualified'", result.sql)
            self.assertEqual(
                result.derivation_program["coverage"], {"explicit_depth": depth}
            )
        query = recursive_sql()
        for invalid in (
            query.replace("o.bead_id = $1", "$1 = $1"),
            query.replace("w.depth < 3", "w.depth < 3 OR w.depth < 3"),
            query.replace("w.depth + 1", "w.depth + 2"),
            query.replace("r.target_bead_id, w.depth", "w.node, w.depth"),
            query.replace(" CYCLE node SET is_cycle USING path", ""),
            query.replace("SELECT node, depth, is_cycle", "SELECT node, depth, path"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(SqlAdmissionError):
                admit_query(
                    invalid,
                    parameter,
                    admitted_anchors=ANCHORS,
                    recursion=RecursionBound("walk", "depth", "node", 3),
                )
        # Reader grants for native CYCLE are not agent-callable SQL functions.
        for direct in (
            "SELECT pg_catalog.record_eq(ROW(1),ROW(1)) AS x",
            "SELECT pg_catalog.array_cat(ARRAY[1],ARRAY[2]) AS x",
            "SELECT ROW(1)=ROW(1) AS x",
            "SELECT ARRAY[1] || ARRAY[2] AS x",
        ):
            with self.subTest(direct=direct), self.assertRaises(SqlAdmissionError):
                admit_query(direct)

    def test_unused_recursive_cte_refuses_without_forcing_a_path(self) -> None:
        query = recursive_sql(2).replace(
            "SELECT node, depth, is_cycle FROM walk",
            "SELECT s.statement_id FROM memory_v1.statements s",
        )
        admitted = admit_query(
            query,
            (SqlParameter(1, "bead_ref", ANCHOR),),
            admitted_anchors=ANCHORS,
            recursion=RecursionBound("walk", "depth", "node", 2),
        )
        with self.assertRaises(SqlAdmissionError) as refused:
            compile_bag_witness(admitted, SqlCatalog.installed().relations)
        self.assertEqual(refused.exception.construct, "witness_qualification_pending")

    def test_evaluation_relation_is_unavailable_without_explicit_trusted_selection(
        self,
    ) -> None:
        query = "SELECT c.bead_id AS id, c.score AS score FROM evaluation_v1.candidates c ORDER BY c.score DESC"
        with self.assertRaises(SqlAdmissionError) as refused:
            admit_query(query)
        self.assertEqual(refused.exception.code, "unavailable")
        result = admit_query(query, evaluation_admitted=True)
        self.assertEqual(result.columns[1].type.pg_type, "float8")

    def test_sql_bypasses_and_scope_manufacturing_are_refused_without_logging_text(
        self,
    ) -> None:
        statements = (
            "SELECT o.bead_id AS id FROM memory_v1.observations o; DELETE FROM memoriesql.beads",
            "WITH x AS (DELETE FROM memoriesql.beads RETURNING id) SELECT id FROM x",
            "SELECT pg_sleep($1) AS n",
            "SELECT pg_catalog.pg_read_file($1) AS n",
            "SELECT set_config($1,$2,$3) AS n",
            "SELECT o.bead_id AS id INTO scratch FROM memory_v1.observations o",
            "SELECT o.bead_id AS id FROM memory_v1.observations o FOR UPDATE",
            "SELECT o.bead_id AS id FROM memory_v1.observations o CROSS JOIN memory_v1.statements s",
            "SELECT o.bead_id AS id FROM memory_v1.observations o JOIN memory_v1.statements s ON o.summary=s.text",
            "SELECT * FROM memory_v1.observations",
            "SELECT b.id FROM memoriesql.beads b",
            "SELECT CAST(o.bead_id AS text) AS id FROM memory_v1.observations o",
            "COPY memoriesql.beads TO STDOUT",
            "SET ROLE memoriesql_application",
            "SELECT 'fixture protected marker",
        )
        for statement in statements:
            with (
                self.subTest(statement=statement),
                self.assertRaises(SqlAdmissionError),
            ):
                with self.assertNoLogs(logging.getLogger("sqlglot")):
                    admit_query(statement)

    def test_parameters_width_offsets_and_nonfinite_values_fail_closed(self) -> None:
        for parameter in (
            SqlParameter(1, "int8", "9223372036854775808"),
            SqlParameter(1, "float8", "nan"),
            SqlParameter(1, "numeric", "NaN"),
            SqlParameter(1, "text", "\x00"),
            SqlParameter(1, "text", "\ud800"),
        ):
            with (
                self.subTest(parameter_type=parameter.type),
                self.assertRaises(SqlAdmissionError),
            ):
                admit_query("SELECT $1 AS value", (parameter,))
        for suffix in ("OFFSET 10001", "LIMIT -1"):
            with self.assertRaises(SqlAdmissionError):
                admit_query(
                    "SELECT o.bead_id AS id FROM memory_v1.observations o " + suffix
                )


if __name__ == "__main__":
    unittest.main()
