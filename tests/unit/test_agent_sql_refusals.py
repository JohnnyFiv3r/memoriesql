"""Database-free checks of how the executor reports a refusal; no execution claim.

The packet reports a timeout or a storage or work limit as `budget_exhausted`.
A call that wrote nothing and failed for any other reason keeps the one
`unavailable` shape that missing and denied authority share. A statement the
executor refuses from its SQL and bound values alone says what kind of refusal
it is and, for a value expression, where.
"""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import psycopg
from psycopg import errors

from memoriesql.application.agent_sql_admission import admit_query
from memoriesql.application.agent_sql_catalog import (
    SqlAdmissionError,
    SqlCatalog,
    SqlParameter,
)
from memoriesql.application.investigation_contracts import (
    QueryRequest,
    ReuseRequest,
    parse_investigation_request,
)
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QueryAuthorityProfile,
)
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
    _admission_error,
    _Failure,
    _state_code,
)
from memoriesql.infrastructure.postgres.relation_sql_population import (
    RelationPopulationError,
)

# A busy database: the caller may simply try again.
BUSY = {
    "57014": "time",
    "55P03": "time",
    "53400": "storage",
    "54000": "storage",
    "40001": "settlement",
    "55000": "settlement",
}
# Denied or lost authority, a duplicate, internal faults and a lost connection.
OTHERS = ("42501", "28000", "P0002", "23505", "XX000", None)
UNAVAILABLE = ("unavailable", {"code": "unavailable"})
FRAME = "memoriesql.infrastructure.postgres.agent_sql_results.relation_projection_frame"
POPULATION = (
    "memoriesql.infrastructure.postgres.agent_sql_results.prepare_query_population"
)
STATEMENTS = "SELECT s.statement_id FROM memory_v1.statements s WHERE "
UNREVIEWED = ("unsupported_query", "feature", "unreviewed_operator")
PENDING = ("settlement_pending", {"code": "settlement"})
REMAINING = {
    "accesses": 128,
    "db_ms": 300000,
    "transport_bytes": 16777216,
    "allocation_bytes": 134217728,
    "run_expires_at": "2026-01-01T00:30:00.000000Z",
}


def failure(sqlstate: str | None) -> BaseException:
    if sqlstate is None:
        return psycopg.OperationalError("fictional lost connection")
    return errors.lookup(sqlstate)("fictional refusal")


class Named(errors.ProgramLimitExceeded):
    """SQLSTATE 54000 under the name the database raises it with."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    @property
    def diag(self) -> Any:
        return SimpleNamespace(message_primary=self.name)


def admitted(*, replay: bool = False) -> dict[str, Any]:
    """An admission as M0038 returns it: one reserved, owned delivery."""
    delivery = str(uuid4())
    return {
        "delivery_ref": delivery,
        "delivery_number": 1,
        "receipt_ref": delivery,
        "replay": replay,
        "step": {"state": "executing"},
        "reserved_db_ms": 30000,
        "reserved_transport": 262144,
        "deadline": "2026-01-01T00:00:30.000000Z",
        "remaining": REMAINING,
        "owner_lock_key": 1,
    }


def failing_at(*statements: str) -> MagicMock:
    """Control connections whose named statements fail as a lost connection."""

    def execute(query: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(query)
        if any(statement in text for statement in statements):
            raise failure(None)
        cursor = MagicMock()
        cursor.fetchone.return_value = (REMAINING if "remaining" in text else None,)
        return cursor

    def connect() -> MagicMock:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.side_effect = execute
        return connection

    return MagicMock(side_effect=connect)


def executor(control: Any) -> PostgresAgentSqlResults:
    return PostgresAgentSqlResults(
        control_factory=control,
        reader_factory=control,
        authority_profile=cast(QueryAuthorityProfile, None),
        credential_sha256="0" * 64,
        workspace_id=UUID(int=1),
    )


def reuse_request(cursor: str | None = None) -> bytes:
    request: dict[str, Any] = {
        "contract_version": 1,
        "run_ref": str(uuid4()),
        "step_key": str(uuid4()),
        "kind": "reuse_result",
        "result": {"result_id": str(uuid4()), "content_digest": "0" * 64},
        "access": {"kind": "standalone"},
        "page_size": 2,
    }
    if cursor is not None:
        request["cursor"] = cursor
    return json.dumps(request).encode()


def query_request(sql: str, parameters: list[dict[str, Any]]) -> QueryRequest:
    parsed = parse_investigation_request(
        json.dumps(
            {
                "contract_version": 1,
                "run_ref": str(uuid4()),
                "step_key": str(uuid4()),
                "kind": "query",
                "catalog_hash": SqlCatalog.installed().hash,
                "sql": sql,
                "parameters": parameters,
                "inputs": [],
                "parents": [],
                "scope": {
                    "source_refs": [],
                    "known_at": "2026-01-01T00:00:00Z",
                    "view": "historical",
                },
                "intent": "enumerate",
                "max_result_bytes": 8192,
                "page_size": 2,
            }
        ).encode()
    )
    assert isinstance(parsed, QueryRequest)
    return parsed


def screen(sql: str, parameters: list[dict[str, Any]]) -> tuple[Any, ...] | None:
    """The executor's refusal of `sql` before any connection or reservation."""
    unused = MagicMock(side_effect=AssertionError("screening opens no connection"))
    try:
        executor(unused)._screen(query_request(sql, parameters))
    except Exception as refused:
        return tuple(
            getattr(refused, name)
            for name in ("outcome", "code", "feature", "position")
        )
    return None


