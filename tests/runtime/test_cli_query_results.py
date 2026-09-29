"""The public CLI drives query and result reuse through a trusted executor."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.authorization import LocalCredential
from memoriesql.cli import main as cli_main
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    qualify_query_authority,
)
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

FIXTURE_SECRET = "fictional orchard session for public acceptance"
READER_PASSWORD = "fictional-cli-reader-password"


class RecordingTransport:
    """Pass-through to the trusted executor that records exact reply bytes."""

    def __init__(self, service: PostgresAgentSqlResults) -> None:
        self.service = service
        self.replies: list[bytes] = []
        self.requests: list[dict[str, Any]] = []
        self.runs_started = 0

    def start_run(self) -> bytes:
        self.runs_started += 1
        return self.service.start_run()

    def handle(self, request: bytes) -> bytes:
        self.requests.append(json.loads(request))
        reply = self.service.handle(request)
        self.replies.append(reply)
        return reply


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class CliQueryResults(unittest.TestCase):
    def setUp(self) -> None:
        self.h = commit_tests.QueryResultCommit(methodName="runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.fixture = self.h.fixture
        self.db = self.h.db
        migrate(self.db, expected_current_version=34, target_version=39)
        self.reader = "pr06_cli_" + uuid4().hex
        self.profile = provision_query_reader(self.db, self.reader, READER_PASSWORD)
        self.addCleanup(self.drop_reader)
        self.assertEqual(
            LocalCredential(FIXTURE_SECRET).sha256(), self.fixture.secret_hash
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.transport = RecordingTransport(
            PostgresAgentSqlResults(
                control_factory=self.fixture.connection,
                reader_factory=self.reader_connection,
                authority_profile=self.profile,
                credential_sha256=self.fixture.secret_hash,
                workspace_id=self.fixture.workspace,
            )
        )

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

    def cli(
        self, arguments: list[str], environment: dict[str, str] | None = None
    ) -> tuple[int, str]:
        output = io.StringIO()
        if environment is None:
            environment = {
                "MEMORIESQL_DATABASE_URL": "postgresql://unused.example/unused",
                "MEMORIESQL_LOCAL_CREDENTIAL": FIXTURE_SECRET,
                "MEMORIESQL_WORKSPACE_ID": str(self.fixture.workspace),
                "MEMORIESQL_STATE_DIR": str(self.root / "state"),
            }
            with (
                patch.dict(os.environ, environment),
                patch(
                    "memoriesql.cli._results_transport", return_value=self.transport
                ),
                redirect_stdout(output),
            ):
                status = cli_main(arguments)
            return status, output.getvalue()
        with patch.dict(os.environ, environment, clear=True), redirect_stdout(output):
            status = cli_main(arguments)
        return status, output.getvalue()

    def query(
        self,
        text: str,
        *extra: str,
        environment: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], str]:
        path = self.root / f"{uuid4()}.sql"
        path.write_text(text, encoding="utf-8")
        status, output = self.cli(
            [
                "query",
                "--file",
                str(path),
                "--intent",
                "enumerate",
                "--view",
                "historical",
                "--page-size",
                "2",
                *extra,
                "--json",
            ],
            environment,
        )
        return status, json.loads(output), output

    def state_files(self) -> list[Path]:
        return sorted((self.root / "state" / "runs").glob("*.json"))

    def test_schema_query_cursor_reuse_and_run_persistence(self) -> None:
        self.fixture.assertion()
        status, output = self.cli(["schema", "--json"])
        self.assertEqual(status, 0)
        schema = json.loads(output)
        self.assertEqual(schema["catalog_hash"], SqlCatalog.installed().hash)
        statuses = schema["relation_status"]
        self.assertEqual(
            sum(value == "prepared" for value in statuses.values()), 14, statuses
        )
        self.assertEqual(
            statuses["evaluation_v1.candidates"], "reserved_evaluation_only"
        )
        self.assertEqual(statuses["memory_v1.entities"], "not_prepared")
        self.assertEqual(self.transport.runs_started, 0, "schema starts no run")

        status, reply, output = self.query(
            "SELECT o.bead_id,o.title,s.statement_id,s.text FROM memory_v1.observations o "
            "JOIN memory_v1.statements s ON s.bead_version_id=o.bead_version_id "
            "ORDER BY o.bead_id,s.statement_id"
        )
        self.assertEqual(status, 0, reply)
        self.assertEqual(output.encode(), self.transport.replies[-1] + b"\n")
        result = reply["result"]
        self.assertTrue(reply["page"]["has_more"])
        (state,) = self.state_files()
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
        self.assertNotIn(FIXTURE_SECRET, state.read_text())
        self.assertNotIn(self.fixture.secret_hash, state.name)

        status, second, _ = self.query(
            "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"
        )
        self.assertEqual(status, 0, second)
        self.assertEqual(second["run_ref"], reply["run_ref"])
        self.assertEqual(self.transport.runs_started, 1)

        status, output = self.cli(
            [
                "result",
                result["result_id"],
                "--digest",
                result["content_digest"],
                "--cursor",
                reply["page"]["next_cursor"],
                "--page-size",
                "2",
                "--json",
            ]
        )
        page = json.loads(output)
        self.assertEqual(status, 0, page)
        self.assertEqual(page["result"]["result_id"], result["result_id"])
        self.assertEqual(page["run_ref"], reply["run_ref"])
        self.assertNotEqual(page["page"]["rows"], reply["page"]["rows"])

        status, output = self.cli(
            [
                "result",
                result["result_id"],
                "--digest",
                "0" * 64,
                "--json",
            ]
        )
        self.assertNotEqual(status, 0)
        self.assertNotEqual(json.loads(output)["outcome"], "available")

    def test_unprepared_relation_and_expired_run_rotation_are_honest(self) -> None:
        status, reply, _ = self.query("SELECT e.entity_id FROM memory_v1.entities e")
        self.assertEqual(status, 3)
        self.assertEqual(reply["outcome"], "unsupported_query")
        self.assertEqual(reply["error"]["feature"], "unprepared_relation")
        first_run = reply["run_ref"]
        (state,) = self.state_files()
        data = json.loads(state.read_text())
        data["expires_at"] = "2000-01-01T00:00:00+00:00"
        state.write_text(json.dumps(data))
        status, rotated, _ = self.query(
            "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"
        )
        self.assertEqual(status, 0, rotated)
        self.assertNotEqual(rotated["run_ref"], first_run)
        self.assertEqual(self.transport.runs_started, 2)
        self.assertTrue(
            all(request["run_ref"] != first_run for request in self.transport.requests[1:])
        )

    def test_trusted_host_environment_composes_the_reviewed_executor(self) -> None:
        self.fixture.assertion()
        base = os.environ["N1_TEST_DATABASE_URL"]
        control_url = make_conninfo(base, dbname=self.db.info.dbname)
        environment = {
            "MEMORIESQL_DATABASE_URL": control_url,
            "MEMORIESQL_QUERY_READER_URL": make_conninfo(
                base,
                dbname=self.db.info.dbname,
                user=self.reader,
                password=READER_PASSWORD,
            ),
            "MEMORIESQL_QUERY_READER_ROLE": self.reader,
            "MEMORIESQL_LOCAL_CREDENTIAL": FIXTURE_SECRET,
            "MEMORIESQL_WORKSPACE_ID": str(self.fixture.workspace),
            "MEMORIESQL_STATE_DIR": str(self.root / "trusted-state"),
        }
        with psycopg.connect(control_url, autocommit=True) as control:
            pin = qualify_query_authority(control, self.profile).profile_sha256
        text = "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"
        status, reply, _ = self.query(
            text,
            environment=environment | {"MEMORIESQL_QUERY_AUTHORITY_SHA256": pin},
        )
        self.assertEqual(status, 0, reply)
        self.assertEqual(reply["outcome"], "available")
        status, drifted, _ = self.query(
            text,
            environment=environment
            | {
                "MEMORIESQL_QUERY_AUTHORITY_SHA256": "0" * 64,
                "MEMORIESQL_STATE_DIR": str(self.root / "drift-state"),
            },
        )
        self.assertEqual((status, drifted["outcome"]), (2, "unavailable"))
        self.assertNotIn(READER_PASSWORD, json.dumps(drifted))
        without_reader = dict(environment)
        del without_reader["MEMORIESQL_QUERY_READER_URL"]
        status, missing, _ = self.query(text, environment=without_reader)
        self.assertEqual(
            (status, missing),
            (
                2,
                {"outcome": "unavailable", "reason": "trusted_query_host_not_configured"},
            ),
        )

    def trusted_environment(self, credential: str, state: str) -> dict[str, str]:
        base = os.environ["N1_TEST_DATABASE_URL"]
        return {
            "MEMORIESQL_DATABASE_URL": make_conninfo(base, dbname=self.db.info.dbname),
            "MEMORIESQL_QUERY_READER_URL": make_conninfo(
                base,
                dbname=self.db.info.dbname,
                user=self.reader,
                password=READER_PASSWORD,
            ),
            "MEMORIESQL_QUERY_READER_ROLE": self.reader,
            "MEMORIESQL_LOCAL_CREDENTIAL": credential,
            "MEMORIESQL_WORKSPACE_ID": str(self.fixture.workspace),
            "MEMORIESQL_STATE_DIR": str(self.root / state),
        }

    def request_cli(
        self, arguments: list[str], payload: dict[str, object], environment: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        path = self.root / f"request-{uuid4()}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        status, output = self.cli(
            [*arguments, "--request-file", str(path), "--json"], environment
        )
        return status, json.loads(output)

    def scope_beads(self, scope: UUID) -> set[str]:
        return {
            str(row[0])
            for row in self.db.execute(
                "SELECT DISTINCT a.bead_id FROM memoriesql.accepted_bead_semantics a "
                "JOIN memoriesql.beads b ON b.tenant_id=a.tenant_id "
                "AND b.bead_id=a.bead_id WHERE b.access_scope_id=%s",
                (scope,),
            ).fetchall()
        }

    def enrolled_source(self, environment: dict[str, str]) -> tuple[UUID, UUID]:
        """Enroll one exact source through the public command.

        Only fictional capture setup follows, as remote_scope() does for its
        explicit scope: the fixture's worker and attestor may write the new
        scope, and the source gets producer and dispatch policies.
        """

        fx = self.fixture
        row = self.db.execute(
            "SELECT source_system, object_kind, schema_version "
            "FROM memoriesql.source_objects WHERE source_object_id=%s",
            (fx.source,),
        ).fetchone()
        assert row is not None
        status, enrolled = self.request_cli(
            ["sources", "enroll"],
            {
                "request_id": str(uuid4()),
                "source_system": row[0],
                "object_kind": row[1],
                "external_object_id": "orchard/enrolled-agent-source",
                "source_schema_version": row[2],
                "exact_source_confirmed": True,
            },
            environment,
        )
        self.assertEqual(status, 0, enrolled)
        scope = UUID(enrolled["receipt"]["access_scope_id"])
        source = UUID(enrolled["receipt"]["source_object_id"])
        pairings = self.db.execute(
            "SELECT r.pairing_grant_id, r.revision, r.allowed_capabilities, "
            "r.allowed_access_scope_ids FROM memoriesql.pairing_grant_revisions AS r "
            "JOIN memoriesql.pairing_grants AS g USING (tenant_id, pairing_grant_id) "
            "WHERE g.paired_principal_id IN (%s,%s) AND r.revision = ("
            "SELECT max(l.revision) FROM memoriesql.pairing_grant_revisions AS l "
            "WHERE l.tenant_id = r.tenant_id AND l.pairing_grant_id = r.pairing_grant_id)",
            (fx.worker_principal, fx.attestor),
        ).fetchall()
        self.assertEqual(len(pairings), 2)
        with self.db.transaction():
            fx.begin()
            for principal in (fx.worker_principal, fx.attestor):
                self.db.execute(
                    "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        principal,
                        scope,
                        ["read", "write"],
                        fx.now,
                        fx.now + timedelta(hours=1),
                    ),
                )
            for pairing, revision, capabilities, scopes in pairings:
                self.db.execute(
                    "SELECT memoriesql.revise_pairing_grant("
                    "%s,%s,%s,%s,'active',%s,%s,%s)",
                    (
                        pairing,
                        revision,
                        capabilities,
                        [*scopes, scope],
                        fx.now,
                        fx.now + timedelta(hours=1),
                        fx.now,
                    ),
                )
        producer, policy = uuid4(), uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies SELECT tenant_id,"
            "workspace_id,%s,%s,%s,producer_principal_id,qualification_ref,"
            "normalization_policy_version,qualification_evidence_sha256,"
            "approved_by_principal_id,created_at,expires_at,status "
            "FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
            (scope, producer, source, fx.policy),
        )
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,"
            "%s,workspace_id,%s,%s,attestor_principal_id,approved_by_principal_id,"
            "qualification_evidence_sha256,created_at,expires_at,status,6 "
            "FROM memoriesql.complete_input_dispatch_policies "
            "WHERE dispatch_policy_id=%s",
            (policy, scope, source, fx.dispatch_policy),
        )
        fx.producers[source], fx.policies[source] = producer, policy
        fx.checkpoints[source], fx.positions[source] = "orchard.enrolled.raw", (0, 0)
        return scope, source

    def test_agent_paired_to_one_enrolled_source_reads_only_that_source(self) -> None:
        owner_environment = self.trusted_environment(FIXTURE_SECRET, "owner-state")
        self.fixture.bead("Fictional owner-private orchard note.", key="private")
        private = self.scope_beads(self.fixture.scope)
        self.assertTrue(private)
        scope, source = self.enrolled_source(owner_environment)
        self.fixture.assertion(scope=scope, source_object=source)
        expected = self.scope_beads(scope)
        self.assertGreaterEqual(len(expected), 2)
        self.assertFalse(expected & private)

        # Pair over that source's own explicit scope only, with an expiry.
        expires = (self.fixture.now + timedelta(hours=1)).isoformat()
        secret_file = self.root / "narrow-agent.secret"
        request = self.root / "narrow-pair.json"
        request.write_text(
            json.dumps(
                {
                    "request_id": str(uuid4()),
                    "capabilities": ["memory.query", "source.read"],
                    "access_scope_ids": [str(scope)],
                    "expires_at": expires,
                    "exact_pairing_confirmed": True,
                }
            ),
            encoding="utf-8",
        )
        status, output = self.cli(
            [
                "clients",
                "pair",
                "--request-file",
                str(request),
                "--secret-file",
                str(secret_file),
                "--json",
            ],
            owner_environment,
        )
        paired = json.loads(output)
        self.assertEqual(status, 0, paired)
        agent_environment = self.trusted_environment(
            secret_file.read_text(encoding="ascii"), "narrow-agent-state"
        )
        text = "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"
        status, before, _ = self.query(text, environment=agent_environment)
        self.assertEqual(status, 0, before)
        self.assertEqual(before["result"]["total_rows"], "0", "no grant, no rows")
        # Before the source grant every member of the relation's closure is
        # unreadable: the relation is simply absent, with no gap or count, and
        # the query stays available.
        count = "SELECT count(*) AS n FROM memory_v1.assessed_relations"
        status, owner_relations, _ = self.query(count, environment=owner_environment)
        self.assertEqual(status, 0, owner_relations)
        self.assertGreaterEqual(int(owner_relations["page"]["rows"][0]["values"][0]), 1)
        status, absent, _ = self.query(count, environment=agent_environment)
        self.assertEqual(status, 0, absent)
        self.assertEqual(absent["page"]["rows"][0]["values"], ["0"])
        self.assertEqual(absent["result"]["coverage"]["gaps"], [])

        status, granted = self.request_cli(
            ["sources", "grant"],
            {
                "request_id": str(uuid4()),
                "source_object_id": str(source),
                "target_principal_id": paired["receipt"]["principal_id"],
                "permission_keys": ["read"],
                "valid_from": self.fixture.now.isoformat(),
                "expires_at": expires,
            },
            owner_environment,
        )
        self.assertEqual(status, 0, granted)
        path = self.root / "narrow.sql"
        path.write_text(text, encoding="utf-8")
        status, output = self.cli(
            [
                "query",
                "--file",
                str(path),
                "--intent",
                "enumerate",
                "--view",
                "historical",
                "--page-size",
                "50",
                "--json",
            ],
            agent_environment,
        )
        reply = json.loads(output)
        self.assertEqual(status, 0, reply)
        seen = {row["values"][0] for row in reply["page"]["rows"]}
        self.assertEqual(seen, expected)
        self.assertFalse(seen & private, "the owner-private scope stays unreadable")
        self.assertEqual(reply["result"]["coverage"]["gaps"], [])
        # With the grant the whole closure is readable: the agent reads the
        # owner's relation, still with no gap.
        status, present, _ = self.query(count, environment=agent_environment)
        self.assertEqual(status, 0, present)
        self.assertEqual(
            present["page"]["rows"][0]["values"],
            owner_relations["page"]["rows"][0]["values"],
        )
        self.assertEqual(present["result"]["coverage"]["gaps"], [])

    def test_paired_agent_queries_the_owner_scope_under_its_own_pairing(self) -> None:
        subject, _, _ = self.fixture.assertion()
        mode = self.db.execute(
            "SELECT mode FROM memoriesql.access_scopes WHERE access_scope_id=%s",
            (self.fixture.scope,),
        ).fetchone()
        self.assertEqual(mode, ("owner_private",))
        text = "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"
        owner_environment = self.trusted_environment(FIXTURE_SECRET, "owner-state")
        status, owner, _ = self.query(text, environment=owner_environment)
        self.assertEqual(status, 0, owner)
        owner_rows = int(owner["result"]["total_rows"])
        self.assertGreater(owner_rows, 0)

        # The owner pairs through the public command; no private grant call.
        request = self.root / "pair.json"
        request.write_text(
            json.dumps(
                {
                    "request_id": str(uuid4()),
                    "capabilities": ["memory.query", "source.read"],
                    "access_scope_ids": [str(self.fixture.scope)],
                    "expires_at": (self.fixture.now + timedelta(hours=1)).isoformat(),
                    "exact_pairing_confirmed": True,
                }
            ),
            encoding="utf-8",
        )
        secret_file = self.root / "agent.secret"
        status, output = self.cli(
            [
                "clients",
                "pair",
                "--request-file",
                str(request),
                "--secret-file",
                str(secret_file),
                "--json",
            ],
            owner_environment,
        )
        paired = json.loads(output)
        self.assertEqual(status, 0, paired)
        agent = secret_file.read_text(encoding="ascii")
        agent_environment = self.trusted_environment(agent, "agent-state")

        status, reply, _ = self.query(text, environment=agent_environment)
        self.assertEqual(status, 0, reply)
        self.assertEqual(int(reply["result"]["total_rows"]), owner_rows)
        self.assertEqual(reply["result"]["coverage"]["gaps"], [])
        self.assertNotEqual(reply["run_ref"], owner["run_ref"])

        # An explicit cutoff with a numeric offset is sent in the accepted Z form.
        known_at = datetime.fromisoformat(
            reply["result"]["frame"]["known_at"].replace("Z", "+00:00")
        )
        status, pinned, _ = self.query(
            text,
            "--known-at",
            known_at.isoformat(),
            environment=agent_environment,
        )
        self.assertEqual(status, 0, pinned)
        self.assertEqual(pinned["result"]["total_rows"], reply["result"]["total_rows"])

        # The agent holds memory.query and source.read over the whole owner
        # scope, so every accepted assessed relation's disclosed dependency
        # closure is readable: it reads the owner's relations with no relation
        # gap. Raw-source provenance stays owner-only and is never selectable.
        path = self.root / "relations.sql"
        path.write_text(
            "SELECT count(*) AS n FROM memory_v1.assessed_relations", encoding="utf-8"
        )
        counts = []
        for environment in (owner_environment, agent_environment):
            errors = io.StringIO()
            with redirect_stderr(errors):
                status, output = self.cli(
                    [
                        "query",
                        "--file",
                        str(path),
                        "--intent",
                        "enumerate",
                        "--view",
                        "resolved",
                    ],
                    environment,
                )
            relations = json.loads(output)
            self.assertEqual(status, 0, relations)
            self.assertEqual(relations["result"]["coverage"]["gaps"], [])
            self.assertNotIn("coverage gap:", errors.getvalue())
            counts.append(relations["page"]["rows"][0]["values"])
        self.assertEqual(counts[1], counts[0])
        self.assertGreaterEqual(int(counts[1][0]), 1)
        agent_relations = relations["result"]

        # The exact relation reader stays owner-only for a paired agent.
        status, output = self.cli(
            ["relations", str(subject), "--json"], agent_environment
        )
        self.assertEqual(
            (status, json.loads(output)),
            (2, {"outcome": "unavailable", "reason": "resource_unavailable"}),
        )

        revoke = self.root / "revoke.json"
        revoke.write_text(
            json.dumps(
                {
                    "pairing_grant_id": paired["receipt"]["pairing_grant_id"],
                    "expected_revision": 1,
                    "capabilities": paired["receipt"]["capabilities"],
                    "access_scope_ids": paired["receipt"]["access_scope_ids"],
                    "exact_revocation_confirmed": True,
                }
            ),
            encoding="utf-8",
        )
        status, output = self.cli(
            ["clients", "revoke", "--request-file", str(revoke), "--json"],
            owner_environment,
        )
        self.assertEqual(status, 0, output)
        status, refused, _ = self.query(text, environment=agent_environment)
        self.assertEqual((status, refused["outcome"]), (2, "unavailable"), refused)
        self.assertNotIn("page", refused)
        status, output = self.cli(
            [
                "result",
                agent_relations["result_id"],
                "--digest",
                agent_relations["content_digest"],
                "--json",
            ],
            agent_environment,
        )
        redisclosed = json.loads(output)
        self.assertEqual((status, redisclosed["outcome"]), (2, "unavailable"), redisclosed)
        self.assertNotIn("page", redisclosed)


if __name__ == "__main__":
    unittest.main()
