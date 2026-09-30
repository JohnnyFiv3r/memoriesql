"""Database-free checks that admitted operators stay inside the reviewed closure.

The packet's grammar admits typed comparisons, LIKE, unary minus and membership
in up to 64 bound values; the restricted reader is granted only a reviewed set of
builtins. An operator outside that set must be refused as unsupported, not fail
inside the reader as a permission error. Admission's inventory is its model of
what PostgreSQL calls; an installed test holds that model to the pinned server.
No execution claim is made here.
"""

from __future__ import annotations

import unittest
from typing import Any
from uuid import UUID

from memoriesql.application.agent_sql_admission import (
    admit_query,
    comparison_builtin,
    hash_builtin,
    require_reviewed,
)
from memoriesql.application.agent_sql_catalog import (
    SqlAdmissionError,
    SqlCatalog,
    SqlParameter,
)
from memoriesql.infrastructure.postgres.query_reader_provisioning import (
    REVIEWED_BUILTINS,
)

STATEMENTS = "SELECT s.statement_id FROM memory_v1.statements s WHERE "
RELATIONS = "SELECT r.relation_id FROM memory_v1.assessed_relations r WHERE "
OPERATORS = ("=", "<>", "<", "<=", ">", ">=")
TYPES = ("text", "uuid", "bool", "int8", "numeric", "float8", "timestamptz")
TEXT = (SqlParameter(1, "text", "fictional"),)
INT8 = (SqlParameter(1, "int8", "1"),)
TIME = (SqlParameter(1, "timestamptz", "2026-01-01T00:00:00Z"),)
BOTH = (SqlParameter(1, "int8", "1"), SqlParameter(2, "text", "fictional"))
TEXTS = (SqlParameter(1, "text", "fictional"), SqlParameter(2, "text", "other"))
Case = tuple[str, tuple[SqlParameter, ...], set[str]]


def builtins(
    sql: str, parameters: tuple[SqlParameter, ...] = (), **context: Any
) -> set[str]:
    return {builtin for builtin, _ in admit_query(sql, parameters, **context).operators}


def one(kind: str, value: Any) -> tuple[SqlParameter, ...]:
    return (SqlParameter(1, kind, value),)


def listed(count: int, kind: str = "int8") -> tuple[str, tuple[SqlParameter, ...]]:
    """An IN list of `count` bound values and its parameters."""
    return (
        ", ".join(f"${position}" for position in range(1, count + 1)),
        tuple(
            SqlParameter(position, kind, str(position))
            for position in range(1, count + 1)
        ),
    )


