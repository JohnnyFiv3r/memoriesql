"""Fictional public query/reuse acceptance; no workload-fit or quality claim."""

from __future__ import annotations

import hashlib
import json
import os
import time
import unittest
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

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

    def service(
        self, secret: str | None = None, reader: Any = None
    ) -> PostgresAgentSqlResults:
        return PostgresAgentSqlResults(
            control_factory=self.fixture.connection,
            reader_factory=reader or self.reader_connection,
            authority_profile=self.profile,
            credential_sha256=secret or self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
        )

    def start(self, secret: str | None = None) -> dict[str, Any]:
        data = json.loads(self.service(secret).start_run())
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
        secret: str | None = None,
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
        return request, self.send(request, secret=secret)

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

    def pair_agent(self, scope: UUID, capabilities: list[str]) -> tuple[UUID, str]:
        """A paired agent through the canonical pairing path, never the owner."""
        grant = uuid4()
        secret = hashlib.sha256(
            ("fictional paired agent " + ",".join(capabilities)).encode()
        ).hexdigest()
        with self.db.transaction():
            self.fixture.begin()
            principal = uuid4()
            self.db.execute(
                "SELECT memoriesql.pair_local_client("
                "%s,%s,%s,%s,'agent','paired_agent',%s,%s,%s,%s,%s)",
                (
                    principal,
                    uuid4(),
                    grant,
                    uuid4(),
                    capabilities,
                    [scope],
                    secret,
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                ),
            )
            self.db.execute(
                "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                (
                    uuid4(),
                    principal,
                    scope,
                    ["read"],
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                ),
            )
        return grant, secret

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

    def test_paired_agent_reads_observations_under_its_own_grants(self) -> None:
        # Regression (PR-06 repro): a paired agent holding memory.query and
        # source.read got an available zero-row result, because observation
        # families required raw source authority that paired_agent cannot hold.
        scope, source_object, _ = self.fixture.remote_scope()
        self.fixture.assertion(scope=scope, source_object=source_object)
        expected = {
            str(row[0])
            for row in self.db.execute(
                "SELECT DISTINCT a.bead_id FROM memoriesql.accepted_bead_semantics a "
                "JOIN memoriesql.beads b ON b.tenant_id=a.tenant_id "
                "AND b.bead_id=a.bead_id WHERE b.access_scope_id=%s",
                (scope,),
            ).fetchall()
        }
        self.assertGreaterEqual(len(expected), 2)
        text = "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"
        count = "SELECT count(*) AS n FROM memory_v1.assessed_relations"
        owner_run = self.start()
        _, owner = self.query(owner_run, text, page_size=50)
        self.assertEqual(owner["outcome"], "available", owner)
        owner_beads = {row["values"][0] for row in owner["page"]["rows"]}
        self.assertLessEqual(expected, owner_beads)
        _, owner_relations = self.query(owner_run, count)
        self.assertEqual(owner_relations["outcome"], "available", owner_relations)
        self.assertNotEqual(owner_relations["page"]["rows"][0]["values"], ["0"])
        self.assertNotIn(
            "relation_tables",
            [g["facet"] for g in owner_relations["result"]["coverage"]["gaps"]],
        )

        grant, agent = self.pair_agent(
            scope, ["memory.inspect", "memory.query", "source.read"]
        )
        run = self.start(agent)
        _, reply = self.query(run, text, page_size=50, secret=agent)
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertEqual({row["values"][0] for row in reply["page"]["rows"]}, expected)
        self.assertEqual(reply["result"]["coverage"]["gaps"], [])
        _, statements = self.query(
            run,
            "SELECT s.statement_id,u.source_unit_id FROM memory_v1.statements s "
            "JOIN memory_v1.statement_sources u ON u.statement_id=s.statement_id",
            secret=agent,
        )
        self.assertEqual(statements["outcome"], "available", statements)
        self.assertGreater(int(statements["result"]["total_rows"]), 0)
        # Assessed relations keep PR-03's raw-source-gated provenance. Zero rows
        # are never presented as absence: the missing capability is a gap.
        _, relations = self.query(run, count, secret=agent)
        self.assertEqual(relations["outcome"], "available", relations)
        self.assertEqual(relations["page"]["rows"][0]["values"], ["0"])
        self.assertIn(
            {"facet": "relation_tables", "reason": "source_raw_read_required"},
            relations["result"]["coverage"]["gaps"],
        )
        # The agent's own result stays reusable under its grants and ends with them.
        again = self.reuse(run, reply["result"], page_size=50, secret=agent)
        self.assertEqual(again["outcome"], "available", again)
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.revise_pairing_grant("
                "%s,1,%s,%s,'revoked',%s,%s,%s)",
                (
                    grant,
                    ["memory.inspect", "memory.query", "source.read"],
                    [scope],
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                    self.fixture.now,
                ),
            )
        revoked = self.reuse(run, reply["result"], page_size=50, secret=agent)
        self.assertEqual(revoked["outcome"], "unavailable", revoked)
        self.assertNotIn("result", revoked)

    def test_agent_without_source_read_sees_disclosed_gap_not_absence(self) -> None:
        scope, source_object, _ = self.fixture.remote_scope()
        self.fixture.assertion(scope=scope, source_object=source_object)
        _, agent = self.pair_agent(scope, ["memory.query"])
        run = self.start(agent)
        _, reply = self.query(
            run, "SELECT o.bead_id FROM memory_v1.observations o", secret=agent
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertEqual(reply["result"]["total_rows"], "0")
        self.assertIn(
            {"facet": "observation_tables", "reason": "source_read_required"},
            reply["result"]["coverage"]["gaps"],
        )

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

    def test_host_death_mid_query_keeps_capacity_until_reader_ends(self) -> None:
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(run, "SELECT bead_id FROM memory_v1.observations")
        leaked: list[Any] = []

        class HostDied(BaseException):
            pass

        class Leaky(psycopg.Connection[Any]):
            # The host dies as the admitted SELECT is dispatched; its reader
            # backend keeps an open transaction, as a still-running query would.
            def cursor(self, *args: Any, **kwargs: Any) -> Any:
                if kwargs.get("name"):
                    raise HostDied()
                return super().cursor(*args, **kwargs)

            def rollback(self) -> None:
                pass

            def close(self) -> None:
                pass

        def leaky() -> Any:
            connection = Leaky.connect(
                make_conninfo(
                    os.environ["N1_TEST_DATABASE_URL"],
                    dbname=self.db.info.dbname,
                    user=self.reader,
                    password=READER_PASSWORD,
                ),
                autocommit=True,
            )
            leaked.append(connection)
            return connection

        with self.assertRaises(HostDied):
            self.service(reader=leaky).handle(json.dumps(request).encode())
        self.assertEqual(
            self.h.scalar(
                "SELECT state FROM memoriesql_query.invocations WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            "executing",
        )
        # The owner session ended, but the reader may still be executing.
        self.assertEqual(self.service().recover_abandoned(), 0)
        refused = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(refused["error"], {"code": "settlement"}, refused)
        pending = self.send(request)
        self.assertEqual(pending["outcome"], "settlement_pending", pending)
        _, blocked = self.query(run, "SELECT bead_version_id FROM memory_v1.observations")
        self.assertEqual(blocked["error"], {"code": "settlement"}, blocked)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )
        # Only confirmed reader termination lets owned settlement proceed.
        pid = leaked[0].info.backend_pid
        psycopg.Connection.close(leaked[0])
        for _ in range(100):
            if not self.h.scalar(
                "SELECT count(*) FROM pg_stat_activity WHERE pid=%s", (pid,)
            ):
                break
            time.sleep(0.05)
        self.assertEqual(self.service().recover_abandoned(), 1)
        self.assertEqual(
            self.h.scalar(
                "SELECT state FROM memoriesql_query.invocations WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            "settled",
        )
        self.assertGreaterEqual(
            self.h.scalar(
                "SELECT charged_db_ms FROM memoriesql.query_deliveries "
                "WHERE outcome='abandoned'"
            ),
            30000,
        )
        # Recovery alone releases the workspace: a new step is admitted without
        # replaying the lost one, and the lost step is never rerun.
        _, fresh = self.query(run, "SELECT bead_version_id FROM memory_v1.observations")
        self.assertEqual(fresh["outcome"], "available", fresh)
        lost = self.send(request)
        self.assertEqual(lost["outcome"], "execution_error", lost)
        closed = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)

    def test_close_refuses_while_run_work_is_unsettled(self) -> None:
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
        refused = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(refused["outcome"], "budget_exhausted", refused)
        self.assertEqual(refused["error"], {"code": "settlement"})
        self.assertEqual(self.service().recover_abandoned(), 1)
        closed = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)
        foreign = json.loads(self.service().close_run(str(uuid4())))
        self.assertEqual(foreign["outcome"], "unavailable", foreign)

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
