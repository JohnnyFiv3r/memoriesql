"""Fictional public query/reuse acceptance; no workload-fit or quality claim."""

from __future__ import annotations

import hashlib
import json
import os
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import POLICY_HASH
from memoriesql.application.evidence_packages import NativeFacts
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.query_reader_provisioning import (
    provision_query_reader,
)
from memoriesql.infrastructure.postgres.query_result_commit import (
    PostgresQueryResultCommit,
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

    @contextmanager
    def in_scope(self, scope: UUID | None) -> Iterator[None]:
        """Author into another fixture scope, as the assertion fixture does."""
        original = self.fixture.scope
        if scope is not None:
            self.fixture.scope = scope
        try:
            yield
        finally:
            self.fixture.scope = original

    def normalized_bead(
        self,
        text: str,
        key: str,
        *,
        raw: str,
        source: UUID | None = None,
        scope: UUID | None = None,
    ) -> UUID:
        """A bead authored from a producer-normalized package over raw bytes.

        The fixture captures `raw` as the retained source range; the sealed part
        holds only `text`, derived from it and lineage-linked to it.
        """
        original = self.fixture.part

        def normalized(content: str, *args: Any, **kwargs: Any) -> Any:
            captured = original(raw, *args, **kwargs)
            return captured.model_copy(
                update={
                    "derivation": "producer_normalized",
                    "content": content,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )

        normalized.__module__ = original.__module__
        with self.in_scope(scope), patch.object(self.fixture, "part", normalized):
            bead: UUID = self.fixture.bead(text, key=key, source=source)
        return bead

    def newer_representation(
        self, text: str, key: str, source: UUID, scope: UUID
    ) -> Any:
        """A sealed, never-materialized newer package for the same occurrence."""
        fixture = self.fixture
        module = __import__("sys").modules[fixture.part.__module__]
        builder = module.build_capture_source_range_command
        checkpoint = fixture.checkpoints[source]
        original = fixture.source
        fixture.source = source
        fixture.offset, fixture.sequence = fixture.positions[source]
        try:
            with (
                self.in_scope(scope),
                patch.object(
                    module,
                    "build_capture_source_range_command",
                    side_effect=lambda **kw: builder(
                        **(kw | {"checkpoint_key": checkpoint})
                    ),
                ),
            ):
                part = fixture.part(text).model_copy(
                    update={
                        "component_key": "orchard.whole",
                        "component_offset": 0,
                        "derivation": "producer_normalized",
                    }
                )
                package = fixture.create(
                    (part,),
                    occurrence_key="orchard." + key,
                    native=NativeFacts(native_id="native." + key),
                    normalization_policy_version="orchard.normalization.v2",
                )
                fixture.append(package, part)
                fixture.seal(package)
            fixture.positions[source] = (fixture.offset, fixture.sequence)
            return package
        finally:
            fixture.source = original

    def invocations(self) -> int:
        return int(self.h.scalar("SELECT count(*) FROM memoriesql_query.invocations"))

    def dispatch_then_die(self, request: dict[str, Any]) -> Any:
        """The host dies as the admitted SELECT is dispatched; returns its reader."""
        leaked: list[Any] = []

        class HostDied(BaseException):
            pass

        class Leaky(psycopg.Connection[Any]):
            # The reader backend keeps an open transaction, as a still-running
            # query would, after its host is gone.
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
        return leaked[0]

    def end_backend(self, pid: int) -> None:
        for _ in range(100):
            if not self.h.scalar(
                "SELECT count(*) FROM pg_stat_activity WHERE pid=%s", (pid,)
            ):
                return
            time.sleep(0.05)
        self.fail("backend did not end")

    def end_owner(self) -> None:
        """Terminate the executor session owning the one reserved delivery,
        then let host recovery settle that delivery as abandoned."""
        key = (
            "hashtextextended(d.tenant_id::text||':query-delivery:'"
            "||d.delivery_ref::text,0)"
        )
        owner = self.h.scalar(
            "SELECT l.pid FROM pg_locks l JOIN memoriesql.query_deliveries d "
            "ON d.state='reserved' WHERE l.locktype='advisory' AND l.granted "
            f"AND l.objsubid=1 AND l.classid::bigint=({key}>>32)&4294967295 "
            f"AND l.objid::bigint={key}&4294967295"
        )
        self.db.execute("SELECT pg_terminate_backend(%s)", (owner,))
        self.end_backend(owner)
        self.assertEqual(self.service().recover_abandoned(), 1)

    def end_reader(self, reader: Any) -> None:
        pid = reader.info.backend_pid
        psycopg.Connection.close(reader)
        self.end_backend(pid)

    def age(self, run_ref: str, by: timedelta, *, results: bool = True) -> None:
        """Move one fictional run, its invocations and its results into the past."""
        shift = f"{int(by.total_seconds())} seconds"
        triggers = (
            ("query_runs", "query_runs_no_update"),
            ("query_result_creations", "query_result_creations_no_update"),
        )
        for table, trigger in triggers:
            self.db.execute(f"ALTER TABLE memoriesql.{table} DISABLE TRIGGER {trigger}")
        try:
            self.db.execute(
                "UPDATE memoriesql.query_runs SET started_at=started_at-%s::interval,"
                "expires_at=expires_at-%s::interval,"
                "default_known_at=default_known_at-%s::interval WHERE run_ref=%s",
                (shift, shift, shift, run_ref),
            )
            self.db.execute(
                "UPDATE memoriesql_query.invocations SET deadline=deadline-%s::interval "
                "WHERE run_ref=%s",
                (shift, run_ref),
            )
            if results:
                self.db.execute(
                    "UPDATE memoriesql.query_result_creations c "
                    "SET created_at=c.created_at-%s::interval,"
                    "expires_at=c.expires_at-%s::interval "
                    "FROM memoriesql.result_preparation_operations o "
                    "WHERE o.tenant_id=c.tenant_id AND o.operation_ref=c.operation_ref "
                    "AND o.run_ref=%s",
                    (shift, shift, run_ref),
                )
        finally:
            for table, trigger in triggers:
                self.db.execute(
                    f"ALTER TABLE memoriesql.{table} ENABLE TRIGGER {trigger}"
                )

    def age_tombstone(self, run_ref: str, by: timedelta) -> None:
        shift = f"{int(by.total_seconds())} seconds"
        triggers = (
            ("query_run_purges", "query_run_purges_no_update"),
            ("query_result_purges", "query_result_purges_no_update"),
        )
        for table, trigger in triggers:
            self.db.execute(f"ALTER TABLE memoriesql.{table} DISABLE TRIGGER {trigger}")
        try:
            for table, _ in triggers:
                self.db.execute(
                    f"UPDATE memoriesql.{table} SET purged_at=purged_at-%s::interval "
                    "WHERE run_ref=%s",
                    (shift, run_ref),
                )
        finally:
            for table, trigger in triggers:
                self.db.execute(
                    f"ALTER TABLE memoriesql.{table} ENABLE TRIGGER {trigger}"
                )

    def retained(self) -> int:
        return int(
            self.h.scalar(
                "SELECT COALESCE(sum(CASE WHEN state='reserved' THEN reservation_bytes "
                "ELSE allocation_bytes END),0) FROM memoriesql.result_preparation_operations"
            )
        )

    def copies(self, needle: str) -> dict[str, int]:
        """Rows of every PR-05 result/access table whose text contains `needle`."""
        found: dict[str, int] = {}
        for table in (
            "memoriesql.query_runs",
            "memoriesql.query_run_closures",
            "memoriesql.query_steps",
            "memoriesql.query_deliveries",
            "memoriesql.query_disclosures",
            "memoriesql.query_visible_refs",
            "memoriesql.query_cursors",
            "memoriesql.query_run_purges",
            "memoriesql.query_result_purges",
            "memoriesql.query_purge_failures",
            "memoriesql.query_result_creations",
            "memoriesql.query_result_identities",
            "memoriesql.result_preparation_operations",
            "memoriesql.result_preparation_parent_holds",
            "memoriesql_query.invocations",
            "memoriesql_query.population_rows",
        ):
            count = int(
                self.h.scalar(
                    f"SELECT count(*) FROM {table} x WHERE strpos(x::text,%s)>0",
                    (needle,),
                )
            )
            if count:
                found[table] = count
        artifacts = int(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE "
                "position(convert_to(%s,'UTF8') IN content_bytes)>0 "
                "OR position(convert_to(%s,'UTF8') IN witness_bytes)>0 "
                "OR position(convert_to(%s,'UTF8') IN dependency_bytes)>0",
                (needle, needle, needle),
            )
        )
        if artifacts:
            found["memoriesql.result_preparation_artifacts"] = artifacts
        return found

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
                "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'revoked',%s,%s,%s)",
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

    def test_paired_agent_cites_a_finding_to_its_retained_source_units(self) -> None:
        # Owner decision (2026-09-28): source.read admits retained unit text
        # through receipted query results; raw bytes, evidence packages and
        # revisiting stay owner-only. Retained text is not exact hydration.
        scope, source_object, _ = self.fixture.remote_scope()
        self.fixture.assertion(scope=scope, source_object=source_object)
        _, agent = self.pair_agent(scope, ["memory.query", "source.read"])
        run = self.start(agent)
        _, reply = self.query(
            run,
            "SELECT o.bead_id,s.statement_id,s.text,ss.evidence_ref,u.source_unit_id,"
            "u.content_sha256,u.text_state,u.search_text,u.package_revision_id "
            "FROM memory_v1.observations o "
            "JOIN memory_v1.statements s ON s.bead_version_id=o.bead_version_id "
            "JOIN memory_v1.statement_sources ss ON ss.statement_id=s.statement_id "
            "JOIN memory_v1.source_units u ON u.source_unit_id=ss.source_unit_id "
            "ORDER BY o.bead_id,s.statement_id,u.source_unit_id",
            page_size=50,
            secret=agent,
        )
        self.assertEqual(reply["outcome"], "available", reply)
        rows = reply["page"]["rows"]
        self.assertTrue(rows)
        for row in rows:
            _, _, _, evidence, unit, digest, state, retained, package = row["values"]
            canonical = self.db.execute(
                "SELECT u.content_hash,u.content_text,m.package_id "
                "FROM memoriesql.source_units u "
                "LEFT JOIN memoriesql.logical_unit_materializations m "
                "ON m.tenant_id=u.tenant_id AND m.source_unit_id=u.source_unit_id "
                "WHERE u.source_unit_id=%s",
                (unit,),
            ).fetchone()
            assert canonical is not None
            # These fixture units are materialized from identity (raw) package
            # parts: the citation binds the pinned package but carries no text.
            self.assertEqual((digest, package), (canonical[0], str(canonical[2])))
            self.assertEqual((state, retained), ("unsupported", None))
            self.assertEqual(
                [ref for ref in row["evidence_refs"] if ref["kind"] == "source"],
                [
                    {
                        "kind": "source",
                        "ref": evidence,
                        "unit_ref": unit,
                        "content_sha256": digest,
                    }
                ],
            )
        # Cited units are hydration-required, and unit text is never exact.
        self.assertTrue(reply["visibility"]["hydration_required"])
        self.assertIn(
            {
                "facet": "source_units.search_text",
                "reason": "normalized_text_not_exact_source",
            },
            reply["result"]["coverage"]["gaps"],
        )
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self.db).begin_context(
                credential_sha256=agent, requested_workspace_id=self.fixture.workspace
            )
            authority = self.db.execute(
                "SELECT memoriesql.current_context_source_authorized("
                "%s,%s,'source.read','read'),"
                "memoriesql.current_context_source_authorized("
                "%s,%s,'source.raw.read','read')",
                (scope, source_object, scope, source_object),
            ).fetchone()
        self.assertEqual(authority, (True, False))

    def test_agent_reads_only_its_pinned_normalized_package_text(self) -> None:
        # Owner decision (2026-09-28, extend to package text): an authorized
        # agent receives the normalized projection of its authorized units from
        # the pinned package only; raw, neighbouring, foreign and newer package
        # content stays out, and reuse ends with the agent's authority.
        scope, source_object, _ = self.fixture.remote_scope()
        marker = "fictional-raw-session-metadata-5150"
        raw = json.dumps({"sessionId": marker, "type": "user", "note": "raw line"})
        served_text = "Fictional normalized turn: the orchard gate opens at dawn."
        foreign_text = "Fictional foreign turn that the agent must never read."
        newer_text = "Fictional newer projection that must never replace the pin."
        served = self.normalized_bead(
            served_text, "normalized-served", raw=raw, source=source_object, scope=scope
        )
        with self.in_scope(scope):
            identity = self.fixture.bead(
                "Fictional identity turn about the barn.",
                key="identity-neighbour",
                source=source_object,
            )
        self.normalized_bead(foreign_text, "normalized-foreign", raw=raw)
        self.newer_representation(newer_text, "normalized-served", source_object, scope)
        pins = {
            bead: (str(unit), str(package), version)
            for bead, unit, package, version in self.db.execute(
                "SELECT m.bead_id,m.source_unit_id,m.package_id,"
                "p.declaration->>'normalization_policy_version' "
                "FROM memoriesql.logical_unit_materializations m "
                "JOIN memoriesql.evidence_packages p ON p.tenant_id=m.tenant_id "
                "AND p.package_id=m.package_id WHERE m.bead_id IN (%s,%s)",
                (served, identity),
            ).fetchall()
        }
        _, agent = self.pair_agent(scope, ["memory.query", "source.read"])
        run = self.start(agent)
        text = (
            "SELECT u.source_unit_id,u.package_revision_id,u.text_state,u.search_text "
            "FROM memory_v1.source_units u ORDER BY u.source_unit_id"
        )
        _, reply = self.query(run, text, page_size=50, secret=agent)
        self.assertEqual(reply["outcome"], "available", reply)
        units = {row["values"][0]: row["values"][1:] for row in reply["page"]["rows"]}
        unit, package, version = pins[served]
        self.assertEqual(units[unit], [package, "available", served_text])
        gaps = reply["result"]["coverage"]["gaps"]
        for reason in (
            "normalized_text_not_exact_source",
            "normalized_projection:" + version,
        ):
            self.assertIn({"facet": "source_units.search_text", "reason": reason}, gaps)
        # The raw (identity) neighbour keeps only its citation; raw bytes, the
        # foreign unit and the newer representation are never delivered.
        neighbour, neighbour_package, _ = pins[identity]
        self.assertEqual(units[neighbour], [neighbour_package, "unsupported", None])
        delivered = json.dumps(reply)
        for text_out in (marker, foreign_text, newer_text):
            self.assertNotIn(text_out, delivered)
        again = self.reuse(run, reply["result"], page_size=50, secret=agent)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["page"]["rows"], reply["page"]["rows"])
        # Saved-result reuse ends with the agent's authority.
        self.db.execute(
            "UPDATE memoriesql.protected_resources "
            "SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source' AND resource_id=%s",
            (source_object,),
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

    def test_hostile_requests_get_closed_refusals_without_side_effects(self) -> None:
        self.fixture.assertion()
        run = self.start()
        request, reply = self.query(run, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(reply["outcome"], "available", reply)
        result = reply["result"]
        operations = "SELECT count(*) FROM memoriesql.result_preparation_operations"
        before = (self.h.scalar(operations), self.invocations())

        def query(**changes: Any) -> dict[str, Any]:
            return {**request, "step_key": str(uuid4()), **changes}

        cases: list[tuple[str, bytes, set[str]]] = [
            ("not json", b"{", {"invalid_request"}),
            ("array", b"[]", {"invalid_request"}),
            ("unknown field", json.dumps(query(extra=1)).encode(), {"invalid_request"}),
            (
                "contract",
                json.dumps(query(contract_version=2)).encode(),
                {"invalid_request"},
            ),
            (
                "page size",
                json.dumps(query(page_size=51)).encode(),
                {"invalid_request"},
            ),
            (
                "offset time",
                json.dumps(
                    query(
                        scope={
                            **request["scope"],
                            "known_at": "2026-09-28T00:00:00+00:00",
                        }
                    )
                ).encode(),
                {"invalid_request"},
            ),
        ]
        for label, text in (
            ("catalog", "SELECT rolname FROM pg_catalog.pg_authid"),
            ("physical", "SELECT payload FROM memoriesql_query.population_rows"),
            ("canonical table", "SELECT bead_id FROM memoriesql.beads"),
            ("dml", "DELETE FROM memory_v1.observations"),
            (
                "cte dml",
                "WITH x AS (DELETE FROM memory_v1.observations RETURNING bead_id) "
                "SELECT bead_id FROM x",
            ),
            (
                "two statements",
                "SELECT bead_id FROM memory_v1.observations; "
                "SELECT bead_id FROM memory_v1.observations",
            ),
            ("function", "SELECT pg_sleep(1)"),
            ("settings", "SELECT current_setting('is_superuser')"),
            ("literal", "SELECT bead_id FROM memory_v1.observations WHERE title='x'"),
        ):
            cases.append(
                (
                    label,
                    json.dumps(query(sql=text)).encode(),
                    {"invalid_request", "unsupported_query"},
                )
            )
        for label, data, allowed in cases:
            with self.subTest(label=label):
                answer = json.loads(self.service().handle(data))
                self.assertIn(answer["outcome"], allowed, answer)
                self.assertTrue(answer["error"]["code"], answer)
                self.assertNotIn("result", answer)
                self.assertNotIn("page", answer)
        self.assertEqual((self.h.scalar(operations), self.invocations()), before)
        # Forged pins and cursors never switch or reveal a result.
        forged = self.reuse(run, {**result, "content_digest": "0" * 64})
        self.assertEqual(forged["outcome"], "unavailable", forged)
        self.assertNotIn("result", forged)
        guessed = self.reuse(run, result, cursor=str(uuid4()))
        self.assertEqual(guessed["outcome"], "invalid_request", guessed)
        self.assertEqual(guessed["error"], {"code": "cursor"})

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
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )

        class Crash(BaseException):
            pass

        def crash(*args: Any, **kwargs: Any) -> bytes:
            raise Crash()

        with patch.object(PostgresAgentSqlResults, "_disclose", crash):
            with self.assertRaises(Crash):
                self.send(request)
        # The owner session is gone, so other work is refused as unsettled.
        _, blocked = self.query(
            run, "SELECT bead_version_id FROM memory_v1.observations"
        )
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
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )

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
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )
        reader = self.dispatch_then_die(request)
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
        _, blocked = self.query(
            run, "SELECT bead_version_id FROM memory_v1.observations"
        )
        self.assertEqual(blocked["error"], {"code": "settlement"}, blocked)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )
        # Only confirmed reader termination lets owned settlement proceed.
        self.end_reader(reader)
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

    def test_commit_refused_after_owner_loss_is_an_execution_error(self) -> None:
        # Trusted-host lane case: the owner session ends after the reader
        # settles and before commit. Recovery abandons the delivery, the commit
        # is refused by its own ownership checks and the single-use preparation
        # is discarded. That is a known execution failure, never unavailable,
        # and the exact redelivery never reruns the query.
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )
        original = PostgresQueryResultCommit.commit

        def owner_lost(commit: Any, ownership: Any, candidate: Any) -> Any:
            self.end_owner()
            return original(
                commit, replace(ownership, ownership_ref=uuid4()), candidate
            )

        with patch.object(PostgresQueryResultCommit, "commit", owner_lost):
            try:
                self.service().handle(json.dumps(request).encode())
            except psycopg.Error:
                pass  # the host's own settlement ran on the terminated owner
        facts = self.db.execute(
            "SELECT (SELECT string_agg(state,',') FROM "
            "memoriesql.result_preparation_operations WHERE run_ref=%s),"
            "(SELECT string_agg(state||':'||outcome,',') FROM "
            "memoriesql.query_deliveries WHERE run_ref=%s),"
            "(SELECT count(*) FROM memoriesql.query_disclosures),"
            "(SELECT state FROM memoriesql.query_steps WHERE run_ref=%s)",
            (run["run_ref"], run["run_ref"], run["run_ref"]),
        ).fetchone()
        self.assertEqual(facts, ("discarded", "settled:abandoned", 0, "executing"))
        invocations = self.invocations()
        again = self.send(request)
        self.assertEqual(
            (again["outcome"], again["error"]),
            ("execution_error", {"code": "database"}),
        )
        self.assertNotIn("result", again)
        self.assertEqual(self.invocations(), invocations)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_result_creations"), 0
        )

    def test_late_commit_after_owner_loss_keeps_the_result_for_redelivery(
        self,
    ) -> None:
        # Trusted-host lane case: the owner session ends after the reader
        # settles and recovery abandons the delivery, but the host's issuer is
        # alive and its commit succeeds. The committed result is immutable: the
        # host replies settlement_pending and the exact redelivery discloses
        # that result without rerunning the query.
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )
        original = PostgresQueryResultCommit.commit

        def owner_lost(commit: Any, ownership: Any, candidate: Any) -> Any:
            self.end_owner()
            return original(commit, ownership, candidate)

        with patch.object(PostgresQueryResultCommit, "commit", owner_lost):
            late = json.loads(self.service().handle(json.dumps(request).encode()))
        self.assertEqual(
            (late["outcome"], late["error"]),
            ("settlement_pending", {"code": "settlement"}),
        )
        self.assertNotIn("result", late)
        self.assertNotIn("page", late)
        facts = self.db.execute(
            "SELECT (SELECT string_agg(state,',') FROM "
            "memoriesql.result_preparation_operations WHERE run_ref=%s),"
            "(SELECT count(*) FROM memoriesql.query_result_creations),"
            "(SELECT string_agg(state||':'||outcome,',') FROM "
            "memoriesql.query_deliveries WHERE run_ref=%s),"
            "(SELECT count(*) FROM memoriesql.query_disclosures)",
            (run["run_ref"], run["run_ref"]),
        ).fetchone()
        self.assertEqual(facts, ("sealed", 1, "settled:abandoned", 0))
        committed = self.h.scalar(
            "SELECT result_id::text FROM memoriesql.query_result_creations"
        )
        invocations = self.invocations()
        again = self.send(request)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["result"]["result_id"], committed)
        self.assertNotEqual(again["access_receipt_ref"], late["access_receipt_ref"])
        self.assertEqual(self.invocations(), invocations)
        _, fresh = self.query(run, "SELECT bead_version_id FROM memory_v1.observations")
        self.assertEqual(fresh["outcome"], "available", fresh)

    def test_close_refuses_while_run_work_is_unsettled(self) -> None:
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )

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
                "source_units.trust_label",
                "source_units.search_text",
            ],
        )

    def test_expiry_cleanup_purges_content_copies_and_then_the_tombstone(self) -> None:
        self.fixture.assertion()
        marker = "fictional-cleanup-marker-7731"
        run = self.start()
        request, reply = self.query(
            run,
            "SELECT o.bead_id AS cleanup_probe_column,s.text FROM memory_v1.observations o "
            "JOIN memory_v1.statements s ON s.bead_version_id=o.bead_version_id "
            "WHERE s.text=$1 OR s.bead_id=o.bead_id ORDER BY o.bead_id,s.statement_id",
            parameters=[{"position": 1, "type": "text", "value": marker}],
            page_size=1,
        )
        self.assertEqual(reply["outcome"], "available", reply)
        result = reply["result"]
        following = self.reuse(run, result, cursor=reply["page"]["next_cursor"])
        self.assertEqual(following["outcome"], "available", following)
        bead = reply["page"]["rows"][0]["values"][0]
        fingerprint = self.h.scalar(
            "SELECT request_fingerprint FROM memoriesql.query_steps "
            "WHERE run_ref=%s AND step_key=%s",
            (run["run_ref"], request["step_key"]),
        )
        needles = (
            marker,
            "cleanup_probe_column",
            result["content_digest"],
            fingerprint,
            bead,
        )
        for needle in needles:
            self.assertTrue(self.copies(needle), needle)
        charged = self.retained()
        self.assertGreater(charged, 8192)
        idle = json.loads(self.service().cleanup_expired())
        self.assertEqual(
            idle["cleanup"]["purged"], {"runs": 0, "results": 0, "tombstones": 0}
        )

        # Run expiry purges the run's own copies; the live result stays reusable.
        self.age(run["run_ref"], timedelta(minutes=31), results=False)
        ran = json.loads(self.service().cleanup_expired())
        self.assertEqual(
            ran["cleanup"]["purged"], {"runs": 1, "results": 0, "tombstones": 0}
        )
        for table, column in (
            ("query_steps", "request_fingerprint"),
            ("query_steps", "content_digest"),
            ("query_deliveries", "response_sha256"),
        ):
            self.assertEqual(
                self.h.scalar(
                    f"SELECT count({column}) FROM memoriesql.{table} WHERE run_ref=%s",
                    (run["run_ref"],),
                ),
                0,
            )
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.query_visible_refs WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            0,
        )
        later = self.start()
        reused = self.reuse(later, result)
        self.assertEqual(reused["outcome"], "available", reused)

        # Result expiry purges the body and every sensitive copy, leaving only
        # the noncontent tombstone; retained allocation drops to the journal.
        self.age(run["run_ref"], timedelta(days=30))
        self.age(later["run_ref"], timedelta(days=30))
        purged = json.loads(self.service().cleanup_expired())
        self.assertEqual(
            purged["cleanup"]["purged"], {"runs": 1, "results": 1, "tombstones": 0}
        )
        for needle in needles:
            self.assertEqual(self.copies(needle), {}, needle)
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
            ),
            0,
        )
        self.assertEqual(self.retained(), 8192)
        step = self.h.scalar(
            "SELECT jsonb_build_object('state',state,'result_id',result_id,"
            "'purged',purged_at IS NOT NULL) FROM memoriesql.query_steps "
            "WHERE run_ref=%s AND step_key=%s",
            (run["run_ref"], request["step_key"]),
        )
        self.assertEqual(
            step,
            {"state": "complete", "result_id": result["result_id"], "purged": True},
        )
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries "
                "WHERE charged_db_ms IS NOT NULL AND response_sha256 IS NULL"
            ),
            self.h.scalar("SELECT count(*) FROM memoriesql.query_deliveries"),
        )
        gone = self.reuse(self.start(), result)
        self.assertEqual(gone["outcome"], "unavailable", gone)
        status = json.loads(self.service().cleanup_status())["cleanup"]
        self.assertEqual(status["overdue"], {"runs": 0, "results": 0, "tombstones": 0})
        self.assertEqual(status["admission"], "open")
        self.assertIsNotNone(status["last_purged_at"])

        # Thirty days after its last purge the tombstone and its charge go too.
        self.age_tombstone(run["run_ref"], timedelta(days=31))
        expired = json.loads(self.service().cleanup_expired())
        self.assertEqual(expired["cleanup"]["purged"]["tombstones"], 1)
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.query_runs WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            0,
        )
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations "
                "WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            0,
        )
        self.assertEqual(self.retained(), 0)

    def test_cleanup_reclaims_a_stranded_reservation_after_run_expiry(self) -> None:
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )
        self.end_reader(self.dispatch_then_die(request))
        self.assertEqual(self.service().recover_abandoned(), 1)
        # Nobody redelivers the lost step, so its reservation stays charged.
        self.assertGreater(self.retained(), 8192)
        self.age(run["run_ref"], timedelta(minutes=31))
        cleaned = json.loads(self.service().cleanup_expired())
        self.assertEqual(
            cleaned["cleanup"]["purged"], {"runs": 1, "results": 0, "tombstones": 0}
        )
        self.assertEqual(self.retained(), 8192)
        self.assertEqual(
            self.h.scalar(
                "SELECT state FROM memoriesql.result_preparation_operations "
                "WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            "discarded",
        )
        self.assertEqual(self.invocations(), 0)
        self.assertEqual(
            self.h.scalar(
                "SELECT outcome FROM memoriesql.query_steps WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            "execution_error",
        )

    def test_interrupted_cleanup_leaves_nothing_partial_and_restart_completes(
        self,
    ) -> None:
        self.fixture.assertion()
        run = self.start()
        for text in (
            "SELECT bead_id FROM memory_v1.observations",
            "SELECT bead_version_id FROM memory_v1.observations",
        ):
            _, reply = self.query(run, text)
            self.assertEqual(reply["outcome"], "available", reply)
        self.age(run["run_ref"], timedelta(days=31))
        # One committed batch; runs come first.
        first = json.loads(self.service().cleanup_expired(batch_size=1, max_batches=1))
        self.assertEqual(
            first["cleanup"]["purged"], {"runs": 1, "results": 0, "tombstones": 0}
        )
        artifacts = "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
        # The next pass dies before commit: nothing it did persists.
        victim = psycopg.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"], dbname=self.db.info.dbname
            )
        )
        pid = victim.info.backend_pid
        try:
            outcome = victim.execute(
                "SELECT memoriesql.purge_expired_query_state_v1(8)"
            ).fetchone()
            self.assertEqual(outcome[0]["purged"]["results"], 2)  # type: ignore[index]
            self.db.execute("SELECT pg_terminate_backend(%s)", (pid,))
        finally:
            try:
                victim.close()
            except psycopg.Error:
                pass
        self.end_backend(pid)
        self.assertEqual(self.h.scalar(artifacts), 2)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_result_purges"), 0
        )
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_purges"), 1
        )
        restarted = json.loads(self.service().cleanup_expired())
        self.assertEqual(
            restarted["cleanup"]["purged"], {"runs": 0, "results": 2, "tombstones": 0}
        )
        self.assertEqual(self.h.scalar(artifacts), 0)

    def test_failed_cleanup_is_visible_refuses_runs_and_recovers(self) -> None:
        self.fixture.assertion()
        run = self.start()
        _, reply = self.query(run, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(reply["outcome"], "available", reply)
        self.age(run["run_ref"], timedelta(days=32))
        self.db.execute(
            "CREATE FUNCTION memoriesql.fictional_cleanup_fault() RETURNS trigger "
            "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'fictional fault'; END$$"
        )
        self.db.execute(
            "REVOKE ALL ON FUNCTION memoriesql.fictional_cleanup_fault() FROM PUBLIC"
        )
        self.db.execute(
            "CREATE TRIGGER fictional_cleanup_fault BEFORE DELETE ON "
            "memoriesql.result_preparation_artifacts FOR EACH ROW "
            "EXECUTE FUNCTION memoriesql.fictional_cleanup_fault()"
        )
        failed = json.loads(self.service().cleanup_expired())
        self.assertEqual(failed["cleanup"]["purged"]["results"], 0)
        self.assertEqual(failed["cleanup"]["failed"], 1)
        status = json.loads(self.service().cleanup_status())["cleanup"]
        self.assertEqual(status["failures"]["count"], 1)
        self.assertEqual(status["failures"]["last_error_code"], "P0001")
        self.assertEqual(status["overdue"]["results"], 1)
        self.assertEqual(status["admission"], "blocked")
        refused = json.loads(self.service().start_run())
        self.assertEqual(
            (refused["outcome"], refused["error"]),
            ("budget_exhausted", {"code": "settlement"}),
        )
        self.db.execute(
            "DROP TRIGGER fictional_cleanup_fault ON memoriesql.result_preparation_artifacts"
        )
        self.db.execute("DROP FUNCTION memoriesql.fictional_cleanup_fault()")
        # The run start's own pass completes the overdue cleanup first.
        self.start()
        status = json.loads(self.service().cleanup_status())["cleanup"]
        self.assertEqual(status["failures"]["count"], 0)
        self.assertEqual(status["overdue"], {"runs": 0, "results": 0, "tombstones": 0})
        self.assertEqual(status["admission"], "open")

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
