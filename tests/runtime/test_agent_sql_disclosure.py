"""Fictional disclosure outcomes: a conflicting completion, deadlines and facts.

Development acceptance only; no workload-fit or quality claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import unittest
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import ref_key
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
)

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_results as results_tests
else:
    import test_agent_sql_results as results_tests

Disclose = Callable[..., bytes]


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AgentSqlDisclosure(unittest.TestCase):
    def setUp(self) -> None:
        self.r = results_tests.AgentSqlResults(methodName="runTest")
        self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        self.db = self.r.db
        self.r.fixture.assertion()

    def request(
        self, run: dict[str, Any], text: str, *, page_size: int, step: str | None = None
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": step or str(uuid4()),
            "kind": "query",
            "catalog_hash": SqlCatalog.installed().hash,
            "sql": text,
            "parameters": [],
            "inputs": [],
            "parents": [],
            "scope": {
                "source_refs": [],
                "known_at": run["default_known_at"],
                "view": "historical",
            },
            "intent": "enumerate",
            "max_result_bytes": 64 * 1024 * 1024,
            "page_size": page_size,
        }

    def send(self, request: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        data = self.r.service().handle(json.dumps(request).encode())
        reply: dict[str, Any] = json.loads(data)
        return data, reply

    def delivery(self, ref: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT to_jsonb(d) FROM memoriesql.query_deliveries d "
            "WHERE d.delivery_ref=%s",
            (UUID(ref),),
        ).fetchone()
        assert row is not None
        found: dict[str, Any] = row[0]
        return found

    def records(self, run: dict[str, Any], *, besides: str) -> dict[str, list[str]]:
        """Every stored row of the run's work except delivery `besides` itself."""
        deliveries = (
            "(SELECT d.delivery_ref FROM memoriesql.query_deliveries d"
            " WHERE d.run_ref=%(run)s)"
        )
        found: dict[str, list[str]] = {}
        for table, predicate in (
            ("query_steps", "t.run_ref=%(run)s"),
            ("query_deliveries", "t.run_ref=%(run)s AND t.delivery_ref<>%(besides)s"),
            ("query_visible_refs", "t.run_ref=%(run)s"),
            ("query_disclosures", "t.delivery_ref IN " + deliveries),
            ("query_cursors", "t.issued_by_delivery IN " + deliveries),
        ):
            found[table] = [
                row[0]
                for row in self.db.execute(
                    f"SELECT to_jsonb(t)::text FROM memoriesql.{table} t "
                    f"WHERE {predicate} ORDER BY 1",
                    {"run": UUID(run["run_ref"]), "besides": UUID(besides)},
                ).fetchall()
            ]
        return found

    def unsettled(self) -> int:
        return int(
            self.r.h.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries "
                "WHERE workspace_id=%s AND state<>'settled'",
                (self.r.fixture.workspace,),
            )
        )

    def visible(self, run: dict[str, Any]) -> list[str]:
        return [
            row[0]
            for row in self.db.execute(
                "SELECT ref_key FROM memoriesql.query_visible_refs "
                "WHERE run_ref=%s ORDER BY 1",
                (UUID(run["run_ref"]),),
            ).fetchall()
        ]

    def test_a_conflicting_completion_settles_only_its_own_delivery(self) -> None:
        run = self.r.start()
        text = "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"
        _, first = self.send(self.request(run, text, page_size=5))
        self.assertEqual(first["outcome"], "available", first)
        committed = first["result"]
        rows = first["page"]["rows"]
        step = str(uuid4())
        request = self.request(run, text, page_size=5, step=step)
        seen: dict[str, Any] = {}
        disclose: Disclose = PostgresAgentSqlResults._disclose

        def completed_elsewhere(
            executor: PostgresAgentSqlResults,
            sent: Any,
            admission: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> bytes:
            # A constructed state: admission keeps one unsettled delivery per
            # workspace, and no request is known to reach this. Before this
            # delivery's disclosure frame takes its snapshot, its step is
            # completed with another committed result, as a concurrent
            # completion of the same step would.
            with self.db.transaction():
                seen["completed"] = self.db.execute(
                    "UPDATE memoriesql.query_steps SET state='complete',"
                    "result_id=%s,content_digest=%s,first_row=1,row_count=%s,"
                    "page_size=5,completed_at=clock_timestamp() "
                    "WHERE run_ref=%s AND step_key=%s AND state='executing'",
                    (
                        UUID(committed["result_id"]),
                        committed["content_digest"],
                        len(rows),
                        UUID(run["run_ref"]),
                        UUID(step),
                    ),
                ).rowcount
            seen["delivery"] = admission["delivery_ref"]
            seen["records"] = self.records(run, besides=admission["delivery_ref"])
            return disclose(executor, sent, admission, *args, **kwargs)

        with patch.object(PostgresAgentSqlResults, "_disclose", completed_elsewhere):
            data, reply = self.send(request)
        self.assertEqual(seen["completed"], 1)
        delivery = seen["delivery"]
        # The approved typed reply, carrying what recovers the step's result.
        self.assertEqual(reply["outcome"], "idempotency_conflict", reply)
        self.assertEqual(reply["error"], {"code": "idempotency"})
        self.assertEqual(reply["run_ref"], run["run_ref"])
        self.assertEqual(reply["step_key"], step)
        self.assertEqual(reply["receipt_ref"], delivery)
        self.assertEqual(reply["access_receipt_ref"], delivery)
        for key in ("result", "page", "visibility", "work"):
            self.assertNotIn(key, reply)
        # Only this delivery settles: once, at its observed work, binding this
        # very reply.
        settled = self.delivery(delivery)
        self.assertEqual(
            (settled["state"], settled["outcome"]), ("settled", "idempotency_conflict")
        )
        self.assertEqual(settled["charged_transport"], len(data))
        self.assertEqual(settled["response_sha256"], hashlib.sha256(data).hexdigest())
        self.assertLess(settled["charged_db_ms"], settled["reserved_db_ms"])
        # Nothing else changes and nothing is applied twice. The step keeps the
        # result and outcome its completion fixed, and the earlier delivery
        # keeps its charge. This delivery disclosed nothing, made nothing
        # visible and issued no cursor.
        self.assertEqual(self.records(run, besides=delivery), seen["records"])
        # Its own committed result is kept, never discarded, and stays unbound.
        own = self.db.execute(
            "SELECT o.state,c.result_id FROM memoriesql.result_preparation_operations o "
            "LEFT JOIN memoriesql.query_result_creations c ON c.tenant_id=o.tenant_id "
            "AND c.operation_ref=o.operation_ref WHERE o.run_ref=%s AND o.step_key=%s",
            (UUID(run["run_ref"]), UUID(step)),
        ).fetchone()
        assert own is not None
        self.assertEqual(own[0], "sealed")
        self.assertIsNotNone(own[1])
        self.assertNotEqual(str(own[1]), committed["result_id"])
        # Nothing stays reserved, so nothing blocks the workspace.
        self.assertEqual(self.unsettled(), 0)

        # The exact redelivery recovers the step's committed result with no
        # rerun, under a new access receipt and charge. The conflict's own
        # settlement is unchanged.
        invocations = self.r.invocations()
        _, again = self.send(request)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["result"]["result_id"], committed["result_id"])
        self.assertEqual(again["page"]["rows"], rows)
        self.assertEqual(again["receipt_ref"], delivery)
        self.assertNotEqual(again["access_receipt_ref"], delivery)
        self.assertEqual(self.r.invocations(), invocations)
        self.assertEqual(self.delivery(delivery), settled)
        self.assertEqual(self.unsettled(), 0)

    def test_a_passed_deadline_is_a_budget_refusal_at_the_page_read_too(self) -> None:
        run = self.r.start()
        _, first = self.send(
            self.request(
                run, "SELECT o.bead_id FROM memory_v1.observations o", page_size=2
            )
        )
        self.assertEqual(first["outcome"], "available", first)
        result = first["result"]
        disclose: Disclose = PostgresAgentSqlResults._disclose
        page_reply = PostgresAgentSqlResults._page_reply

        def due(seconds: int) -> Disclose:
            def disclosing(
                executor: PostgresAgentSqlResults,
                sent: Any,
                admission: dict[str, Any],
                *args: Any,
                **kwargs: Any,
            ) -> bytes:
                with self.db.transaction():
                    self.db.execute(
                        "UPDATE memoriesql.query_deliveries SET deadline="
                        "clock_timestamp()+%s*interval '1 second' WHERE delivery_ref=%s",
                        (seconds, UUID(admission["delivery_ref"])),
                    )
                return disclose(executor, sent, admission, *args, **kwargs)

            return disclosing

        def slowly(
            executor: PostgresAgentSqlResults, *args: Any, **kwargs: Any
        ) -> tuple[bytes, int, dict[str, Any]]:
            built = page_reply(executor, *args, **kwargs)
            time.sleep(2.5)
            return built

        # The page read finds the delivery's deadline already passed.
        with patch.object(PostgresAgentSqlResults, "_disclose", due(-1)):
            at_read = self.r.reuse(run, result)
        # The disclosure finds it passed once the page has been read.
        with (
            patch.object(PostgresAgentSqlResults, "_disclose", due(2)),
            patch.object(PostgresAgentSqlResults, "_page_reply", slowly),
        ):
            at_disclosure = self.r.reuse(run, result)
        self.assertEqual(at_disclosure["outcome"], "budget_exhausted", at_disclosure)
        # A passed deadline is a budget refusal, never missing authority, and
        # both checks report it alike.
        self.assertEqual(at_read["outcome"], "budget_exhausted", at_read)
        self.assertEqual(at_read["error"], at_disclosure["error"])
        self.assertNotEqual(at_read["error"]["code"], "unavailable")
        for reply in (at_read, at_disclosure):
            for key in ("result", "page", "visibility"):
                self.assertNotIn(key, reply)
        self.assertEqual(self.unsettled(), 0)

    def test_an_aggregate_makes_its_fact_visible_and_no_contributor(self) -> None:
        run = self.r.start()
        request = self.request(
            run, "SELECT count(*) AS n FROM memory_v1.statement_sources", page_size=2
        )
        _, reply = self.send(request)
        self.assertEqual(reply["outcome"], "available", reply)
        result = reply["result"]
        (row,) = reply["page"]["rows"]
        self.assertEqual(
            row["fact_ref"], {"result_id": result["result_id"], "row_ref": "1"}
        )
        self.assertEqual(row["evidence_refs"], [])
        fact = {"kind": "result_fact", "fact": row["fact_ref"]}
        self.assertEqual(
            reply["visibility"],
            {"new_refs": [fact], "previous_refs": [], "hydration_required": []},
        )
        self.assertEqual(self.visible(run), [ref_key(fact)])
        # It stays usable as evidence in this run: reuse and the exact
        # redelivery show it as previously visible and add nothing.
        reused = self.r.reuse(run, result)
        self.assertEqual(reused["outcome"], "available", reused)
        self.assertEqual(
            reused["visibility"],
            {"new_refs": [], "previous_refs": [fact], "hydration_required": []},
        )
        _, again = self.send(request)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["visibility"], reused["visibility"])
        self.assertEqual(self.visible(run), [ref_key(fact)])
        # Only under current authority: losing a contributor refuses the whole
        # aggregate, and the refusal shows and records nothing.
        self.db.execute(
            "UPDATE memoriesql.protected_resources "
            "SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source'"
        )
        refused = self.r.reuse(run, result)
        self.assertEqual(refused["outcome"], "unavailable", refused)
        self.assertNotIn("visibility", refused)
        self.assertEqual(self.visible(run), [ref_key(fact)])

    def test_every_delivered_row_makes_its_own_fact_visible(self) -> None:
        run = self.r.start()
        _, reply = self.send(
            self.request(
                run,
                "SELECT o.bead_id,s.statement_id FROM memory_v1.observations o "
                "JOIN memory_v1.statements s ON s.bead_version_id=o.bead_version_id "
                "ORDER BY o.bead_id,s.statement_id",
                page_size=2,
            )
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertTrue(reply["page"]["has_more"])
        result = reply["result"]
        rows = reply["page"]["rows"]
        refs = reply["visibility"]["new_refs"]
        self.assertEqual(
            [ref for ref in refs if ref["kind"] == "result_fact"],
            [{"kind": "result_fact", "fact": row["fact_ref"]} for row in rows],
        )
        # Each row's own evidence is visible beside its fact, and nothing else.
        evidence = {ref_key(ref): ref for row in rows for ref in row["evidence_refs"]}
        self.assertTrue(evidence)
        for ref in evidence.values():
            self.assertIn(ref, refs)
        self.assertEqual(len(refs), len(rows) + len(evidence))
        # An undelivered row's fact is not visible until its page is delivered.
        third = {
            "kind": "result_fact",
            "fact": {"result_id": result["result_id"], "row_ref": "3"},
        }
        self.assertNotIn(ref_key(third), self.visible(run))
        following = self.r.reuse(run, result, cursor=reply["page"]["next_cursor"])
        self.assertEqual(following["outcome"], "available", following)
        self.assertIn(third, following["visibility"]["new_refs"])
        self.assertIn(ref_key(third), self.visible(run))


if __name__ == "__main__":
    unittest.main()
