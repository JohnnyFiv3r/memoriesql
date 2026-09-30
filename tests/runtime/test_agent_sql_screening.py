"""What the public executor refuses from a statement and its bound values alone.

Fictional data through the production provisioning path. An identifier binds
only under its reference type, and only when the caller can see it. No workload
or quality claim.
"""

from __future__ import annotations

import os
import unittest
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_results as results_tests
else:
    import test_agent_sql_results as results_tests


def bound(kind: str, value: Any, position: int = 1) -> list[dict[str, Any]]:
    return [{"position": position, "type": kind, "value": value}]


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


if __name__ == "__main__":
    unittest.main()