class AgentSqlRefusals(unittest.TestCase):
    def replies(self, error: BaseException) -> dict[str, dict[str, Any]]:
        """Every unowned control call's reply when its connection fails."""
        service = executor(MagicMock(side_effect=error))
        admission = json.loads(service.handle(reuse_request()))
        # A refused admission owns nothing: no receipt and no charge.
        for field in ("receipt_ref", "access_receipt_ref", "remaining"):
            self.assertIsNone(admission[field], field)
        return {
            "start_run": json.loads(service.start_run()),
            "close_run": json.loads(service.close_run(str(uuid4()))),
            "cleanup_status": json.loads(service.cleanup_status()),
            "admission": admission,
        }

    def test_a_busy_database_is_a_budget_refusal_at_every_unowned_call(self) -> None:
        for sqlstate, code in BUSY.items():
            for call, reply in self.replies(failure(sqlstate)).items():
                with self.subTest(sqlstate=sqlstate, call=call):
                    self.assertEqual(
                        (reply["outcome"], reply["error"]),
                        ("budget_exhausted", {"code": code}),
                    )

    def test_every_other_failure_keeps_the_shared_unavailable_shape(self) -> None:
        refusals = [failure(sqlstate) for sqlstate in OTHERS]
        refusals.append(PermissionError("fictional missing context"))
        for error in refusals:
            for call, reply in self.replies(error).items():
                with self.subTest(error=type(error).__name__, call=call):
                    self.assertEqual((reply["outcome"], reply["error"]), UNAVAILABLE)

    def test_operator_cleanup_reports_a_busy_database_with_its_progress(self) -> None:
        for sqlstate, expected in (
            *[
                (state, ("budget_exhausted", {"code": code}))
                for state, code in BUSY.items()
            ],
            *[(state, ("execution_error", {"code": "database"})) for state in OTHERS],
        ):
            with self.subTest(sqlstate=sqlstate):
                service = executor(MagicMock(side_effect=failure(sqlstate)))
                reply = json.loads(service.cleanup_expired())
                self.assertEqual((reply["outcome"], reply["error"]), expected)
                self.assertEqual(
                    reply["cleanup"],
                    {
                        "purged": {"runs": 0, "results": 0, "tombstones": 0},
                        "pending": 0,
                        "failed": 0,
                        "batches": 0,
                    },
                )

    def cursor_failure(self, error: BaseException) -> tuple[str, str | None]:
        """What resolving a reuse cursor fails with when its frame raises."""
        service = executor(MagicMock())
        request = parse_investigation_request(reuse_request(cursor=str(uuid4())))
        assert isinstance(request, ReuseRequest)
        start = time.monotonic()
        with patch(FRAME, side_effect=error):
            try:
                service._reuse(request, {}, start, start + 30)
            except Exception as refused:
                return getattr(refused, "outcome", ""), getattr(refused, "code", None)
        self.fail("cursor resolution did not refuse")

    def test_cursor_resolution_classifies_failures_as_disclosure_does(self) -> None:
        for sqlstate, expected in (
            ("55P03", ("budget_exhausted", "time")),
            ("57014", ("budget_exhausted", "time")),
            ("54000", ("budget_exhausted", "storage")),
            ("42501", ("unavailable", "unavailable")),
            ("28000", ("unavailable", "unavailable")),
            ("XX000", ("execution_error", "database")),
            (None, ("execution_error", "database")),
        ):
            with self.subTest(sqlstate=sqlstate):
                self.assertEqual(self.cursor_failure(failure(sqlstate)), expected)
        self.assertEqual(
            self.cursor_failure(PermissionError("fictional missing context")),
            ("unavailable", "unavailable"),
        )

    def test_sqlstate_54000_says_which_limit_ran_out(self) -> None:
        # The database raises 54000 for a passed deadline and for byte limits;
        # only the deadline is `time`.
        for name, code in (
            ("query_delivery_work_exhausted", "time"),
            ("query_result_work_exhausted", "time"),
            ("preparation_storage_exhausted", "storage"),
            ("population_budget_exhausted", "storage"),
            ("query_stage_budget_exhausted", "storage"),
        ):
            with self.subTest(name=name):
                self.assertEqual(_state_code(Named(name)), code)
                self.assertEqual(
                    self.cursor_failure(Named(name)), ("budget_exhausted", code)
                )
                reply = self.replies(Named(name))["start_run"]
                self.assertEqual(
                    (reply["outcome"], reply["error"]),
                    ("budget_exhausted", {"code": code}),
                )

    def test_a_population_says_which_budget_ran_out(self) -> None:
        request = query_request(STATEMENTS + "s.kind = $1", [])
        for limit in ("time", "storage"):
            start = time.monotonic()
            refusal = RelationPopulationError("budget_exhausted", limit=limit)
            with self.subTest(limit=limit), patch(POPULATION, side_effect=refusal):
                with self.assertRaises(_Failure) as refused:
                    executor(MagicMock())._execute(
                        request, MagicMock(), MagicMock(), {}, start, start + 30
                    )
                self.assertEqual(
                    (refused.exception.outcome, refused.exception.code),
                    ("budget_exhausted", limit),
                )

    def test_a_failure_before_any_commit_is_known_and_owns_nothing(self) -> None:
        # Nothing of the step can commit, so the caller discards its
        # reservation instead of holding it until the run expires. A busy
        # database stays a budget refusal.
        request = query_request(STATEMENTS + "s.kind = $1", [])
        for error, outcome, code in (
            (RuntimeError("fictional fault"), "execution_error", "database"),
            (failure(None), "execution_error", "database"),
            (failure("XX000"), "execution_error", "database"),
            (failure("40001"), "budget_exhausted", "settlement"),
            (failure("55000"), "budget_exhausted", "settlement"),
            (failure("53400"), "budget_exhausted", "storage"),
        ):
            start = time.monotonic()
            with (
                self.subTest(error=repr(error)),
                patch(POPULATION, side_effect=error),
                self.assertRaises(_Failure) as refused,
            ):
                executor(MagicMock())._execute(
                    request, MagicMock(), MagicMock(), {}, start, start + 30
                )
            failed = refused.exception
            self.assertEqual(
                (failed.outcome, failed.code, failed.pending, failed.committed),
                (outcome, code, False, False),
            )


