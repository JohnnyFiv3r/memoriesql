"""Installed fictional local agent pairing against disposable PostgreSQL."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.authorization import LocalCredential
from memoriesql.application.source_enrollment import EnrollExactSource
from memoriesql.cli import main as cli_main
from memoriesql.domain.authorization import (
    ResourceKind,
    ResourceReference,
    ScopePermission,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.local_client_pairing import (
    pairing_identities,
)
from memoriesql.infrastructure.postgres.source_enrollment import (
    PostgresSourceEnrollment,
)

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate

OWNER_CREDENTIAL = "fictional orchard owner session credential"
AGENT_CAPABILITIES = ["memory.inspect", "memory.query", "source.read"]


class LocalClientPairing(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "pr06_pairing_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.url = make_conninfo(self.admin_url, dbname=self.database)
        self.db = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.cleanup_database)
        migrate(self.db, expected_current_version=0, target_version=36)
        (
            self.tenant,
            self.user,
            identity,
            self.principal,
            self.workspace,
            default_scope,
            membership,
            credential,
            session,
        ) = (uuid.uuid4() for _ in range(9))
        self.owner_secret = hashlib.sha256(OWNER_CREDENTIAL.encode("utf-8")).hexdigest()
        self.now = datetime.now(UTC)
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.bootstrap_personal_local(%s,%s,%s,%s,%s,%s,%s,%s,%s,'memoriesql.local',%s,'Fictional orchard',%s,%s,%s)",
                (
                    self.tenant,
                    self.user,
                    identity,
                    self.principal,
                    self.workspace,
                    default_scope,
                    membership,
                    credential,
                    session,
                    str(identity),
                    self.owner_secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = PostgresSourceEnrollment(
            self.db, credential_sha256=self.owner_secret, workspace_id=self.workspace
        ).enroll(
            EnrollExactSource(
                request_id=uuid.uuid4(),
                source_system="fictional-orchard",
                object_kind="transcript",
                external_object_id="orchard/paired-agent-session.jsonl",
                source_schema_version=1,
                exact_source_confirmed=True,
            )
        )

    def cleanup_database(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def run_cli(
        self, arguments: list[str], payload: dict[str, object], credential: str
    ) -> tuple[int, dict[str, Any]]:
        request = self.root / f"request-{uuid.uuid4()}.json"
        request.write_text(json.dumps(payload), encoding="utf-8")
        environment = {
            "MEMORIESQL_DATABASE_URL": self.url,
            "MEMORIESQL_LOCAL_CREDENTIAL": credential,
            "MEMORIESQL_WORKSPACE_ID": str(self.workspace),
        }
        output = io.StringIO()
        with patch.dict(os.environ, environment), redirect_stdout(output):
            status = cli_main([*arguments, "--request-file", str(request), "--json"])
        return status, json.loads(output.getvalue())

    def pair(
        self, payload: dict[str, object], secret: Path, credential: str
    ) -> tuple[int, dict[str, Any]]:
        return self.run_cli(
            ["clients", "pair", "--secret-file", str(secret)], payload, credential
        )

    def pairing_request(self, **changes: object) -> dict[str, object]:
        return {
            "request_id": str(uuid.uuid4()),
            "capabilities": AGENT_CAPABILITIES,
            "access_scope_ids": [str(self.source.access_scope_id)],
            "expires_at": (self.now + timedelta(hours=1)).isoformat(),
            "exact_pairing_confirmed": True,
        } | changes

    def source_readable(self, secret_sha256: str) -> bool:
        with psycopg.connect(self.url) as connection:
            try:
                with connection.transaction():
                    connection.execute("SET LOCAL ROLE memoriesql_application")
                    port = PostgresAuthorizationPort(connection)
                    context = port.begin_context(
                        credential_sha256=secret_sha256,
                        requested_workspace_id=self.workspace,
                    )
                    return port.authorize_resource(
                        context=context,
                        resource=ResourceReference(
                            kind=ResourceKind.SOURCE,
                            resource_id=self.source.source_object_id,
                        ),
                        capability="source.read",
                        permission=ScopePermission.READ,
                        request_id=uuid.uuid4(),
                    ).allowed
            except (PermissionError, psycopg.Error):
                return False

    def principal_count(self) -> int:
        row = self.db.execute("SELECT count(*) FROM memoriesql.principals").fetchone()
        assert row is not None
        return int(row[0])

    def test_cli_pairs_grants_and_revokes_one_agent_under_current_authority(
        self,
    ) -> None:
        request = self.pairing_request()
        secret_file = self.root / "agent.secret"
        status, paired = self.pair(request, secret_file, OWNER_CREDENTIAL)
        self.assertEqual(status, 0, paired)
        secret = secret_file.read_text(encoding="ascii")
        self.assertNotIn(secret, json.dumps(paired))
        self.assertEqual(stat.S_IMODE(secret_file.stat().st_mode), 0o600)
        principal, _pairing, grant, _credential = pairing_identities(
            uuid.UUID(str(request["request_id"]))
        )
        receipt = paired["receipt"]
        self.assertEqual(receipt["principal_id"], str(principal))
        self.assertEqual(receipt["pairing_grant_id"], str(grant))
        agent = LocalCredential(secret).sha256()
        self.assertFalse(
            self.source_readable(agent), "an explicit scope needs a source grant"
        )

        grant_request: dict[str, object] = {
            "request_id": str(uuid.uuid4()),
            "source_object_id": str(self.source.source_object_id),
            "target_principal_id": str(principal),
            "permission_keys": ["read"],
            "valid_from": self.now.isoformat(),
            "expires_at": (self.now + timedelta(hours=1)).isoformat(),
        }
        status, granted = self.run_cli(
            ["sources", "grant"], grant_request, OWNER_CREDENTIAL
        )
        self.assertEqual(status, 0, granted)
        self.assertTrue(self.source_readable(agent))

        principals = self.principal_count()
        replay_file = self.root / "replay.secret"
        status, replay = self.pair(request, replay_file, OWNER_CREDENTIAL)
        self.assertEqual(
            (status, replay),
            (2, {"outcome": "unavailable", "reason": "resource_unavailable"}),
        )
        self.assertFalse(replay_file.exists())
        self.assertEqual(self.principal_count(), principals)

        revoke: dict[str, object] = {
            "pairing_grant_id": str(grant),
            "expected_revision": 1,
            "capabilities": AGENT_CAPABILITIES,
            "access_scope_ids": [str(self.source.access_scope_id)],
            "exact_revocation_confirmed": True,
        }
        status, agent_revoke = self.run_cli(["clients", "revoke"], revoke, secret)
        self.assertEqual(status, 2, "an agent cannot revise its own pairing")
        self.assertEqual(agent_revoke["reason"], "resource_unavailable")
        status, revoked = self.run_cli(["clients", "revoke"], revoke, OWNER_CREDENTIAL)
        self.assertEqual(status, 0, revoked)
        self.assertEqual(revoked["receipt"]["revision"], 2)
        self.assertFalse(self.source_readable(agent))
        status, stale = self.run_cli(["clients", "revoke"], revoke, OWNER_CREDENTIAL)
        self.assertEqual(
            (status, stale),
            (3, {"outcome": "failed", "reason": "pairing_revision_conflict"}),
        )

    def test_pairing_refuses_escalation_foreign_scope_and_agent_callers(self) -> None:
        principals = self.principal_count()
        for changes in (
            {"capabilities": ["client.pair"]},
            {"access_scope_ids": [str(uuid.uuid4())]},
        ):
            with self.subTest(changes=changes):
                secret_file = self.root / f"refused-{uuid.uuid4()}.secret"
                status, refused = self.pair(
                    self.pairing_request(**changes), secret_file, OWNER_CREDENTIAL
                )
                self.assertEqual(
                    (status, refused),
                    (2, {"outcome": "unavailable", "reason": "resource_unavailable"}),
                )
                self.assertFalse(secret_file.exists())
        self.assertEqual(self.principal_count(), principals)

        agent_file = self.root / "agent.secret"
        status, _ = self.pair(self.pairing_request(), agent_file, OWNER_CREDENTIAL)
        self.assertEqual(status, 0)
        nested_file = self.root / "nested.secret"
        status, nested = self.pair(
            self.pairing_request(),
            nested_file,
            agent_file.read_text(encoding="ascii"),
        )
        self.assertEqual(
            (status, nested),
            (2, {"outcome": "unavailable", "reason": "resource_unavailable"}),
        )
        self.assertFalse(nested_file.exists())


if __name__ == "__main__":
    unittest.main()
