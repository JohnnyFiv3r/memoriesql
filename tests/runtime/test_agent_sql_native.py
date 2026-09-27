"""Native agreement on seen fictional rows, independent of lifecycle qualification."""

from __future__ import annotations

import os
import unittest
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_admission import RecursionBound, admit_select
from memoriesql.application.agent_sql_catalog import (
    SqlCatalog,
    SqlColumn,
    SqlParameter,
    SqlRelation,
    SqlType,
)


def identity(number: int) -> UUID:
    return UUID(int=number)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AgentSqlNative(unittest.TestCase):
    admin: ClassVar[psycopg.Connection[tuple[Any, ...]]]
    db: ClassVar[psycopg.Connection[tuple[Any, ...]]]
    name: ClassVar[str]
    dsn: ClassVar[str]
    catalog: ClassVar[SqlCatalog]
    edges: ClassVar[list[tuple[int, int]]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.admin = psycopg.connect(os.environ["N1_TEST_DATABASE_URL"], autocommit=True)
        cls.name = "pr05_native_" + uuid4().hex
        cls.admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name))
        )
        cls.dsn = make_conninfo(os.environ["N1_TEST_DATABASE_URL"], dbname=cls.name)
        cls.db = psycopg.connect(cls.dsn, autocommit=True)
        cls.db.execute("SET timezone='UTC'")
        cls.catalog = SqlCatalog.installed()
        cls.db.execute(
            "CREATE SCHEMA memory_v1; CREATE SCHEMA evaluation_v1; CREATE SCHEMA input"
        )
        for name, relation in cls.catalog.relations.items():
            schema, table = name.split(".")
            fields = [
                sql.SQL("{} {}{}").format(
                    sql.Identifier(c.name),
                    sql.SQL(c.type.pg_type),
                    sql.SQL(' COLLATE "C"' if c.type.pg_type == "text" else ""),
                )
                for c in relation.columns
            ]
            cls.db.execute(
                sql.SQL("CREATE TABLE {} ({})").format(
                    sql.Identifier(schema, table), sql.SQL(",").join(fields)
                )
            )
        cls.db.execute("CREATE TABLE input.prior (id uuid, n numeric)")
        for number in range(1, 7):
            cls.insert(
                "memory_v1.observations",
                number,
                {
                    "bead_id": identity(number),
                    "bead_version_id": identity(100 + number),
                    "title": "orchard" if number < 4 else None,
                    "summary": "fictional orchard " + str(number),
                    "recorded_at": datetime(2024, number, 1, tzinfo=UTC),
                },
            )
        for number, bead in enumerate([1, 1, 2, 3, 4], 1):
            cls.insert(
                "memory_v1.statements",
                number + 200,
                {
                    "bead_id": identity(bead),
                    "bead_version_id": identity(100 + bead),
                    "statement_id": identity(200 + number),
                    "text": "fictional " + str(number),
                },
            )
        # Qualified diamond and cycle; an indeterminate-root edge is not path support.
        cls.edges = [(1, 2), (1, 3), (2, 4), (3, 4), (4, 1), (1, 5)]
        for number, (source, target) in enumerate(cls.edges, 1):
            cls.insert(
                "memory_v1.assessed_relations",
                300 + number,
                {
                    "source_bead_id": identity(source),
                    "target_bead_id": identity(target),
                    "source_bead_version_id": identity(100 + source),
                    "target_bead_version_id": identity(100 + target),
                    "support_eligible": True,
                    "roots_status": "qualified" if target != 5 else "indeterminate",
                },
            )
        cls.db.execute(
            "INSERT INTO input.prior VALUES (%s, %s), (%s, %s)",
            (identity(1), Decimal("2.5"), identity(2), Decimal("3.5")),
        )
        for digest in ("a" * 64, "b" * 64):
            cls.insert("memory_v1.source_units", 500, {"content_sha256": digest})
        cls.insert(
            "memory_v1.statement_sources",
            500,
            {"source_unit_id": identity(500), "content_sha256": "a" * 64},
        )

    @classmethod
    def insert(cls, name: str, number: int, overrides: dict[str, Any]) -> None:
        values: list[Any] = []
        relation = cls.catalog.relations[name]
        for c in relation.columns:
            default: Any = {
                "uuid": identity(number),
                "text": "fictional",
                "bool": False,
                "int8": number,
                "numeric": Decimal(number),
                "float8": float(number),
                "timestamptz": datetime(2024, 1, 1, tzinfo=UTC),
            }[c.type.pg_type]
            values.append(overrides.get(c.name, default))
        cls.db.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(*name.split(".")),
                sql.SQL(",").join(sql.Identifier(c.name) for c in relation.columns),
                sql.SQL(",").join(sql.Placeholder() for _ in values),
            ),
            values,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(cls.name))
        )
        cls.admin.close()

    def execute(
        self, query: str, parameters: tuple[SqlParameter, ...] = (), **context: Any
    ) -> list[tuple[Any, ...]]:
        admitted = admit_select(query, parameters, **context)
        with self.db.cursor() as cursor:
            cursor.execute(admitted.sql, admitted.parameters)
            assert cursor.description
            expected_oids = {
                "uuid": 2950,
                "text": 25,
                "bool": 16,
                "int8": 20,
                "numeric": 1700,
                "float8": 701,
                "timestamptz": 1184,
            }
            self.assertEqual(
                [c.type_code for c in cursor.description],
                [expected_oids[c.type.pg_type] for c in admitted.columns],
            )
            self.assertEqual(
                [c.name for c in cursor.description], [c.name for c in admitted.columns]
            )
            return cursor.fetchall()

    def test_bound_driver_values_percent_headers_and_fully_qualified_columns(
        self,
    ) -> None:
        rows = self.execute(
            'SELECT memory_v1.observations.bead_id AS "%(p1)s%%" FROM memory_v1.observations WHERE observations.summary ILIKE $1 ORDER BY observations.bead_id',
            (SqlParameter(1, "text", "%orchard%"),),
        )
        self.assertEqual(rows, [(identity(i),) for i in range(1, 7)])

    def test_content_versions_join_by_bytes_and_order_by_complete_visible_key(
        self,
    ) -> None:
        rows = self.execute(
            "SELECT u.source_unit_id AS unit,u.content_sha256 AS digest,row_number() OVER (ORDER BY u.source_unit_id,u.content_sha256) AS n FROM memory_v1.source_units u ORDER BY u.source_unit_id,u.content_sha256"
        )
        self.assertEqual(
            rows, [(identity(500), "a" * 64, 1), (identity(500), "b" * 64, 2)]
        )
        rows = self.execute(
            "SELECT u.content_sha256 AS digest FROM memory_v1.source_units u JOIN memory_v1.statement_sources s ON s.source_unit_id=u.source_unit_id AND s.content_sha256=u.content_sha256"
        )
        self.assertEqual(rows, [("a" * 64,)])

    def test_parameter_only_collation_and_group_aggregates_feed_windows(self) -> None:
        params = (SqlParameter(1, "text", "İ"), SqlParameter(2, "text", "i"))
        self.assertEqual(
            self.execute("SELECT $1 ILIKE $2 AS matched", params), [(False,)]
        )
        rows = self.execute("SELECT $1 AS text UNION SELECT $2 AS text", params)
        self.assertEqual(set(rows), {("i",), ("İ",)})
        admitted = admit_select("SELECT lower($1) AS text", (params[0],))
        rows = self.db.execute(
            "SELECT pg_collation_for(q.text) FROM (" + admitted.sql + ") q",
            admitted.parameters,
        ).fetchall()
        self.assertEqual(rows, [('"C"',)])
        rows = self.execute(
            "SELECT s.bead_id AS id, sum(count(*)) OVER (ORDER BY s.bead_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n FROM memory_v1.statements s GROUP BY s.bead_id ORDER BY s.bead_id"
        )
        self.assertEqual(
            rows,
            [
                (identity(1), Decimal(2)),
                (identity(2), Decimal(3)),
                (identity(3), Decimal(4)),
                (identity(4), Decimal(5)),
            ],
        )

    def test_sets_preserve_nulls_and_bag_multiplicity(self) -> None:
        a = Counter([identity(i) for i in range(1, 7)])
        b = Counter(identity(i) for i in [1, 1, 2, 3, 4])
        left = "SELECT o.bead_id AS id FROM memory_v1.observations o"
        right = "SELECT s.bead_id AS id FROM memory_v1.statements s"
        expectations = {
            "UNION ALL": a + b,
            "UNION": Counter(set(a) | set(b)),
            "INTERSECT ALL": a & b,
            "INTERSECT": Counter(set(a) & set(b)),
            "EXCEPT ALL": a - b,
            "EXCEPT": Counter(set(a) - set(b)),
        }
        for operation, expected in expectations.items():
            with self.subTest(operation=operation):
                self.assertEqual(
                    Counter(
                        row[0]
                        for row in self.execute(left + " " + operation + " " + right)
                    ),
                    expected,
                )
        self.assertEqual(
            self.execute(
                "SELECT o.title AS title FROM memory_v1.observations o INTERSECT SELECT o.title AS title FROM memory_v1.observations o"
            ),
            [(None,), ("orchard",)],
        )

    def test_join_counts_and_outer_nonmatches(self) -> None:
        for kind, count in [("INNER", 5), ("LEFT", 7), ("RIGHT", 5), ("FULL", 7)]:
            with self.subTest(kind=kind):
                rows = self.execute(
                    f"SELECT o.bead_id AS id, s.statement_id AS sid FROM memory_v1.observations o {kind} JOIN memory_v1.statements s ON s.bead_id=o.bead_id"
                )
                self.assertEqual(len(rows), count)
        rows = self.execute(
            "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE NOT EXISTS (SELECT s.statement_id AS sid FROM memory_v1.statements s WHERE s.bead_id=o.bead_id) ORDER BY o.bead_id"
        )
        self.assertEqual(rows, [(identity(5),), (identity(6),)])

    def test_saved_group_facts_compose_and_integer_sum_promotes(self) -> None:
        saved = SqlRelation(
            (
                SqlColumn("id", SqlType("uuid", "bead_ref")),
                SqlColumn("n", SqlType("numeric")),
            ),
            (("id",),),
        )
        self.assertEqual(
            self.execute(
                "SELECT sum(p.n) AS total, avg(p.n) AS mean FROM input.prior p",
                inputs={"prior": saved},
            ),
            [(Decimal("6"), Decimal("3"))],
        )
        rows = self.execute(
            "WITH groups AS (SELECT s.bead_id AS id, count(*) AS n FROM memory_v1.statements s GROUP BY s.bead_id) SELECT sum(g.n) AS total FROM groups g"
        )
        self.assertEqual(rows, [(Decimal(5),)])

    def test_alias_group_case_filter_empty_and_checked_numeric_casts(self) -> None:
        rows = self.execute(
            "SELECT lower(o.title) AS label, count(*) AS n FROM memory_v1.observations o GROUP BY label ORDER BY label"
        )
        self.assertEqual(rows, [("orchard", 3), (None, 3)])
        rows = self.execute(
            "SELECT count(*) FILTER (WHERE o.title IS NOT NULL) AS n, sum($1::numeric) AS total, avg($2::int8) AS mean FROM memory_v1.observations o",
            (SqlParameter(1, "numeric", "2.5"), SqlParameter(2, "int8", "3")),
        )
        self.assertEqual(rows, [(3, Decimal("15"), Decimal("3"))])
        rows = self.execute(
            "SELECT count(*) AS n, sum($1) AS total FROM memory_v1.observations o WHERE o.title=$2",
            (SqlParameter(1, "int8", "1"), SqlParameter(2, "text", "absent")),
        )
        self.assertEqual(rows, [(0, None)])
        rows = self.execute(
            "SELECT CASE WHEN o.title IS NULL THEN $1 ELSE upper(o.title) END AS label FROM memory_v1.observations o ORDER BY o.bead_id",
            (SqlParameter(1, "text", "UNTITLED"),),
        )
        self.assertEqual(rows, [("ORCHARD",)] * 3 + [("UNTITLED",)] * 3)

    def test_native_uuid_and_boolean_comparable_aggregates(self) -> None:
        self.assertEqual(
            self.execute(
                "SELECT min(o.bead_id) AS low, max(o.bead_id) AS high FROM memory_v1.observations o"
            ),
            [(identity(1), identity(6))],
        )
        self.assertEqual(
            self.execute(
                "SELECT min(o.bead_id) FILTER (WHERE o.title IS NULL) AS low FROM memory_v1.observations o"
            ),
            [(identity(4),)],
        )
        self.assertEqual(
            self.execute(
                "SELECT min(r.support_eligible) AS low, max(r.support_eligible) AS high FROM memory_v1.assessed_relations r"
            ),
            [(True, True)],
        )
        rows = self.execute(
            "SELECT min(o.bead_id) OVER (ORDER BY o.bead_id ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) AS low FROM memory_v1.observations o ORDER BY o.bead_id"
        )
        self.assertEqual(rows, [(identity(i),) for i in range(1, 7)])

    def test_windows_keep_peers_total_order_neighbors_and_frame_multiplicity(
        self,
    ) -> None:
        rows = self.execute(
            "SELECT row_number() OVER (ORDER BY o.bead_id, o.bead_version_id) AS rn, rank() OVER (ORDER BY o.title) AS rank, dense_rank() OVER (ORDER BY o.title) AS dense, lag(o.summary, 1, $1) OVER (ORDER BY o.bead_id, o.bead_version_id) AS prior, count(*) OVER (ORDER BY o.bead_id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS n FROM memory_v1.observations o ORDER BY o.bead_id",
            (SqlParameter(1, "text", "start"),),
        )
        self.assertEqual([r[0] for r in rows], list(range(1, 7)))
        self.assertEqual([r[1] for r in rows], [1, 1, 1, 4, 4, 4])
        self.assertEqual([r[2] for r in rows], [1, 1, 1, 2, 2, 2])
        self.assertEqual(
            [r[3] for r in rows],
            ["start"] + ["fictional orchard " + str(i) for i in range(1, 6)],
        )
        self.assertEqual([r[4] for r in rows], [1, 2, 2, 2, 2, 2])

    def test_typed_array_membership_and_scalar_cardinality_failures(self) -> None:
        anchors = frozenset(("bead_ref", str(identity(i))) for i in [1, 3])
        query = "SELECT o.bead_id AS id FROM memory_v1.observations o WHERE o.bead_id=ANY($1::uuid[]) ORDER BY o.bead_id"
        self.assertEqual(
            self.execute(
                query,
                (SqlParameter(1, "bead_ref[]", [str(identity(1)), str(identity(3))]),),
                admitted_anchors=anchors,
            ),
            [(identity(1),), (identity(3),)],
        )
        self.assertEqual(
            self.execute(
                query, (SqlParameter(1, "bead_ref[]", []),), admitted_anchors=anchors
            ),
            [],
        )
        with self.assertRaises(psycopg.errors.CardinalityViolation):
            self.execute(
                "SELECT (SELECT s.statement_id AS id FROM memory_v1.statements s WHERE s.bead_id=o.bead_id) AS sid FROM memory_v1.observations o"
            )

    def test_date_trunc_and_same_type_null_functions(self) -> None:
        rows = self.execute(
            "SELECT date_trunc('month', o.recorded_at, 'UTC') AS month, coalesce(o.title,$1) AS title, nullif(o.title,$2) AS other FROM memory_v1.observations o ORDER BY o.bead_id",
            (SqlParameter(1, "text", "untitled"), SqlParameter(2, "text", "orchard")),
        )
        self.assertEqual(
            [r[0] for r in rows],
            [datetime(2024, i, 1, tzinfo=UTC) for i in range(1, 7)],
        )
        self.assertTrue(all(r[2] is None for r in rows))

    def test_bounded_native_cycles_diamonds_and_all_depths(self) -> None:
        for depth in range(1, 9):
            query = f"""WITH RECURSIVE walk(node, depth) AS (
              SELECT o.bead_id, 0 FROM memory_v1.observations o WHERE o.bead_id=$1
              UNION ALL SELECT r.target_bead_id, w.depth+1
              FROM walk w JOIN memory_v1.assessed_relations r ON r.source_bead_id=w.node
              WHERE w.depth < {depth}
            ) CYCLE node SET is_cycle USING path
            SELECT node, depth, is_cycle FROM walk"""
            rows = self.execute(
                query,
                (SqlParameter(1, "bead_ref", str(identity(1))),),
                admitted_anchors=frozenset({("bead_ref", str(identity(1)))}),
                recursion=RecursionBound("walk", "depth", "node", depth),
            )
            expected: list[tuple[Any, ...]] = []

            def visit(node: int, level: int, path: tuple[int, ...]) -> None:
                cycling = node in path
                expected.append((identity(node), level, cycling))
                if cycling or level >= depth:
                    return
                for source, target in self.edges:
                    if source == node and target != 5:
                        visit(target, level + 1, (*path, node))

            visit(1, 0, ())
            self.assertEqual(Counter(rows), Counter(expected))
            self.assertTrue(all(row[1] <= depth for row in rows))


if __name__ == "__main__":
    unittest.main()
