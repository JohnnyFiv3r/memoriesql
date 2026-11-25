"""Seen fictional admission fixtures; these never access the evaluation holdout."""

from __future__ import annotations

import hashlib
import logging
import unittest
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlglot import exp, parse_one

from memoriesql.application.agent_sql_admission import RecursionBound, admit_query
from memoriesql.application.agent_sql_catalog import (
    SqlAdmissionError,
    SqlCatalog,
    SqlColumn,
    SqlParameter,
    SqlRelation,
    SqlType,
)
from memoriesql.application.agent_sql_results import (
    SavedQueryRows,
    admit_saved_query,
    refinement_inputs,
)
from memoriesql.application.agent_sql_witness import (
    SavedInputExecution,
    compile_bag_witness,
)
from memoriesql.application.investigation_contracts import (
    InputBinding,
    QueryRequest,
    result_json_bytes,
)
from memoriesql.infrastructure.postgres.query_witness import NativeWitnessBuilder
from memoriesql.infrastructure.postgres.relation_sql_population import (
    PreparedRelationPopulation,
)

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

    def test_saved_refinement_admits_only_the_exact_typed_parent_rows(self) -> None:
        catalog = SqlCatalog.installed()
        pin = {"result_id": ANCHOR, "content_digest": "a" * 64}
        parent = {"result": pin, "access": {"kind": "standalone"}}
        scope = {
            "view": "historical",
            "known_at": "2026-11-21T00:00:00Z",
            "source_refs": [],
        }
        request = QueryRequest.model_validate({
            "contract_version": 1,
            "kind": "query",
            "catalog_hash": catalog.hash,
            "run_ref": ANCHOR,
            "step_key": ANCHOR,
            "intent": "refine",
            "scope": scope,
            "inputs": [parent | {"alias": "prior"}],
            "parents": [parent],
            "sql": "SELECT prior.number FROM input.prior WHERE prior.number > $1",
            "parameters": [{"position": 1, "type": "int8", "value": "17"}],
            "max_result_bytes": 16384,
            "page_size": 20,
        })
        metadata: dict[str, Any] = {
            "result_id": ANCHOR,
            "catalog_hash": catalog.hash,
            "serializer": "result-json-v1",
            "provenance_revision": "native-bag-v1",
            "frame": {"view": "historical", "known_at": "2026-11-21T00:00:00.000000Z"},
            "query": {"scope": scope},
            "result_schema": [
                {"name": "number", "pg_type": "int8", "ref_type": None, "nullable": False},
            ],
            "total_rows": "2",
        }
        rows = [{"row_ref": str(i), "values": ["18"]} for i in range(1, 3)]
        binding, = refinement_inputs(request)
        saved = SavedQueryRows.from_result(request, metadata, rows, binding=binding, catalog=catalog)
        self.assertEqual(saved.rows, ((18,), (18,)))
        result = admit_saved_query(
            (saved,),
            request.sql,
            tuple(p.sql_parameter(catalog) for p in request.parameters),
            catalog=catalog,
        )
        self.assertEqual(result.relations, ("input.prior",))
        self.assertEqual(result.columns, (SqlColumn("number", SqlType("int8")),))
        bound = SavedInputExecution.bind(result, (saved,), lambda: None)
        self.assertEqual(bound.tables["input.prior"].rows, ((18, 1), (18, 2)))
        self.assertEqual(bound.parameters, {"p1": 17, "saved_1_0": 18, "saved_2_0": 18})
        plan = compile_bag_witness(
            result, bound.schemas, saved_input=bound
        )
        for physical_sql in (bound.sql(result), plan.sql):
            self.assertIn('WITH "__memoriesql_saved_0"', physical_sql)
            self.assertIn("%(saved_1_0)s", physical_sql)
            self.assertIn("%(p1)s", physical_sql)
            self.assertNotIn('"input"."prior"', physical_sql)

        with self.assertRaisesRegex(SqlAdmissionError, "saved_input_only"):
            admit_saved_query(
                (saved,),
                "SELECT q.number FROM input.prior q UNION ALL "
                "SELECT s.sequence AS number FROM memory_v1.statements s",
                (),
                catalog=catalog,
            )
        with self.assertRaisesRegex(SqlAdmissionError, "saved_input_only"):
            admit_saved_query(
                (saved,),
                "SELECT $1 AS number", (SqlParameter(1, "int8", "1"),), catalog=catalog
            )
        with self.assertRaisesRegex(SqlAdmissionError, "saved_input_frame"):
            SavedQueryRows.from_result(
                request, metadata | {"frame": metadata["frame"] | {"view": "resolved"}},
                rows, binding=binding, catalog=catalog,
            )
        with self.assertRaisesRegex(SqlAdmissionError, "saved_result"):
            SavedQueryRows.from_result(
                request, metadata, rows[::-1], binding=binding, catalog=catalog,
            )

    def test_saved_input_schema_qualification_survives_lowering(self) -> None:
        catalog = SqlCatalog.installed()
        binding = InputBinding.model_validate({
            "alias": "prior",
            "result": {"result_id": ANCHOR, "content_digest": "a" * 64},
            "access": {"kind": "standalone"},
        })
        saved = SavedQueryRows(
            binding, SqlRelation((SqlColumn("number", SqlType("int8")),)), (),
        )

        def lowered(statement: str) -> tuple[exp.Select, exp.Select]:
            query = admit_saved_query((saved,), statement, (), catalog=catalog)
            bound = SavedInputExecution.bind(query, (saved,), lambda: None)
            plan = compile_bag_witness(
                query, bound.schemas, saved_input=bound
            )
            native = parse_one(bound.sql(query), dialect="postgres")
            witness = parse_one(plan.sql, dialect="postgres")
            assert isinstance(native, exp.Select) and isinstance(witness, exp.Select)
            return native, witness

        for tree in lowered("SELECT input.prior.number FROM input.prior"):
            column = tree.selects[0]
            assert isinstance(column, exp.Column)
            self.assertFalse(column.db)
            self.assertEqual(column.table, tree.args["from_"].this.alias_or_name)
            self.assertEqual(column.name, "number")

        correlated = (
            "SELECT input.prior.number, prior.number AS same FROM input.prior "
            "WHERE EXISTS (SELECT prior.number FROM input.prior AS prior "
            "WHERE prior.number = input.prior.number)"
        )
        for tree in lowered(correlated):
            predicate, = (
                part for part in tree.find_all(exp.EQ)
                if isinstance(part.this, exp.Column) and part.this.name == "number"
            )
            self.assertEqual(predicate.this.table, "prior")
            self.assertFalse(predicate.this.db)
            self.assertIsInstance(predicate.expression, exp.Column)
            self.assertFalse(predicate.expression.db)
            self.assertEqual(predicate.expression.name, "number")
            self.assertNotEqual(predicate.expression.table, "prior")
            self.assertEqual(
                {
                    column.table
                    for projection in tree.selects[:2]
                    for column in projection.find_all(exp.Column)
                },
                {predicate.expression.table},
            )

        nested = (
            'WITH "__memoriesql_input_ref_0" AS '
            '(SELECT input.prior.number AS "input.prior.number" FROM input.prior) '
            'SELECT p."input.prior.number" FROM "__memoriesql_input_ref_0" p'
        )
        for tree in lowered(nested):
            self.assertEqual(tree.selects[0].name, "input.prior.number")
            self.assertTrue(all(not column.db for column in tree.find_all(exp.Column)))
            number_refs = [
                column for column in tree.find_all(exp.Column) if column.name == "number"
            ]
            self.assertTrue(number_refs)
            self.assertTrue(all(ref.table != "__memoriesql_input_ref_0" for ref in number_refs))

        for refused in (
            "SELECT input.prior.number FROM input.prior AS renamed",
            "SELECT input.wrong.number FROM input.prior AS wrong",
        ):
            with self.subTest(sql=refused), self.assertRaisesRegex(
                SqlAdmissionError, "column_qualification"
            ):
                admit_saved_query((saved,), refused, (), catalog=catalog)

    def test_saved_witness_ids_bind_the_exact_parent_in_a_shared_frame(self) -> None:
        catalog = SqlCatalog.installed()
        frame = UUID(int=23)
        known = datetime(2026, 11, 21, tzinfo=UTC)
        relation = SqlRelation((SqlColumn("number", SqlType("int8")),))

        def witness_refs(
            result_id: str, digest: str, *, filtered: bool = False
        ) -> tuple[str, str, str]:
            binding = InputBinding.model_validate({
                "alias": "prior",
                "result": {"result_id": result_id, "content_digest": digest},
                "access": {"kind": "standalone"},
            })
            saved = SavedQueryRows(binding, relation, ((18,),))
            statement = "SELECT q.number FROM input.prior q"
            parameters: tuple[SqlParameter, ...] = ()
            if filtered:
                statement += " WHERE q.number >= $1"
                parameters = (SqlParameter(1, "int8", "0"),)
            query = admit_saved_query((saved,), statement, parameters, catalog=catalog)
            bound = SavedInputExecution.bind(query, (saved,), lambda: None)
            plan = compile_bag_witness(
                query, bound.schemas, saved_input=bound
            )
            population = PreparedRelationPopulation(
                known, known, catalog.hash, {}, {}, {}, b"", b"", "a" * 64, 0,
                saved_inputs=(saved,),
            )
            program_sha256 = hashlib.sha256(
                result_json_bytes({
                    "sql": statement,
                    "parameters": [vars(value) for value in parameters],
                    "recursion": None,
                    "derivation_program": query.derivation_program,
                })
            ).hexdigest()
            builder = NativeWitnessBuilder(
                plan, population, frame, saved_input=bound,
                saved_program_sha256=program_sha256,
            )
            member, = builder.base["members"]
            self.assertEqual(member["frame_ref"], str(frame))
            self.assertEqual(member["parent_fact_ref"], {"result_id": result_id, "row_ref": "1"})
            self.assertEqual(plan.stages[2]["operation"], "filter" if filtered else "project")
            shared_trace = [2, [1, [0, [1]]]]
            node_ref = builder.add(shared_trace)
            return str(builder.population_ref), str(member["member_ref"]), str(node_ref)

        first = witness_refs(ANCHOR, "a" * 64)
        self.assertEqual(first, witness_refs(ANCHOR, "a" * 64))
        for changed in (witness_refs(str(UUID(int=24)), "a" * 64),
                        witness_refs(ANCHOR, "b" * 64)):
            self.assertTrue(set(first).isdisjoint(changed))
        sibling = witness_refs(ANCHOR, "a" * 64, filtered=True)
        self.assertEqual(first[:2], sibling[:2])
        self.assertNotEqual(first[2], sibling[2])
        self.assertEqual(sibling, witness_refs(ANCHOR, "a" * 64, filtered=True))

    def test_saved_join_binds_distinct_typed_inputs_and_parent_facts(self) -> None:
        catalog = SqlCatalog.installed()
        relation = SqlRelation((SqlColumn("id", SqlType("uuid", "bead_ref")),))
        saved = tuple(
            SavedQueryRows(
                InputBinding.model_validate({
                    "alias": alias,
                    "result": {"result_id": str(UUID(int=index)), "content_digest": "a" * 64},
                    "access": {"kind": "standalone"},
                }),
                relation,
                ((UUID(ANCHOR),),),
            )
            for index, alias in enumerate(("right_saved", "left_saved"), 1)
        )
        sql = (
            "SELECT input.left_saved.id FROM input.left_saved "
            "JOIN input.right_saved ON input.left_saved.id=input.right_saved.id"
        )
        query = admit_saved_query(saved, sql, (), catalog=catalog)
        bound = SavedInputExecution.bind(query, saved, lambda: None)
        self.assertEqual(bound.parameters, {"saved_0_1_0": UUID(ANCHOR), "saved_1_1_0": UUID(ANCHOR)})
        plan = compile_bag_witness(query, bound.schemas, saved_input=bound)
        for statement in (bound.sql(query), plan.sql):
            self.assertIn('"__memoriesql_saved_0"', statement)
            self.assertIn('"__memoriesql_saved_1"', statement)
            self.assertNotIn("input.", statement)
        known = datetime(2026, 11, 21, tzinfo=UTC)
        population = PreparedRelationPopulation(
            known, known, catalog.hash, {}, {}, {}, b"", b"", "a" * 64, 0, saved_inputs=saved,
        )
        builder = NativeWitnessBuilder(
            plan, population, UUID(int=23), saved_input=bound, saved_program_sha256="a" * 64,
        )
        self.assertIn("join", {stage["operation"] for stage in plan.stages})
        members = builder.base["members"]
        self.assertEqual(
            {member["relation"]: member["parent_fact_ref"] for member in members},
            {
                "input." + source.binding.alias: {
                    "result_id": source.binding.result.result_id, "row_ref": "1",
                }
                for source in saved
            },
        )
        self.assertEqual(len({member["member_ref"] for member in members}), 2)
        with self.assertRaisesRegex(SqlAdmissionError, "saved_input_only"):
            admit_saved_query(saved, "SELECT id FROM input.left_saved", (), catalog=catalog)
        with self.assertRaisesRegex(SqlAdmissionError, "saved_input_join"):
            admit_saved_query(saved, sql.replace("JOIN", "LEFT JOIN"), (), catalog=catalog)
        with self.assertRaisesRegex(SqlAdmissionError, "budget_exhausted"):
            SavedInputExecution.bind(
                query,
                tuple(SavedQueryRows(source.binding, relation, source.rows * 32768) for source in saved),
                lambda: self.fail("aggregate bind budget must be checked before lowering"),
            )

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

    def test_reverse_recursive_endpoint_and_multi_anchor_bounds(self) -> None:
        reverse = (
            recursive_sql(3)
            .replace(
                "SELECT o.bead_id, 0 FROM memory_v1.observations o WHERE o.bead_id = $1",
                "SELECT r.target_bead_id, 0 FROM memory_v1.assessed_relations r "
                "WHERE r.target_bead_id = ANY($1::uuid[])",
            )
            .replace(
                "SELECT r.target_bead_id, w.depth + 1",
                "SELECT r.source_bead_id, w.depth + 1",
            )
            .replace("r.source_bead_id = w.node", "r.target_bead_id = w.node")
        )
        anchors = [str(UUID(int=i)) for i in range(1, 17)]
        admitted = frozenset(("bead_ref", value) for value in anchors)
        result = admit_query(
            reverse,
            (SqlParameter(1, "bead_ref[]", anchors),),
            admitted_anchors=admitted,
            recursion=RecursionBound("walk", "depth", "node", 3),
        )
        self.assertIn("r.target_bead_id = w.node", result.sql)
        with self.assertRaises(SqlAdmissionError):
            admit_query(
                reverse,
                (SqlParameter(1, "bead_ref[]", [*anchors, str(UUID(int=17))]),),
                admitted_anchors=admitted | {("bead_ref", str(UUID(int=17)))},
                recursion=RecursionBound("walk", "depth", "node", 3),
            )
        with self.assertRaises(SqlAdmissionError):
            admit_query(
                reverse.replace(
                    "r.target_bead_id = w.node", "r.source_bead_id = w.node"
                ),
                (SqlParameter(1, "bead_ref[]", anchors),),
                admitted_anchors=admitted,
                recursion=RecursionBound("walk", "depth", "node", 3),
            )

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

    def test_adjacent_parameters_parse_and_dollar_quoting_stays_refused(self) -> None:
        head = "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE "
        values = (SqlParameter(1, "text", "one"), SqlParameter(2, "text", "two"))
        for predicate in (
            "o.summary IN ($1,$2)",
            "o.summary = coalesce($1,$2)",
            "($1=$2)",
            "$1<>$2",
            "o.summary=$1 OR o.title=$2",
        ):
            with self.subTest(predicate=predicate):
                result = admit_query(head + predicate, values)
                self.assertEqual(result.parameters, {"p1": "one", "p2": "two"})
                self.assertEqual(result.sql.count("%(p"), 2)
        # `$` starts only a positional parameter, so no dollar-quoted text is
        # read as a value: not the one literal date_trunc accepts either.
        for predicate in (
            "o.summary = $$fictional$$",
            "o.summary = $tag$fictional$tag$",
            "o.recorded_at = date_trunc('day', o.recorded_at, $$UTC$$)",
            "o.summary = $name",
            "o.summary = $1$",
        ):
            with (
                self.subTest(predicate=predicate),
                self.assertRaises(SqlAdmissionError),
                self.assertNoLogs(logging.getLogger("sqlglot")),
            ):
                admit_query(head + predicate)

    def test_a_refused_value_expression_says_where(self) -> None:
        head = "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE "
        text = (SqlParameter(1, "text", "fictional"),)
        # An operator has no token of its own: it is located by the first token
        # of its operands, and a parameter by its `$`.
        for predicate, parameters, construct, token in (
            ("o.nope = $1", text, "unknown_column", "o.nope"),
            ("nope = $1", text, "unknown_column", "nope"),
            ("o.recorded_at = $1", text, "incompatible_types", "o.recorded_at"),
            ("$1 = o.recorded_at", text, "incompatible_types", "$1"),
            ("o.summary = $1 AND o.title", text, "predicate_type", "o.title"),
            ("o.title + $1 = $1", text, "numeric_expression", "o.title"),
            ("o.bead_id = o.bead_version_id", (), "reference_type", "o.bead_id"),
            ("o.summary = 'fictional'", (), "unbound_literal", "'fictional'"),
        ):
            with (
                self.subTest(predicate=predicate),
                self.assertRaises(SqlAdmissionError) as refused,
            ):
                admit_query(head + predicate, parameters)
            self.assertEqual(
                (refused.exception.construct, refused.exception.position),
                (construct, len(head) + predicate.index(token)),
            )
        # A clause, a relation or a whole statement has no single place.
        select = "SELECT o.bead_id AS id FROM memory_v1.observations o"
        for statement, construct in (
            (select + " FOR UPDATE", "clause"),
            (
                select + " UNION SELECT s.kind AS id FROM memory_v1.statements s",
                "incompatible_types",
            ),
            ("SELECT o.bead_id AS id FROM memory_v1.nothing o", "relation"),
            ("SELECT c.bead_id AS id FROM evaluation_v1.candidates c", "relation"),
        ):
            with (
                self.subTest(statement=statement),
                self.assertRaises(SqlAdmissionError) as refused,
            ):
                admit_query(statement)
            self.assertEqual(
                (refused.exception.construct, refused.exception.position),
                (construct, None),
            )

    def test_an_identifier_column_needs_a_value_of_its_reference_type(self) -> None:
        statement = "11111111-1111-4111-8111-111111111112"
        sql = (
            "SELECT ss.statement_id FROM memory_v1.statement_sources ss "
            "WHERE ss.statement_id = ANY($1::uuid[])"
        )
        # A plain uuid never names an identifier. The refusal says so: the same
        # physical type, under the wrong reference type.
        with self.assertRaises(SqlAdmissionError) as plain:
            admit_query(sql, (SqlParameter(1, "uuid[]", [statement]),))
        self.assertEqual(
            (plain.exception.code, plain.exception.construct, plain.exception.position),
            ("invalid_request", "reference_type", sql.index("ss.statement_id = ANY")),
        )
        # So does another identifier kind over the same physical type.
        with self.assertRaises(SqlAdmissionError) as other:
            admit_query(
                sql,
                (SqlParameter(1, "bead_ref[]", [ANCHOR]),),
                admitted_anchors=ANCHORS,
            )
        self.assertEqual(other.exception.construct, "reference_type")
        # The column's own reference type binds, once the identifier is visible.
        typed = (SqlParameter(1, "statement_ref[]", [statement]),)
        with self.assertRaises(SqlAdmissionError) as unseen:
            admit_query(sql, typed)
        self.assertEqual(
            (unseen.exception.code, unseen.exception.construct),
            ("unavailable", "parameter_anchor"),
        )
        admitted = admit_query(
            sql, typed, admitted_anchors=frozenset({("statement_ref", statement)})
        )
        self.assertEqual(admitted.parameters, {"p1": [UUID(statement)]})
        # A different physical type stays the general mismatch.
        with self.assertRaises(SqlAdmissionError) as physical:
            admit_query(sql, (SqlParameter(1, "text[]", [statement]),))
        self.assertEqual(physical.exception.construct, "array_type")

    def test_an_admitted_comparison_never_changes_meaning(self) -> None:
        # The parser reads `x = y IS NULL` as `x = (y IS NULL)` and emits it
        # unparenthesized; PostgreSQL reads `(x = y) IS NULL`. It never chains
        # comparisons. Such an operand is refused where its comparison starts.
        head = "SELECT r.relation_id FROM memory_v1.assessed_relations r WHERE "
        flag = (SqlParameter(1, "bool", True),)
        for predicate, parameters in (
            ("r.support_eligible = r.correction_pending IS NULL", ()),
            ("r.support_eligible = r.correction_pending IS NOT NULL", ()),
            ("r.support_eligible <> r.correction_pending IS NULL", ()),
            ("r.support_eligible IS NULL = $1", flag),
            ("r.support_eligible = r.correction_pending = $1", flag),
            ("r.revision < r.revision = $1", flag),
            ("$1 = r.support_eligible = r.correction_pending", flag),
        ):
            with (
                self.subTest(predicate=predicate),
                self.assertRaises(SqlAdmissionError) as refused,
            ):
                admit_query(head + predicate, parameters)
            self.assertEqual(
                (
                    refused.exception.code,
                    refused.exception.construct,
                    refused.exception.position,
                ),
                ("unsupported", "comparison_grouping", len(head)),
            )
        # Parentheses say which is meant, and the emitted SQL keeps them.
        for predicate, parameters in (
            ("r.support_eligible = (r.correction_pending IS NULL)", ()),
            ("(r.support_eligible = r.correction_pending) IS NULL", ()),
            ("(r.support_eligible = r.correction_pending) = $1", flag),
        ):
            with self.subTest(predicate=predicate):
                emitted = admit_query(head + predicate, parameters).sql
                self.assertIn(predicate.split(" = $1")[0], emitted)

    def test_not_like_is_its_own_operator(self) -> None:
        head = "SELECT s.statement_id FROM memory_v1.statements s WHERE "
        text = (SqlParameter(1, "text", "fictional%"),)
        both = (*text, SqlParameter(2, "text", "fictional"))
        # `x NOT LIKE y` calls the negated matcher, and the planner turns NOT
        # over it back into LIKE. Two bound values are matched as written.
        for predicate, parameters, builtin, token in (
            ("s.kind NOT LIKE $1", text, "textnlike(text,text)", "s.kind"),
            ("s.kind NOT ILIKE $1", text, "texticnlike(text,text)", "s.kind"),
            ("NOT (s.kind NOT LIKE $1)", text, "textlike(text,text)", "s.kind"),
            ("NOT (s.kind NOT ILIKE $1)", text, "texticlike(text,text)", "s.kind"),
            ("$2 NOT LIKE $1", both, "textnlike(text,text)", "$2"),
            ("NOT ($2 NOT LIKE $1)", both, "textnlike(text,text)", "$2"),
        ):
            with self.subTest(predicate=predicate):
                admitted = admit_query(head + predicate, parameters)
                self.assertEqual(
                    dict(admitted.operators),
                    {builtin: len(head) + predicate.index(token)},
                )
                self.assertIn(" NOT ", admitted.sql)

    def test_recursion_over_correction_edges_is_refused_explicitly(self) -> None:
        # Expanding correction edges needs a database predicate for a qualified
        # correction path, which no migration defines. Admission refuses it
        # instead of emitting a call to a function that does not exist.
        corrections = (
            recursive_sql(3)
            .replace("r.target_bead_id, w.depth", "k.successor_bead_id, w.depth")
            .replace(
                "memory_v1.assessed_relations r ON r.source_bead_id",
                "memory_v1.corrections k ON k.predecessor_bead_id",
            )
        )
        self.assertIn("memory_v1.corrections k", corrections)
        with self.assertRaises(SqlAdmissionError) as refused:
            admit_query(
                corrections,
                (SqlParameter(1, "bead_ref", ANCHOR),),
                admitted_anchors=ANCHORS,
                recursion=RecursionBound("walk", "depth", "node", 3),
            )
        self.assertEqual(
            (
                refused.exception.code,
                refused.exception.construct,
                refused.exception.position,
            ),
            ("unsupported", "correction_recursion", None),
        )


if __name__ == "__main__":
    unittest.main()