class AgentSqlUnknownSettlement(unittest.TestCase):
    """`handle()` answers every admitted request; it never raises."""

    def handle(self, control: Any, admission: dict[str, Any]) -> dict[str, Any]:
        with patch.object(PostgresAgentSqlResults, "_admit", return_value=admission):
            reply: dict[str, Any] = json.loads(
                executor(control).handle(reuse_request())
            )
        return reply

    def test_an_unconfirmed_abandonment_stays_pending(self) -> None:
        open_step = {
            "refused": "pending_self",
            "blocking_delivery": str(uuid4()),
            "remaining": REMAINING,
        }
        for error in (failure("55P03"), failure(None)):
            with self.subTest(error=type(error).__name__):
                reply = self.handle(MagicMock(side_effect=error), open_step)
                self.assertEqual((reply["outcome"], reply["error"]), PENDING)
                self.assertEqual(reply["remaining"], REMAINING)

    def test_an_access_that_cannot_take_ownership_stays_pending(self) -> None:
        for control in (
            MagicMock(side_effect=failure(None)),
            failing_at("pg_advisory_lock"),
        ):
            admission = admitted()
            reply = self.handle(control, admission)
            self.assertEqual((reply["outcome"], reply["error"]), PENDING)
            self.assertEqual(reply["access_receipt_ref"], admission["delivery_ref"])
            # Recovery charges the full reservation; the reply says so.
            self.assertEqual(reply["remaining"]["db_ms"], 300000 - 30000)

    def test_an_unrecorded_outcome_is_pending_never_a_raise(self) -> None:
        for refusal in (
            _Failure("execution_error", "database"),
            _Failure("settlement_pending", "settlement", pending=True),
        ):
            for statement in (
                "query_delivery_remaining_v1",
                "settle_query_delivery_v1",
            ):
                with (
                    self.subTest(outcome=refusal.outcome, statement=statement),
                    patch.object(
                        PostgresAgentSqlResults, "_reuse", side_effect=refusal
                    ),
                ):
                    reply = self.handle(failing_at(statement), admitted())
                    self.assertEqual((reply["outcome"], reply["error"]), PENDING)


