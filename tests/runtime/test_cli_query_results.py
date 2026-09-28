"""The public CLI drives query and result reuse through a trusted executor."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.authorization import LocalCredential
from memoriesql.cli import main as cli_main
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
)

if TYPE_CHECKING:
    from tests.runtime import query_authority_fixture as authority_fixture
    from tests.runtime import test_query_result_commit as commit_tests
    from tests.runtime.test_postgres_runtime import migrate
else:
    import query_authority_fixture as authority_fixture
    import test_query_result_commit as commit_tests
    from test_postgres_runtime import migrate

FIXTURE_SECRET = "fictional orchard session for public acceptance"


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
        migrate(self.db, expected_current_version=34, target_version=38)
        self.reader = "pr06_cli_" + uuid4().hex
        self.profile = authority_fixture.provision_fictional_reader(
            self.db, self.reader
        )
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
                password="fictional-only",
            ),
            autocommit=True,
        )

    def cli(self, arguments: list[str]) -> tuple[int, str]:
        environment = {
            "MEMORIESQL_DATABASE_URL": "postgresql://unused.example/unused",
            "MEMORIESQL_LOCAL_CREDENTIAL": FIXTURE_SECRET,
            "MEMORIESQL_WORKSPACE_ID": str(self.fixture.workspace),
            "MEMORIESQL_STATE_DIR": str(self.root / "state"),
        }
        output = io.StringIO()
        with (
            patch.dict(os.environ, environment),
            patch("memoriesql.cli._results_transport", return_value=self.transport),
            redirect_stdout(output),
        ):
            status = cli_main(arguments)
        return status, output.getvalue()

    def query(self, text: str, *extra: str) -> tuple[int, dict[str, Any], str]:
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
            ]
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


if __name__ == "__main__":
    unittest.main()
