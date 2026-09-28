"""The CLI reaches the trusted query host only through its socket client."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

from memoriesql.cli import main

WORKSPACE = UUID(int=21)
BEAD = UUID(int=22)
SECRET = "fictional-paired-agent-secret-with-ample-length"
AGENT_ENV = {
    "MEMORIESQL_RESULTS_SOCKET": "/fictional/query-host/host.sock",
    "MEMORIESQL_LOCAL_CREDENTIAL": SECRET,
    "MEMORIESQL_WORKSPACE_ID": str(WORKSPACE),
    # Present only to prove it is never used once the socket is configured.
    "MEMORIESQL_DATABASE_URL": "postgresql://fictional.example/never-used",
}
RUN = {
    "contract_version": 1,
    "outcome": "available",
    "run": {
        "run_ref": str(UUID(int=23)),
        "expires_at": "2099-01-01T00:00:00Z",
        "default_known_at": "2026-09-28T21:26:24.818Z",
    },
}
REPLY = b'{"contract_version":1,"outcome":"available","run_ref":"x"}'


class FakeHost:
    instances: list[FakeHost] = []

    def __init__(self, socket_path: Path, credential: str, workspace_id: UUID) -> None:
        self.socket_path, self.credential, self.workspace_id = (
            socket_path,
            credential,
            workspace_id,
        )
        self.requests: list[bytes] = []
        self.reads: list[tuple[str, bytes]] = []
        FakeHost.instances.append(self)

    def start_run(self) -> bytes:
        return json.dumps(RUN).encode()

    def handle(self, request: bytes) -> bytes:
        self.requests.append(request)
        return REPLY

    def read(self, command: str, request: bytes) -> bytes:
        self.reads.append((command, request))
        return b'{"bead":null,"contract_version":1,"outcome":"unavailable"}'


def invoke(arguments: list[str], environment: dict[str, str]) -> tuple[int, str]:
    output = io.StringIO()
    with (
        patch.dict(os.environ, environment, clear=True),
        patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
        patch("memoriesql.cli.BrokerTransport", FakeHost),
        redirect_stdout(output),
    ):
        status = main(arguments)
    return status, output.getvalue()


class SocketClientTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeHost.instances.clear()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.environment = AGENT_ENV | {"MEMORIESQL_STATE_DIR": str(self.root)}

    def test_query_goes_only_to_the_host_with_the_agents_own_credential(self) -> None:
        sql = self.root / "query.sql"
        sql.write_text("SELECT o.bead_id FROM memory_v1.observations o", "utf-8")
        status, output = invoke(
            [
                "query",
                "--file",
                str(sql),
                "--intent",
                "enumerate",
                "--view",
                "resolved",
                "--json",
            ],
            self.environment,
        )
        self.assertEqual(status, 0, output)
        self.assertEqual(output.encode(), REPLY + b"\n")
        (host,) = FakeHost.instances
        self.assertEqual(host.socket_path, Path(AGENT_ENV["MEMORIESQL_RESULTS_SOCKET"]))
        self.assertEqual(host.credential, SECRET)
        self.assertEqual(host.workspace_id, WORKSPACE)
        (request,) = host.requests
        self.assertNotIn(SECRET, request.decode())
        self.assertNotIn("never-used", request.decode())

    def test_reads_go_only_to_the_host(self) -> None:
        status, output = invoke(["inspect", str(BEAD), "--json"], self.environment)
        self.assertEqual(status, 2, output)
        self.assertEqual(json.loads(output)["outcome"], "unavailable")
        (host,) = FakeHost.instances
        ((command, body),) = host.reads
        self.assertEqual(command, "inspect")
        self.assertEqual(json.loads(body)["bead_id"], str(BEAD))

    def test_socket_without_the_agents_identity_contacts_nothing(self) -> None:
        environment = {
            "MEMORIESQL_RESULTS_SOCKET": AGENT_ENV["MEMORIESQL_RESULTS_SOCKET"],
            "MEMORIESQL_DATABASE_URL": AGENT_ENV["MEMORIESQL_DATABASE_URL"],
            "MEMORIESQL_STATE_DIR": str(self.root),
        }
        sql = self.root / "query.sql"
        sql.write_text("SELECT 1", "utf-8")
        for arguments in (
            ["inspect", str(BEAD), "--json"],
            [
                "query",
                "--file",
                str(sql),
                "--intent",
                "discover",
                "--view",
                "resolved",
                "--json",
            ],
        ):
            status, output = invoke(arguments, environment)
            self.assertEqual(status, 2, output)
            self.assertEqual(
                json.loads(output),
                {"outcome": "unavailable", "reason": "local_read_identity_required"},
            )
        self.assertEqual(FakeHost.instances, [])

    def test_capabilities_and_doctor_report_the_host_protocol(self) -> None:
        status, output = invoke(["capabilities", "--json"], self.environment)
        self.assertEqual(status, 0)
        capabilities = json.loads(output)
        self.assertEqual(capabilities["query_host_protocol_version"], 1)
        self.assertIn("broker serve", capabilities["core_commands"])
        reads = capabilities["paired_agent_reads"]
        self.assertEqual(reads["query"], "available")
        for command in ("inspect", "source", "relations"):
            self.assertTrue(reads[command].startswith("resource_unavailable"))
        status, output = invoke(["doctor", "--json"], self.environment)
        self.assertEqual(status, 0)
        doctor = json.loads(output)
        self.assertTrue(doctor["query_host_socket_configured"])
        self.assertEqual(doctor["query_host_protocol_version"], 1)
        self.assertNotIn(SECRET, output)


class BrokerCommandTests(unittest.TestCase):
    def test_operator_commands_dispatch_to_the_host_entry_points(self) -> None:
        config = Path("/fictional/query-host/config.json")
        calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        def recorder(name: str, code: int) -> Any:
            def record(*args: Any, **kwargs: Any) -> int:
                calls.append((name, args, kwargs))
                return code

            return record

        with (
            patch(
                "memoriesql.infrastructure.results_broker.server.serve",
                recorder("serve", 0),
            ),
            patch(
                "memoriesql.infrastructure.results_broker.admin.provision",
                recorder("provision", 2),
            ),
            patch(
                "memoriesql.infrastructure.results_broker.admin.check",
                recorder("check", 3),
            ),
            patch(
                "memoriesql.infrastructure.results_broker.admin.list_runs",
                recorder("runs", 0),
            ),
            patch(
                "memoriesql.infrastructure.results_broker.admin.close_run",
                recorder("close-run", 0),
            ),
        ):
            self.assertEqual(main(["broker", "serve", "--config", str(config)]), 0)
            self.assertEqual(
                main(
                    [
                        "broker",
                        "provision",
                        "--config",
                        str(config),
                        "--workspace-id",
                        str(WORKSPACE),
                        "--client-uid",
                        "501",
                        "--database-port",
                        "5432",
                        "--rotate",
                        "--reader-role",
                        "fictional_reader",
                    ]
                ),
                2,
            )
            self.assertEqual(main(["broker", "check", "--config", str(config)]), 3)
            self.assertEqual(main(["broker", "runs", "--config", str(config)]), 0)
            run_ref = UUID(int=24)
            self.assertEqual(
                main(["broker", "close-run", "--config", str(config), str(run_ref)]),
                0,
            )
        names = [name for name, _, _ in calls]
        self.assertEqual(names, ["serve", "provision", "check", "runs", "close-run"])
        _, arguments, options = calls[1]
        self.assertEqual(arguments, (config,))
        self.assertEqual(options["workspace_id"], WORKSPACE)
        self.assertEqual(options["client_uid"], 501)
        self.assertEqual(options["database_port"], 5432)
        self.assertIsNone(options["database_host"])
        self.assertTrue(options["rotate"])
        self.assertEqual(options["control_role"], "memoriesql_query_host")
        self.assertEqual(options["reader_role"], "fictional_reader")
        self.assertNotIn("admin_url", options)
        self.assertEqual(calls[4][1], (config, run_ref))


if __name__ == "__main__":
    unittest.main()
