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
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations
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

    def test_unknown_owner_credential_is_refused_without_disclosure(self) -> None:
        unknown = "fictional unknown orchard credential for refusal"
        secret_file = self.root / "unknown.secret"
        status, refused = self.pair(self.pairing_request(), secret_file, unknown)
        self.assertEqual(
            (status, refused),
            (2, {"outcome": "unavailable", "reason": "resource_unavailable"}),
        )
        self.assertFalse(secret_file.exists())
        revoke: dict[str, object] = {
            "pairing_grant_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "capabilities": AGENT_CAPABILITIES,
            "access_scope_ids": [str(self.source.access_scope_id)],
            "exact_revocation_confirmed": True,
        }
        status, refused = self.run_cli(["clients", "revoke"], revoke, unknown)
        self.assertEqual(
            (status, refused),
            (2, {"outcome": "unavailable", "reason": "resource_unavailable"}),
        )

    def test_a_revoked_service_pairing_leaves_a_membership_that_grants_nothing(
        self,
    ) -> None:
        # Revoking a paired service's grant, as a revert does, records a revoked
        # revision. The service's principal, workspace membership, credential
        # and source grant stay active. None of them grants anything: every use
        # of a paired principal's membership also needs its latest pairing-grant
        # revision to be active.
        migrate(
            self.db,
            expected_current_version=36,
            target_version=discover_migrations()[-1].version,
        )
        principal, pairing, grant, credential = (uuid.uuid4() for _ in range(4))
        secret = hashlib.sha256(b"fictional orchard background service").hexdigest()
        scope = self.source.access_scope_id
        capabilities = ["memory.query", "source.read"]
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self.db).begin_context(
                credential_sha256=self.owner_secret,
                requested_workspace_id=self.workspace,
            )
            self.db.execute(
                "SELECT memoriesql.pair_local_client("
                "%s,%s,%s,%s,'service','background_service',%s,%s,%s,%s,%s)",
                (
                    principal,
                    pairing,
                    grant,
                    credential,
                    capabilities,
                    [scope],
                    secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        status, granted = self.run_cli(
            ["sources", "grant"],
            {
                "request_id": str(uuid.uuid4()),
                "source_object_id": str(self.source.source_object_id),
                "target_principal_id": str(principal),
                "permission_keys": ["read"],
                "valid_from": self.now.isoformat(),
                "expires_at": (self.now + timedelta(hours=1)).isoformat(),
            },
            OWNER_CREDENTIAL,
        )
        self.assertEqual(status, 0, granted)
        self.assertTrue(self.source_readable(secret))

        # A context the service opened before the revert, held open across it.
        opened = psycopg.connect(self.url)
        self.addCleanup(opened.close)
        opened.execute("SET LOCAL ROLE memoriesql_application")
        port = PostgresAuthorizationPort(opened)
        context = port.begin_context(
            credential_sha256=secret, requested_workspace_id=self.workspace
        )

        def reads() -> bool:
            capable = opened.execute(
                "SELECT memoriesql.current_context_has_capability('source.read')"
            ).fetchone()
            return (
                bool(capable and capable[0])
                and port.authorize_resource(
                    context=context,
                    resource=ResourceReference(
                        kind=ResourceKind.SOURCE,
                        resource_id=self.source.source_object_id,
                    ),
                    capability="source.read",
                    permission=ScopePermission.READ,
                    request_id=uuid.uuid4(),
                ).allowed
            )

        self.assertTrue(reads())
        status, revoked = self.run_cli(
            ["clients", "revoke"],
            {
                "pairing_grant_id": str(grant),
                "expected_revision": 1,
                "capabilities": capabilities,
                "access_scope_ids": [str(scope)],
                "exact_revocation_confirmed": True,
            },
            OWNER_CREDENTIAL,
        )
        self.assertEqual(status, 0, revoked)

        # The residue: everything but the pairing grant stays active.
        residue = self.db.execute(
            "SELECT (SELECT status FROM memoriesql.principals WHERE principal_id=%s),"
            "(SELECT status FROM memoriesql.workspace_memberships"
            " WHERE principal_id=%s),"
            "(SELECT status FROM memoriesql.authentication_credentials"
            " WHERE principal_id=%s),"
            "(SELECT r.status FROM memoriesql.access_grants g"
            " JOIN memoriesql.access_grant_revisions r ON r.tenant_id=g.tenant_id"
            " AND r.grant_id=g.grant_id WHERE g.target_principal_id=%s"
            " ORDER BY r.revision DESC LIMIT 1)",
            (principal, principal, principal, principal),
        ).fetchone()
        self.assertEqual(residue, ("active", "active", "active", "active"))

        # It grants nothing. The open context lost its authority at once, and
        # the service can no longer authenticate.
        self.assertFalse(reads())
        opened.rollback()
        self.assertFalse(self.source_readable(secret))
        with self.assertRaises(psycopg.errors.InvalidAuthorizationSpecification):
            with psycopg.connect(self.url) as again, again.transaction():
                again.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(again).begin_context(
                    credential_sha256=secret, requested_workspace_id=self.workspace
                )

        # Every database object that reads a membership is one of these. Each
        # uses a paired principal's membership only with that principal's
        # latest pairing-grant revision active, or only inside an authenticated
        # context, which that revision fences, or is an owner-side write.
        authenticated = {
            # Starts a context: a paired principal needs its latest
            # pairing-grant revision active and unexpired.
            "begin_authorization_context",
            # Inside a context: its pairing-grant revision must still be the
            # latest and active, so a revocation ends it at once.
            "current_context_has_capability",
            # These lock the context principal's role rows while they use the
            # context's own authority.
            "recover_transcript_fold_v1",
            "revisiting_source_authorize",
            "source_revisiting_authorize",
            # A task's origin: a paired origin's latest pairing-grant revision
            # must be active. Tasks copy the origin's grant from its context.
            "semantic_task_origin_authorized",
            "semantic_task_origin_capability_authorized",
            "relation_assessment_origin_scope_authorized",
        }
        owner_side = {
            # Create memberships, or bound a pairing grant by the paired role.
            "bootstrap_personal_local",
            "pair_local_client",
            "validate_pairing_grant_revision",
            # A grant needs an active member as its target; an unpaired one
            # cannot authenticate to use it.
            "create_access_grant",
        }
        user = "n.nspname NOT LIKE 'pg\\_%%' AND n.nspname<>'information_schema'"
        readers = {
            str(row[0])
            for row in self.db.execute(
                "SELECT p.proname FROM pg_proc p"
                " JOIN pg_namespace n ON n.oid=p.pronamespace"
                f" WHERE {user} AND p.prosrc LIKE %s"
                " UNION ALL SELECT c.relname FROM pg_class c"
                " JOIN pg_namespace n ON n.oid=c.relnamespace"
                f" WHERE {user} AND c.relkind IN ('v','m')"
                " AND pg_get_viewdef(c.oid) LIKE %s"
                " UNION ALL SELECT p.polname FROM pg_policy p"
                " WHERE pg_get_expr(p.polqual,p.polrelid) LIKE %s"
                " OR pg_get_expr(p.polwithcheck,p.polrelid) LIKE %s",
                ("%workspace_memberships%",) * 4,
            ).fetchall()
        }
        self.assertEqual(readers, authenticated | owner_side)


if __name__ == "__main__":
    unittest.main()
