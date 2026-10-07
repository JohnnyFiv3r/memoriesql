"""Fictional public query/reuse acceptance; no workload-fit or quality claim."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

import memoriesql.infrastructure.postgres.agent_sql_results as results_module
from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import AGENT_RELATION_TABLES, POLICY_HASH
from memoriesql.application.evidence_packages import NativeFacts
from memoriesql.application.relation_inspection import InspectBeadRelationsV2
from memoriesql.application.relation_lifecycle import (
    RecordRelationEvent,
    RelationAction,
)
from memoriesql.application.stored_bead_inspection import (
    InspectStoredBead,
    ReadStoredBeadEvidence,
    StoredEvidenceSelection,
)
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
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
)
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.relation_sql_population import (
    prepare_query_population,
)
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_query_result_commit as commit_tests
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
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
        migrate(self.db, expected_current_version=34, target_version=43)
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
        known_at: str | None = None,
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
                "known_at": known_at or run["default_known_at"],
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

    def pair_agent(
        self, scope: UUID, capabilities: list[str], label: str = ""
    ) -> tuple[UUID, str]:
        """A paired agent through the canonical pairing path, never the owner."""
        return self.pair_agent_over([scope], capabilities, label)

    def pair_agent_over(
        self, scopes: list[UUID], capabilities: list[str], label: str = ""
    ) -> tuple[UUID, str]:
        """One pairing over several scopes, with a read grant on each."""
        grant = uuid4()
        secret = hashlib.sha256(
            ("fictional paired agent " + label + ",".join(capabilities)).encode()
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
                    scopes,
                    secret,
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                ),
            )
            for scope in scopes:
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

    # -- owner decisions 6 and 8 of 2026-10-06: inspection withholds as queries
    # do, and a denial leaves the trace an unknown note leaves --

    def inspector(self) -> PostgresStoredBeadInspection:
        return PostgresStoredBeadInspection(
            self.db,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
        )

    def audit_rows(self) -> int:
        count: int = self.h.scalar(
            "SELECT count(*) FROM memoriesql.authorization_audit_events"
        )
        return count

    def test_inspection_withholds_what_the_population_withholds(self) -> None:
        # Owner decision 8: inspection and queries withhold alike, in the
        # published inspection record shape. A note whose correction the reader
        # cannot read is withheld by the query population, its whole family.
        # Inspection now withholds it exactly as a note that does not exist.
        _, target, _ = self.fixture.assertion()
        hidden_source = self.fixture.add_source("inspection-hidden-correction")
        local = self.fixture.source
        self.fixture.source = hidden_source
        try:
            self.fixture.correction(target)
        finally:
            self.fixture.source = local
        reader = self.inspector()
        self.assertIsNotNone(reader.inspect(InspectStoredBead(bead_id=target)).bead)
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',"
            "revoked_at=clock_timestamp() WHERE resource_id=%s",
            (hidden_source,),
        )
        run = self.start()
        _, reply = self.query(
            run, "SELECT o.bead_id FROM memory_v1.observations o", page_size=50
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertNotIn(
            str(target), {row["values"][0] for row in reply["page"]["rows"]}
        )
        hidden = reader.inspect(InspectStoredBead(bead_id=target))
        missing = reader.inspect(InspectStoredBead(bead_id=uuid4()))
        self.assertEqual(hidden, missing)
        self.assertIsNone(hidden.bead)

    def test_a_late_inspection_denial_leaves_the_trace_of_an_unknown_note(
        self,
    ) -> None:
        # Pairs 3 and 4 (owner decision 6): a note denied late, after its first
        # sources were authorized, wrote persistent "allowed" audit rows that an
        # unknown note never writes. Here one statement also cites a unit of a
        # second source of the same scope, as a multi-unit note can (attached
        # by the test administrator), and that source is revoked. The denial
        # now leaves exactly the unknown note's trace: none.
        _, target, _ = self.fixture.assertion()
        other_source = self.fixture.add_source("inspection-late-denial")
        other = self.fixture.bead(
            "Fictional second source: the cellar was dry.",
            key="inspection-late-" + uuid4().hex[:8],
            source=other_source,
        )
        with self.db.transaction():
            self.db.execute("SET LOCAL session_replication_role=replica")
            self.db.execute(
                "INSERT INTO memoriesql.bead_semantic_statement_evidence "
                "SELECT s.tenant_id,s.workspace_id,s.access_scope_id,s.statement_id,"
                "u.event_id,u.source_unit_id,u.content_hash,clock_timestamp() "
                "FROM memoriesql.bead_semantic_statements s "
                "JOIN memoriesql.beads o ON o.bead_id=%s "
                "JOIN memoriesql.source_units u ON u.source_unit_id=o.source_unit_id "
                "WHERE s.bead_id=%s ORDER BY s.statement_sequence LIMIT 1",
                (other, target),
            )
        reader = self.inspector()
        self.assertIsNotNone(reader.inspect(InspectStoredBead(bead_id=target)).bead)
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',"
            "revoked_at=clock_timestamp() WHERE resource_id=%s",
            (other_source,),
        )
        before = self.audit_rows()
        denied = reader.inspect(InspectStoredBead(bead_id=target))
        denied_rows = self.audit_rows() - before
        before = self.audit_rows()
        missing = reader.inspect(InspectStoredBead(bead_id=uuid4()))
        missing_rows = self.audit_rows() - before
        self.assertEqual(denied, missing)
        self.assertEqual(denied_rows, missing_rows, (denied_rows, missing_rows))

    def test_an_unreadable_oversized_note_never_reports_its_size(self) -> None:
        # Pair 4 (owner decision 6): inspection decided its statement budget
        # before it authorized the statements' sources, so a note the reader
        # cannot read reported budget_exhausted where an unknown note reports
        # unavailable. Here the note carries 33 statements under its watermark,
        # as a full note does after one correction, and its first statement
        # names a context unit of a second source, which is revoked. (The test
        # administrator attaches both; each copied statement carries the first
        # statement's own evidence, as every statement must.) The population's
        # own reads never consult context, so this isolates the order of
        # authorization.
        _, target, _ = self.fixture.assertion()
        other_source = self.fixture.add_source("inspection-oversized")
        other = self.fixture.bead(
            "Fictional second source: the barn was painted.",
            key="inspection-oversized-" + uuid4().hex[:8],
            source=other_source,
        )
        with self.db.transaction():
            self.db.execute("SET LOCAL session_replication_role=replica")
            self.db.execute(
                "WITH head AS (SELECT * FROM memoriesql.bead_semantic_statements "
                "WHERE bead_id=%s ORDER BY statement_sequence LIMIT 1), "
                "top AS (SELECT max(statement_sequence) sequence FROM "
                "memoriesql.bead_semantic_statements WHERE bead_id=%s), "
                "copies AS (INSERT INTO memoriesql.bead_semantic_statements("
                "tenant_id,workspace_id,access_scope_id,statement_id,bead_id,"
                "bead_version_id,event_id,source_unit_id,statement_sequence,"
                "statement_kind,statement_text,context_source_ids,"
                "authored_by_principal_id,semantic_task_id,semantic_attempt_id,"
                "semantic_run_id,created_at) "
                "SELECT h.tenant_id,h.workspace_id,h.access_scope_id,"
                "gen_random_uuid(),h.bead_id,h.bead_version_id,h.event_id,"
                "h.source_unit_id,top.sequence+n,h.statement_kind,"
                "h.statement_text||' (copy '||n||')',h.context_source_ids,"
                "h.authored_by_principal_id,h.semantic_task_id,"
                "h.semantic_attempt_id,h.semantic_run_id,h.created_at "
                "FROM head h, top, generate_series(1,32) n "
                "RETURNING tenant_id,workspace_id,access_scope_id,statement_id) "
                "INSERT INTO memoriesql.bead_semantic_statement_evidence("
                "tenant_id,workspace_id,access_scope_id,statement_id,"
                "evidence_event_id,evidence_source_unit_id,evidence_content_hash,"
                "linked_at) "
                "SELECT c.tenant_id,c.workspace_id,c.access_scope_id,c.statement_id,"
                "x.evidence_event_id,x.evidence_source_unit_id,"
                "x.evidence_content_hash,x.linked_at "
                "FROM copies c CROSS JOIN head h "
                "JOIN memoriesql.bead_semantic_statement_evidence x "
                "ON x.tenant_id=h.tenant_id AND x.statement_id=h.statement_id",
                (target, target),
            )
            self.db.execute(
                "UPDATE memoriesql.bead_statement_revisions r SET "
                "statement_watermark=(SELECT max(statement_sequence) FROM "
                "memoriesql.bead_semantic_statements WHERE bead_id=%s) "
                "FROM memoriesql.accepted_bead_semantics a "
                "WHERE a.bead_id=%s AND r.bead_version_id=a.bead_version_id",
                (target, target),
            )
            self.db.execute(
                "UPDATE memoriesql.bead_semantic_statements s SET "
                "context_source_ids=ARRAY[o.source_unit_id] "
                "FROM memoriesql.beads o WHERE o.bead_id=%s AND s.bead_id=%s "
                "AND s.statement_sequence=(SELECT min(statement_sequence) FROM "
                "memoriesql.bead_semantic_statements WHERE bead_id=%s)",
                (other, target, target),
            )
        reader = self.inspector()
        # A reader of the whole note learns that it is over budget.
        self.assertEqual(
            reader.inspect(InspectStoredBead(bead_id=target)).outcome,
            "budget_exhausted",
        )
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',"
            "revoked_at=clock_timestamp() WHERE resource_id=%s",
            (other_source,),
        )
        hidden = reader.inspect(InspectStoredBead(bead_id=target))
        missing = reader.inspect(InspectStoredBead(bead_id=uuid4()))
        self.assertEqual(hidden, missing)
        self.assertEqual(hidden.outcome, "unavailable")

    def test_a_refused_evidence_read_leaves_the_trace_of_an_unknown_note(
        self,
    ) -> None:
        # Pair 3 for the evidence reader (CLI `source`): a read refused after
        # the note's own inspection succeeded kept that inspection's "allowed"
        # audit rows, which a read of an unknown note never writes. Here the
        # selection names a package that supports nothing in the note.
        _, target, _ = self.fixture.assertion()
        reader = self.inspector()
        self.assertIsNotNone(reader.inspect(InspectStoredBead(bead_id=target)).bead)
        selection = StoredEvidenceSelection(
            package_id=uuid4(), inventory_sha256="0" * 64, part_ordinal=0, limit=8
        )
        before = self.audit_rows()
        refused = reader.read(
            ReadStoredBeadEvidence(bead_id=target, selection=selection)
        )
        refused_rows = self.audit_rows() - before
        before = self.audit_rows()
        missing = reader.read(
            ReadStoredBeadEvidence(bead_id=uuid4(), selection=selection)
        )
        missing_rows = self.audit_rows() - before
        self.assertEqual(refused.outcome, "unavailable")
        self.assertEqual(refused, missing)
        self.assertEqual(refused_rows, missing_rows, (refused_rows, missing_rows))

    def relations_of(self, bead: UUID, secret: str | None = None) -> Any:
        return PostgresRelationAssessments(
            self.db,
            credential_sha256=secret or self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
        ).inspect_relations(InspectBeadRelationsV2(bead_id=bead))

    def test_a_late_relation_inspection_denial_leaves_the_trace_of_an_unknown_bead(
        self,
    ) -> None:
        # Pair 3 for relation inspection (CLI `relations`): a relation's target
        # lies in a second source, which is revoked. Inspecting the relation's
        # source passed that bead's own source, writing "allowed" audit rows,
        # and was then denied on the target. The rows persisted, where an
        # unknown bead leaves none; now the denial leaves none either.
        fixture = self.fixture
        tag = uuid4().hex[:8]
        other_source = fixture.add_source("relation-late-" + tag)
        source = fixture.bead(
            "Fictional source: the ledger balanced.",
            "Fictional second observation: every crate was counted.",
            key="relation-late-source-" + tag,
        )
        target = fixture.bead(
            "Fictional target: the audit passed.",
            key="relation-late-target-" + tag,
            source=other_source,
        )
        fixture.activate_relations(source, (target,), key="relation-late-" + tag)
        fixture.propose(
            lambda packet: [
                fixture.proposal(
                    packet,
                    "supports",
                    fixture.endpoint(packet, source, 0),
                    fixture.endpoint(packet, target, 0),
                    basis="agent_inferred",
                )
            ]
        )
        fixture.run_relations()
        self.assertEqual(self.relations_of(source).outcome, "available")
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',"
            "revoked_at=clock_timestamp() WHERE resource_id=%s",
            (other_source,),
        )
        before = self.audit_rows()
        denied = self.relations_of(source)
        denied_rows = self.audit_rows() - before
        before = self.audit_rows()
        missing = self.relations_of(uuid4())
        missing_rows = self.audit_rows() - before
        self.assertEqual(denied.outcome, "unavailable")
        self.assertEqual(denied, missing)
        self.assertEqual(denied_rows, missing_rows, (denied_rows, missing_rows))

    def test_relation_inspection_needs_the_whole_task_of_a_relation_it_shows(
        self,
    ) -> None:
        # Owner decision 7 in inspection, as it already holds. An assessed
        # relation's endpoints and basis beads are pinned to its task, the task
        # disposes of every pinned pair, and inspection authorizes both beads of
        # each pair it lists. So a raw reader of the endpoints' scope never reads
        # the relation's text by inspecting its target either.
        case = self.relation_quoting_a_hidden_candidate()
        row = self.db.execute(
            "SELECT target_bead_id FROM memoriesql.assessed_relations "
            "WHERE relation_id=%s",
            (case["relation_id"],),
        ).fetchone()
        assert row is not None
        self.assertIn(case["text"], self.relations_of(row[0]).model_dump_json())
        _, reader = self.fixture.second_human(case["ends"])
        self.assertEqual(
            self.relations_of(row[0], reader), self.relations_of(uuid4(), reader)
        )

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
        # AM-5: the fixture relation's whole closure lies in the agent's scope, so
        # the agent reads it as the owner does, with no relation gap.
        _, relations = self.query(run, count, secret=agent)
        self.assertEqual(relations["outcome"], "available", relations)
        self.assertEqual(
            relations["page"]["rows"][0]["values"],
            owner_relations["page"]["rows"][0]["values"],
        )
        self.assertEqual(relations["result"]["coverage"]["gaps"], [])
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

    # AM-5 relation reads: the six readable tables, compared on stable columns
    # (evidence_ref is a per-result private ref and differs between results).
    RELATION_READS = {
        "assessed_relations": (
            "SELECT r.relation_id,r.type_key,r.type_revision,r.source_bead_id,"
            "r.source_bead_version_id,r.target_bead_id,r.target_bead_version_id,"
            "r.basis,r.rationale,r.author_confidence,r.state,r.support_eligible,"
            "r.support_reason,r.correction_pending,r.roots_status,"
            "r.independent_root_count FROM memory_v1.assessed_relations r "
            "ORDER BY r.relation_id"
        ),
        "relation_statements": (
            "SELECT s.relation_id,s.role,s.statement_id,s.bead_id,s.text "
            "FROM memory_v1.relation_statements s "
            "ORDER BY s.relation_id,s.role,s.statement_id"
        ),
        "relation_evidence": (
            "SELECT e.relation_id,e.statement_id,e.source_unit_id,e.content_sha256,"
            "e.roots_status FROM memory_v1.relation_evidence e "
            "ORDER BY e.relation_id,e.statement_id,e.source_unit_id"
        ),
        "relation_types": (
            "SELECT t.type_key,t.type_revision,t.label,t.definition "
            "FROM memory_v1.relation_types t ORDER BY t.type_key,t.type_revision"
        ),
    }
    TYPES = "SELECT t.type_key,t.type_revision FROM memory_v1.relation_types t"
    # Every column of each of the nine relation tables, in catalog order.
    ALL_RELATION_COLUMNS = {
        "assessed_relations": (
            "relation_id",
            "task_id",
            "type_key",
            "type_revision",
            "source_bead_id",
            "source_bead_version_id",
            "target_bead_id",
            "target_bead_version_id",
            "basis",
            "rationale",
            "qualification",
            "author_confidence",
            "author_run_ref",
            "specialist_run_ref",
            "acceptance_receipt_id",
            "recorded_at",
            "state",
            "head_token",
            "support_eligible",
            "support_reason",
            "correction_pending",
            "roots_status",
            "independent_root_count",
        ),
        "relation_statements": (
            "relation_id",
            "role",
            "statement_id",
            "bead_id",
            "bead_version_id",
            "text",
        ),
        "relation_evidence": (
            "relation_id",
            "statement_id",
            "source_unit_id",
            "content_sha256",
            "evidence_ref",
            "roots_status",
        ),
        "relation_events": (
            "event_id",
            "relation_id",
            "action",
            "related_relation_id",
            "reason",
            "origin",
            "authoring_bead_id",
            "effective_at",
            "recorded_at",
            "event_number",
            "previous_event_id",
            "recorded_by_principal_id",
            "recorded_by_user_id",
            "idempotency_receipt_id",
        ),
        "relation_event_evidence": (
            "event_id",
            "statement_id",
            "source_unit_id",
            "bead_id",
            "bead_version_id",
            "statement_text",
            "content_sha256",
            "evidence_ref",
        ),
        "relation_types": (
            "type_key",
            "type_revision",
            "namespace",
            "label",
            "definition",
            "endpoint_rule",
            "forward_reading",
            "inverse_reading",
            "symmetric",
            "evidence_expectation",
            "example",
            "counterexample",
            "cycle_policy",
        ),
        "relation_pairs": (
            "task_id",
            "first_bead_id",
            "second_bead_id",
            "first_bead_version_id",
            "second_bead_version_id",
            "disposition",
            "abstention",
            "reason",
        ),
        "relation_corrections": (
            "relation_id",
            "role",
            "correcting_bead_id",
            "correcting_bead_version_id",
        ),
        "relation_replacements": ("relation_id", "replacement_relation_id"),
    }
    # How many leading columns of each are its catalog key.
    RELATION_KEYS = {
        "assessed_relations": 1,
        "relation_statements": 3,
        "relation_evidence": 3,
        "relation_events": 1,
        "relation_event_evidence": 3,
        "relation_types": 2,
        "relation_pairs": 3,
        "relation_corrections": 3,
        "relation_replacements": 2,
    }

    def every_relation_row(
        self, run: dict[str, Any], *, secret: str | None = None
    ) -> dict[str, tuple[list[list[Any]], str]]:
        """Each relation table read whole, every column: its rows and the whole
        reply as JSON."""
        seen: dict[str, tuple[list[list[Any]], str]] = {}
        for table, columns in self.ALL_RELATION_COLUMNS.items():
            listed = ",".join("t." + column for column in columns)
            keys = ",".join("t." + c for c in columns[: self.RELATION_KEYS[table]])
            text = f"SELECT {listed} FROM memory_v1.{table} t ORDER BY {keys}"
            _, reply = self.query(run, text, page_size=50, secret=secret)
            self.assertEqual(reply["outcome"], "available", reply)
            self.assertFalse(reply["page"]["has_more"], table)
            seen[table] = (
                [row["values"] for row in reply["page"]["rows"]],
                json.dumps(reply),
            )
        return seen

    def close(self, run: dict[str, Any], secret: str | None = None) -> None:
        closed = json.loads(self.service(secret).close_run(run["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)

    def supports_types(self, run: dict[str, Any], secret: str) -> int:
        """How many 'supports' type pins the caller receives (type_key is a typed
        reference, so it is not filtered with a text parameter)."""
        _, reply = self.query(run, self.TYPES, page_size=50, secret=secret)
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertEqual(reply["result"]["coverage"]["gaps"], [])
        return sum(1 for row in reply["page"]["rows"] if row["values"][0] == "supports")

    def home_agent(self, capabilities: list[str]) -> tuple[UUID, str]:
        """A paired agent over its own explicit scope; the owner-private default
        scope admits no grants to other principals."""
        home, _, _ = self.fixture.remote_scope()
        return self.pair_agent(home, capabilities, label="home")

    def paired_principal(self, grant: UUID) -> UUID:
        principal: UUID = self.h.scalar(
            "SELECT paired_principal_id FROM memoriesql.pairing_grants "
            "WHERE pairing_grant_id=%s",
            (grant,),
        )
        return principal

    def relation_rows(
        self,
        run: dict[str, Any],
        relation: UUID,
        *,
        secret: str | None = None,
        known_at: str | None = None,
    ) -> dict[str, list[list[Any]]]:
        """Each readable relation table's rows for one relation, as a caller sees them."""
        seen: dict[str, list[list[Any]]] = {}
        for table, text in self.RELATION_READS.items():
            _, reply = self.query(
                run, text, page_size=50, secret=secret, known_at=known_at
            )
            self.assertEqual(reply["outcome"], "available", reply)
            self.assertEqual(reply["result"]["coverage"]["gaps"], [], table)
            rows = [row["values"] for row in reply["page"]["rows"]]
            # Type pins are keyed by type; the fixture relation is 'supports'.
            key = "supports" if table == "relation_types" else str(relation)
            seen[table] = [r for r in rows if r[0] == key]
        return seen

    def test_paired_agent_reads_a_real_assessed_relation_under_am5(self) -> None:
        # AM-5: memory.query and source.read over the relation's whole disclosed
        # closure read its six tables with the owner's values. Lifecycle history
        # and pair coverage stay owner-only; raw provenance never appears.
        grant, agent = self.home_agent(
            ["memory.inspect", "memory.query", "source.read"]
        )
        relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.paired_principal(grant)
        )
        owner_run, run = self.start(), self.start(agent)
        owner = self.relation_rows(owner_run, relation.relation_id)
        mine = self.relation_rows(run, relation.relation_id, secret=agent)
        self.assertEqual(len(owner["assessed_relations"]), 1)
        self.assertEqual(mine, owner)
        state = mine["assessed_relations"][0]
        self.assertEqual(state[1], "supports")
        self.assertEqual(state[7], "agent_inferred")
        self.assertEqual(state[10], "active")
        self.assertEqual(
            {row[1] for row in mine["relation_statements"]}, {"source", "target"}
        )
        self.assertTrue(mine["relation_evidence"])
        # Pair coverage (and lifecycle history) stays with the owner.
        pairs = "SELECT count(*) AS n FROM memory_v1.relation_pairs p"
        _, owner_pairs = self.query(owner_run, pairs)
        self.assertNotEqual(owner_pairs["page"]["rows"][0]["values"], ["0"])
        _, my_pairs = self.query(run, pairs, secret=agent)
        self.assertEqual(my_pairs["outcome"], "available", my_pairs)
        self.assertEqual(my_pairs["page"]["rows"][0]["values"], ["0"])
        self.assertEqual(
            my_pairs["result"]["coverage"]["gaps"],
            [{"facet": "relation_history", "reason": "owner_only"}],
        )
        # No source-object identity (a root identity) appears anywhere.
        objects = {
            str(row[0])
            for row in self.db.execute(
                "SELECT source_object_id FROM memoriesql.source_objects"
            ).fetchall()
        }
        self.assertTrue(objects)
        _, whole = self.query(
            run, self.RELATION_READS["assessed_relations"], page_size=50, secret=agent
        )
        # Every column of every readable relation table, as the agent receives it.
        catalog = SqlCatalog.installed()
        for table in sorted(AGENT_RELATION_TABLES):
            columns = ",".join("t." + c.name for c in catalog.relations[table].columns)
            _, reply = self.query(
                run, f"SELECT {columns} FROM {table} t", page_size=50, secret=agent
            )
            self.assertEqual(reply["outcome"], "available", reply)
            disclosed = json.dumps(reply)
            self.assertFalse([o for o in objects if o in disclosed], table)
        # The inspection reader stays owner-only.
        with self.fixture.connection() as connection:
            port = PostgresRelationAssessments(
                connection, credential_sha256=agent, workspace_id=self.fixture.workspace
            )
            inspected = port.inspect_relations(
                InspectBeadRelationsV2(bead_id=relation.source_bead_id)
            )
        self.assertEqual(inspected.outcome, "unavailable")
        # One closure member lapses: the relation is absent from a fresh step of
        # the same run, with nothing disclosed about it; the saved result ends.
        self.assertEqual(self.supports_types(run, agent), 1)
        relation_agents.revoke_member(self.db, self.fixture, relation)
        after = self.relation_rows(run, relation.relation_id, secret=agent)
        self.assertEqual(
            after,
            {
                "assessed_relations": [],
                "relation_statements": [],
                "relation_evidence": [],
                "relation_types": [],
            },
        )
        self.assertEqual(self.supports_types(run, agent), 0)
        again = self.reuse(run, whole["result"], page_size=50, secret=agent)
        self.assertEqual(again["outcome"], "unavailable", again)
        self.assertNotIn("result", again)
        # The source endpoint stays readable on its own.
        _, observations = self.query(
            run,
            "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id",
            page_size=50,
            secret=agent,
        )
        self.assertIn(
            str(relation.source_bead_id),
            {row["values"][0] for row in observations["page"]["rows"]},
        )

    def test_agent_relation_needs_its_whole_closure_and_current_grants(self) -> None:
        # A workspace admits two active runs, so each caller's run is closed after use.
        caps = ["memory.inspect", "memory.query", "source.read"]
        grant, agent = self.home_agent(caps)
        relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.paired_principal(grant)
        )
        # Another agent with the same capabilities but only the source's scope:
        # the relation is withheld whole, with no gap, count or type pin, while
        # the source endpoint itself stays readable.
        _, partial = self.pair_agent(relation.source_scope, caps, label="partial")
        run = self.start(partial)
        self.assertEqual(
            self.relation_rows(run, relation.relation_id, secret=partial),
            {
                "assessed_relations": [],
                "relation_statements": [],
                "relation_evidence": [],
                "relation_types": [],
            },
        )
        self.assertEqual(self.supports_types(run, partial), 0)
        _, seen = self.query(
            run,
            "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id",
            page_size=50,
            secret=partial,
        )
        self.assertIn(
            str(relation.source_bead_id),
            {row["values"][0] for row in seen["page"]["rows"]},
        )
        self.close(run, partial)
        # Without source.read, every relation table is withheld and says so.
        # memory.query over every closure member, but no source.read. This is
        # withheld by bead-version authorization too (M0011 already requires
        # source.read on each version's event), so it does not isolate the AM-5
        # gate; the closure-member cases below exercise that gate per member.
        _, blind = self.pair_agent_over(
            [relation.source_scope, relation.remote_scope],
            ["memory.inspect", "memory.query"],
            label="blind",
        )
        run = self.start(blind)
        _, reply = self.query(
            run, self.RELATION_READS["assessed_relations"], secret=blind
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertEqual(reply["page"]["rows"], [])
        self.assertEqual(
            reply["result"]["coverage"]["gaps"],
            [{"facet": "relation_tables", "reason": "source_read_required"}],
        )
        self.close(run, blind)
        # The full-closure agent reads it until its remote grant expires.
        run = self.start(agent)
        self.assertEqual(
            len(
                self.relation_rows(run, relation.relation_id, secret=agent)[
                    "assessed_relations"
                ]
            ),
            1,
        )
        ends = datetime.now(UTC) + timedelta(seconds=2)
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,1,%s,'active',%s,%s,%s)",
                (
                    relation.grants["remote_target"],
                    ["read"],
                    self.fixture.now,
                    ends,
                    self.fixture.now,
                ),
            )
        time.sleep(max(0.0, (ends - datetime.now(UTC)).total_seconds()) + 0.5)
        self.assertEqual(
            self.relation_rows(run, relation.relation_id, secret=agent)[
                "assessed_relations"
            ],
            [],
        )

    # -- owner decision 7 of 2026-10-06: a relation's author text needs its whole
    # task (read-path audit candidate 1) --

    def relation_quoting_a_hidden_candidate(self) -> dict[str, Any]:
        """A relation-assessment task pins its subject, its target and a third
        candidate. The fictional author quotes that candidate in the accepted
        relation's qualification. Both endpoints lie in one explicit scope, and
        the candidate in another."""
        fixture, db = self.fixture, self.db
        tag = uuid4().hex[:8]
        (ends, ends_source), (hidden_scope, hidden_source) = [
            relation_agents.explicit_scope(db, fixture) for _ in range(2)
        ]
        text = (
            f"Fictional hidden candidate {tag}: the west orchard flooded on day nine."
        )
        source = fixture.remote_bead(
            "Fictional source: the orchard ledger balanced.",
            "Fictional second observation: every crate was counted.",
            key="task-text-source-" + tag,
            scope=ends,
            source=ends_source,
        )
        target = fixture.remote_bead(
            "Fictional target: the orchard audit passed.",
            key="task-text-target-" + tag,
            scope=ends,
            source=ends_source,
        )
        hidden = fixture.remote_bead(
            text,
            key="task-text-hidden-" + tag,
            scope=hidden_scope,
            source=hidden_source,
        )
        fixture.activate_relations(source, (target, hidden), key="task-text-" + tag)
        fixture.propose(
            lambda packet: [
                fixture.proposal(
                    packet,
                    "supports",
                    fixture.endpoint(packet, source, 0),
                    fixture.endpoint(packet, target, 0),
                    basis="agent_inferred",
                    qualification="Unless " + text,
                )
            ]
        )
        fixture.run_relations()
        row = db.execute(
            "SELECT relation_id FROM memoriesql.assessed_relations "
            "WHERE source_bead_id=%s AND target_bead_id=%s",
            (source, target),
        ).fetchone()
        assert row is not None, "the fictional relation was not accepted"
        return {
            "relation_id": str(row[0]),
            "text": text,
            "ends": ends,
            "hidden_scope": hidden_scope,
        }

    def relation_text_seen(
        self, case: dict[str, Any], secret: str | None
    ) -> tuple[int, list[str]]:
        """How many rows of the relation the caller reads, and which relation
        tables' replies carry the hidden candidate's text."""
        run = self.start(secret)
        seen = self.every_relation_row(run, secret=secret)
        self.close(run, secret)
        rows = len(
            [r for r in seen["assessed_relations"][0] if r[0] == case["relation_id"]]
        )
        carriers = sorted(
            table for table, (_, reply) in seen.items() if case["text"] in reply
        )
        return rows, carriers

    def test_a_relation_is_withheld_from_an_agent_that_cannot_read_its_whole_task(
        self,
    ) -> None:
        # Owner decision 7 of 2026-10-06: the author wrote a relation's rationale
        # and qualification from its task's whole packet. An agent reads the
        # relation, text and all, only if it can read every bead pinned to that
        # task. Reading both endpoints is not enough, and the relation is
        # withheld whole.
        case = self.relation_quoting_a_hidden_candidate()
        caps = ["memory.inspect", "memory.query", "source.read"]
        _, partial = self.pair_agent_over(
            [case["ends"]], caps, label="task-text-partial"
        )
        self.assertEqual(self.relation_text_seen(case, partial), (0, []))
        _, whole = self.pair_agent_over(
            [case["ends"], case["hidden_scope"]], caps, label="task-text-whole"
        )
        rows, carriers = self.relation_text_seen(case, whole)
        self.assertEqual(rows, 1)
        self.assertIn("assessed_relations", carriers)

    def test_a_relation_is_withheld_from_an_owner_mode_reader_of_part_of_its_task(
        self,
    ) -> None:
        # The same rule in owner mode: a raw-read holder granted only the
        # endpoints' scope reads nothing of the relation; the owner, who reads
        # the whole task, reads it with its text.
        case = self.relation_quoting_a_hidden_candidate()
        _, reader = self.fixture.second_human(case["ends"])
        self.assertEqual(self.relation_text_seen(case, reader), (0, []))
        rows, carriers = self.relation_text_seen(case, None)
        self.assertEqual(rows, 1)
        self.assertIn("assessed_relations", carriers)

    @staticmethod
    def distinctive(values: list[Any]) -> set[str]:
        """The values that identify a relation's records: identifiers, hashes
        and authored text, never short enum words or numbers."""
        found: set[str] = set()
        for value in values:
            text = str(value) if value is not None else ""
            if len(text) >= 20 and not text.isdigit():
                found.add(text)
        return found

    def test_withheld_and_blind_agents_read_no_relation_value_anywhere(self) -> None:
        # PR-06's #73 host case 2 as an installed test (F3). An agent missing one
        # closure member, and an agent without source.read, each read all nine
        # relation tables, every column. Neither reply carries any value of the
        # relation anywhere; withholding adds no gap the full-closure agent does
        # not also get; reusing a result an agent has lost replies exactly as an
        # unknown result does; and agents with the same readable scopes get the
        # same frame digests.
        caps = ["memory.inspect", "memory.query", "source.read"]
        grant, agent = self.home_agent(caps)
        relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.paired_principal(grant)
        )
        rid = str(relation.relation_id)
        owner_run = self.start()
        owner = self.every_relation_row(owner_run)
        self.close(owner_run)
        mine = [r for r in owner["assessed_relations"][0] if r[0] == rid]
        self.assertEqual(len(mine), 1)
        task = mine[0][1]
        relation_values: set[str] = set()
        for table, (rows, _) in owner.items():
            if table == "relation_types":
                continue
            for row in rows:
                cells = [str(v) for v in row]
                if rid in cells or task in cells:
                    relation_values |= self.distinctive(row)
        self.assertIn(rid, relation_values)

        def own_reads(run: dict[str, Any], secret: str) -> str:
            """What the caller may read on its own: its observation family."""
            catalog = SqlCatalog.installed()
            texts = []
            for table in (
                "observations",
                "statements",
                "statement_sources",
                "source_units",
            ):
                name = "memory_v1." + table
                columns = ",".join(
                    "t." + c.name for c in catalog.relations[name].columns
                )
                _, reply = self.query(
                    run, f"SELECT {columns} FROM {name} t", page_size=50, secret=secret
                )
                self.assertEqual(reply["outcome"], "available", reply)
                texts.append(json.dumps(reply["page"]["rows"]))
            return "".join(texts)

        def gaps(seen: dict[str, tuple[list[list[Any]], str]]) -> dict[str, Any]:
            return {
                table: json.loads(reply)["result"]["coverage"]["gaps"]
                for table, (_, reply) in seen.items()
            }

        full_run = self.start(agent)
        full = self.every_relation_row(full_run, secret=agent)
        self.assertEqual(
            [r for r in full["assessed_relations"][0] if r[0] == rid][0], mine[0]
        )
        _, saved = self.query(
            full_run,
            self.RELATION_READS["assessed_relations"],
            page_size=50,
            secret=agent,
        )
        self.close(full_run, agent)
        _, partial = self.pair_agent(relation.source_scope, caps, label="partial-f3")
        _, blind = self.pair_agent_over(
            [relation.source_scope, relation.remote_scope],
            ["memory.inspect", "memory.query"],
            label="blind-f3",
        )
        for name, secret in (("partial", partial), ("blind", blind)):
            with self.subTest(caller=name):
                run = self.start(secret)
                seen = self.every_relation_row(run, secret=secret)
                legitimate = own_reads(run, secret)
                self.close(run, secret)
                hidden = {v for v in relation_values if v not in legitimate}
                self.assertIn(rid, hidden)
                for table, (rows, reply) in seen.items():
                    if table != "relation_types":
                        self.assertEqual(rows, [], table)
                    for value in hidden:
                        self.assertNotIn(value, reply, table)
                if name == "partial":
                    # Withholding adds no gap the full-closure agent lacks.
                    self.assertEqual(gaps(seen), gaps(full))
                else:
                    for table, found in gaps(seen).items():
                        self.assertIn(
                            {
                                "facet": "relation_tables",
                                "reason": "source_read_required",
                            },
                            found,
                            table,
                        )
        # Same readable scopes, same frame: the same frame digests.
        _, twin = self.pair_agent_over(
            [relation.source_scope, relation.remote_scope], caps, label="twin-f3"
        )
        first_run = self.start(agent)
        _, first = self.query(
            first_run,
            self.RELATION_READS["assessed_relations"],
            page_size=50,
            secret=agent,
        )
        self.close(first_run, agent)
        twin_run = self.start(twin)
        _, second = self.query(
            twin_run,
            self.RELATION_READS["assessed_relations"],
            page_size=50,
            secret=twin,
            known_at=first["result"]["frame"]["known_at"],
        )
        self.close(twin_run, twin)
        for field in ("snapshot_digest", "projection_manifest_sha256"):
            self.assertEqual(
                second["result"]["frame"][field], first["result"]["frame"][field], field
            )
        # A lost result's reuse refusal is byte-identical to an unknown result's,
        # apart from the per-request fields.
        relation_agents.revoke_member(self.db, self.fixture, relation)
        run = self.start(agent)
        lost = self.reuse(run, saved["result"], page_size=50, secret=agent)
        unknown = self.reuse(
            run,
            {"result_id": str(uuid4()), "content_digest": "0" * 64},
            page_size=50,
            secret=agent,
        )
        self.close(run, agent)
        per_request = {"step_key", "receipt_ref", "access_receipt_ref", "remaining"}

        def shape(reply: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in reply.items() if k not in per_request}

        self.assertEqual(lost["outcome"], "unavailable", lost)
        self.assertEqual(shape(lost), shape(unknown))
        self.assertEqual(sorted(lost), sorted(unknown))

    def test_agent_relation_lifecycle_matches_the_owner_frame_by_frame(self) -> None:
        grant, agent = self.home_agent(
            ["memory.inspect", "memory.query", "source.read"]
        )
        relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.paired_principal(grant)
        )
        source, rid = relation.source_bead_id, relation.relation_id

        def states(
            owner_run: dict[str, Any], run: dict[str, Any], known_at: str | None = None
        ) -> list[Any]:
            owner = self.relation_rows(owner_run, rid, known_at=known_at)
            mine = self.relation_rows(run, rid, secret=agent, known_at=known_at)
            # One projection: the agent sees exactly the owner's state at the frame.
            self.assertEqual(mine["assessed_relations"], owner["assessed_relations"])
            row: list[Any] = mine["assessed_relations"][0]
            return row

        disputed = self.fixture.governance.record_event(
            self.fixture.lifecycle_command(source, rid, "dispute", "am5-dispute")
        )
        self.assertIsNotNone(disputed.relation_event_id)
        owner_run, run = self.start(), self.start(agent)
        disputed_at = run["default_known_at"]
        row = states(owner_run, run)
        self.assertEqual((row[10], row[11], row[12]), ("disputed", False, "disputed"))
        self.close(owner_run)
        self.close(run, agent)
        retracted = self.fixture.governance.record_event(
            self.fixture.lifecycle_command(source, rid, "retract", "am5-retract")
        )
        self.assertIsNotNone(retracted.relation_event_id)
        owner_run, run = self.start(), self.start(agent)
        row = states(owner_run, run)
        self.assertEqual((row[10], row[11], row[12]), ("retracted", False, "retracted"))
        # As of the disputed frame, both still see the dispute, never the retraction.
        row = states(owner_run, run, known_at=disputed_at)
        self.assertEqual((row[10], row[11], row[12]), ("disputed", False, "disputed"))
        # History stays owner-only: the owner sees both events, the agent a gap.
        events = "SELECT count(*) AS n FROM memory_v1.relation_events v"
        _, owner_events = self.query(owner_run, events)
        self.assertEqual(owner_events["page"]["rows"][0]["values"], ["2"])
        _, my_events = self.query(run, events, secret=agent)
        self.assertEqual(my_events["page"]["rows"][0]["values"], ["0"])
        self.assertEqual(
            my_events["result"]["coverage"]["gaps"],
            [{"facet": "relation_history", "reason": "owner_only"}],
        )

    def test_agent_relation_reads_stay_inside_their_tenant(self) -> None:
        grant, agent = self.home_agent(
            ["memory.inspect", "memory.query", "source.read"]
        )
        relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.paired_principal(grant)
        )
        workspace, _, owner, _ = self.fixture.second_tenant()
        scope = uuid4()
        # A paired agent of the second tenant, with the same capabilities over its
        # own scope, reads in the agent mode and sees none of the first tenant.
        stranger = hashlib.sha256(b"fictional second-tenant paired agent").hexdigest()
        principal, now = uuid4(), datetime.now(UTC)
        capabilities = ["memory.inspect", "memory.query", "source.read"]
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self.db).begin_context(
                credential_sha256=owner, requested_workspace_id=workspace
            )
            # Owner-private scopes admit no grants to other principals.
            self.db.execute(
                "SELECT memoriesql.create_access_scope(%s,%s,'explicit',%s,%s)",
                (scope, uuid4(), "Fictional second orchard agents", now),
            )
            self.db.execute(
                "SELECT memoriesql.pair_local_client("
                "%s,%s,%s,%s,'agent','paired_agent',%s,%s,%s,%s,%s)",
                (
                    principal,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    capabilities,
                    [scope],
                    stranger,
                    now,
                    now + timedelta(hours=1),
                ),
            )
            self.db.execute(
                "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                (uuid4(), principal, scope, ["read"], now, now + timedelta(hours=1)),
            )
        other = PostgresAgentSqlResults(
            control_factory=self.fixture.connection,
            reader_factory=self.reader_connection,
            authority_profile=self.profile,
            credential_sha256=stranger,
            workspace_id=workspace,
        )
        started = json.loads(other.start_run())
        self.assertEqual(started["outcome"], "available", started)
        request = {
            "contract_version": 1,
            "run_ref": started["run"]["run_ref"],
            "step_key": str(uuid4()),
            "kind": "query",
            "catalog_hash": SqlCatalog.installed().hash,
            "sql": self.RELATION_READS["assessed_relations"],
            "parameters": [],
            "inputs": [],
            "parents": [],
            "scope": {
                "source_refs": [],
                "known_at": started["run"]["default_known_at"],
                "view": "historical",
            },
            "intent": "enumerate",
            "max_result_bytes": 64 * 1024 * 1024,
            "page_size": 50,
        }
        reply = json.loads(other.handle(json.dumps(request).encode()))
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertEqual(reply["page"]["rows"], [])
        self.assertEqual(reply["result"]["coverage"]["gaps"], [])
        self.assertNotIn(str(relation.relation_id), json.dumps(reply))
        # The first tenant's agent credential opens nothing in the second.
        foreign = PostgresAgentSqlResults(
            control_factory=self.fixture.connection,
            reader_factory=self.reader_connection,
            authority_profile=self.profile,
            credential_sha256=agent,
            workspace_id=workspace,
        )
        self.assertEqual(json.loads(foreign.start_run())["outcome"], "unavailable")

    def relation_in(
        self, subject: UUID, candidates: tuple[UUID, ...], key: str, build: Any
    ) -> UUID:
        """One accepted assessed relation from a fresh activation; its ID."""
        known = "SELECT relation_id FROM memoriesql.assessed_relations"
        before = {row[0] for row in self.db.execute(known).fetchall()}
        self.fixture.activate_relations(subject, candidates, key=key)
        self.fixture.propose(build)
        self.fixture.run_relations()
        (created,) = {row[0] for row in self.db.execute(known).fetchall()} - before
        relation: UUID = created
        return relation

    @staticmethod
    def citing(proposal: dict[str, Any], statements: list[str]) -> dict[str, Any]:
        """Keep only these statements' evidence; any 1 to 8 may be cited."""
        proposal["evidence"] = [
            e for e in proposal["evidence"] if e["statement_id"] in statements
        ]
        return proposal

    def test_a_relation_whose_replacement_is_withheld_is_withheld_too(self) -> None:
        # Review P1. X, W and Z live in their own scopes, and W is derived from
        # Z. A (X supports W) cites only X's evidence. B replaces A and cites
        # W's, whose root lineage reaches Z. An agent that reads X and W but not
        # Z cannot read B, so it must not read A either: superseded, with B in
        # relation_replacements, A would disclose that the hidden B exists.
        fixture = self.fixture
        tag = uuid4().hex[:8]
        scopes = [relation_agents.explicit_scope(self.db, fixture) for _ in range(3)]
        (sx, ox), (sw, ow), (sz, oz) = scopes
        x = fixture.remote_bead(
            "Fictional X: the orchard ledger balanced.",
            key="x-" + tag,
            scope=sx,
            source=ox,
        )
        w = fixture.remote_bead(
            "Fictional W: the audit relied on the ledger.",
            key="w-" + tag,
            scope=sw,
            source=ow,
        )
        z = fixture.remote_bead(
            "Fictional Z: the ledger's first draft.",
            key="z-" + tag,
            scope=sz,
            source=oz,
        )
        derived = self.relation_in(
            w,
            (z,),
            "derived-" + tag,
            lambda p: [
                fixture.proposal(
                    p,
                    "derived_from",
                    fixture.endpoint(p, w, 0),
                    fixture.endpoint(p, z, 0),
                    basis="agent_inferred",
                )
            ],
        )

        def supports(p: Any, cited: UUID, **extra: Any) -> list[dict[str, Any]]:
            proposal = fixture.proposal(
                p,
                "supports",
                fixture.endpoint(p, x, 0),
                fixture.endpoint(p, w, 0),
                basis="agent_inferred",
                **extra,
            )
            return [
                self.citing(proposal, fixture.endpoint(p, cited, 0)["statement_ids"])
            ]

        first = self.relation_in(x, (w,), "first-" + tag, lambda p: supports(p, x))
        retires = dict(
            relation_kind="assessed",
            relation_id=str(first),
            reason="Fictional exact replacement.",
        )
        second = self.relation_in(
            x, (w,), "second-" + tag, lambda p: supports(p, w, retires=retires)
        )
        _, agent = self.pair_agent_over(
            [sx, sw], ["memory.inspect", "memory.query", "source.read"], label="no-z"
        )
        owner_run, run = self.start(), self.start(agent)
        reads = dict(self.RELATION_READS)
        reads["relation_replacements"] = (
            "SELECT p.relation_id,p.replacement_relation_id "
            "FROM memory_v1.relation_replacements p "
            "ORDER BY p.relation_id,p.replacement_relation_id"
        )
        # The owner reads all three, with A superseded by B.
        _, owner_replaced = self.query(
            owner_run, reads["relation_replacements"], page_size=50
        )
        self.assertIn(
            [str(first), str(second)],
            [row["values"] for row in owner_replaced["page"]["rows"]],
        )
        _, owner_relations = self.query(
            owner_run, reads["assessed_relations"], page_size=50
        )
        states = {
            row["values"][0]: row["values"][10]
            for row in owner_relations["page"]["rows"]
        }
        self.assertEqual(states[str(first)], "superseded")
        self.assertIn(str(second), states)
        self.assertIn(str(derived), states)
        # The agent reads none of them, in any table, and no replacement row.
        for table, text in reads.items():
            _, reply = self.query(run, text, page_size=50, secret=agent)
            self.assertEqual(reply["outcome"], "available", reply)
            self.assertEqual(reply["result"]["coverage"]["gaps"], [], table)
            disclosed = json.dumps(reply["page"])
            for relation in (first, second, derived):
                self.assertNotIn(str(relation), disclosed, table)
        self.assertEqual(self.supports_types(run, agent), 0)
        self.close(owner_run)
        self.close(run, agent)

    def reader_without_z(self) -> tuple[UUID, UUID, UUID, UUID, str]:
        """Review P1's X, W and Z, and a raw-read holder that cannot read Z.

        A (X supports W) cites only X. B replaces A and cites W, whose root
        lineage reaches Z through a derived_from relation. The reader holds
        source.raw.read, so it reads in owner mode, but its authority covers X
        and W only. Returns A, B, the derived relation, Z and the reader's secret.
        """
        fixture = self.fixture
        tag = uuid4().hex[:8]
        scopes = [relation_agents.explicit_scope(self.db, fixture) for _ in range(3)]
        (sx, ox), (sw, ow), (sz, oz) = scopes
        x = fixture.remote_bead(
            "Fictional X: the orchard ledger balanced.",
            key="x-" + tag,
            scope=sx,
            source=ox,
        )
        w = fixture.remote_bead(
            "Fictional W: the audit relied on the ledger.",
            key="w-" + tag,
            scope=sw,
            source=ow,
        )
        z = fixture.remote_bead(
            "Fictional Z: the ledger's first draft.",
            key="z-" + tag,
            scope=sz,
            source=oz,
        )
        derived = self.relation_in(
            w,
            (z,),
            "derived-" + tag,
            lambda p: [
                fixture.proposal(
                    p,
                    "derived_from",
                    fixture.endpoint(p, w, 0),
                    fixture.endpoint(p, z, 0),
                    basis="agent_inferred",
                )
            ],
        )

        def supports(p: Any, cited: UUID, **extra: Any) -> list[dict[str, Any]]:
            proposal = fixture.proposal(
                p,
                "supports",
                fixture.endpoint(p, x, 0),
                fixture.endpoint(p, w, 0),
                basis="agent_inferred",
                **extra,
            )
            return [
                self.citing(proposal, fixture.endpoint(p, cited, 0)["statement_ids"])
            ]

        first = self.relation_in(x, (w,), "first-" + tag, lambda p: supports(p, x))
        retires = dict(
            relation_kind="assessed",
            relation_id=str(first),
            reason="Fictional exact replacement.",
        )
        second = self.relation_in(
            x, (w,), "second-" + tag, lambda p: supports(p, w, retires=retires)
        )
        # A raw-read holder whose authority covers X and W but not Z. In the
        # shipped roles only the owner holds source.raw.read; a deployment can
        # grant it to a service role, as here.
        self.db.execute(
            "INSERT INTO memoriesql.role_capabilities "
            "VALUES('background_service','source.raw.read') ON CONFLICT DO NOTHING"
        )
        reader = hashlib.sha256(b"fictional raw reader without z").hexdigest()
        principal = uuid4()
        with self.db.transaction():
            fixture.begin()
            self.db.execute(
                "SELECT memoriesql.pair_local_client("
                "%s,%s,%s,%s,'service','background_service',%s,%s,%s,%s,%s)",
                (
                    principal,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    ["memory.query", "source.read", "source.raw.read"],
                    [sx, sw],
                    reader,
                    fixture.now,
                    fixture.now + timedelta(hours=1),
                ),
            )
            for scope in (sx, sw):
                self.db.execute(
                    "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        principal,
                        scope,
                        ["read"],
                        fixture.now,
                        fixture.now + timedelta(hours=1),
                    ),
                )
        return first, second, derived, z, reader

    def test_an_owner_mode_reader_never_sees_a_relation_it_cannot_read(self) -> None:
        # Owner decision 5b: owner mode is not permission to bypass an evidence
        # restriction. The reader cannot read B, so it must not read A either.
        # Superseded, with B in relation_replacements, A would disclose that
        # the unreadable B exists. All nine relation tables are read, every
        # column, so no identifier, task or lifecycle state of the three
        # relations, and nothing of Z, reaches the reader.
        first, second, derived, z, reader = self.reader_without_z()
        withheld = {str(first), str(second), str(derived)}
        owner_run, run = self.start(), self.start(reader)
        owner = self.every_relation_row(owner_run)

        def column(table: str, name: str) -> int:
            return self.ALL_RELATION_COLUMNS[table].index(name)

        # The owner reads all three relations, their tasks' pair coverage and
        # their lifecycle: A superseded by B, with its lifecycle events.
        relations = {
            row[0]: row for row in owner["assessed_relations"][0] if row[0] in withheld
        }
        self.assertEqual(set(relations), withheld)
        state = column("assessed_relations", "state")
        self.assertEqual(relations[str(first)][state], "superseded")
        self.assertIn([str(first), str(second)], owner["relation_replacements"][0])
        events = [row for row in owner["relation_events"][0] if row[1] in withheld]
        self.assertTrue(events)
        tasks = {row[1] for row in relations.values()}
        self.assertEqual(len(tasks), 3)
        self.assertLessEqual(tasks, {row[0] for row in owner["relation_pairs"][0]})
        receipt = column("assessed_relations", "acceptance_receipt_id")
        idempotency = column("relation_events", "idempotency_receipt_id")
        hidden = (
            withheld
            | tasks
            | {row[receipt] for row in relations.values() if row[receipt]}
            | {row[0] for row in events}
            | {row[idempotency] for row in events if row[idempotency]}
            | {str(z)}
        )
        # The owner-mode reader without Z reads none of it, in any table:
        # no row about these relations, their tasks or their lifecycle, and
        # none of their identifiers anywhere in a reply.
        mine = self.every_relation_row(run, secret=reader)
        for table, (rows, reply) in mine.items():
            if table != "relation_types":
                self.assertEqual(rows, [], table)
            for value in hidden:
                self.assertNotIn(value, reply, table)
        self.close(owner_run)
        self.close(run, reader)

    def test_an_owner_mode_reader_sees_no_pair_coverage_of_a_relation_it_cannot_read(
        self,
    ) -> None:
        # Owner decision 5b, for pair coverage: a task that recorded a relation
        # withheld from the reader discloses none of its pairs, which would
        # show that the task assessed and proposed the withheld relation.
        first, second, derived, _, reader = self.reader_without_z()
        tasks = {
            str(row[0])
            for row in self.db.execute(
                "SELECT task_id FROM memoriesql.assessed_relations"
                " WHERE relation_id = ANY(%s)",
                ([first, second, derived],),
            ).fetchall()
        }
        pairs = (
            "SELECT p.task_id,p.first_bead_id,p.second_bead_id "
            "FROM memory_v1.relation_pairs p "
            "ORDER BY p.task_id,p.first_bead_id,p.second_bead_id"
        )
        owner_run, run = self.start(), self.start(reader)
        _, owner_pairs = self.query(owner_run, pairs, page_size=50)
        self.assertLessEqual(
            tasks, {row["values"][0] for row in owner_pairs["page"]["rows"]}
        )
        _, reader_pairs = self.query(run, pairs, page_size=50, secret=reader)
        self.assertEqual(reader_pairs["outcome"], "available", reader_pairs)
        self.assertFalse(
            tasks & {row["values"][0] for row in reader_pairs["page"]["rows"]}
        )
        self.close(owner_run)
        self.close(run, reader)

    def hidden_history(
        self, action: RelationAction = "confirm"
    ) -> tuple[str, Callable[[], None]]:
        """A paired agent, and owner-only history that changes no row it reads.

        B is authored with a derived_from relation to C, and the assessed R
        cites only B, so R's root lineage reaches C through that authored
        relation. An authored lifecycle event on it, `confirm`, `retract` or
        `supersede`, recorded after the agent's frame, is owner-only history.
        """
        fixture = self.fixture
        tag = uuid4().hex[:8]
        (sb, ob), (sc, oc), (st, ot) = [
            relation_agents.explicit_scope(self.db, fixture) for _ in range(3)
        ]
        c = fixture.remote_bead(
            "Fictional C: the ledger's first count.",
            key="c-" + tag,
            scope=sc,
            source=oc,
        )
        ids: dict[str, str] = {}
        with self.in_scope(sb):
            b = fixture.author(
                "Fictional B: the audit drew on the first count.",
                "b-" + tag,
                candidates=(c,),
                plan=lambda extras, bead, found: ids.setdefault(
                    "lineage", fixture.relate(extras, bead, found[0], "derived_from")
                ),
                source=ob,
            )
        lineage = UUID(ids["lineage"])
        replacement: UUID | None = None
        if action == "supersede":
            # A second authored derived_from relation into C, to replace the
            # lineage with. It lies outside R's own lineage.
            with self.in_scope(sb):
                fixture.author(
                    "Fictional B2: a second reading of the first count.",
                    "b2-" + tag,
                    candidates=(c,),
                    plan=lambda extras, bead, found: ids.setdefault(
                        "replacement",
                        fixture.relate(extras, bead, found[0], "derived_from"),
                    ),
                    source=ob,
                )
            replacement = UUID(ids["replacement"])
        t = fixture.remote_bead(
            "Fictional T: the audit's finding.", key="t-" + tag, scope=st, source=ot
        )
        self.relation_in(
            b,
            (t,),
            "r-" + tag,
            lambda p: [
                self.citing(
                    fixture.proposal(
                        p,
                        "supports",
                        fixture.endpoint(p, b, 0),
                        fixture.endpoint(p, t, 0),
                        basis="agent_inferred",
                    ),
                    fixture.endpoint(p, b, 0)["statement_ids"],
                )
            ],
        )
        _, agent = self.pair_agent_over(
            [sb, sc, st],
            ["memory.inspect", "memory.query", "source.read"],
            label="digest-" + tag,
        )

        def confirm() -> None:
            fixture.lifecycle.record_relation_event(
                RecordRelationEvent(
                    idempotency_key=f"orchard.{action}." + tag,
                    relation_id=lineage,
                    action=action,
                    replacement_relation_id=replacement,
                    reason="Fictional owner review.",
                    expected_last_event_id=fixture.latest_relation_event(lineage),
                )
            )

        return agent, confirm

    HIDDEN_HISTORY_QUERY = (
        "SELECT r.relation_id,r.state,r.roots_status,r.independent_root_count "
        "FROM memory_v1.assessed_relations r ORDER BY r.relation_id"
    )

    def test_an_agents_frame_digests_ignore_history_it_cannot_see(self) -> None:
        # Owner decision 6. No row the agent receives changes, so the digests in
        # its reply must not change either, while the protected manifest its
        # results bind to does.
        fixture = self.fixture
        agent, confirm = self.hidden_history()
        query = self.HIDDEN_HISTORY_QUERY

        def read() -> tuple[list[Any], dict[str, Any], str]:
            # A run reads as of its start, so each read is a new run.
            run = self.start(agent)
            _, reply = self.query(run, query, page_size=50, secret=agent)
            self.assertEqual(reply["outcome"], "available", reply)
            self.close(run, agent)
            with prepare_query_population(
                self.db,
                credential_sha256=agent,
                workspace_id=fixture.workspace,
                known_at=None,
                view="resolved",
                byte_budget=64 * 1024 * 1024,
            ) as population:
                protected = population.dependency_manifest_sha256
            rows = [row["values"] for row in reply["page"]["rows"]]
            return rows, reply["result"]["frame"], protected

        rows, frame, protected = read()
        self.assertTrue(rows)
        confirm()
        after, after_frame, after_protected = read()
        self.assertEqual(after, rows)
        # The protected record the results bind to did change.
        self.assertNotEqual(after_protected, protected)
        for field in ("snapshot_digest", "projection_manifest_sha256"):
            self.assertEqual(after_frame[field], frame[field], field)

    def test_an_agents_content_digest_hashes_nothing_it_cannot_see(self) -> None:
        # Owner decision 6 (Codex P1 on #78). The reply's content_digest is the
        # SHA-256 of the sealed body, so that body holds only what the caller
        # may see. The protected manifest and the witness, which commit to
        # records the caller may not read, are bound in the internal partitions.
        agent, confirm = self.hidden_history()

        def sealed() -> tuple[dict[str, Any], str, tuple[str, str]]:
            run = self.start(agent)
            request, reply = self.query(
                run, self.HIDDEN_HISTORY_QUERY, page_size=50, secret=agent
            )
            self.assertEqual(reply["outcome"], "available", reply)
            result = reply["result"]
            # The same immutable result reaches the agent fresh, by exact
            # redelivery of its step, and reused from a later run.
            again = self.send(request, secret=agent)
            self.close(run, agent)
            later = self.start(agent)
            reused = self.reuse(later, result, page_size=50, secret=agent)
            self.close(later, agent)
            replies = (reply, again, reused)
            for each in replies:
                self.assertEqual(each["outcome"], "available", each)
                self.assertEqual(
                    (each["result"]["result_id"], each["result"]["content_digest"]),
                    (result["result_id"], result["content_digest"]),
                )
                self.assertEqual(each["result"]["frame"], result["frame"])
            row = self.db.execute(
                "SELECT content_bytes,witness_bytes,dependency_bytes "
                "FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                (UUID(result["result_id"]),),
            ).fetchone()
            assert row is not None
            content, witness, dependencies = (bytes(part) for part in row)
            self.assertEqual(
                hashlib.sha256(content).hexdigest(), result["content_digest"]
            )
            manifest = base64.b64decode(json.loads(dependencies)["manifest_base64"])
            hidden = (
                hashlib.sha256(manifest).hexdigest(),
                hashlib.sha256(witness).hexdigest(),
            )
            # No disclosed field of any of the three replies carries either.
            for each in replies:
                disclosed = json.dumps(each)
                for value in hidden:
                    self.assertNotIn(value, disclosed)
            return json.loads(content), content.decode("utf-8"), hidden

        before, before_text, before_hidden = sealed()
        confirm()
        after, after_text, after_hidden = sealed()
        # The protected manifest moved on history the agent cannot read.
        self.assertNotEqual(after_hidden[0], before_hidden[0])
        for body, text, hidden in (
            (before, before_text, before_hidden),
            (after, after_text, after_hidden),
        ):
            for value in hidden:
                self.assertNotIn(value, text)
            self.assertNotIn("witness_sha256", body)
            for field in ("snapshot_digest", "projection_manifest_sha256"):
                self.assertNotIn(field, body["frame"])
        for field in ("snapshot_digest", "projection_manifest_sha256"):
            self.assertEqual(
                after["wire_frame"][field], before["wire_frame"][field], field
            )

    def test_an_agents_saved_result_survives_history_it_cannot_see(self) -> None:
        # The owner's refinement (2026-10-06): a reused result is re-authorized
        # for the reusing caller, and hidden history never decides what it is
        # served. An owner-only confirm on R's root lineage changes no row the
        # agent receives, so the agent's saved result stays reusable.
        agent, confirm = self.hidden_history()
        run = self.start(agent)
        _, reply = self.query(
            run, self.HIDDEN_HISTORY_QUERY, page_size=50, secret=agent
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.close(run, agent)
        confirm()
        later = self.start(agent)
        again = self.reuse(later, reply["result"], page_size=50, secret=agent)
        self.close(later, agent)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["page"]["rows"], reply["page"]["rows"])

    def test_an_agents_saved_result_survives_a_retraction_it_cannot_see(self) -> None:
        # John, 2026-10-06: "Add the retract and replace probes." An owner-only
        # retraction of R's root-lineage relation, recorded after the agent's
        # result was saved, does not decide what the agent is served: its reuse
        # stays available with the same rows.
        agent, retract = self.hidden_history("retract")
        run = self.start(agent)
        _, reply = self.query(
            run, self.HIDDEN_HISTORY_QUERY, page_size=50, secret=agent
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.close(run, agent)
        retract()
        later = self.start(agent)
        again = self.reuse(later, reply["result"], page_size=50, secret=agent)
        self.close(later, agent)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["page"]["rows"], reply["page"]["rows"])

    def test_an_agents_saved_result_survives_a_replacement_it_cannot_see(
        self,
    ) -> None:
        # The replace probe: an owner-only supersession of R's root-lineage
        # relation by another relation, recorded after the agent's result was
        # saved, leaves the agent's reuse available with the same rows.
        agent, supersede = self.hidden_history("supersede")
        run = self.start(agent)
        _, reply = self.query(
            run, self.HIDDEN_HISTORY_QUERY, page_size=50, secret=agent
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.close(run, agent)
        supersede()
        later = self.start(agent)
        again = self.reuse(later, reply["result"], page_size=50, secret=agent)
        self.close(later, agent)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["page"]["rows"], reply["page"]["rows"])

    def test_an_agents_head_token_ignores_history_it_cannot_see(self) -> None:
        # The owner's decision of 2026-10-05: a caller that cannot read
        # relation_events gets a head_token over what it may see, acceptance
        # and its visible corrections with no events. An owner-only confirm
        # moves the owner's head and changes nothing the agent receives, its
        # frame digests included.
        grant, agent = self.home_agent(
            ["memory.inspect", "memory.query", "source.read"]
        )
        relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.paired_principal(grant)
        )
        source, rid = relation.source_bead_id, relation.relation_id
        columns = self.ALL_RELATION_COLUMNS["assessed_relations"]
        listed = ",".join("r." + column for column in columns)
        text = f"SELECT {listed} FROM memory_v1.assessed_relations r ORDER BY r.relation_id"
        head = columns.index("head_token")

        def read(secret: str | None) -> tuple[list[Any], dict[str, Any]]:
            # A run reads as of its start, so each read is a new run.
            run = self.start(secret)
            _, reply = self.query(run, text, page_size=50, secret=secret)
            self.assertEqual(reply["outcome"], "available", reply)
            self.close(run, secret)
            rows = [
                row["values"]
                for row in reply["page"]["rows"]
                if row["values"][0] == str(rid)
            ]
            self.assertEqual(len(rows), 1)
            return rows[0], reply["result"]["frame"]

        (owner, _), (mine, frame) = read(None), read(agent)
        # With no history yet, the agent's row is the owner's, head included.
        self.assertEqual(mine, owner)
        confirmed = self.fixture.governance.record_event(
            self.fixture.lifecycle_command(source, rid, "confirm", "am5-confirm")
        )
        self.assertIsNotNone(confirmed.relation_event_id)
        (owner_after, _), (mine_after, frame_after) = read(None), read(agent)
        self.assertNotEqual(owner_after[head], owner[head])
        self.assertEqual(mine_after, mine)
        for field in ("snapshot_digest", "projection_manifest_sha256"):
            self.assertEqual(frame_after[field], frame[field], field)
        # Every other column is still the owner's value.
        self.assertEqual(
            mine_after[:head] + mine_after[head + 1 :],
            owner_after[:head] + owner_after[head + 1 :],
        )

    def withheld_with(
        self, relation: UUID, scopes: list[UUID], member: UUID, label: str
    ) -> None:
        """The relation is read whole while `member`'s scope is readable, and is
        absent everywhere, with no type pin, once only that scope's grant goes;
        a result saved before then is no longer disclosed."""
        caps = ["memory.inspect", "memory.query", "source.read"]
        pairing, agent = self.pair_agent_over([*scopes, member], caps, label=label)
        run = self.start(agent)
        self.assertEqual(
            len(self.relation_rows(run, relation, secret=agent)["assessed_relations"]),
            1,
            label,
        )
        _, saved = self.query(
            run, self.RELATION_READS["assessed_relations"], page_size=50, secret=agent
        )
        access: UUID = self.h.scalar(
            "SELECT grant_id FROM memoriesql.access_grants "
            "WHERE target_principal_id=%s AND access_scope_id=%s",
            (self.paired_principal(pairing), member),
        )
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,1,%s,'revoked',%s,%s,%s)",
                (
                    access,
                    ["read"],
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                    self.fixture.now,
                ),
            )
        self.assertEqual(
            self.relation_rows(run, relation, secret=agent),
            {
                "assessed_relations": [],
                "relation_statements": [],
                "relation_evidence": [],
                "relation_types": [],
            },
            label,
        )
        again = self.reuse(run, saved["result"], page_size=50, secret=agent)
        self.assertEqual(again["outcome"], "unavailable", label)
        self.close(run, agent)

    def test_each_kind_of_closure_member_withholds_its_relation(self) -> None:
        # Review P2: one agent-mode case per closure member class that AM-5
        # gates, each member in its own scope: a basis statement, lifecycle
        # evidence, a replacement relation and a derivation-root lineage member.
        fixture = self.fixture
        tag = uuid4().hex[:8]
        (sx, ox), (sw, ow), (sm, om) = (
            relation_agents.explicit_scope(self.db, fixture) for _ in range(3)
        )
        x = fixture.remote_bead(
            "Fictional X: the orchard ledger balanced.",
            "Fictional X: every crate was counted.",
            key="x-" + tag,
            scope=sx,
            source=ox,
        )
        w = fixture.remote_bead(
            "Fictional W: the audit relied on the ledger.",
            key="w-" + tag,
            scope=sw,
            source=ow,
        )
        m = fixture.remote_bead(
            "Fictional M: the auditor's notes support the ledger.",
            key="m-" + tag,
            scope=sm,
            source=om,
        )

        def supports(p: Any, **extra: Any) -> list[dict[str, Any]]:
            return [
                fixture.proposal(
                    p,
                    "supports",
                    fixture.endpoint(p, x, 0),
                    fixture.endpoint(p, w, 0),
                    basis="agent_inferred",
                    **extra,
                )
            ]

        # A basis statement in the member's scope.
        based = self.relation_in(
            x,
            (w, m),
            "basis-" + tag,
            lambda p: [
                fixture.proposal(
                    p,
                    "supports",
                    fixture.endpoint(p, x, 1),
                    fixture.endpoint(p, w, 0),
                    basis_statements=(fixture.basis_pin(p, m, 0),),
                )
            ],
        )
        self.withheld_with(based, [sx, sw], sm, "basis")
        # Lifecycle evidence in the member's scope: a dispute that cites M.
        disputed = self.relation_in(x, (w,), "disputed-" + tag, supports)
        cited = self.db.execute(
            "SELECT s.statement_id,e.evidence_source_unit_id,e.evidence_content_hash "
            "FROM memoriesql.bead_semantic_statements s "
            "JOIN memoriesql.bead_semantic_statement_evidence e "
            "ON e.tenant_id=s.tenant_id AND e.statement_id=s.statement_id "
            "WHERE s.bead_id=%s ORDER BY s.statement_id LIMIT 1",
            (m,),
        ).fetchone()
        assert cited is not None
        fixture.governance.record_event(
            fixture.lifecycle_command(
                x,
                disputed,
                "dispute",
                "member-dispute-" + tag,
                evidence=[
                    dict(
                        statement_id=cited[0],
                        source_unit_id=cited[1],
                        content_hash=cited[2],
                    )
                ],
            )
        )
        self.withheld_with(disputed, [sx, sw], sm, "lifecycle evidence")
        # A replacement whose own closure reaches the member's scope: it keeps
        # the endpoints and rests on a basis statement there.
        replaced = self.relation_in(x, (w,), "replaced-" + tag, supports)
        retires = dict(
            relation_kind="assessed",
            relation_id=str(replaced),
            reason="Fictional exact replacement.",
        )
        self.relation_in(
            x,
            (w, m),
            "replacement-" + tag,
            lambda p: [
                fixture.proposal(
                    p,
                    "supports",
                    fixture.endpoint(p, x, 0),
                    fixture.endpoint(p, w, 0),
                    basis_statements=(fixture.basis_pin(p, m, 0),),
                    retires=retires,
                )
            ],
        )
        self.withheld_with(replaced, [sx, sw], sm, "replacement")
        # A derivation-root lineage member: W is derived from M, and the
        # relation cites W's evidence.
        self.relation_in(
            w,
            (m,),
            "derived-" + tag,
            lambda p: [
                fixture.proposal(
                    p,
                    "derived_from",
                    fixture.endpoint(p, w, 0),
                    fixture.endpoint(p, m, 0),
                    basis="agent_inferred",
                )
            ],
        )
        lineage = self.relation_in(
            x,
            (w,),
            "lineage-" + tag,
            lambda p: [
                self.citing(supports(p)[0], fixture.endpoint(p, w, 0)["statement_ids"])
            ],
        )
        self.withheld_with(lineage, [sx, sw], sm, "root lineage")

    def test_a_paired_service_reads_relations_only_as_before_am5(self) -> None:
        # Review P2: AM-5 names paired agents. A paired background service with
        # memory.query and source.read over a relation's whole closure keeps
        # the earlier gate: no relation without raw source authority, and the
        # gap says so.
        grant, _ = self.home_agent(["memory.inspect", "memory.query", "source.read"])
        relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.paired_principal(grant)
        )
        service = hashlib.sha256(b"fictional paired background service").hexdigest()
        principal, scopes = uuid4(), [relation.source_scope, relation.remote_scope]
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.pair_local_client("
                "%s,%s,%s,%s,'service','background_service',%s,%s,%s,%s,%s)",
                (
                    principal,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    ["memory.query", "source.read"],
                    scopes,
                    service,
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                ),
            )
            for scope in scopes:
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
        run = self.start(service)
        _, reply = self.query(
            run, self.RELATION_READS["assessed_relations"], page_size=50, secret=service
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertEqual(reply["page"]["rows"], [])
        self.assertEqual(
            reply["result"]["coverage"]["gaps"],
            [{"facet": "relation_tables", "reason": "source_raw_read_required"}],
        )
        self.assertNotIn(str(relation.relation_id), json.dumps(reply))
        # Its observations read as before.
        _, seen = self.query(
            run,
            "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id",
            page_size=50,
            secret=service,
        )
        self.assertIn(
            str(relation.source_bead_id),
            {row["values"][0] for row in seen["page"]["rows"]},
        )
        self.close(run, service)

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

    def test_cleanup_is_not_starved_by_pending_subjects(self) -> None:
        # Review finding: a pending subject ahead of a purgeable one must not
        # stop the pass. max_items counts purged subjects only.
        self.fixture.assertion()
        first = self.start()
        _, done = self.query(first, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(done["outcome"], "available", done)
        stuck = self.start()
        request, _ = self.request_only(
            stuck, "SELECT bead_version_id FROM memory_v1.observations"
        )
        reader = self.dispatch_then_die(request)
        try:
            self.age(first["run_ref"], timedelta(days=31))
            self.age(stuck["run_ref"], timedelta(minutes=31), results=False)
            cleaned = json.loads(
                self.service().cleanup_expired(batch_size=1, max_batches=4)
            )
            self.assertEqual(
                cleaned["cleanup"]["purged"], {"runs": 1, "results": 1, "tombstones": 0}
            )
            self.assertEqual(cleaned["cleanup"]["pending"], 1)
            self.assertEqual(
                self.h.scalar("SELECT count(*) FROM memoriesql.query_result_creations"),
                0,
            )
        finally:
            self.end_reader(reader)

    def test_oversized_row_is_refused_never_paged_as_empty(self) -> None:
        # Review finding: when one row exceeds the page's transport limit but
        # the metadata alone fits, the reply refuses. It never returns an
        # empty page whose cursor points back at the same row.
        self.fixture.assertion()
        run = self.start()
        ordered = "SELECT o.bead_id FROM memory_v1.observations o"
        _, reply = self.query(run, ordered + " ORDER BY o.bead_id", page_size=1)
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertTrue(reply["page"]["has_more"])
        _, empty = self.query(
            run,
            ordered + " WHERE o.bead_type_key=$1 ORDER BY o.bead_id",
            parameters=[{"position": 1, "type": "text", "value": "no-such-type"}],
            page_size=1,
        )
        self.assertEqual(empty["page"]["rows"], [], empty)
        one_row = int(reply["work"]["transport_bytes"])
        no_rows = int(empty["work"]["transport_bytes"])
        self.assertGreater(one_row - no_rows, 128)
        original = PostgresAgentSqlResults._admit

        def tight(service: Any, request: Any, kind: str, fingerprint: str) -> Any:
            admission = original(service, request, kind, fingerprint)
            admission["reserved_transport"] = (one_row + no_rows) // 2
            return admission

        with patch.object(PostgresAgentSqlResults, "_admit", tight):
            refused = self.reuse(run, reply["result"], page_size=1)
            # A later page, where an empty self-pointing page would loop.
            later = self.reuse(
                run, reply["result"], cursor=reply["page"]["next_cursor"], page_size=1
            )
        for answer in (refused, later):
            self.assertEqual(
                (answer["outcome"], answer["error"]),
                ("budget_exhausted", {"code": "transport"}),
            )
            self.assertNotIn("page", answer)

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

    # Closing a run (the close_run contract) and the workspace caps under
    # concurrency. A call checks a cap after taking the workspace lock; what
    # another call committed while it waited must count.

    @contextmanager
    def concurrently(self, action: Callable[[], Any]) -> Iterator[list[Any]]:
        """Run `action` on its own thread and connections once the next results
        frame has taken its snapshot, give it up to two seconds, then let that
        frame's call go on.

        Without the query-access lock the action commits inside that window,
        after the frame's snapshot, as a concurrent call can. With the lock it
        waits on the frame and fails its 500 ms lock wait. Yields what the action
        returned or raised."""
        original = relation_projection_frame
        outcome: list[Any] = []
        threads: list[threading.Thread] = []

        def run() -> None:
            try:
                outcome.append(action())
            except BaseException as error:  # recorded, never lost
                outcome.append(error)

        @contextmanager
        def frame(*args: Any, **kwargs: Any) -> Iterator[Any]:
            with original(*args, **kwargs) as connection:
                if not threads:
                    thread = threading.Thread(target=run)
                    threads.append(thread)
                    thread.start()
                    thread.join(timeout=2)
                yield connection

        with patch.object(results_module, "relation_projection_frame", frame):
            yield outcome
        for thread in threads:
            thread.join(timeout=30)
        self.assertTrue(threads)

    def active_runs(self) -> int:
        return int(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.query_runs r WHERE r.workspace_id=%s"
                " AND r.expires_at>clock_timestamp() AND NOT EXISTS(SELECT 1 FROM"
                " memoriesql.query_run_closures k WHERE k.tenant_id=r.tenant_id"
                " AND k.run_ref=r.run_ref)",
                (self.fixture.workspace,),
            )
        )

    def run_records(self, run: dict[str, Any]) -> list[Any]:
        """Every stored row of the run's work, results, charges and cursors."""
        deliveries = (
            "(SELECT d.delivery_ref FROM memoriesql.query_deliveries d"
            " WHERE d.run_ref=%s)"
        )
        tables = {
            "query_steps": "t.run_ref=%s",
            "query_deliveries": "t.run_ref=%s",
            "query_visible_refs": "t.run_ref=%s",
            "query_disclosures": "t.delivery_ref IN " + deliveries,
            "query_cursors": "t.issued_by_delivery IN " + deliveries,
        }
        records = []
        for table, predicate in tables.items():
            records.append(
                [
                    row[0]
                    for row in self.db.execute(
                        f"SELECT to_jsonb(t)::text FROM memoriesql.{table} t "
                        f"WHERE {predicate} ORDER BY 1",
                        (UUID(run["run_ref"]),),
                    ).fetchall()
                ]
            )
        return records

    def test_concurrent_starts_keep_two_active_runs(self) -> None:
        self.start()
        with self.concurrently(self.service().start_run) as other:
            late = json.loads(self.service().start_run())
        self.assertLessEqual(self.active_runs(), 2, (late, other))

    def test_concurrent_admissions_keep_one_operation(self) -> None:
        self.fixture.assertion()
        first, second = self.start(), self.start()
        request, _ = self.request_only(
            first, "SELECT bead_id FROM memory_v1.observations"
        )
        with self.concurrently(lambda: self.dispatch_then_die(request)) as other:
            self.query(second, "SELECT bead_version_id FROM memory_v1.observations")
        # At most one of the two operations was ever admitted.
        admitted = self.h.scalar(
            "SELECT count(*) FROM memoriesql.query_deliveries WHERE run_ref IN (%s,%s)",
            (first["run_ref"], second["run_ref"]),
        )
        self.assertLessEqual(admitted, 1, other)
        for reader in other:
            if isinstance(reader, psycopg.Connection):
                self.end_reader(reader)

    def test_close_concurrent_with_admission_never_closes_beside_work(
        self,
    ) -> None:
        self.fixture.assertion()
        run = self.start()
        request, _ = self.request_only(
            run, "SELECT bead_id FROM memory_v1.observations"
        )
        with self.concurrently(lambda: self.dispatch_then_die(request)) as other:
            closed = json.loads(self.service().close_run(run["run_ref"]))
        unsettled = self.h.scalar(
            "SELECT count(*) FROM memoriesql.query_deliveries"
            " WHERE run_ref=%s AND state<>'settled'",
            (run["run_ref"],),
        )
        closures = self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures")
        # Never a closure beside unsettled work.
        self.assertFalse(closures and unsettled, (closed, other))
        for reader in other:
            if isinstance(reader, psycopg.Connection):
                self.end_reader(reader)

    def test_concurrent_closes_never_fail_after_a_close(self) -> None:
        run = self.start()
        with self.concurrently(
            lambda: self.service().close_run(run["run_ref"])
        ) as other:
            late = self.service().close_run(run["run_ref"])
        # The call that holds the lock closes; a repeat returns the same reply.
        self.assertEqual(json.loads(late)["outcome"], "available", (late, other))
        self.assertEqual(self.service().close_run(run["run_ref"]), late)

    def test_a_crash_before_close_commits_leaves_the_run_open(self) -> None:
        self.fixture.assertion()
        run = self.start()
        original = relation_projection_frame

        class Crash(BaseException):
            pass

        @contextmanager
        def dying(*args: Any, **kwargs: Any) -> Iterator[Any]:
            with original(*args, **kwargs) as connection:
                yield connection
                raise Crash()  # the host dies after the call, before commit

        with patch.object(results_module, "relation_projection_frame", dying):
            with self.assertRaises(Crash):
                self.service().close_run(run["run_ref"])
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )
        _, open_run = self.query(run, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(open_run["outcome"], "available", open_run)
        closed = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)

    def test_another_principal_cannot_close_a_run(self) -> None:
        scope, source_object, _ = self.fixture.remote_scope()
        self.fixture.assertion(scope=scope, source_object=source_object)
        run = self.start()
        _, other = self.fixture.second_human(scope)
        foreign = self.service(other).close_run(run["run_ref"])
        unknown = self.service(other).close_run(str(uuid4()))
        self.assertEqual(json.loads(foreign)["outcome"], "unavailable", foreign)
        self.assertEqual(foreign, unknown)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )
        _, mine = self.query(run, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(mine["outcome"], "available", mine)

    def other_tenant(self) -> tuple[UUID, str]:
        """A second fictional tenant's owner: its workspace and credential secret.
        Public fixture pattern (test_result_preparation, test_authored_relations)."""
        secret = hashlib.sha256(b"other fictional tenant closing runs").hexdigest()
        tenant, user, identity, principal, workspace, scope, policy = (
            uuid4() for _ in range(7)
        )
        credential, session, now = uuid4(), uuid4(), self.fixture.now
        rows = (
            ("users", (tenant, user, "active", "Other fictional tenant", now, None)),
            (
                "auth_identities",
                (
                    tenant,
                    identity,
                    user,
                    "memoriesql.local",
                    str(identity),
                    "local_interactive",
                    "active",
                    now,
                    None,
                ),
            ),
            (
                "principals",
                (tenant, principal, "human", user, user, "active", now, None),
            ),
            ("workspaces", (tenant, workspace, "personal_local", user, "active", now)),
            (
                "workspace_memberships",
                (
                    tenant,
                    workspace,
                    principal,
                    "personal_owner",
                    "active",
                    1,
                    now,
                    now,
                    None,
                ),
            ),
            (
                "access_scopes",
                (
                    tenant,
                    workspace,
                    scope,
                    user,
                    "owner_private",
                    policy,
                    "active",
                    now,
                ),
            ),
            (
                "access_policy_revisions",
                (
                    tenant,
                    workspace,
                    scope,
                    policy,
                    1,
                    "owner_private",
                    user,
                    "Fictional default scope",
                    principal,
                    now,
                ),
            ),
            (
                "authentication_credentials",
                (
                    tenant,
                    credential,
                    principal,
                    "local_session",
                    secret,
                    "active",
                    now,
                    now + timedelta(hours=1),
                    None,
                ),
            ),
            ("local_auth_sessions", (tenant, session, credential, identity, now)),
        )
        with self.db.transaction():
            for table, values in rows:
                self.db.execute(
                    sql.SQL("INSERT INTO memoriesql.{} VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(sql.Placeholder() for _ in values),
                    ),
                    values,
                )
        return workspace, secret

    def test_another_tenant_cannot_close_a_run(self) -> None:
        run = self.start()
        workspace, secret = self.other_tenant()
        other = PostgresAgentSqlResults(
            control_factory=self.fixture.connection,
            reader_factory=self.reader_connection,
            authority_profile=self.profile,
            credential_sha256=secret,
            workspace_id=workspace,
        )
        foreign = other.close_run(run["run_ref"])
        self.assertEqual(json.loads(foreign)["outcome"], "unavailable", foreign)
        self.assertEqual(foreign, other.close_run(str(uuid4())))
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )

    def test_another_workspace_cannot_close_a_run(self) -> None:
        # The run's owner, authenticated into a second workspace of the same
        # tenant, cannot close a run of the first: ownership includes the
        # workspace, and the reply equals an unknown run's.
        run = self.start()
        fixture, workspace, scope, policy = self.fixture, uuid4(), uuid4(), uuid4()
        tenant, user, principal, now = (
            fixture.tenant,
            fixture.user,
            fixture.principal,
            fixture.now,
        )
        rows = (
            ("workspaces", (tenant, workspace, "personal_local", user, "active", now)),
            (
                "workspace_memberships",
                (
                    tenant,
                    workspace,
                    principal,
                    "personal_owner",
                    "active",
                    1,
                    now,
                    now,
                    None,
                ),
            ),
            (
                "access_scopes",
                (
                    tenant,
                    workspace,
                    scope,
                    user,
                    "owner_private",
                    policy,
                    "active",
                    now,
                ),
            ),
            (
                "access_policy_revisions",
                (
                    tenant,
                    workspace,
                    scope,
                    policy,
                    1,
                    "owner_private",
                    user,
                    "Fictional second workspace",
                    principal,
                    now,
                ),
            ),
        )
        with self.db.transaction():
            for table, values in rows:
                self.db.execute(
                    sql.SQL("INSERT INTO memoriesql.{} VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(sql.Placeholder() for _ in values),
                    ),
                    values,
                )
        elsewhere = PostgresAgentSqlResults(
            control_factory=self.fixture.connection,
            reader_factory=self.reader_connection,
            authority_profile=self.profile,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=workspace,
        )
        foreign = elsewhere.close_run(run["run_ref"])
        self.assertEqual(json.loads(foreign)["outcome"], "unavailable", foreign)
        self.assertEqual(foreign, elsewhere.close_run(str(uuid4())))
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )
        closed = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)

    def test_an_expired_run_may_be_closed_and_nothing_else_changes(self) -> None:
        run = self.start()
        # Age the run past its 30 minutes, as the test administrator only.
        with self.db.transaction():
            self.db.execute("SET LOCAL session_replication_role=replica")
            self.db.execute(
                "UPDATE memoriesql.query_runs SET started_at=started_at-interval '31 minutes',"
                " expires_at=expires_at-interval '31 minutes',"
                " default_known_at=default_known_at-interval '31 minutes' WHERE run_ref=%s",
                (UUID(run["run_ref"]),),
            )
        before = self.run_records(run)
        closed = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)
        self.assertEqual(self.run_records(run), before)

    def test_close_rechecks_authority_on_every_call(self) -> None:
        scope, _, _ = self.fixture.remote_scope()
        capabilities = ["memory.inspect", "memory.query", "source.read"]
        grant, agent = self.pair_agent(scope, capabilities)
        run = self.start(agent)
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'revoked',%s,%s,%s)",
                (
                    grant,
                    capabilities,
                    [scope],
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                    self.fixture.now,
                ),
            )
        revoked = json.loads(self.service(agent).close_run(run["run_ref"]))
        self.assertEqual(revoked["outcome"], "unavailable", revoked)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )

    def test_a_close_records_who_closed_the_run(self) -> None:
        # Attributable authority: the closure durably names the owner principal
        # and the credential it presented, with the time.
        run = self.start()
        closed = json.loads(self.service().close_run(run["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)
        row = self.db.execute(
            "SELECT k.closed_by_principal_id=r.principal_id,"
            " k.closed_by_credential_id=a.credential_id,"
            " to_char(k.closed_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
            " FROM memoriesql.query_run_closures k"
            " JOIN memoriesql.query_runs r USING(tenant_id,run_ref)"
            " JOIN memoriesql.authentication_credentials a"
            " ON a.tenant_id=k.tenant_id AND a.secret_sha256=%s"
            " WHERE k.run_ref=%s",
            (self.fixture.secret_hash, UUID(run["run_ref"])),
        ).fetchone()
        self.assertEqual(row, (True, True, closed["closed"]["closed_at"]))

    def test_closing_changes_no_step_result_charge_or_cursor(self) -> None:
        # Closing is not completion and not settled billing: every stored row
        # of the run is byte-identical after it, and its result stays reusable.
        self.fixture.assertion()
        first = self.start()
        _, reply = self.query(first, "SELECT bead_id FROM memory_v1.observations")
        self.assertEqual(reply["outcome"], "available", reply)
        before = self.run_records(first)
        closed = json.loads(self.service().close_run(first["run_ref"]))
        self.assertEqual(closed["outcome"], "available", closed)
        self.assertEqual(self.run_records(first), before)
        second = self.start()
        reused = self.reuse(second, reply["result"])
        self.assertEqual(reused["outcome"], "available", reused)

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