class AgentSqlOperators(unittest.TestCase):
    def check(self, head: str, cases: tuple[Case, ...], **context: Any) -> None:
        for predicate, parameters, expected in cases:
            with self.subTest(predicate=predicate, parameters=parameters):
                self.assertEqual(
                    builtins(head + predicate, parameters, **context), expected
                )

    def test_a_comparison_resolves_to_its_builtin_through_planner_negation(
        self,
    ) -> None:
        # PostgreSQL rewrites NOT over a comparison into the comparison's negator,
        # through AND, OR and NOT, but not through CASE.
        self.check(
            STATEMENTS,
            (
                ("s.kind = $1", TEXT, {"texteq(text,text)"}),
                ("NOT (s.kind = $1)", TEXT, {"textne(text,text)"}),
                ("NOT (NOT (s.kind = $1))", TEXT, {"texteq(text,text)"}),
                ("s.kind <> $1", TEXT, {"textne(text,text)"}),
                ("lower(s.text) LIKE $1", TEXT, {"textlike(text,text)"}),
                ("NOT (s.text ILIKE $1)", TEXT, {"texticnlike(text,text)"}),
                (
                    "s.recorded_at >= $1",
                    TIME,
                    {"timestamptz_ge(timestamptz,timestamptz)"},
                ),
                (
                    "NOT (s.sequence < $1 AND s.kind = $2)",
                    BOTH,
                    {"int8ge(bigint,bigint)", "textne(text,text)"},
                ),
                (
                    "NOT (s.sequence < $1 OR NOT (s.kind = $2))",
                    BOTH,
                    {"int8ge(bigint,bigint)", "texteq(text,text)"},
                ),
                (
                    "CASE WHEN s.sequence = $1 THEN NOT (s.kind = $2)"
                    " ELSE s.kind = $2 END",
                    BOTH,
                    {"int8eq(bigint,bigint)", "textne(text,text)", "texteq(text,text)"},
                ),
                (
                    "NOT (CASE WHEN s.sequence = $1 THEN s.kind = $2 END)",
                    BOTH,
                    {"int8eq(bigint,bigint)", "texteq(text,text)"},
                ),
                ("-s.sequence < $1", INT8, {"int8um(bigint)", "int8lt(bigint,bigint)"}),
                ("nullif(s.kind, $1) IS NULL", TEXT, {"texteq(text,text)"}),
                ("s.correction_reason IS NOT NULL", (), set()),
            ),
        )

    def test_bound_values_fold_before_operators_resolve(self) -> None:
        absent = one("text", None)
        # A comparison, match or negation of NULL calls nothing: it is NULL.
        self.check(
            STATEMENTS,
            (
                ("s.kind <> $1", absent, set()),
                ("NOT (s.kind = $1)", absent, set()),
                ("s.kind <> NULL", (), set()),
                ("s.kind IN ($1)", absent, set()),
                ("s.text LIKE $1", absent, set()),
                ("nullif(s.kind, $1) IS NULL", absent, set()),
                ("s.sequence < -$1", one("int8", None), {"int8lt(bigint,bigint)"}),
                # The optional-filter idiom binds NULL to switch a filter off.
                ("$1 IS NULL OR s.kind <> $1", absent, set()),
                ("$1 IS NULL OR s.kind <> $1", TEXT, {"textne(text,text)"}),
                # Two bound values are compared while planning, as written; a
                # NOT above them only negates the constant that leaves.
                ("$1 = $2", TEXTS, {"texteq(text,text)"}),
                ("NOT ($1 = $2)", TEXTS, {"texteq(text,text)"}),
                ("$1 <> $2", TEXTS, {"textne(text,text)"}),
                ("NOT ($1 LIKE $2)", TEXTS, {"textlike(text,text)"}),
                ("$1 IN ($2)", TEXTS, {"texteq(text,text)"}),
            ),
        )
        # `x = true` is `x` and `x = false` is `NOT x`: no boolean builtin is
        # called, and the negation lands on the operators inside x.
        yes, no = one("bool", True), one("bool", False)
        inner = "(r.state = r.roots_status)"
        self.check(
            RELATIONS,
            (
                ("r.support_eligible = $1", yes, set()),
                ("r.support_eligible <> $1", yes, set()),
                ("NOT (r.support_eligible = $1)", no, set()),
                ("$1 <> r.support_eligible", no, set()),
                ("r.support_eligible IN ($1)", yes, set()),
                (inner + " = $1", yes, {"texteq(text,text)"}),
                (inner + " = $1", no, {"textne(text,text)"}),
                (inner + " <> $1", yes, {"textne(text,text)"}),
                (inner + " <> $1", no, {"texteq(text,text)"}),
                ("NOT (" + inner + " = $1)", no, {"texteq(text,text)"}),
                ("$1 = " + inner, no, {"textne(text,text)"}),
                (
                    "(r.author_confidence < r.author_confidence) = $1",
                    no,
                    {"numeric_ge(numeric,numeric)"},
                ),
                # Only equality against one bound flag is simplified.
                ("r.support_eligible < $1", yes, {"boollt(boolean,boolean)"}),
                (
                    "r.support_eligible = r.correction_pending",
                    (),
                    {"booleq(boolean,boolean)"},
                ),
                (
                    "NOT (r.support_eligible = r.correction_pending)",
                    (),
                    {"boolne(boolean,boolean)"},
                ),
                (
                    "$1 = $2",
                    (SqlParameter(1, "bool", True), SqlParameter(2, "bool", False)),
                    {"booleq(boolean,boolean)"},
                ),
                ("r.support_eligible = $1", one("bool", None), set()),
            ),
        )

    def test_membership_counts_its_bound_values(self) -> None:
        # From nine bound values PostgreSQL probes a hash table, which calls the
        # type's equality and hash functions whether or not a NOT is above it.
        eight, nine = [str(n) for n in range(8)], [str(n) for n in range(9)]
        equal, unequal = "texteq(text,text)", "textne(text,text)"
        hashed = {equal, "hashtext(text)"}
        array = "s.kind = ANY($1::text[])"
        self.check(
            STATEMENTS,
            (
                (array, one("text[]", eight), {equal}),
                (array, one("text[]", nine), hashed),
                ("NOT (" + array + ")", one("text[]", eight), {unequal}),
                ("NOT (" + array + ")", one("text[]", nine), hashed),
                (array, one("text[]", []), {equal}),
                (
                    "s.recorded_at = ANY($1::timestamptz[])",
                    one("timestamptz[]", ["2026-01-01T00:00:00Z"] * 9),
                    {
                        "timestamptz_eq(timestamptz,timestamptz)",
                        "timestamptz_hash(timestamptz)",
                    },
                ),
            ),
        )
        for count, negated, expected in (
            (2, False, {"int8eq(bigint,bigint)"}),
            (8, False, {"int8eq(bigint,bigint)"}),
            (9, False, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
            (2, True, {"int8ne(bigint,bigint)"}),
            (8, True, {"int8ne(bigint,bigint)"}),
            (9, True, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
            (64, True, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
        ):
            values, parameters = listed(count)
            predicate = "s.sequence " + ("NOT " if negated else "") + f"IN ({values})"
            with self.subTest(predicate=predicate):
                self.assertEqual(builtins(STATEMENTS + predicate, parameters), expected)
        # A bound left side is compared while planning: equality alone.
        values, parameters = listed(9, "text")
        self.assertEqual(
            builtins(STATEMENTS + f"$1 IN ({values})", parameters), {equal}
        )
        self.assertEqual(
            builtins(STATEMENTS + f"NOT ($1 IN ({values}))", parameters), {equal}
        )
        # Every other catalog type, over a column of that type.
        identifiers = [str(UUID(int=n)) for n in range(1, 10)]
        self.assertEqual(
            builtins(
                STATEMENTS + "s.statement_id = ANY($1::uuid[])",
                one("statement_ref[]", identifiers),
                admitted_anchors=frozenset(("statement_ref", v) for v in identifiers),
            ),
            {"uuid_eq(uuid,uuid)", "uuid_hash(uuid)"},
        )
        self.check(
            RELATIONS,
            (
                (
                    "r.author_confidence = ANY($1::numeric[])",
                    one("numeric[]", ["0." + str(n) for n in range(1, 10)]),
                    {"numeric_eq(numeric,numeric)", "hash_numeric(numeric)"},
                ),
                (
                    "r.support_eligible = ANY($1::bool[])",
                    one("bool[]", [True] * 9),
                    {"booleq(boolean,boolean)", "hashbool(boolean)"},
                ),
            ),
        )
        self.assertEqual(
            builtins(
                "SELECT c.candidate_ref FROM evaluation_v1.candidates c "
                "WHERE c.score = ANY($1::float8[])",
                one("float8[]", [float(n).hex() for n in range(9)]),
                evaluation_admitted=True,
            ),
            {
                "float8eq(double precision,double precision)",
                "hashfloat8(double precision)",
            },
        )

    def test_implied_equality_is_part_of_the_inventory(self) -> None:
        statements = "memory_v1.statements s"
        cases: tuple[Case, ...] = (
            ("SELECT DISTINCT s.kind FROM " + statements, (), {"texteq(text,text)"}),
            (
                "SELECT s.kind, count(DISTINCT s.bead_id) AS n, min(s.recorded_at) AS t,"
                " max(s.statement_id) AS m FROM " + statements + " GROUP BY s.kind",
                (),
                {
                    "texteq(text,text)",
                    "uuid_eq(uuid,uuid)",
                    "min(timestamptz)",
                    "max(text)",
                },
            ),
            (
                "SELECT s.kind AS k FROM " + statements + " INTERSECT ALL "
                "SELECT s.kind AS k FROM " + statements,
                (),
                {"texteq(text,text)"},
            ),
            (
                "SELECT s.kind AS k FROM " + statements + " UNION ALL "
                "SELECT s.kind AS k FROM " + statements,
                (),
                set(),
            ),
            (
                "SELECT s.statement_id, rank() OVER (PARTITION BY s.bead_id "
                "ORDER BY s.recorded_at) AS r FROM " + statements,
                (),
                {"uuid_eq(uuid,uuid)", "timestamptz_eq(timestamptz,timestamptz)"},
            ),
            (
                "SELECT s.statement_id FROM " + statements + " JOIN "
                "memory_v1.statement_sources ss ON ss.statement_id = s.statement_id",
                (),
                {"uuid_eq(uuid,uuid)"},
            ),
            (
                "SELECT $1 AS f UNION SELECT $2 AS f",
                (
                    SqlParameter(1, "float8", (1.5).hex()),
                    SqlParameter(2, "float8", (2.5).hex()),
                ),
                {"float8eq(double precision,double precision)"},
            ),
        )
        self.check("", cases)

    def test_the_reviewed_closure_covers_exactly_these_operators(self) -> None:
        # The reader's closure today. Widening it is a reviewed change to the
        # reader's provisioning, made there and recorded here.
        reviewed = {
            (operator, name)
            for name in TYPES
            for operator in OPERATORS
            if comparison_builtin(operator, name) in REVIEWED_BUILTINS
        }
        self.assertEqual(
            reviewed,
            {(operator, name) for name in ("int8", "numeric") for operator in OPERATORS}
            | {("=", name) for name in ("text", "uuid", "bool", "timestamptz")},
        )
        for builtin in (
            "textlike(text,text)",
            "textnlike(text,text)",
            "texticlike(text,text)",
            "texticnlike(text,text)",
            "int8um(bigint)",
            "numeric_uminus(numeric)",
            "min(double precision)",
            "max(double precision)",
            *(hash_builtin(name) for name in TYPES),
        ):
            self.assertNotIn(builtin, REVIEWED_BUILTINS)
        for builtin in (
            "min(bigint)",
            "max(numeric)",
            "min(text)",
            "max(timestamptz)",
            "bool_and(boolean)",
            "bool_or(boolean)",
        ):
            self.assertIn(builtin, REVIEWED_BUILTINS)

    def test_every_public_column_type_has_reviewed_equality(self) -> None:
        # The witness lowering adds grouping, IS NOT DISTINCT FROM and window
        # keys over values a statement already selects, orders or aggregates,
        # which admission does not inventory. They call only equality, so every
        # type a public relation can produce must keep its equality reviewed.
        produced = {
            column.type.pg_type
            for name, relation in SqlCatalog.installed().relations.items()
            if name.startswith("memory_v1.")
            for column in relation.columns
        }
        self.assertEqual(
            produced, {"text", "uuid", "bool", "int8", "numeric", "timestamptz"}
        )
        for name in sorted(produced):
            self.assertIn(comparison_builtin("=", name), REVIEWED_BUILTINS, name)

    def test_an_unreviewed_operator_is_refused_at_its_leftmost_use(self) -> None:
        reviewed = frozenset(REVIEWED_BUILTINS)

        def refused(sql: str, parameters: tuple[SqlParameter, ...]) -> int | None:
            with self.assertRaises(SqlAdmissionError) as error:
                require_reviewed(admit_query(sql, parameters), reviewed)
            self.assertEqual(
                (error.exception.code, error.exception.construct),
                ("unsupported", "unreviewed_operator"),
            )
            return error.exception.position

        # Two unreviewed operators: the one written first, although the WHERE
        # clause is bound before the select list.
        sql = (
            "SELECT s.statement_id, CASE WHEN s.kind <> $1 THEN s.kind END AS other "
            "FROM memory_v1.statements s WHERE s.text LIKE $1"
        )
        self.assertEqual(refused(sql, TEXT), sql.index("s.kind <>"))
        # One builtin used twice is located at its leftmost use.
        sql = STATEMENTS + "s.text <> $1 OR s.kind <> $1"
        self.assertEqual(refused(sql, TEXT), sql.index("s.text <>"))
        # A parameter is located by its `$`, an output alias where GROUP BY
        # names it.
        sql = STATEMENTS + "$1 <> s.kind"
        self.assertEqual(refused(sql, TEXT), sql.index("$1"))
        sql = (
            "SELECT $1 AS shade, count(*) AS n FROM memory_v1.statements s "
            "GROUP BY shade"
        )
        self.assertEqual(
            refused(sql, one("float8", (0.5).hex())), sql.index("shade", 20)
        )
        require_reviewed(admit_query(STATEMENTS + "s.kind = $1", TEXT), reviewed)


if __name__ == "__main__":
    unittest.main()
