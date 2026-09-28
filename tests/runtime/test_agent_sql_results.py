"""Fictional public query/reuse acceptance; no workload-fit or quality claim."""

from __future__ import annotations

import json
import os
import unittest
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import POLICY_HASH
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
)
from memoriesql.infrastructure.postgres.query_reader_provisioning import (
    provision_query_reader,
)

if TYPE_CHECKING:
    from tests.runtime import test_query_result_commit as commit_tests
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_query_result_commit as commit_tests
    from test_postgres_runtime import migrate

READER_PASSWORD = "fictional-reader-only-0001"


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AgentSqlResults(unittest.TestCase):
    def setUp(self) -> None:
        self.h = commit_tests.QueryResultCommit(methodName="runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.fixture = self.h.fixture
        self.db = self.h.db
        migrate(self.db, expected_current_version=34, target_version=38)
        # The production reviewed provisioning path, on fictional data.
        self.reader = "pr05_results_" + uuid4().hex
        self.profile = provision_query_reader(self.db, self.reader, READER_PASSWORD)
        self.addCleanup(self.drop_reader)

    def drop_reader(self) -> None:
        self.db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(self.reader)))
        self.db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.reader)))

    def reader_connection(self) -> psycopg.Connection[Any]:
        return psycopg.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"],
                dbname=self.db.info.dbname,
                user=self.reader,
                password=READER_PASSWORD,
            ),
            autocommit=True,
        )

    def service(self, secret: str | None = None) -> PostgresAgentSqlResults:
        return PostgresAgentSqlResults(
            control_factory=self.fixture.connection,
            reader_factory=self.reader_connection,
            authority_profile=self.profile,
            credential_sha256=secret or self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
        )

    def start(self) -> dict[str, Any]:
        data = json.loads(self.service().start_run())
        self.assertEqual(data["outcome"], "available", data)
        run: dict[str, Any] = data["run"]
        self.assertEqual(run["policy_hash"], POLICY_HASH)
        return run

    def query(
        self,
        run: dict[str, Any],
        text: str,
        *,
        step: str | None = None,
        parameters: list[dict[str, Any]] | None = None,
        view: str = "historical",
        page_size: int = 2,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        request = {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": step or str(uuid4()),
            "kind": "query",
            "catalog_hash": SqlCatalog.installed().hash,
            "sql": text,
            "parameters": parameters or [],
            "inputs": [],
            "parents": [],
            "scope": {
                "source_refs": [],
                "known_at": run["default_known_at"],
                "view": view,
            },
            "intent": "enumerate",
            "max_result_bytes": 64 * 1024 * 1024,
            "page_size": page_size,
        }
        return request, self.send(request)

    def reuse(
        self,
        run: dict[str, Any],
        result: dict[str, Any],
        *,
        cursor: str | None = None,
        page_size: int = 2,
        step: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": step or str(uuid4()),
            "kind": "reuse_result",
            "result": {
                "result_id": result["result_id"],
                "content_digest": result["content_digest"],
            },
            "access": {"kind": "standalone"},
            "page_size": page_size,
        }
        if cursor is not None:
            request["cursor"] = cursor
        return self.send(request, secret=secret)

    def send(
        self, request: dict[str, Any], *, secret: str | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(
            self.service(secret).handle(json.dumps(request).encode())
        )
        return data

    def invocations(self) -> int:
        return int(self.h.scalar("SELECT count(*) FROM memoriesql_query.invocations"))

    def test_query_page_cursor_reuse_and_redelivery_without_rerun(self) -> None:
        source, target, _ = self.fixture.assertion()
        run = self.start()
        request, reply = self.query(
            run,
            """SELECT o.bead_id,o.title,s.statement_id,s.text
               FROM memory_v1.observations o
               JOIN memory_v1.statements s ON s.bead_version_id=o.bead_version_id
               ORDER BY o.bead_id,s.statement_id""",
            page_size=2,
        )
        self.assertEqual(reply["outcome"], "available", reply)
        result = reply["result"]
        total = int(result["total_rows"])
        self.assertGreaterEqual(total, 3)
        self.assertEqual(result["frame"]["view"], "historical")
        self.assertIsNone(result["frame"]["lifecycle_projection_version"])
        self.assertEqual(result["coverage"]["query_result"], "complete")
        self.assertEqual(result["coverage"]["source_capture"], "unknown")
        self.assertEqual(
            result["order_basis"]["columns"],
            [
                {"column": "bead_id", "direction": "asc", "nulls": "last"},
                {"column": "statement_id", "direction": "asc", "nulls": "last"},
            ],
        )
        page = reply["page"]
        self.assertEqual([row["row_ref"] for row in page["rows"]], ["1", "2"])
        self.assertTrue(page["has_more"])
        kinds = {ref["kind"] for row in page["rows"] for ref in row["evidence_refs"]}
        self.assertEqual(kinds, {"observation", "statement"})
        self.assertTrue(reply["visibility"]["new_refs"])
        self.assertEqual(reply["visibility"]["previous_refs"], [])
        self.assertEqual(reply["work"]["measurement_state"], "unavailable")
        self.assertEqual(reply["remaining"]["accesses"], 127)
        base = self.invocations()
        following = self.reuse(run, result, cursor=page["next_cursor"])
        self.assertEqual(following["outcome"], "available", following)
        self.assertEqual(following["page"]["rows"][0]["row_ref"], "3")
        self.assertEqual(
            following["result"]["content_digest"], result["content_digest"]
        )
        # Exact redelivery returns the same stored page under a new access receipt.
        again = self.send(request)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["result"]["result_id"], result["result_id"])
        self.assertEqual(again["receipt_ref"], reply["receipt_ref"])
        self.assertNotEqual(again["access_receipt_ref"], reply["access_receipt_ref"])
        self.assertEqual(again["page"]["rows"], page["rows"])
        self.assertTrue(again["visibility"]["previous_refs"])
        self.assertEqual(self.invocations(), base)
        self.assertEqual(again["remaining"]["accesses"], 125)
        self.assertIn(str(source), json.dumps(page["rows"]) + json.dumps(following))
        self.assertIn(str(target), json.dumps(again) + json.dumps(following))

    def test_distinct_refusals_and_empty_result(self) -> None:
        self.fixture.assertion()
        run = self.start()
        _, empty = self.query(
            run,
            "SELECT bead_id FROM memory_v1.observations WHERE bead_type_key=$1",
            parameters=[{"position": 1, "type": "text", "value": "no-such-type"}],
        )
        self.assertEqual(empty["outcome"], "available", empty)
        self.assertEqual(empty["result"]["total_rows"], "0")
        self.assertEqual(empty["page"]["rows"], [])
        _, unsupported = self.query(run, "SELECT entity_id FROM memory_v1.entities")
        self.assertEqual(unsupported["outcome"], "unsupported_query", unsupported)
        self.assertEqual(unsupported["error"]["feature"], "unprepared_relation")
        self.assertNotIn("result", unsupported)
        _, invalid = self.query(run, "SELEC bead_id FROM memory_v1.observations")
        self.assertEqual(invalid["outcome"], "invalid_request", invalid)
        step = str(uuid4())
        self.query(run, "SELECT bead_id FROM memory_v1.observations", step=step)
        _, conflict = self.query(
            run, "SELECT bead_version_id FROM memory_v1.observations", step=step
        )
        self.assertEqual(conflict["outcome"], "idempotency_conflict", conflict)
        missing = self.reuse(
            run, {"result_id": str(uuid4()), "content_digest": "0" * 64}
        )
        self.assertEqual(missing["outcome"], "unavailable", missing)
        self.assertNotIn("result", missing)

    def test_revocation_refuses_whole_aggregate_and_regrant_restores(self) -> None:
        self.fixture.assertion()
        run = self.start()
        _, reply = self.query(
            run, "SELECT count(*) AS n FROM memory_v1.statement_sources"
        )
        self.assertEqual(reply["outcome"], "available", reply)
        result = reply["result"]
        self.assertEqual([g["facet"] for g in result["coverage"]["gaps"]], [])
        self.db.execute(
            "UPDATE memoriesql.protected_resources "
            "SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source'"
        )
        refused = self.reuse(run, result)
        self.assertEqual(refused["outcome"], "unavailable", refused)
        self.assertNotIn("result", refused)
        self.db.execute(
            "UPDATE memoriesql.protected_resources "
            "SET status='active',revoked_at=NULL WHERE resource_kind='source'"
        )
        restored = self.reuse(run, result)
        self.assertEqual(restored["outcome"], "available", restored)
        self.assertEqual(
            restored["page"]["rows"][0]["values"], reply["page"]["rows"][0]["values"]
        )

    def test_other_principal_cannot_reuse_or_use_run(self) -> None:
        scope, source_object, _ = self.fixture.remote_scope()
        self.fixture.assertion(scope=scope, source_object=source_object)
        run = self.start()
        _, reply = self.query(run, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(reply["outcome"], "available", reply)
        _, other_secret = self.fixture.second_human(scope)
        stolen = self.reuse(run, reply["result"], secret=other_secret)
        self.assertEqual(stolen["outcome"], "unavailable", stolen)

    def test_resolved_view_withholds_corrected_predecessor(self) -> None:
        _, target, _ = self.fixture.assertion()
        corrected = self.fixture.correction(target)
        run = self.start()
        _, historical = self.query(
            run,
            "SELECT bead_id,correction_state FROM memory_v1.observations ORDER BY bead_id",
            page_size=10,
        )
        self.assertEqual(historical["outcome"], "available", historical)
        states = {
            row["values"][0]: row["values"][1] for row in historical["page"]["rows"]
        }
        self.assertEqual(states[str(target)], "superseded")
        self.assertEqual(states[str(corrected)], "unsuperseded")
        _, resolved = self.query(
            run,
            "SELECT bead_id FROM memory_v1.observations ORDER BY bead_id",
            view="resolved",
            page_size=10,
        )
        present = {row["values"][0] for row in resolved["page"]["rows"]}
        self.assertNotIn(str(target), present)
        self.assertIn(str(corrected), present)
        _, lineage = self.query(
            run,
            "SELECT predecessor_bead_id,successor_bead_id FROM memory_v1.corrections",
        )
        self.assertEqual(
            lineage["page"]["rows"][0]["values"], [str(target), str(corrected)]
        )


    def test_run_admission_expiry_and_no_budget_reset(self) -> None:
        first = self.start()
        self.start()
        third = json.loads(self.service().start_run())
        self.assertEqual(third["outcome"], "budget_exhausted", third)
        self.assertEqual(third["error"], {"code": "database"})
        self.db.execute(
            "ALTER TABLE memoriesql.query_runs DISABLE TRIGGER query_runs_no_update"
        )
        try:
            self.db.execute(
                "UPDATE memoriesql.query_runs SET "
                "started_at=started_at-interval '31 minutes',"
                "expires_at=expires_at-interval '31 minutes',"
                "default_known_at=default_known_at-interval '31 minutes' "
                "WHERE run_ref=%s",
                (first["run_ref"],),
            )
        finally:
            self.db.execute(
                "ALTER TABLE memoriesql.query_runs ENABLE TRIGGER query_runs_no_update"
            )
        _, expired = self.query(first, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(expired["outcome"], "budget_exhausted", expired)
        self.assertEqual(expired["error"], {"code": "time"})
        self.assertEqual(json.loads(self.service().start_run())["outcome"], "available")

    def test_crash_after_commit_redelivers_same_result_without_rerun(self) -> None:
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(run, "SELECT bead_id FROM memory_v1.observations")

        class Crash(BaseException):
            pass

        def crash(*args: Any, **kwargs: Any) -> bytes:
            raise Crash()

        with patch.object(PostgresAgentSqlResults, "_disclose", crash):
            with self.assertRaises(Crash):
                self.send(request)
        # The owner session is gone, so other work is refused as unsettled.
        _, blocked = self.query(run, "SELECT bead_version_id FROM memory_v1.observations")
        self.assertEqual(blocked["outcome"], "budget_exhausted", blocked)
        self.assertEqual(blocked["error"], {"code": "settlement"})
        before = self.invocations()
        again = self.send(request)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(self.invocations(), before)
        charged = self.h.scalar(
            "SELECT charged_db_ms FROM memoriesql.query_deliveries WHERE outcome='abandoned'"
        )
        self.assertEqual(charged, 30000)
        _, after = self.query(run, "SELECT bead_version_id FROM memory_v1.observations")
        self.assertEqual(after["outcome"], "available", after)

    def test_host_recovery_abandons_dead_owner_without_disclosure(self) -> None:
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(run, "SELECT bead_id FROM memory_v1.observations")

        class Crash(BaseException):
            pass

        def crash(*args: Any, **kwargs: Any) -> bytes:
            raise Crash()

        with patch.object(PostgresAgentSqlResults, "_execute", crash):
            with self.assertRaises(Crash):
                self.send(request)
        self.assertEqual(self.service().recover_abandoned(), 1)
        self.assertEqual(self.service().recover_abandoned(), 0)
        # A single-use preparation owner is never replayed into a new SELECT:
        # the lost attempt fails truthfully and a new step key executes.
        again = self.send(request)
        self.assertEqual(again["outcome"], "execution_error", again)
        self.assertEqual(self.send(request)["outcome"], "execution_error")
        _, fresh = self.query(run, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(fresh["outcome"], "available", fresh)
        self.assertEqual(self.invocations(), 1)

    def test_owner_close_frees_slot_without_refund(self) -> None:
        first = self.start()
        self.start()
        _, reply = self.query(first, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(reply["outcome"], "available", reply)
        closed = json.loads(self.service().close_run(first["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)
        self.assertEqual(json.loads(self.service().close_run(first["run_ref"])), closed)
        _, after = self.query(first, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(after["outcome"], "budget_exhausted", after)
        self.assertEqual(json.loads(self.service().start_run())["outcome"], "available")
        window = self.h.scalar(
            "SELECT sum(charged_db_ms) FROM memoriesql.query_deliveries WHERE run_ref=%s",
            (first["run_ref"],),
        )
        self.assertGreater(window, 0)

    def test_composition_shapes_over_prepared_observation_relations(self) -> None:
        self.fixture.assertion()
        run = self.start()
        for text in (
            "SELECT o.bead_type_key,count(*) AS n FROM memory_v1.observations o "
            "GROUP BY o.bead_type_key",
            "SELECT s.statement_id,row_number() OVER "
            "(PARTITION BY s.bead_id ORDER BY s.statement_id) AS position "
            "FROM memory_v1.statements s",
            "SELECT bead_id FROM memory_v1.observations UNION ALL "
            "SELECT bead_id FROM memory_v1.statements",
            "SELECT o.bead_id FROM memory_v1.observations o WHERE EXISTS "
            "(SELECT s.statement_id FROM memory_v1.statements s "
            "WHERE s.bead_id=o.bead_id)",
            "SELECT u.source_kind,count(DISTINCT u.source_unit_id) AS units "
            "FROM memory_v1.source_units u GROUP BY u.source_kind",
            "SELECT o.title,r.type_key FROM memory_v1.assessed_relations r "
            "JOIN memory_v1.observations o ON o.bead_id=r.target_bead_id",
        ):
            with self.subTest(text=text):
                _, reply = self.query(run, text, page_size=5)
                self.assertEqual(reply["outcome"], "available", reply)
                self.assertGreater(int(reply["result"]["total_rows"]), 0)
        _, related = self.query(
            run,
            "SELECT r.type_key,s.text FROM memory_v1.assessed_relations r "
            "JOIN memory_v1.relation_statements s ON s.relation_id=r.relation_id",
        )
        frame = related["result"]["frame"]
        self.assertEqual(frame["lifecycle_projection_version"], 1)
        self.assertRegex(frame["projection_manifest_sha256"], "^[0-9a-f]{64}$")
        _, sources = self.query(
            run, "SELECT source_unit_id,text_state FROM memory_v1.source_units"
        )
        self.assertEqual(
            [gap["facet"] for gap in sources["result"]["coverage"]["gaps"]],
            [
                "source_units.occurrence_ref",
                "source_units.package_revision_id",
                "source_units.trust_label",
            ],
        )

    def request_only(
        self, run: dict[str, Any], text: str
    ) -> tuple[dict[str, Any], None]:
        return {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": str(uuid4()),
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
            "page_size": 2,
        }, None

if __name__ == "__main__":
    unittest.main()
