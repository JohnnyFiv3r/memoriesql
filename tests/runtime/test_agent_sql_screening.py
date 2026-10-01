"""What the public executor refuses from a statement and its bound values alone.

Fictional data through the production provisioning path. An identifier binds
only under its reference type, and only when the caller can see it. Admission's
inventory of the builtins a statement's operators call is held to the pinned
server's own catalog and privilege checks; an admitted operator then either runs
as the restricted reader or is refused as unsupported before any work. It never
fails inside the reader as a permission error. A reader missing any reviewed
grant answers every query `unavailable` until the release's grant helper
restores it. No workload or quality claim.
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import cycle, islice
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_admission import (
    AdmittedQuery,
    admit_query,
    comparison_builtin,
    hash_builtin,
    require_reviewed,
)
from memoriesql.application.agent_sql_catalog import (
    SqlAdmissionError,
    SqlCatalog,
    SqlColumn,
    SqlParameter,
    SqlRelation,
    SqlType,
)
from memoriesql.infrastructure.postgres.query_reader_provisioning import (
    REVIEWED_BUILTINS,
    grant_reviewed_closure,
)

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_results as results_tests
else:
    import test_agent_sql_results as results_tests

OPERATORS = ("=", "<>", "<", "<=", ">", ">=")
STATEMENTS = "SELECT s.statement_id FROM memory_v1.statements s WHERE "
RELATIONS = "SELECT r.relation_id FROM memory_v1.assessed_relations r WHERE "
UNREVIEWED = {"code": "feature", "feature": "unreviewed_operator"}
NAMES = {
    "text": "text",
    "uuid": "uuid",
    "bool": "boolean",
    "int8": "bigint",
    "numeric": "numeric",
    "float8": "double precision",
    "timestamptz": "timestamptz",
}

# One scratch table with a column of every catalog type, read by a role that
# may call no function at all until a test grants it one.
PROBE_PASSWORD = "fictional-probe-only-0001"
TYPED = "SELECT v.i FROM pr05_probe.typed v WHERE "
COLUMNS = {
    "text": "v.t",
    "uuid": "v.u",
    "bool": "v.b",
    "int8": "v.i",
    "numeric": "v.n",
    "float8": "v.f",
    "timestamptz": "v.ts",
}
PROBE_CATALOG = SqlCatalog(
    "0" * 64,
    {
        "pr05_probe.typed": SqlRelation(
            (
                SqlColumn("i", SqlType("int8")),
                SqlColumn("t", SqlType("text", nullable=True)),
                SqlColumn("t2", SqlType("text", nullable=True)),
                SqlColumn("u", SqlType("uuid", nullable=True)),
                SqlColumn("b", SqlType("bool", nullable=True)),
                SqlColumn("b2", SqlType("bool", nullable=True)),
                SqlColumn("n", SqlType("numeric", nullable=True)),
                SqlColumn("ts", SqlType("timestamptz", nullable=True)),
                SqlColumn("f", SqlType("float8", nullable=True)),
                SqlColumn("r", SqlType("uuid", "probe_ref", nullable=True)),
            ),
            (("i",),),
        )
    },
    {"probe_ref": "uuid"},
    "C",
)
# What admission's own lowering and the shapes below call besides operators:
# psycopg binds a small integer as smallint, which the emitted cast widens.
PROBE_BASELINE = (
    "int8(smallint)",
    "int8(integer)",
    "lower(text)",
    "count()",
    'count("any")',
    "rank()",
)


def bound(kind: str, value: Any, position: int = 1) -> list[dict[str, Any]]:
    return [{"position": position, "type": kind, "value": value}]


def members(kind: str, count: int) -> list[Any]:
    """`count` distinct bound values of one catalog type."""
    return [
        {
            "text": f"fictional-{n}",
            "uuid": str(UUID(int=n + 1)),
            "bool": n % 2 == 0,
            "int8": str(n),
            "numeric": f"{n}.5",
            "float8": float(n).hex(),
            "timestamptz": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=n))
            .isoformat()
            .replace("+00:00", "Z"),
        }[kind]
        for n in range(count)
    ]


def typed(parameters: list[dict[str, Any]]) -> tuple[SqlParameter, ...]:
    return tuple(SqlParameter(p["position"], p["type"], p["value"]) for p in parameters)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AgentSqlScreening(unittest.TestCase):
    def setUp(self) -> None:
        self.results = results_tests.AgentSqlResults(methodName="runTest")
        self.results.setUp()
        self.addCleanup(self.results.doCleanups)

    def begin(self) -> None:
        self.results.fixture.assertion()
        self.run_state = self.results.start()

    def ask(
        self, text: str, parameters: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        _, reply = self.results.query(
            self.run_state, text, parameters=parameters, page_size=50
        )
        return reply

    def work(self) -> tuple[int, int]:
        """Reader invocations and result preparations made so far."""
        return (
            self.results.invocations(),
            int(
                self.results.h.scalar(
                    "SELECT count(*) FROM memoriesql.result_preparation_operations"
                )
            ),
        )

    # -- identifiers -----------------------------------------------------------

    def test_identifier_parameters_bind_by_reference_type_and_visibility(
        self,
    ) -> None:
        self.begin()
        listed = self.ask(
            "SELECT s.statement_id FROM memory_v1.statements s ORDER BY s.statement_id"
        )
        self.assertEqual(listed["outcome"], "available", listed)
        statements = [row["values"][0] for row in listed["page"]["rows"]]
        self.assertTrue(statements)
        every = (
            "SELECT ss.statement_id, ss.source_unit_id "
            "FROM memory_v1.statement_sources ss "
            "ORDER BY ss.statement_id, ss.source_unit_id"
        )
        chosen = every.replace(
            " ORDER BY", " WHERE ss.statement_id = ANY($1::uuid[]) ORDER BY"
        )
        # A plain uuid[] names no identifier. The reply says which mismatch it
        # is and where, so the caller can rebind without guessing.
        before = self.work()
        plain = self.ask(chosen, bound("uuid[]", statements))
        self.assertEqual(
            (plain["outcome"], plain["error"]),
            (
                "invalid_request",
                {
                    "code": "type",
                    "feature": "reference_type",
                    "position": chosen.index("ss.statement_id = ANY"),
                },
            ),
        )
        self.assertEqual(self.work(), before)
        # The column's own reference type binds identifiers the caller can see.
        typed_reply = self.ask(chosen, bound("statement_ref[]", statements))
        self.assertEqual(typed_reply["outcome"], "available", typed_reply)
        unfiltered = self.ask(every)
        self.assertTrue(typed_reply["page"]["rows"])
        self.assertEqual(
            [row["values"] for row in typed_reply["page"]["rows"]],
            [row["values"] for row in unfiltered["page"]["rows"]],
        )
        one = self.ask(
            "SELECT s.statement_id FROM memory_v1.statements s "
            "WHERE s.statement_id = $1",
            bound("statement_ref", statements[0]),
        )
        self.assertEqual(
            [row["values"] for row in one["page"]["rows"]], [[statements[0]]]
        )
        # An identifier outside the caller's visible population gets the one
        # shared unavailable shape, whatever else is bound beside it.
        unseen = self.ask(chosen, bound("statement_ref[]", [*statements, str(uuid4())]))
        self.assertEqual(
            (unseen["outcome"], unseen["error"]),
            ("unavailable", {"code": "unavailable"}),
        )
        # The same rule over a relation identifier.
        relations = [
            row["values"][0]
            for row in self.ask(
                "SELECT r.relation_id FROM memory_v1.assessed_relations r"
            )["page"]["rows"]
        ]
        self.assertTrue(relations)
        evidence = (
            "SELECT e.relation_id, e.statement_id FROM memory_v1.relation_evidence e "
            "WHERE e.relation_id = ANY($1::uuid[])"
        )
        plain = self.ask(evidence, bound("uuid[]", relations))
        self.assertEqual(plain["outcome"], "invalid_request", plain)
        self.assertEqual(plain["error"]["feature"], "reference_type")
        typed_reply = self.ask(evidence, bound("relation_ref[]", relations))
        self.assertEqual(typed_reply["outcome"], "available", typed_reply)
        self.assertTrue(typed_reply["page"]["rows"])

    # -- operators -------------------------------------------------------------

    def test_operator_builtins_are_the_servers_own(self) -> None:
        # The pinned server's operator catalog, not this package's memory of it.
        db = self.results.db
        operator = (
            "SELECT o.oprcode::oid, n.oprcode::oid FROM pg_operator o "
            "LEFT JOIN pg_operator n ON n.oid=o.oprnegate "
            "WHERE o.oprnamespace='pg_catalog'::regnamespace AND o.oprname=%s "
            "AND o.oprleft=%s::regtype AND o.oprright=%s::regtype"
        )
        resolve = "SELECT to_regprocedure('pg_catalog.'||%s)::oid"
        negators = {"=": "<>", "<>": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}
        for kind, name in NAMES.items():
            for symbol in OPERATORS:
                with self.subTest(kind=kind, operator=symbol):
                    row = db.execute(operator, (symbol, name, name)).fetchone()
                    assert row is not None
                    expected = (
                        db.execute(
                            resolve, (comparison_builtin(symbol, kind),)
                        ).fetchone(),
                        db.execute(
                            resolve, (comparison_builtin(negators[symbol], kind),)
                        ).fetchone(),
                    )
                    self.assertEqual(((row[0],), (row[1],)), expected)
        for symbol, builtin, negated in (
            ("~~", "textlike(text,text)", "textnlike(text,text)"),
            ("~~*", "texticlike(text,text)", "texticnlike(text,text)"),
        ):
            row = db.execute(operator, (symbol, "text", "text")).fetchone()
            assert row is not None
            self.assertEqual(
                ((row[0],), (row[1],)),
                (
                    db.execute(resolve, (builtin,)).fetchone(),
                    db.execute(resolve, (negated,)).fetchone(),
                ),
            )
        for name, builtin in (
            ("bigint", "int8um(bigint)"),
            ("numeric", "numeric_uminus(numeric)"),
        ):
            self.assertEqual(
                db.execute(
                    "SELECT oprcode::oid FROM pg_operator WHERE oprname='-' "
                    "AND oprnamespace='pg_catalog'::regnamespace "
                    "AND oprleft=0 AND oprright=%s::regtype",
                    (name,),
                ).fetchone(),
                db.execute(resolve, (builtin,)).fetchone(),
            )
        # A hashed membership probe calls the type's default hash function.
        for kind, name in NAMES.items():
            with self.subTest(hashed=kind):
                self.assertEqual(
                    db.execute(
                        "SELECT p.amproc::oid FROM pg_opclass c "
                        "JOIN pg_am a ON a.oid=c.opcmethod "
                        "JOIN pg_amproc p ON p.amprocfamily=c.opcfamily "
                        "AND p.amproclefttype=c.opcintype "
                        "AND p.amprocrighttype=c.opcintype "
                        "WHERE a.amname='hash' AND c.opcdefault AND p.amprocnum=1 "
                        "AND c.opcintype=%s::regtype",
                        (name,),
                    ).fetchone(),
                    db.execute(resolve, (hash_builtin(kind),)).fetchone(),
                )
        # Every reviewed builtin exists on this server.
        for builtin in REVIEWED_BUILTINS:
            self.assertNotEqual(
                db.execute(resolve, (builtin,)).fetchone(), (None,), builtin
            )

    def probe(self) -> None:
        """A scratch table and a role that reads it and may call no function."""
        db = self.results.db
        db.execute(
            "CREATE SCHEMA pr05_probe; "
            "CREATE TABLE pr05_probe.typed (i bigint NOT NULL, "
            't text COLLATE "C", t2 text COLLATE "C", u uuid, b boolean, b2 boolean, '
            "n numeric, ts timestamptz, f double precision, r uuid); "
            "INSERT INTO pr05_probe.typed VALUES "
            "(1,'fictional','fictional','00000000-0000-0000-0000-000000000001',"
            "true,false,0.5,'2026-01-01T00:00:00Z',1.5,"
            "'00000000-0000-0000-0000-000000000011'),"
            "(2,'other','fictional','00000000-0000-0000-0000-000000000002',"
            "false,false,1.5,'2026-01-02T00:00:00Z',2.5,"
            "'00000000-0000-0000-0000-000000000011'),"
            "(3,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)"
        )
        self.role = "pr05_probe_" + uuid4().hex
        role = sql.Identifier(self.role)
        db.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT "
                "NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(role, sql.Literal(PROBE_PASSWORD))
        )
        self.addCleanup(db.execute, sql.SQL("DROP ROLE {}").format(role))
        self.addCleanup(db.execute, sql.SQL("DROP OWNED BY {}").format(role))
        db.execute(
            sql.SQL(
                "GRANT USAGE ON SCHEMA pr05_probe TO {}; "
                "GRANT SELECT ON pr05_probe.typed TO {}"
            ).format(role, role)
        )
        self.privilege("GRANT", PROBE_BASELINE)
        self.reader = psycopg.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"],
                dbname=db.info.dbname,
                user=self.role,
                password=PROBE_PASSWORD,
            ),
            autocommit=True,
        )
        self.addCleanup(self.reader.close)

    def privilege(self, action: str, builtins: Any) -> None:
        for builtin in builtins:
            self.results.db.execute(
                sql.SQL(
                    action
                    + " EXECUTE ON FUNCTION pg_catalog."
                    + builtin
                    + (" TO {}" if action == "GRANT" else " FROM {}")
                ).format(sql.Identifier(self.role))
            )

    def denied(self, query: AdmittedQuery) -> bool:
        """Whether PostgreSQL refuses the probe role a function `query` calls.

        The same two calls the restricted executor makes as the reader: a plan
        with its parameters bound, then a server-side cursor.
        """
        reader = self.reader
        reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            reader.execute("SET LOCAL search_path=pg_catalog")
            reader.execute("EXPLAIN (COSTS FALSE) " + query.sql, query.parameters)
            with reader.cursor(name="probe_" + uuid4().hex) as cursor:
                cursor.execute(query.sql, query.parameters)
                cursor.fetchall()
        except psycopg.errors.InsufficientPrivilege:
            return True
        finally:
            reader.rollback()
        return False

    def holds(
        self,
        text: str,
        parameters: list[dict[str, Any]] | None = None,
        *,
        exact: bool = True,
        anchors: frozenset[tuple[str, str]] = frozenset(),
    ) -> set[str]:
        """Hold the inventory of `text` to PostgreSQL's own privilege checks.

        Granting exactly the inventoried builtins lets the statement plan and
        run. When `exact`, revoking any one of them makes PostgreSQL refuse it.
        """
        query = admit_query(
            text,
            typed(parameters or []),
            admitted_anchors=anchors,
            catalog=PROBE_CATALOG,
        )
        needed = sorted({builtin for builtin, _ in query.operators})
        with self.subTest(sql=text, parameters=parameters):
            self.privilege("GRANT", needed)
            try:
                self.assertFalse(self.denied(query), ("not sufficient", needed))
                for builtin in needed if exact else ():
                    self.privilege("REVOKE", [builtin])
                    self.assertTrue(self.denied(query), ("not necessary", builtin))
                    self.privilege("GRANT", [builtin])
            finally:
                self.privilege("REVOKE", needed)
        return set(needed)

    def test_the_inventory_is_what_postgresql_checks(self) -> None:
        self.probe()
        # With no operator builtin granted, no operator runs at all.
        bare = admit_query(
            TYPED + "v.i = $1", typed(bound("int8", "1")), catalog=PROBE_CATALOG
        )
        self.assertTrue(self.denied(bare))
        value = {kind: members(kind, 1)[0] for kind in NAMES}
        # Every comparison over every catalog type, and under NOT, where the
        # planner calls the negator instead.
        for kind in NAMES:
            for symbol in OPERATORS:
                form = COLUMNS[kind] + " " + symbol + " "
                parameters = None if kind == "bool" else bound(kind, value[kind])
                # `b = $1` is simplified away, so booleans compare two columns.
                form += "v.b2" if kind == "bool" else "$1"
                self.assertEqual(
                    self.holds(TYPED + form, parameters),
                    {comparison_builtin(symbol, kind)},
                )
                self.holds(TYPED + "NOT (" + form + ")", parameters)
        text, other = bound("text", "fictional"), bound("text", "other", 2)
        number = bound("int8", "1")
        both = [*number, *other]
        for predicate, parameters in (
            ("NOT (NOT (v.t = $1))", text),
            ("NOT (v.i < $1 AND v.t = $2)", both),
            ("NOT (v.i < $1 OR NOT (v.t = $2))", both),
            ("CASE WHEN v.i = $1 THEN NOT (v.t = $2) ELSE v.t = $2 END", both),
            ("NOT (CASE WHEN v.i = $1 THEN v.t = $2 END)", both),
            ("v.t LIKE $1", text),
            ("NOT (v.t LIKE $1)", text),
            ("v.t ILIKE $1", text),
            ("NOT (v.t ILIKE $1)", text),
            # `NOT LIKE` is an operator of its own, negated back into LIKE.
            ("v.t NOT LIKE $1", text),
            ("NOT (v.t NOT LIKE $1)", text),
            ("v.t NOT ILIKE $1", text),
            ("NOT (v.t NOT ILIKE $1)", text),
            ("lower(v.t) LIKE $1", text),
            ("-v.i < $1", number),
            ("v.i < -$1", number),
            ("-v.n < $1", bound("numeric", "0.5")),
            ("nullif(v.t, $1) IS NULL", text),
            ("v.t IN ($1)", text),
            ("v.t NOT IN ($1)", text),
        ):
            self.holds(TYPED + predicate, parameters)
        # Bound values fold before operators resolve.
        absent, yes, no = bound("text", None), bound("bool", True), bound("bool", False)
        pair = [*text, *other]
        for predicate, parameters, expected in (
            ("v.t <> $1", absent, set()),
            ("NOT (v.t = $1)", absent, set()),
            ("v.t <> NULL", None, set()),
            ("v.t IN ($1)", absent, set()),
            ("v.t LIKE $1", absent, set()),
            ("nullif(v.t, $1) IS NULL", absent, set()),
            ("$1 IS NULL OR v.t <> $1", absent, set()),
            ("$1 IS NULL OR v.t <> $1", text, {"textne(text,text)"}),
            ("$1 = $2", pair, {"texteq(text,text)"}),
            ("NOT ($1 = $2)", pair, {"texteq(text,text)"}),
            ("$1 <> $2", pair, {"textne(text,text)"}),
            ("NOT ($1 LIKE $2)", pair, {"textlike(text,text)"}),
            ("$1 NOT LIKE $2", pair, {"textnlike(text,text)"}),
            ("NOT ($1 NOT LIKE $2)", pair, {"textnlike(text,text)"}),
            ("v.t NOT LIKE $1", absent, set()),
            ("v.t NOT LIKE $1", text, {"textnlike(text,text)"}),
            ("NOT (v.t NOT ILIKE $1)", text, {"texticlike(text,text)"}),
            ("v.b = $1", yes, set()),
            ("v.b <> $1", yes, set()),
            ("v.b = $1", no, set()),
            ("NOT (v.b = $1)", no, set()),
            ("$1 <> v.b", no, set()),
            ("v.b = $1", bound("bool", None), set()),
            ("(v.t = v.t2) = $1", yes, {"texteq(text,text)"}),
            ("(v.t = v.t2) = $1", no, {"textne(text,text)"}),
            ("(v.t = v.t2) <> $1", yes, {"textne(text,text)"}),
            ("NOT ((v.t = v.t2) = $1)", no, {"texteq(text,text)"}),
            ("(v.i < v.i) = $1", no, {"int8ge(bigint,bigint)"}),
            ("v.b < $1", yes, {"boollt(boolean,boolean)"}),
        ):
            self.assertEqual(self.holds(TYPED + predicate, parameters), expected)
        # Membership: equality up to eight bound values, a hash probe from nine
        # up to the packet's 64. Revoking the type's hash function, or its
        # equality, then makes PostgreSQL refuse the statement.
        for kind in NAMES:
            array = COLUMNS[kind] + " = ANY($1::" + kind + "[])"
            equal = comparison_builtin("=", kind)
            hashed = {equal, hash_builtin(kind)}
            for predicate, count, expected in (
                (array, 8, {equal}),
                (array, 9, hashed),
                (array, 16, hashed),
                (array, 64, hashed),
                ("NOT (" + array + ")", 8, {comparison_builtin("<>", kind)}),
                ("NOT (" + array + ")", 9, hashed),
                ("NOT (" + array + ")", 64, hashed),
            ):
                parameters = bound(kind + "[]", members(kind, count))
                self.assertEqual(self.holds(TYPED + predicate, parameters), expected)
        for count, negated, expected in (
            (2, False, {"int8eq(bigint,bigint)"}),
            (8, False, {"int8eq(bigint,bigint)"}),
            (9, False, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
            (16, False, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
            (64, False, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
            (2, True, {"int8ne(bigint,bigint)"}),
            (9, True, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
            (64, True, {"int8eq(bigint,bigint)", "hashint8(bigint)"}),
        ):
            parameters = [
                parameter
                for position, member in enumerate(members("int8", count), 1)
                for parameter in bound("int8", member, position)
            ]
            listed = ", ".join(f"${p['position']}" for p in parameters)
            predicate = "v.i " + ("NOT " if negated else "") + "IN (" + listed + ")"
            self.assertEqual(self.holds(TYPED + predicate, parameters), expected)
        # A bound left side is compared while planning: equality alone.
        nine = [*text, *bound("text[]", members("text", 9), 2)]
        for predicate in ("$1 = ANY($2::text[])", "NOT ($1 = ANY($2::text[]))"):
            self.assertEqual(self.holds(TYPED + predicate, nine), {"texteq(text,text)"})
        # Nothing to compare with: equality is recorded, not proved necessary.
        self.holds(TYPED + "v.t = ANY($1::text[])", bound("text[]", []), exact=False)
        # Implied equality. PostgreSQL may choose a plan that calls less, so
        # these are shown sufficient, not necessary.
        table = "pr05_probe.typed v"
        extremes = (
            "SELECT min(v.t) AS a, max(v.u) AS b, min(v.b) AS c, max(v.n) AS d,"
            " min(v.ts) AS e FROM " + table
        )
        self.assertEqual(
            self.holds(extremes),
            {
                "min(text)",
                "max(text)",
                "bool_and(boolean)",
                "max(numeric)",
                "min(timestamptz)",
            },
        )
        for statement in (
            "SELECT DISTINCT v.t FROM " + table,
            "SELECT v.t, count(*) AS n FROM " + table + " GROUP BY v.t",
            "SELECT count(DISTINCT v.u) AS n FROM " + table,
            "SELECT v.i, rank() OVER (PARTITION BY v.t ORDER BY v.ts) AS r FROM "
            + table,
            "SELECT v.t AS k FROM " + table + " INTERSECT "
            "SELECT w.t2 AS k FROM pr05_probe.typed w",
            "SELECT v.n AS k FROM " + table + " UNION "
            "SELECT w.n AS k FROM pr05_probe.typed w",
            "SELECT v.i FROM " + table + " JOIN pr05_probe.typed w ON w.r = v.r",
        ):
            self.holds(statement, exact=False)

    def test_admitted_operators_run_or_are_unsupported_never_unavailable(
        self,
    ) -> None:
        self.begin()
        statements = [
            row["values"][0]
            for row in self.ask("SELECT s.statement_id FROM memory_v1.statements s")[
                "page"
            ]["rows"]
        ]
        self.assertTrue(statements)
        moment = bound("timestamptz", self.run_state["default_known_at"])
        text, number = bound("text", "fictional"), bound("int8", "1")
        every = " ".join(OPERATORS)
        # Each operand with the comparisons the reader's closure runs today.
        operands: tuple[tuple[str, str, list[dict[str, Any]], str, bool], ...] = (
            (STATEMENTS, "s.kind {} $1", text, "=", False),
            (STATEMENTS, "s.statement_id {} s.statement_id", [], "=", False),
            (STATEMENTS, "s.sequence {} $1", number, every, True),
            (STATEMENTS, "s.recorded_at {} $1", moment, "=", False),
            # `flag = $1` and `flag <> $1` call no builtin.
            (RELATIONS, "r.support_eligible {} $1", bound("bool", True), "= <>", True),
            (
                RELATIONS,
                "r.author_confidence {} $1",
                bound("numeric", "0.5"),
                every,
                True,
            ),
        )
        shapes: list[tuple[str, list[dict[str, Any]], bool]] = [
            (head + form.format(symbol), parameters, symbol in runs.split())
            for head, form, parameters, runs, _ in operands
            for symbol in OPERATORS
        ]
        # The planner rewrites NOT over a comparison into its negator.
        shapes += [
            (head + "NOT (" + form.format("=") + ")", parameters, negated)
            for head, form, parameters, _, negated in operands
        ]
        pattern = bound("text", "%fictional%")
        kinds = members("text", 9)
        sequence = [
            parameter
            for position, member in enumerate(members("int8", 64), 1)
            for parameter in bound("int8", member, position)
        ]

        def listed(count: int) -> str:
            return ", ".join(f"${p['position']}" for p in sequence[:count])

        shapes += [
            (STATEMENTS + "s.text LIKE $1", pattern, False),
            (STATEMENTS + "s.text ILIKE $1", pattern, False),
            (STATEMENTS + "NOT (s.text LIKE $1)", pattern, False),
            (STATEMENTS + "s.text NOT LIKE $1", pattern, False),
            (STATEMENTS + "s.text NOT ILIKE $1", pattern, False),
            (STATEMENTS + "s.kind IN ($1)", text, True),
            (STATEMENTS + "s.kind NOT IN ($1)", text, False),
            (STATEMENTS + "-s.sequence < $1", number, False),
            (RELATIONS + "-r.author_confidence < $1", bound("numeric", "0.5"), False),
            (
                "SELECT min(s.recorded_at) AS earliest, max(s.statement_id) AS latest,"
                " min(s.kind) AS lowest FROM memory_v1.statements s",
                [],
                True,
            ),
            (
                "SELECT min(r.support_eligible) AS agreed,"
                " max(r.author_confidence) AS highest"
                " FROM memory_v1.assessed_relations r",
                [],
                True,
            ),
            # A NULL filter value switches the filter off; nothing is called.
            (STATEMENTS + "$1 IS NULL OR s.kind <> $1", bound("text", None), True),
            (STATEMENTS + "$1 IS NULL OR s.kind <> $1", text, False),
            (STATEMENTS + "(s.kind = s.text) = $1", bound("bool", True), True),
            (STATEMENTS + "(s.kind = s.text) = $1", bound("bool", False), False),
            # Up to eight bound values compare; from nine they are probed through
            # a hash table. Under NOT, up to eight need the refused `<>`.
            (STATEMENTS + "s.kind = ANY($1::text[])", bound("text[]", kinds[:8]), True),
            (
                STATEMENTS + "NOT (s.kind = ANY($1::text[]))",
                bound("text[]", kinds[:8]),
                False,
            ),
            (
                STATEMENTS + "NOT (s.kind = ANY($1::text[]))",
                bound("text[]", kinds),
                True,
            ),
            (STATEMENTS + f"s.sequence IN ({listed(8)})", sequence[:8], True),
            (STATEMENTS + "s.sequence NOT IN ($1, $2)", sequence[:2], True),
        ]
        # Nine, 16 and 64 bound values of every type a public relation has run.
        arrays: tuple[tuple[str, str, str, str, Callable[[int], list[Any]]], ...] = (
            (STATEMENTS, "s.kind", "text", "text", lambda n: members("text", n)),
            (
                STATEMENTS,
                "s.statement_id",
                "uuid",
                "statement_ref",
                lambda n: list(islice(cycle(statements), n)),
            ),
            (
                RELATIONS,
                "r.support_eligible",
                "bool",
                "bool",
                lambda n: members("bool", n),
            ),
            (STATEMENTS, "s.sequence", "bigint", "int8", lambda n: members("int8", n)),
            (
                RELATIONS,
                "r.author_confidence",
                "numeric",
                "numeric",
                lambda n: members("numeric", n),
            ),
            (
                STATEMENTS,
                "s.recorded_at",
                "timestamptz",
                "timestamptz",
                lambda n: members("timestamptz", n),
            ),
        )
        shapes += [
            (
                head + column + " = ANY($1::" + cast_type + "[])",
                bound(kind + "[]", values(count)),
                True,
            )
            for head, column, cast_type, kind, values in arrays
            for count in (9, 16, 64)
        ]
        shapes += [
            (STATEMENTS + f"s.sequence IN ({listed(count)})", sequence[:count], True)
            for count in (9, 16, 64)
        ]
        reviewed = frozenset(REVIEWED_BUILTINS)
        for text_sql, parameters, runs in shapes:
            with self.subTest(sql=text_sql, parameters=parameters):
                # The closure decides the expectation written above. Identifiers
                # are the caller's own, visible ones.
                anchors = frozenset(
                    (p["type"].removesuffix("[]"), value)
                    for p in parameters
                    for value in (
                        p["value"] if isinstance(p["value"], list) else [p["value"]]
                    )
                    if isinstance(value, str)
                )
                try:
                    require_reviewed(
                        admit_query(
                            text_sql, typed(parameters), admitted_anchors=anchors
                        ),
                        reviewed,
                    )
                    position = None
                except SqlAdmissionError as refused:
                    self.assertEqual(refused.construct, "unreviewed_operator")
                    position = refused.position
                    self.assertIsNotNone(position)
                self.assertEqual(position is None, runs)
                before = self.work()
                reply = self.ask(text_sql, parameters)
                if runs:
                    self.assertEqual(reply["outcome"], "available", reply)
                else:
                    self.assertEqual(
                        (reply["outcome"], reply["error"]),
                        ("unsupported_query", UNREVIEWED | {"position": position}),
                    )
                    # Screened from the request alone: nothing reserved or sent.
                    self.assertEqual(self.work(), before)
        self.assertGreater(sum(runs for _, _, runs in shapes), 20)
        self.assertGreater(sum(not runs for _, _, runs in shapes), 20)

    def test_a_bound_identifier_does_not_postpone_an_operator_refusal(self) -> None:
        self.begin()
        listed = self.ask(
            "SELECT s.statement_id FROM memory_v1.statements s ORDER BY s.statement_id"
        )
        visible = listed["page"]["rows"][0]["values"][0]
        predicate = "s.statement_id = $1 AND s.kind <> $2"
        expected = (
            "unsupported_query",
            UNREVIEWED | {"position": len(STATEMENTS) + predicate.index("s.kind")},
        )
        # Seen or not, the identifier changes nothing about an operator refusal,
        # and nothing is reserved or prepared for it.
        for identifier in (visible, str(uuid4())):
            before = self.work()
            reply = self.ask(
                STATEMENTS + predicate,
                [
                    *bound("statement_ref", identifier),
                    *bound("text", "fictional", 2),
                ],
            )
            self.assertEqual((reply["outcome"], reply["error"]), expected)
            self.assertEqual(self.work(), before)
        array = "s.statement_id = ANY($1::uuid[])"
        unseen = [str(uuid4()) for _ in range(9)]
        before = self.work()
        eight = self.ask(
            STATEMENTS + "NOT (" + array + ")", bound("statement_ref[]", unseen[:8])
        )
        self.assertEqual(
            (eight["outcome"], eight["error"]),
            (
                "unsupported_query",
                UNREVIEWED | {"position": len(STATEMENTS) + len("NOT (")},
            ),
        )
        self.assertEqual(self.work(), before)
        # A supported statement is still held to what its caller can see.
        nine = self.ask(STATEMENTS + array, bound("statement_ref[]", unseen))
        self.assertEqual(
            (nine["outcome"], nine["error"]),
            ("unavailable", {"code": "unavailable"}),
        )
        seen = self.ask(STATEMENTS + array, bound("statement_ref[]", [visible] * 9))
        self.assertEqual([row["values"] for row in seen["page"]["rows"]], [[visible]])

    def test_a_reader_missing_a_reviewed_grant_answers_nothing_until_granted(
        self,
    ) -> None:
        # The rollout rule: a provisioned reader must hold exactly the reviewed
        # closure. Without one of the six hash functions every query replies
        # unavailable, not only lists, until the release's grant helper runs.
        self.begin()
        query = STATEMENTS + "s.kind = $1"
        text = bound("text", "fictional")
        self.assertEqual(self.ask(query, text)["outcome"], "available")
        db, reader = self.results.db, self.results.reader
        for signature in ("uuid_hash(uuid)", "hashbool(boolean)"):
            db.execute(
                sql.SQL(
                    "REVOKE EXECUTE ON FUNCTION pg_catalog." + signature + " FROM {}"
                ).format(sql.Identifier(reader))
            )
            refused = self.ask(query, text)
            self.assertEqual(
                (refused["outcome"], refused["error"]),
                ("unavailable", {"code": "unavailable"}),
                signature,
            )
            grant_reviewed_closure(db, reader)
            grant_reviewed_closure(db, reader)
            self.assertEqual(self.ask(query, text)["outcome"], "available", signature)


if __name__ == "__main__":
    unittest.main()
