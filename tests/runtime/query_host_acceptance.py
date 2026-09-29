"""Fictional acceptance of the trusted query host against real PostgreSQL.

Run by scripts/prove_query_host.py (it needs Unix sockets and separate host
processes, which the installed acceptance runner denies). Every case uses the
production provisioning path, a canonically paired fictional agent and the
real executor; no owner data, provider or model call is involved.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.authorization import LocalCredential
from memoriesql.application.relation_inspection import InspectBeadRelationsV2
from memoriesql.application.stored_bead_inspection import InspectStoredBead
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
)
from memoriesql.infrastructure.results_broker import admin
from memoriesql.infrastructure.results_broker.client import BrokerTransport
from memoriesql.infrastructure.results_broker.config import (
    BrokerConfig,
    commit_config,
    load_config,
    stage_config,
)
from memoriesql.infrastructure.results_broker.readers import DirectReadTransport
from memoriesql.infrastructure.results_broker.server import (
    HostRefused,
    Server,
    open_server,
    prepare_host,
)
from memoriesql.infrastructure.results_broker.wire import (
    READ_UNAVAILABLE_REPLY,
    UNAVAILABLE_REPLY,
)

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_query_result_commit as commit_tests
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
    import test_query_result_commit as commit_tests
    from test_postgres_runtime import migrate

HUMAN_SECRET = "fictional orchard session for public acceptance"
AGENT_CAPABILITIES = ["memory.inspect", "memory.query", "source.read"]
OBSERVATIONS = "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id"


def _short_root() -> Path:
    # AF_UNIX paths are limited to ~104 bytes: never nest them in a long tree.
    return Path(
        tempfile.mkdtemp(prefix="mqh", dir="/tmp" if Path("/tmp").is_dir() else None)
    )


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class QueryHostHarness(unittest.TestCase):
    """Provisioned host, paired agent and helpers; adapter proofs subclass it."""

    def setUp(self) -> None:
        self.h = commit_tests.QueryResultCommit(methodName="runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.fixture = self.h.fixture
        self.db = self.h.db
        migrate(self.db, expected_current_version=34, target_version=39)
        self.root = _short_root()
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "config").mkdir(mode=0o700)
        (self.root / "state").mkdir(mode=0o700)
        (self.root / "run").mkdir(mode=0o750)
        self.gates = self.root / "gates"
        self.gates.mkdir(mode=0o700)
        self.config_path = self.root / "config" / "broker.json"
        suffix = uuid4().hex[:12]
        self.roles = (f"mqh_control_{suffix}", f"mqh_reader_{suffix}")
        self.addCleanup(self.drop_roles)
        self.admin_url = make_conninfo(
            os.environ["N1_TEST_DATABASE_URL"], dbname=self.db.info.dbname
        )
        target = conninfo_to_dict(self.admin_url)
        code, output = self.operator(
            admin.provision,
            self.config_path,
            workspace_id=self.fixture.workspace,
            client_uid=os.geteuid(),
            database_host=str(target.get("host") or "127.0.0.1"),
            database_port=int(target.get("port") or 5432),
            database_name=self.db.info.dbname,
            control_role=self.roles[0],
            reader_role=self.roles[1],
            admin_url=lambda: self.admin_url,
        )
        self.assertEqual(code, 0, output)
        self.config = load_config(self.config_path)
        self.scope, source_object, _ = self.fixture.remote_scope()
        self.fixture.assertion(scope=self.scope, source_object=source_object)
        self.principals: dict[UUID, UUID] = {}
        self.grant, self.agent_secret = self.pair_agent(AGENT_CAPABILITIES)
        self.agent_hash = LocalCredential(self.agent_secret).sha256()
        self.processes: list[subprocess.Popen[bytes]] = []
        self.addCleanup(self.reap)

    # -- fixture helpers -----------------------------------------------------

    def operator(self, command: Any, *args: Any, **kwargs: Any) -> tuple[int, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = command(*args, **kwargs)
        return int(code), stream.getvalue()

    def drop_roles(self) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as db:
            for role in self.roles:
                db.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE usename=%s",
                    (role,),
                )
                if db.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
                ).fetchone():
                    db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def pair_agent(
        self,
        capabilities: list[str],
        *,
        scopes: list[UUID] | None = None,
        issued_ago: timedelta = timedelta(0),
        lifetime: timedelta = timedelta(hours=1),
    ) -> tuple[UUID, str]:
        """A paired agent through the canonical pairing path, never the owner."""
        grant, principal = uuid4(), uuid4()
        secret = secrets.token_urlsafe(32)
        issued = self.fixture.now - issued_ago
        targets = scopes or [self.scope]
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.pair_local_client("
                "%s,%s,%s,%s,'agent','paired_agent',%s,%s,%s,%s,%s)",
                (
                    principal,
                    uuid4(),
                    grant,
                    uuid4(),
                    capabilities,
                    targets,
                    LocalCredential(secret).sha256(),
                    issued,
                    issued + lifetime,
                ),
            )
            for scope in targets:
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
        self.principals[grant] = principal
        return grant, secret

    def revoke(self, grant: UUID) -> None:
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'revoked',%s,%s,%s)",
                (
                    grant,
                    AGENT_CAPABILITIES,
                    [self.scope],
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                    self.fixture.now,
                ),
            )

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(statement, values).fetchone()
        return None if row is None else row[0]

    def signal_backend(self, pid: int, name: str) -> None:
        """Signal one server backend as the database's own OS user (superuser)."""
        self.db.execute(
            sql.SQL("COPY (SELECT 1) TO PROGRAM {}").format(
                sql.Literal(f"kill -{name} {int(pid)}")
            )
        )

    def wait_backend_gone(self, pid: int) -> None:
        for _ in range(400):
            if not self.scalar(
                "SELECT count(*) FROM pg_stat_activity WHERE pid=%s", (pid,)
            ):
                return
            time.sleep(0.05)
        self.fail("reader backend never ended")

    # -- host helpers ------------------------------------------------------------

    def serve_in_process(self, config: BrokerConfig | None = None) -> Server:
        server = open_server(config or self.config)
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()

        def stop() -> None:
            server.stop()
            worker.join(30)

        self.addCleanup(stop)
        return server

    def spawn(self, *, fault: str | None = None) -> subprocess.Popen[bytes]:
        if fault is not None:
            code = (
                "import sys; sys.path.insert(0, sys.argv[3]); "
                "import query_host_fault as f; "
                "sys.exit(f.main(sys.argv[1], sys.argv[2], sys.argv[4]))"
            )
        else:
            code = (
                "import sys; from pathlib import Path; "
                "from memoriesql.infrastructure.results_broker.server import serve; "
                "sys.exit(serve(Path(sys.argv[1])))"
            )
        log = (self.root / f"host-{len(self.processes)}.log").open("wb")
        self.addCleanup(log.close)
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                code,
                str(self.config_path),
                str(self.gates),
                str(Path(__file__).resolve().parent),
                fault or "none",
            ],
            stdout=log,
            stderr=log,
        )
        self.processes.append(process)
        for _ in range(600):
            if process.poll() is not None:
                self.fail(f"host exited early with {process.returncode}")
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.connect(str(self.config.socket_path))
                return process
            except OSError:
                time.sleep(0.05)
        self.fail("host never listened")

    def reap(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(30)

    def transport(self, secret: str | None = None, **kwargs: Any) -> BrokerTransport:
        return BrokerTransport(
            self.config.socket_path,
            secret or self.agent_secret,
            kwargs.get("workspace_id", self.fixture.workspace),
            expected_peer_uid=os.geteuid(),
        )

    def start(self, transport: BrokerTransport | None = None) -> dict[str, Any]:
        data = json.loads((transport or self.transport()).start_run())
        self.assertEqual(data["outcome"], "available", data)
        run: dict[str, Any] = data["run"]
        return run

    def query_bytes(
        self,
        run: dict[str, Any],
        text: str,
        *,
        step: str | None = None,
        page_size: int = 1,
    ) -> bytes:
        return json.dumps(
            {
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
        ).encode()

    def reuse_bytes(
        self, run: dict[str, Any], result: dict[str, Any], cursor: str | None
    ) -> bytes:
        request: dict[str, Any] = {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": str(uuid4()),
            "kind": "reuse_result",
            "result": {
                "result_id": result["result_id"],
                "content_digest": result["content_digest"],
            },
            "access": {"kind": "standalone"},
            "page_size": 1,
        }
        if cursor is not None:
            request["cursor"] = cursor
        return json.dumps(request).encode()


class QueryHostAcceptance(QueryHostHarness):
    # -- acceptance ----------------------------------------------------------

    def test_replies_are_the_executors_bytes_and_readers_match_direct(self) -> None:
        self.serve_in_process()
        captured: list[bytes] = []
        start_run, handle = (
            PostgresAgentSqlResults.start_run,
            PostgresAgentSqlResults.handle,
        )

        def spy_start(executor: PostgresAgentSqlResults) -> bytes:
            captured.append(start_run(executor))
            return captured[-1]

        def spy_handle(executor: PostgresAgentSqlResults, data: bytes) -> bytes:
            captured.append(handle(executor, data))
            return captured[-1]

        transport = self.transport()
        with (
            patch.object(PostgresAgentSqlResults, "start_run", spy_start),
            patch.object(PostgresAgentSqlResults, "handle", spy_handle),
        ):
            raw_run = transport.start_run()
            self.assertEqual(raw_run, captured[-1])
            run = json.loads(raw_run)["run"]
            raw = transport.handle(self.query_bytes(run, OBSERVATIONS))
            self.assertEqual(raw, captured[-1])
            first = json.loads(raw)
            self.assertEqual(first["outcome"], "available", first)
            self.assertTrue(first["page"]["has_more"])
            raw_page = transport.handle(
                self.reuse_bytes(run, first["result"], first["page"]["next_cursor"])
            )
            self.assertEqual(raw_page, captured[-1])
            page = json.loads(raw_page)
            self.assertEqual(page["outcome"], "available", page)
        bead = first["page"]["rows"][0]["values"][0]
        direct = DirectReadTransport(
            self.config.control_conninfo(), self.agent_hash, self.fixture.workspace
        )
        for command, request in (
            ("inspect", InspectStoredBead(bead_id=UUID(bead)).model_dump_json()),
            (
                "relations",
                InspectBeadRelationsV2(bead_id=UUID(bead)).model_dump_json(),
            ),
        ):
            with self.subTest(command=command):
                through_host = transport.read(command, request.encode())
                self.assertEqual(through_host, direct.read(command, request.encode()))
                # The existing readers authorize source revisiting, which needs
                # raw-source authority a paired agent never holds: the host
                # carries their refusal unchanged and substitutes nothing.
                self.assertEqual(json.loads(through_host)["outcome"], "unavailable")
        # An unreadable selection is refused identically on both paths.
        bogus = json.dumps({"bead_id": str(uuid4())}).encode()
        self.assertEqual(transport.read("source", bogus), direct.read("source", bogus))

    def test_refusals_are_indistinguishable_and_reach_no_executor(self) -> None:
        self.serve_in_process()
        revoked_grant, revoked = self.pair_agent(AGENT_CAPABILITIES)
        self.revoke(revoked_grant)
        _, expired = self.pair_agent(
            AGENT_CAPABILITIES,
            issued_ago=timedelta(hours=2),
            lifetime=timedelta(hours=1),
        )
        run = self.start()
        request = self.query_bytes(run, OBSERVATIONS)
        calls: list[str] = []
        handle = PostgresAgentSqlResults.handle

        def spy(executor: PostgresAgentSqlResults, data: bytes) -> bytes:
            calls.append("handle")
            return handle(executor, data)

        inspect = InspectStoredBead(bead_id=uuid4()).model_dump_json().encode()
        cases = {
            "unknown": self.transport(secrets.token_urlsafe(32)),
            "human_owner": self.transport(HUMAN_SECRET),
            "revoked": self.transport(revoked),
            "expired": self.transport(expired),
            "other_workspace": self.transport(workspace_id=uuid4()),
            "short_secret": self.transport("short"),
        }
        with patch.object(PostgresAgentSqlResults, "handle", spy):
            for name, transport in cases.items():
                with self.subTest(case=name):
                    self.assertEqual(transport.start_run(), UNAVAILABLE_REPLY)
                    self.assertEqual(transport.handle(request), UNAVAILABLE_REPLY)
                    self.assertEqual(
                        transport.read("inspect", inspect), READ_UNAVAILABLE_REPLY
                    )
        self.assertEqual(calls, [])
        # The paired agent itself is still served.
        reply = json.loads(self.transport().handle(request))
        self.assertEqual(reply["outcome"], "available", reply)

    def test_request_content_never_reaches_replies_or_the_host_log(self) -> None:
        sentinel = "fictional_sentinel_" + secrets.token_hex(12)
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            self.serve_in_process()
            transport = self.transport()
            run = self.start(transport)
            replies = [
                transport.handle(
                    self.query_bytes(
                        run, f"SELECT {sentinel} FROM memory_v1.observations"
                    )
                )
            ]
            parameterized = json.loads(
                self.query_bytes(
                    run,
                    "SELECT o.bead_id FROM memory_v1.observations o WHERE o.title = $1",
                )
            )
            parameterized["parameters"] = [
                {"position": 1, "type": "text", "value": sentinel}
            ]
            replies.append(transport.handle(json.dumps(parameterized).encode()))
            replies.append(
                transport.read(
                    "inspect",
                    json.dumps({"bead_id": sentinel}).encode(),
                )
            )
            # The host logs each request after replying: start plus three.
            for _ in range(500):
                if log.getvalue().count('"event": "request"') >= 4:
                    break
                time.sleep(0.01)
        outcomes = [json.loads(reply).get("outcome") for reply in replies]
        self.assertEqual(outcomes[1], "available", replies[1])
        # The log correlates with the executor's receipts by identifier only.
        answered = json.loads(replies[1])
        logged = [
            (event.get("run_ref"), event.get("access_receipt_ref"))
            for event in map(json.loads, log.getvalue().splitlines())
            if event.get("event") == "request"
        ]
        self.assertIn(
            (answered["run_ref"], answered["access_receipt_ref"]), logged, logged
        )
        for reply in replies:
            self.assertNotIn(sentinel.encode(), reply)
        self.assertNotIn(sentinel, log.getvalue())
        self.assertNotIn(self.agent_secret, log.getvalue())
        self.assertNotIn(self.agent_hash, log.getvalue())

    def test_a_page_that_cannot_fit_is_refused_never_empty(self) -> None:
        # PR-05 7aa767b: a row larger than the page's transport limit refuses
        # instead of paging as empty with a cursor pointing back at itself. The
        # host passes that refusal through on first and later pages alike.
        self.serve_in_process()
        transport = self.transport()
        run = self.start(transport)
        ordered = "SELECT o.bead_id FROM memory_v1.observations o"
        reply = json.loads(
            transport.handle(self.query_bytes(run, ordered + " ORDER BY o.bead_id"))
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertTrue(reply["page"]["has_more"])
        request = json.loads(
            self.query_bytes(
                run, ordered + " WHERE o.bead_type_key=$1 ORDER BY o.bead_id"
            )
        )
        request["parameters"] = [
            {"position": 1, "type": "text", "value": "no-such-type"}
        ]
        empty = json.loads(transport.handle(json.dumps(request).encode()))
        self.assertEqual(empty["page"]["rows"], [], empty)
        one_row = int(reply["work"]["transport_bytes"])
        no_rows = int(empty["work"]["transport_bytes"])
        self.assertGreater(one_row - no_rows, 128)
        admit = PostgresAgentSqlResults._admit

        def tight(
            executor: PostgresAgentSqlResults, request: Any, kind: str, fingerprint: str
        ) -> Any:
            admission = admit(executor, request, kind, fingerprint)
            admission["reserved_transport"] = (one_row + no_rows) // 2
            return admission

        with patch.object(PostgresAgentSqlResults, "_admit", tight):
            first = transport.handle(self.reuse_bytes(run, reply["result"], None))
            later = transport.handle(
                self.reuse_bytes(run, reply["result"], reply["page"]["next_cursor"])
            )
        for raw in (first, later):
            answer = json.loads(raw)
            self.assertEqual(
                (answer["outcome"], answer["error"]),
                ("budget_exhausted", {"code": "transport"}),
            )
            self.assertNotIn("page", answer)

    def test_other_uid_is_refused_before_database_work(self) -> None:
        other = self.config.model_copy(update={"client_uid": os.geteuid() + 1})
        commit_config(stage_config(self.config_path, other), self.config_path)
        self.serve_in_process(other)
        with patch(
            "memoriesql.infrastructure.results_broker.server.TrustedHost.paired_agent",
            side_effect=AssertionError("authenticated a refused peer"),
        ):
            self.assertEqual(self.transport().start_run(), UNAVAILABLE_REPLY)

    def test_admission_bypass_cannot_write_elevate_or_widen(self) -> None:
        self.serve_in_process()
        transport = self.transport()
        run = self.start(transport)
        # Only the five operations exist; other kinds reach the executor's own
        # closed refusals, never an owner action.
        for kind in ("close_run", "inspect", "hydrate_source"):
            data = json.loads(self.query_bytes(run, OBSERVATIONS))
            data["kind"] = kind
            reply = json.loads(transport.handle(json.dumps(data).encode()))
            self.assertIn(reply["outcome"], {"unsupported_query", "invalid_request"})
        for text in (
            "SELECT set_config('work_mem','1GB',false) AS s",
            "DELETE FROM memory_v1.observations",
            "SELECT pg_read_file('/etc/passwd') AS f",
            "SELECT * FROM memoriesql.beads",
        ):
            reply = json.loads(transport.handle(self.query_bytes(run, text)))
            self.assertIn(
                reply["outcome"], {"unsupported_query", "invalid_request"}, text
            )
        # A raw reader login (as if admission were bypassed) still cannot write,
        # elevate or read canonical tables; USERSET changes remain possible for
        # a holder of raw credentials, the packet's stated limitation.
        with psycopg.connect(self.config.reader_conninfo(), autocommit=True) as raw:
            for statement in (
                "INSERT INTO memoriesql.beads DEFAULT VALUES",
                "SET ROLE memoriesql_application",
                "SELECT count(*) FROM memoriesql.beads",
                "CREATE TEMP TABLE t(x int)",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        raw.execute(statement)
            raw.execute("SET work_mem='8MB'")
        with psycopg.connect(
            self.config.control_conninfo(), autocommit=True
        ) as control:
            for statement in (
                "CREATE ROLE mqh_escalation",
                "ALTER ROLE CURRENT_USER BYPASSRLS",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        control.execute(statement)

    def test_operator_close_is_owner_only_and_refused_while_unsettled(self) -> None:
        self.serve_in_process()
        transport = self.transport()
        run = self.start(transport)
        code, listing = self.operator(admin.list_runs, self.config_path)
        self.assertEqual(code, 0, listing)
        self.assertIn(run["run_ref"], listing)
        self.assertNotIn(self.agent_hash, listing)

        class Crash(BaseException):
            pass

        def crash(*args: Any, **kwargs: Any) -> bytes:
            raise Crash()

        request = self.query_bytes(run, OBSERVATIONS)
        with patch.object(PostgresAgentSqlResults, "_execute", crash):
            with self.assertRaises(Crash):
                prepare_host(self.config).executor(self.agent_hash).handle(request)
        code, refused = self.operator(
            admin.close_run, self.config_path, UUID(run["run_ref"])
        )
        self.assertEqual(code, admin.EXIT_FAILED, refused)
        self.assertEqual(json.loads(refused)["error"], {"code": "settlement"})
        # The next request recovers the dead owner's delivery; close then works.
        fresh = json.loads(transport.handle(self.query_bytes(run, OBSERVATIONS)))
        self.assertEqual(fresh["outcome"], "available", fresh)
        code, closed = self.operator(
            admin.close_run, self.config_path, UUID(run["run_ref"])
        )
        self.assertEqual(code, 0, closed)
        self.assertEqual(json.loads(closed)["outcome"], "available")
        after = json.loads(transport.handle(self.query_bytes(run, OBSERVATIONS)))
        self.assertEqual(after["outcome"], "budget_exhausted", after)
        code, unknown = self.operator(admin.close_run, self.config_path, uuid4())
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertEqual(unknown.strip().encode(), UNAVAILABLE_REPLY)

    def test_provisioning_rotation_and_privilege_preflight(self) -> None:
        before = self.config
        code, output = self.operator(
            admin.provision,
            self.config_path,
            rotate=True,
            admin_url=lambda: self.admin_url,
        )
        self.assertEqual(code, 0, output)
        self.assertNotIn(before.control.password.get_secret_value(), output)
        after = load_config(self.config_path)
        self.assertEqual(after.profile_sha256, before.profile_sha256)
        with self.assertRaises(psycopg.OperationalError):
            psycopg.connect(before.control_conninfo()).close()
        with self.assertRaises(psycopg.OperationalError):
            psycopg.connect(before.reader_conninfo()).close()
        prepare_host(after)
        # Once the roles change, the new secrets are durable even if pinning
        # then fails: the host is never left holding passwords the database
        # no longer accepts.
        with patch(
            "memoriesql.infrastructure.results_broker.admin.reviewed_query_reader_profile",
            side_effect=RuntimeError("reviewed profile unavailable"),
        ):
            code, output = self.operator(
                admin.provision,
                self.config_path,
                rotate=True,
                admin_url=lambda: self.admin_url,
            )
        self.assertEqual(code, admin.EXIT_REFUSED, output)
        self.assertIn("not pinned", output)
        interrupted = load_config(self.config_path)
        self.assertNotEqual(
            interrupted.control.password.get_secret_value(),
            after.control.password.get_secret_value(),
        )
        psycopg.connect(interrupted.control_conninfo()).close()
        psycopg.connect(interrupted.reader_conninfo()).close()
        code, output = self.operator(
            admin.provision,
            self.config_path,
            rotate=True,
            admin_url=lambda: self.admin_url,
        )
        self.assertEqual(code, 0, output)
        after = load_config(self.config_path)
        stored = self.scalar(
            "SELECT rolpassword FROM pg_authid WHERE rolname=%s", (after.control.role,)
        )
        self.assertTrue(str(stored).startswith("SCRAM-SHA-256$4096:"))
        # Least privilege: NOINHERIT membership, superuser and a drifted pin refuse.
        control = sql.Identifier(after.control.role)
        self.db.execute(
            sql.SQL("REVOKE memoriesql_application FROM {}").format(control)
        )
        self.db.execute(
            sql.SQL(
                "GRANT memoriesql_application TO {} WITH INHERIT FALSE, SET TRUE"
            ).format(control)
        )
        with self.assertRaisesRegex(HostRefused, "INHERIT member"):
            prepare_host(after)
        self.db.execute(
            sql.SQL("REVOKE memoriesql_application FROM {}").format(control)
        )
        self.db.execute(
            sql.SQL(
                "GRANT memoriesql_application TO {} WITH INHERIT TRUE, SET TRUE"
            ).format(control)
        )
        self.db.execute(sql.SQL("ALTER ROLE {} SUPERUSER").format(control))
        with self.assertRaisesRegex(HostRefused, "superuser"):
            prepare_host(after)
        self.db.execute(sql.SQL("ALTER ROLE {} NOSUPERUSER").format(control))
        reader = sql.Identifier(after.reader.role)
        self.db.execute(sql.SQL("REVOKE {} FROM {}").format(reader, control))
        with self.assertRaisesRegex(HostRefused, "inherit its reader role"):
            prepare_host(after)
        self.db.execute(
            sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(
                reader, control
            )
        )
        self.db.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
            ).format(control)
        )
        with self.assertRaisesRegex(HostRefused, "default_procedure_privileges"):
            prepare_host(after)
        self.db.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            ).format(control)
        )
        prepare_host(after)
        with self.assertRaisesRegex(HostRefused, "pinned profile"):
            prepare_host(after.model_copy(update={"profile_sha256": "f" * 64}))
        code, report = self.operator(admin.check, self.config_path)
        checks = {item["check"]: item for item in json.loads(report)["checks"]}
        self.assertTrue(checks["database_authority"]["ok"], report)
        self.assertTrue(checks["configuration"]["ok"], report)
        for role in self.roles:
            self.assertTrue(checks[f"passwordless_login_refused:{role}"]["ok"], report)
        # Here the service and client are one uid, which check must report.
        self.assertFalse(checks["client_cannot_modify_host"]["ok"], report)
        self.assertEqual(code, admin.EXIT_REFUSED)

    def test_restart_redelivers_the_same_result_without_rerun(self) -> None:
        host = self.spawn()
        transport = self.transport()
        run = self.start(transport)
        request = self.query_bytes(run, OBSERVATIONS)
        first = json.loads(transport.handle(request))
        self.assertEqual(first["outcome"], "available", first)
        invocations = self.scalar("SELECT count(*) FROM memoriesql_query.invocations")
        host.send_signal(signal.SIGTERM)
        self.assertEqual(host.wait(60), 0)
        self.assertFalse(self.config.socket_path.exists())
        self.spawn()
        again = json.loads(transport.handle(request))
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["result"]["result_id"], first["result"]["result_id"])
        self.assertEqual(
            again["result"]["content_digest"], first["result"]["content_digest"]
        )
        self.assertNotEqual(again["access_receipt_ref"], first["access_receipt_ref"])
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.invocations"),
            invocations,
        )

    def _paused_query(
        self, transport: BrokerTransport, run: dict[str, Any], request: bytes
    ) -> tuple[threading.Thread, list[bytes], int]:
        replies: list[bytes] = []
        worker = threading.Thread(
            target=lambda: replies.append(transport.handle(request))
        )
        worker.start()
        pid_file = self.gates / "reader.pid"
        for _ in range(600):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.05)
        else:
            self.fail("reader never reached the admitted SELECT")
        return worker, replies, int(pid_file.read_text())

    def _assert_capacity_held(
        self, run: dict[str, Any], lost: bytes, reader_pid: int
    ) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM pg_stat_activity WHERE pid=%s", (reader_pid,)
            ),
            1,
        )
        self.assertEqual(
            self.scalar(
                "SELECT state FROM memoriesql_query.invocations WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            "executing",
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries "
                "WHERE run_ref=%s AND state<>'settled'",
                (run["run_ref"],),
            ),
            1,
        )
        executor = prepare_host(self.config).executor(self.agent_hash)
        self.assertEqual(executor.recover_abandoned(), 0)
        code, refused = self.operator(
            admin.close_run, self.config_path, UUID(run["run_ref"])
        )
        self.assertNotEqual(code, 0, refused)
        self.assertEqual(json.loads(refused)["error"], {"code": "settlement"})
        pending = json.loads(executor.handle(lost))
        self.assertEqual(pending["outcome"], "settlement_pending", pending)
        blocked = json.loads(executor.handle(self.query_bytes(run, OBSERVATIONS)))
        self.assertEqual(blocked["error"], {"code": "settlement"}, blocked)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_run_closures"), 0
        )

    def test_sigkill_mid_query_keeps_capacity_until_reader_ends(self) -> None:
        host = self.spawn(fault="reader")
        transport = self.transport()
        run = self.start(transport)
        lost = self.query_bytes(run, OBSERVATIONS)
        worker, replies, reader_pid = self._paused_query(transport, run, lost)
        # Freeze the reader backend, release the gate so the SELECT is sent to
        # it, then kill the whole host while that SELECT is still in PostgreSQL.
        self.signal_backend(reader_pid, "STOP")
        (self.gates / "reader.go").write_text("go")
        time.sleep(0.5)
        host.send_signal(signal.SIGKILL)
        host.wait(30)
        worker.join(30)
        self.assertEqual(replies, [UNAVAILABLE_REPLY])
        try:
            self._assert_capacity_held(run, lost, reader_pid)
        finally:
            self.signal_backend(reader_pid, "CONT")
        self.wait_backend_gone(reader_pid)
        (self.gates / "reader.go").unlink()
        (self.gates / "reader.pid").unlink()
        # A restarted host recovers on the next request; nothing is replayed.
        self.spawn()
        fresh = json.loads(transport.handle(self.query_bytes(run, OBSERVATIONS)))
        self.assertEqual(fresh["outcome"], "available", fresh)
        abandoned = self.db.execute(
            "SELECT outcome,charged_db_ms,reserved_db_ms FROM memoriesql.query_deliveries "
            "WHERE run_ref=%s AND outcome='abandoned'",
            (run["run_ref"],),
        ).fetchall()
        self.assertEqual(len(abandoned), 1, abandoned)
        self.assertGreaterEqual(abandoned[0][1], abandoned[0][2])
        again = json.loads(transport.handle(lost))
        self.assertEqual(again["outcome"], "execution_error", again)
        code, closed = self.operator(
            admin.close_run, self.config_path, UUID(run["run_ref"])
        )
        self.assertEqual(code, 0, closed)

    def test_lost_owner_connection_keeps_capacity_until_work_ends(self) -> None:
        self.spawn(fault="reader")
        transport = self.transport()
        run = self.start(transport)
        lost = self.query_bytes(run, OBSERVATIONS)
        worker, replies, reader_pid = self._paused_query(transport, run, lost)
        # The executor's owner session holds the delivery's session advisory lock.
        owner = self.scalar(
            "SELECT l.pid FROM memoriesql.query_deliveries d "
            "JOIN pg_locks l ON l.locktype='advisory' AND l.granted AND l.objsubid=1 "
            "AND ((l.classid::bigint<<32)|l.objid::bigint)="
            "hashtextextended(d.tenant_id::text||':query-delivery:'||d.delivery_ref::text,0) "
            "WHERE d.run_ref=%s AND d.state='reserved'",
            (run["run_ref"],),
        )
        self.assertIsNotNone(owner)
        self.assertTrue(self.scalar("SELECT pg_terminate_backend(%s)", (owner,)))
        self.signal_backend(reader_pid, "STOP")
        (self.gates / "reader.go").write_text("go")
        time.sleep(0.5)
        try:
            self._assert_capacity_held(run, lost, reader_pid)
        finally:
            self.signal_backend(reader_pid, "CONT")
        worker.join(60)
        self.assertEqual(len(replies), 1)
        outcome = json.loads(replies[0])["outcome"]
        self.assertIn(outcome, {"available", "execution_error", "unavailable"}, replies)
        self.wait_backend_gone(reader_pid) if outcome != "available" else None
        # Whatever the lost-owner delivery became, it is settled exactly once
        # and the workspace admits new work afterwards.
        fresh = json.loads(transport.handle(self.query_bytes(run, OBSERVATIONS)))
        self.assertEqual(fresh["outcome"], "available", fresh)
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries "
                "WHERE run_ref=%s AND state<>'settled'",
                (run["run_ref"],),
            ),
            0,
        )

    def owner_backend(self, run: dict[str, Any]) -> int:
        """The executor's owner session: holder of the delivery's advisory lock."""
        owner = self.scalar(
            "SELECT l.pid FROM memoriesql.query_deliveries d "
            "JOIN pg_locks l ON l.locktype='advisory' AND l.granted AND l.objsubid=1 "
            "AND ((l.classid::bigint<<32)|l.objid::bigint)="
            "hashtextextended(d.tenant_id::text||':query-delivery:'||d.delivery_ref::text,0) "
            "WHERE d.run_ref=%s AND d.state='reserved'",
            (run["run_ref"],),
        )
        self.assertIsNotNone(owner)
        return int(owner)

    def test_owner_loss_after_reader_settles_settles_once_without_receipt(
        self,
    ) -> None:
        self.spawn(fault="commit")
        transport = self.transport()
        run = self.start(transport)
        lost = self.query_bytes(run, OBSERVATIONS)
        replies: list[bytes] = []
        worker = threading.Thread(target=lambda: replies.append(transport.handle(lost)))
        worker.start()
        for _ in range(600):
            if (self.gates / "commit.ready").exists():
                break
            time.sleep(0.05)
        else:
            self.fail("host never reached the result commit")
        # The reader has finished and settled; only commit and disclosure remain.
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql_query.invocations "
                "WHERE run_ref=%s AND state<>'settled'",
                (run["run_ref"],),
            ),
            0,
        )
        self.assertTrue(
            self.scalar("SELECT pg_terminate_backend(%s)", (self.owner_backend(run),))
        )
        executor = prepare_host(self.config).executor(self.agent_hash)
        for _ in range(100):
            if executor.recover_abandoned():
                break
            time.sleep(0.05)
        (self.gates / "commit.go").write_text("go")
        worker.join(60)
        self.assertEqual(len(replies), 1)
        # The late commit itself succeeds (its issuer is alive), but the access
        # was taken over by recovery: the host thread may not disclose, and its
        # outcome is settlement_pending, never a false failure or a discard.
        late = json.loads(replies[0])
        self.assertEqual(late["outcome"], "settlement_pending", late)
        self.assertEqual(late["error"], {"code": "settlement"}, late)
        self.assertNotIn("result", late)
        self.assertNotIn("page", late)
        deliveries = self.db.execute(
            "SELECT delivery_ref,state,outcome,charged_db_ms,reserved_db_ms "
            "FROM memoriesql.query_deliveries WHERE run_ref=%s",
            (run["run_ref"],),
        ).fetchall()
        self.assertEqual(len(deliveries), 1, deliveries)
        delivery, state, outcome, charged, reserved = deliveries[0]
        self.assertEqual((state, outcome), ("settled", "abandoned"))
        self.assertGreaterEqual(charged, reserved)
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.query_disclosures WHERE delivery_ref=%s",
                (delivery,),
            ),
            0,
        )
        invocations = self.scalar(
            "SELECT count(*) FROM memoriesql_query.invocations WHERE run_ref=%s",
            (run["run_ref"],),
        )
        self.assertEqual(invocations, 1)
        # A committed result is immutable: a failed first disclosure never
        # discards it.
        committed = self.db.execute(
            "SELECT o.state,x.result_id FROM memoriesql.result_preparation_operations o "
            "JOIN memoriesql.query_result_creations x ON x.tenant_id=o.tenant_id "
            "AND x.operation_ref=o.operation_ref WHERE o.run_ref=%s",
            (run["run_ref"],),
        ).fetchall()
        self.assertEqual(len(committed), 1, committed)
        self.assertEqual(committed[0][0], "sealed")
        # Exact redelivery discloses that committed result under current
        # authority, with a new access receipt and without rerunning the SELECT.
        again = json.loads(transport.handle(lost))
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["result"]["result_id"], str(committed[0][1]))
        self.assertNotEqual(again["access_receipt_ref"], late["access_receipt_ref"])
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql_query.invocations WHERE run_ref=%s",
                (run["run_ref"],),
            ),
            invocations,
        )
        fresh = json.loads(transport.handle(self.query_bytes(run, OBSERVATIONS)))
        self.assertEqual(fresh["outcome"], "available", fresh)

    def test_host_runs_expiry_cleanup_and_check_reports_it(self) -> None:
        self.serve_in_process()
        self.start()
        record = self.config.state_dir / "cleanup.json"
        for _ in range(200):
            if record.exists():
                break
            time.sleep(0.05)
        self.assertEqual(json.loads(record.read_text())["outcome"], "available")
        code, output = self.operator(admin.cleanup, self.config_path)
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["outcome"], "available")
        code, report = self.operator(admin.check, self.config_path)
        checks = {item["check"]: item for item in json.loads(report)["checks"]}
        self.assertTrue(checks["cleanup_recent"]["ok"], report)
        self.assertTrue(checks["cleanup_status"]["ok"], report)
        record.write_text(
            json.dumps(
                {
                    "at": "2000-01-01T00:00:00+00:00",
                    "outcome": "available",
                    "cleanup": {},
                }
            )
        )
        code, report = self.operator(admin.check, self.config_path)
        checks = {item["check"]: item for item in json.loads(report)["checks"]}
        self.assertFalse(checks["cleanup_recent"]["ok"], report)


