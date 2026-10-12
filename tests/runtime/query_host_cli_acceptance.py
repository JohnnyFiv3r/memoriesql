"""The public CLI reaches memory only through the trusted query host socket.

Run by scripts/prove_query_host.py beside the host acceptance cases: it needs a
host process and a Unix socket, which the confined acceptance runner denies.
Every case uses the fictional provisioned host and paired agent of the harness.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.cli import main as cli_main
from memoriesql.infrastructure.results_broker.client import BrokerTransport
from memoriesql.infrastructure.results_broker.wire import UNAVAILABLE_REPLY

if TYPE_CHECKING:
    from tests.runtime.query_host_acceptance import OBSERVATIONS, QueryHostHarness
else:
    from query_host_acceptance import OBSERVATIONS, QueryHostHarness


class QueryHostCliAcceptance(QueryHostHarness):
    def test_public_cli_reaches_memory_only_through_the_host_socket(self) -> None:
        """The agent's CLI holds its own paired secret and a socket, nothing else."""
        # A separate host process, so refusing database connections below
        # applies only to the agent's CLI in this process.
        self.spawn()
        environment = {
            "MEMORIESQL_RESULTS_SOCKET": str(self.config.socket_path),
            "MEMORIESQL_LOCAL_CREDENTIAL": self.agent_secret,
            "MEMORIESQL_WORKSPACE_ID": str(self.fixture.workspace),
            "MEMORIESQL_STATE_DIR": str(self.root / "agent-state"),
        }
        query = self.root / "agent.sql"
        query.write_text(OBSERVATIONS, encoding="utf-8")

        def cli(arguments: list[str], *, pinned: bool = True) -> tuple[int, Any]:
            output = io.StringIO()
            # This test host runs as the caller's own uid; production refuses
            # that by default, so only the peer uid is pinned here.
            transport = (
                partial(BrokerTransport, expected_peer_uid=os.geteuid())
                if pinned
                else BrokerTransport
            )
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("memoriesql.cli.BrokerTransport", transport),
                patch("psycopg.connect", side_effect=AssertionError("no database")),
                contextlib.redirect_stdout(output),
            ):
                status = cli_main([*arguments, "--json"])
            return status, json.loads(output.getvalue())

        status, schema = cli(["schema"])
        self.assertEqual(status, 0, schema)
        self.assertEqual(schema["catalog_hash"], SqlCatalog.installed().hash)
        status, reply = cli(
            [
                "query",
                "--file",
                str(query),
                "--intent",
                "enumerate",
                "--view",
                "resolved",
                "--page-size",
                "1",
            ]
        )
        self.assertEqual(status, 0, reply)
        self.assertEqual(reply["result"]["coverage"]["gaps"], [])
        self.assertGreaterEqual(int(reply["result"]["total_rows"]), 2)
        self.assertTrue(reply["page"]["has_more"])
        first = reply["page"]["rows"][0]["values"][0]
        status, page = cli(
            [
                "result",
                reply["result"]["result_id"],
                "--digest",
                reply["result"]["content_digest"],
                "--cursor",
                reply["page"]["next_cursor"],
                "--page-size",
                "1",
            ]
        )
        self.assertEqual(status, 0, page)
        self.assertEqual(page["run_ref"], reply["run_ref"])
        self.assertNotEqual(page["page"]["rows"][0]["values"][0], first)
        # The exact source readers stay owner-only for a paired agent.
        status, inspected = cli(["inspect", first])
        self.assertEqual((status, inspected["outcome"]), (2, "unavailable"))
        # Unpinned, the production client refuses a host running as the caller.
        status, refused = cli(["query", "--file", str(query), "--intent",
                               "discover", "--view", "resolved"], pinned=False)
        self.assertEqual((status, refused), (2, json.loads(UNAVAILABLE_REPLY)))