class AgentSqlScreenedRefusals(unittest.TestCase):
    def test_safe_codes_never_carry_an_internal_construct(self) -> None:
        # An identifier that is not visible, a lost frame and a misprovisioned
        # reader all share the one unavailable shape.
        for construct in ("parameter_anchor", "source_frame", "procedure_privileges"):
            self.assertEqual(
                _admission_error("unavailable", construct), ("unavailable", None)
            )
        self.assertEqual(
            _admission_error("invalid_request", "reference_type"),
            ("type", "reference_type"),
        )
        self.assertEqual(
            _admission_error("invalid_request", "incompatible_types"), ("type", None)
        )
        # A relation this caller may not name is unavailable, and says no more.
        self.assertEqual(
            screen("SELECT c.candidate_ref FROM evaluation_v1.candidates c", []),
            ("unavailable", "unavailable", None, None),
        )

    def test_an_identifier_parameter_must_carry_its_reference_type(self) -> None:
        identifiers = [str(uuid4()), str(uuid4())]
        sql = (
            "SELECT ss.statement_id FROM memory_v1.statement_sources ss "
            "WHERE ss.statement_id = ANY($1::uuid[])"
        )
        # A plain uuid never names an identifier: a located type error that
        # says which kind of mismatch it is.
        self.assertEqual(
            screen(sql, [{"position": 1, "type": "uuid[]", "value": identifiers}]),
            (
                "invalid_request",
                "type",
                "reference_type",
                sql.index("ss.statement_id = ANY"),
            ),
        )
        # The column's own reference type passes the screen. Whether the
        # identifiers are visible is decided after preparation.
        self.assertIsNone(
            screen(
                sql, [{"position": 1, "type": "statement_ref[]", "value": identifiers}]
            )
        )

    def test_the_executor_refuses_an_unreviewed_operator_before_any_work(self) -> None:
        self.assertEqual(
            _admission_error("unsupported_query", "unreviewed_operator"),
            ("feature", "unreviewed_operator"),
        )
        text = [{"position": 1, "type": "text", "value": "fictional"}]
        moment = [
            {"position": 1, "type": "timestamptz", "value": "2026-01-01T00:00:00Z"}
        ]
        number = [{"position": 1, "type": "int8", "value": "1"}]
        values = [str(n) for n in range(9)]
        nine = [{"position": 1, "type": "text[]", "value": values}]
        eight = [{"position": 1, "type": "text[]", "value": values[:8]}]
        # Each refusal points at the first token of the operator's operands.
        for predicate, parameters, token in (
            ("NOT (s.kind = $1)", text, "s.kind"),
            ("s.kind <> $1", text, "s.kind"),
            ("lower(s.text) LIKE $1", text, "lower"),
            ("s.text NOT LIKE $1", text, "s.text"),
            ("NOT (s.text NOT ILIKE $1)", text, "s.text"),
            ("s.recorded_at >= $1", moment, "s.recorded_at"),
            ("s.sequence = $1 AND -s.sequence < s.sequence", number, "s.sequence <"),
            # Under NOT, up to eight values compare with `<>`, which is refused.
            ("NOT (s.kind = ANY($1::text[]))", eight, "s.kind"),
        ):
            with self.subTest(predicate=predicate):
                self.assertEqual(
                    screen(STATEMENTS + predicate, parameters),
                    (*UNREVIEWED, len(STATEMENTS) + predicate.index(token)),
                )
        # A comparison PostgreSQL would group differently is refused where it
        # starts, before any work, and says why.
        flag = [{"position": 1, "type": "bool", "value": True}]
        for predicate, parameters in (
            ("s.kind = s.text IS NULL", []),
            ("s.kind = s.text = $1", flag),
        ):
            with self.subTest(predicate=predicate):
                self.assertEqual(
                    screen(STATEMENTS + predicate, parameters),
                    (
                        "unsupported_query",
                        "feature",
                        "comparison_grouping",
                        len(STATEMENTS),
                    ),
                )
        for predicate, parameters in (
            ("s.kind = $1", text),
            ("NOT (NOT (s.kind = $1))", text),
            ("s.recorded_at = $1", moment),
            ("NOT (s.sequence = $1)", number),
            ("s.kind = ANY($1::text[])", eight),
            # From nine values the planner probes a hash table: equality and the
            # type's hash function, both reviewed, under NOT as well.
            ("s.kind = ANY($1::text[])", nine),
            ("NOT (s.kind = ANY($1::text[]))", nine),
        ):
            with self.subTest(predicate=predicate):
                self.assertIsNone(screen(STATEMENTS + predicate, parameters))

    def test_a_bound_identifier_does_not_postpone_the_refusal(self) -> None:
        # Whether an identifier is visible is decided after preparation. What the
        # SQL and the bound types decide is refused before it, identifier or not.
        identifier = str(uuid4())
        bound = [
            {"position": 1, "type": "statement_ref", "value": identifier},
            {"position": 2, "type": "text", "value": "fictional"},
        ]
        predicate = "s.statement_id = $1 AND s.kind <> $2"
        self.assertEqual(
            screen(STATEMENTS + predicate, bound),
            (*UNREVIEWED, len(STATEMENTS) + predicate.index("s.kind")),
        )
        nine = [str(uuid4()) for _ in range(9)]
        many = [{"position": 1, "type": "statement_ref[]", "value": nine}]
        fewer = [{"position": 1, "type": "statement_ref[]", "value": nine[:8]}]
        array = "s.statement_id = ANY($1::uuid[])"
        self.assertEqual(
            screen(STATEMENTS + "NOT (" + array + ")", fewer),
            (*UNREVIEWED, len(STATEMENTS) + len("NOT (")),
        )
        # A supported statement passes the screen with its identifiers unchecked:
        # only the full admission after preparation decides their visibility.
        self.assertIsNone(screen(STATEMENTS + "s.statement_id = $1", bound[:1]))
        self.assertIsNone(screen(STATEMENTS + array, fewer))
        self.assertIsNone(screen(STATEMENTS + array, many))
        with self.assertRaises(SqlAdmissionError) as unseen:
            admit_query(
                STATEMENTS + "s.statement_id = $1",
                (SqlParameter(1, "statement_ref", identifier),),
            )
        self.assertEqual(
            (unseen.exception.code, unseen.exception.construct),
            ("unavailable", "parameter_anchor"),
        )


if __name__ == "__main__":
    unittest.main()