if __name__ == "__main__":
    unittest.main()


# PR-05's AM-5 reads (tests/runtime/test_agent_sql_results.py), trimmed to the
# columns these cases compare. Rows are selected, never filtered by id: admission
# refuses literals, and a relation_ref parameter must be an admitted anchor.
RELATION_READS = {
    "assessed_relations": (
        "SELECT r.relation_id,r.type_key,r.basis,r.state "
        "FROM memory_v1.assessed_relations r ORDER BY r.relation_id"
    ),
    "relation_statements": (
        "SELECT s.relation_id,s.role,s.statement_id "
        "FROM memory_v1.relation_statements s "
        "ORDER BY s.relation_id,s.role,s.statement_id"
    ),
    "relation_evidence": (
        "SELECT e.relation_id,e.statement_id,e.source_unit_id "
        "FROM memory_v1.relation_evidence e "
        "ORDER BY e.relation_id,e.statement_id,e.source_unit_id"
    ),
    "relation_types": (
        "SELECT t.type_key,t.type_revision FROM memory_v1.relation_types t "
        "ORDER BY t.type_key,t.type_revision"
    ),
}
WITHHELD: dict[str, list[list[Any]]] = {table: [] for table in RELATION_READS}


class QueryHostRelationReads(QueryHostHarness):
    """AM-5 through the socket: an agent reads a relation whole or not at all."""

    def setUp(self) -> None:
        super().setUp()
        # A dedicated agent paired once over a fresh explicit scope. It cannot
        # read the harness's own assertion relation, so any 'supports' pin it
        # sees belongs to this relation alone. PR-05's shared fixture then
        # relates beads in two new explicit scopes, which that single pairing
        # now reads.
        home, _source = relation_agents.explicit_scope(self.db, self.fixture)
        grant, self.relation_secret = self.pair_agent(AGENT_CAPABILITIES, scopes=[home])
        self.relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.principals[grant]
        )
        self.serve_in_process()

    def ask(
        self, transport: BrokerTransport, run: dict[str, Any], text: str
    ) -> dict[str, Any]:
        reply: dict[str, Any] = json.loads(
            transport.handle(self.query_bytes(run, text, page_size=50))
        )
        self.assertEqual(reply["outcome"], "available", reply)
        return reply

    def relation_rows(
        self, transport: BrokerTransport, run: dict[str, Any]
    ) -> dict[str, list[list[Any]]]:
        """Each agent relation table's rows of the fixture relation, via the host."""
        seen: dict[str, list[list[Any]]] = {}
        for table, text in RELATION_READS.items():
            reply = self.ask(transport, run, text)
            self.assertFalse(reply["page"]["has_more"], table)
            self.assertEqual(reply["result"]["coverage"]["gaps"], [], table)
            key = (
                "supports"
                if table == "relation_types"
                else str(self.relation.relation_id)
            )
            rows = [row["values"] for row in reply["page"]["rows"]]
            seen[table] = [values for values in rows if values[0] == key]
        return seen

    def test_an_agent_reads_a_whole_relation_as_the_executors_bytes(self) -> None:
        captured: list[bytes] = []
        handle = PostgresAgentSqlResults.handle

        def spy(executor: PostgresAgentSqlResults, data: bytes) -> bytes:
            captured.append(handle(executor, data))
            return captured[-1]

        transport = self.transport(self.relation_secret)
        run = self.start(transport)
        with patch.object(PostgresAgentSqlResults, "handle", spy):
            raw = transport.handle(
                self.query_bytes(
                    run, RELATION_READS["assessed_relations"], page_size=50
                )
            )
        self.assertEqual(raw, captured[-1])
        seen = self.relation_rows(transport, run)
        self.assertEqual(
            [values[1:] for values in seen["assessed_relations"]],
            [["supports", "agent_inferred", "active"]],
        )
        self.assertEqual(
            {values[1] for values in seen["relation_statements"]}, {"source", "target"}
        )
        self.assertTrue(seen["relation_evidence"])
        self.assertEqual(len(seen["relation_types"]), 1)

    def test_one_unreadable_member_withholds_the_relation_whole(self) -> None:
        transport = self.transport(self.relation_secret)
        run = self.start(transport)
        saved = self.ask(transport, run, RELATION_READS["assessed_relations"])
        relation = str(self.relation.relation_id)
        self.assertIn(relation, {row["values"][0] for row in saved["page"]["rows"]})
        self.assertEqual(len(self.relation_rows(transport, run)["relation_types"]), 1)
        relation_agents.revoke_member(self.db, self.fixture, self.relation)
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            # A fresh step of the same run: absent everywhere, no gap or type pin.
            after = self.relation_rows(transport, run)
            again = json.loads(
                transport.handle(self.reuse_bytes(run, saved["result"], None))
            )
            for _ in range(500):
                if log.getvalue().count('"event": "request"') >= 5:
                    break
                time.sleep(0.01)
        self.assertEqual(after, WITHHELD)
        # The saved result that held it is no longer disclosed at all.
        self.assertEqual(again["outcome"], "unavailable", again)
        self.assertNotIn("result", again)
        self.assertNotIn(relation, log.getvalue())
        # The source endpoint stays readable on its own.
        observations = self.ask(transport, run, OBSERVATIONS)
        self.assertIn(
            str(self.relation.source_bead_id),
            {row["values"][0] for row in observations["page"]["rows"]},
        )

    def test_without_source_read_relation_tables_are_a_gap(self) -> None:
        # memory.query over every closure scope but no source.read: only the
        # agent gate withholds the relation.
        _grant, blind = self.pair_agent(
            ["memory.inspect", "memory.query"],
            scopes=[self.relation.source_scope, self.relation.remote_scope],
        )
        transport = self.transport(blind)
        run = self.start(transport)
        reply = self.ask(transport, run, RELATION_READS["assessed_relations"])
        self.assertEqual(reply["page"]["rows"], [])
        self.assertEqual(
            reply["result"]["coverage"]["gaps"],
            [{"facet": "relation_tables", "reason": "source_read_required"}],
        )

    def test_relation_history_and_the_inspection_reader_stay_owner_only(self) -> None:
        transport = self.transport(self.relation_secret)
        run = self.start(transport)
        for table in ("relation_events", "relation_event_evidence", "relation_pairs"):
            reply = self.ask(
                transport, run, f"SELECT count(*) AS n FROM memory_v1.{table} t"
            )
            self.assertEqual(reply["page"]["rows"][0]["values"], ["0"], table)
            self.assertEqual(
                reply["result"]["coverage"]["gaps"],
                [{"facet": "relation_history", "reason": "owner_only"}],
                table,
            )
        request = InspectBeadRelationsV2(bead_id=self.relation.source_bead_id)
        inspected = json.loads(
            transport.read("relations", request.model_dump_json().encode())
        )
        self.assertEqual(inspected["outcome"], "unavailable")
