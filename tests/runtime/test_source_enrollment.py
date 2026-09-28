"""Installed fictional source authority against disposable PostgreSQL."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import time
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
from pydantic import ValidationError

from memoriesql.application.source_enrollment import (
    EnrollExactSource,
    GrantExactSource,
    RevokeExactSource,
)
from memoriesql.cli import main as cli_main
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.source_enrollment import (
    PostgresSourceEnrollment,
)

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate

OWNER_CREDENTIAL = "fictional orchard owner session credential"


class ExactSourceEnrollment(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "pr06_source_" + uuid.uuid4().hex
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
            _default_scope,
            membership,
            self.credential,
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
                    _default_scope,
                    membership,
                    self.credential,
                    session,
                    str(identity),
                    self.owner_secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )

    def cleanup_database(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def api(
        self,
        connection: psycopg.Connection[Any] | None = None,
        *,
        secret: str | None = None,
    ) -> PostgresSourceEnrollment:
        return PostgresSourceEnrollment(
            connection or self.db,
            credential_sha256=secret or self.owner_secret,
            workspace_id=self.workspace,
        )

    def begin(self, secret: str) -> None:
        self.db.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(self.db).begin_context(
            credential_sha256=secret, requested_workspace_id=self.workspace
        )

    def test_cli_exact_source_lifecycle_uses_current_authority(self) -> None:
        def run(action: str, payload: dict[str, object]) -> tuple[int, dict[str, Any]]:
            with tempfile.TemporaryDirectory() as directory:
                request = Path(directory) / "request.json"
                request.write_text(json.dumps(payload), encoding="utf-8")
                output = io.StringIO()
                with redirect_stdout(output):
                    status = cli_main(
                        ["sources", action, "--request-file", str(request), "--json"]
                    )
                return status, json.loads(output.getvalue())

        environment = {
            "MEMORIESQL_DATABASE_URL": self.url,
            "MEMORIESQL_LOCAL_CREDENTIAL": OWNER_CREDENTIAL,
            "MEMORIESQL_WORKSPACE_ID": str(self.workspace),
        }
        enrollment_request = {
            "request_id": str(uuid.uuid4()),
            "source_system": "fictional-orchard",
            "object_kind": "transcript",
            "external_object_id": "selected/cli-session.jsonl",
            "source_schema_version": 1,
            "exact_source_confirmed": True,
        }
        with patch.dict(os.environ, environment):
            status, enrolled = run("enroll", enrollment_request)
            self.assertEqual(status, 0)
            self.assertFalse(enrolled["receipt"]["replayed"])
            source_id = enrolled["receipt"]["source_object_id"]
            self.assertTrue(run("enroll", enrollment_request)[1]["receipt"]["replayed"])

            grant_request = {
                "request_id": str(uuid.uuid4()),
                "source_object_id": source_id,
                "target_principal_id": str(self.principal),
                "permission_keys": ["read"],
                "valid_from": self.now.isoformat(),
                "expires_at": (self.now + timedelta(hours=1)).isoformat(),
            }
            status, granted = run("grant", grant_request)
            self.assertEqual(status, 0)
            self.assertFalse(granted["receipt"]["replayed"])

            revoke_request = {
                "request_id": str(uuid.uuid4()),
                "source_object_id": source_id,
                "reason": "fictional CLI revocation",
            }
            status, revoked = run("revoke", revoke_request)
            self.assertEqual(status, 0)
            self.assertFalse(revoked["receipt"]["replayed"])
            self.assertTrue(run("revoke", revoke_request)[1]["receipt"]["replayed"])
            status, denied = run("grant", grant_request)
            self.assertEqual(status, 2)
            self.assertEqual(
                denied, {"outcome": "unavailable", "reason": "resource_unavailable"}
            )

    def test_exact_source_grant_restart_and_revocation(self) -> None:
        selection = EnrollExactSource(
            request_id=uuid.uuid4(),
            source_system="fictional-orchard",
            installation_id="local-test",
            object_kind="transcript",
            external_object_id="orchard/selected-session.jsonl",
            source_schema_version=1,
            exact_source_confirmed=True,
        )
        enrollment = self.api().enroll(selection)
        self.assertEqual(enrollment.source_object_id, selection.request_id)
        self.assertFalse(enrollment.replayed)
        with psycopg.connect(self.url, autocommit=True) as restarted:
            replay = self.api(restarted).enroll(selection)
        self.assertEqual(replay.access_scope_id, enrollment.access_scope_id)
        self.assertTrue(replay.replayed)
        with self.assertRaises(PermissionError):
            self.api().enroll(selection.model_copy(update={"request_id": uuid.uuid4()}))

        device = uuid.uuid4()
        device_secret = hashlib.sha256(b"fictional paired capture device").hexdigest()
        with self.db.transaction():
            self.begin(self.owner_secret)
            self.db.execute(
                "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,'device','paired_device',%s,%s,%s,%s,%s)",
                (
                    device,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    uuid.uuid4(),
                    ["memory.capture", "source.read"],
                    [enrollment.access_scope_id],
                    device_secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        with self.assertRaises(PermissionError):
            self.api(secret=device_secret).enroll(
                selection.model_copy(update={"request_id": uuid.uuid4()})
            )
        grant = GrantExactSource(
            request_id=uuid.uuid4(),
            source_object_id=enrollment.source_object_id,
            target_principal_id=device,
            permission_keys=("read", "write"),
            valid_from=self.now,
            expires_at=self.now + timedelta(hours=1),
        )
        receipt = self.api().grant(grant)
        self.assertEqual(receipt.grant_id, grant.request_id)
        self.assertFalse(receipt.replayed)
        self.assertTrue(self.api().grant(grant).replayed)
        unrelated = self.api().enroll(
            selection.model_copy(
                update={
                    "request_id": uuid.uuid4(),
                    "external_object_id": "orchard/different-session.jsonl",
                }
            )
        )
        self.assertNotEqual(unrelated.access_scope_id, enrollment.access_scope_id)
        with self.db.transaction():
            self.begin(device_secret)
            row = self.db.execute(
                "SELECT memoriesql.current_context_source_authorized(%s,%s,'memory.capture','write')",
                (enrollment.access_scope_id, enrollment.source_object_id),
            ).fetchone()
            self.assertEqual(row, (True,))
            other = self.db.execute(
                "SELECT memoriesql.current_context_source_authorized(%s,%s,'memory.capture','write')",
                (unrelated.access_scope_id, unrelated.source_object_id),
            ).fetchone()
            self.assertEqual(other, (False,))

        revoked = RevokeExactSource(
            request_id=uuid.uuid4(),
            source_object_id=enrollment.source_object_id,
            reason="fictional source removed by its owner",
        )
        first_revocation = self.api().revoke(revoked)
        self.assertFalse(first_revocation.replayed)
        replayed_revocation = self.api().revoke(revoked)
        self.assertTrue(replayed_revocation.replayed)
        self.assertEqual(replayed_revocation.recorded_at, first_revocation.recorded_at)
        with self.db.transaction():
            self.begin(device_secret)
            row = self.db.execute(
                "SELECT memoriesql.current_context_source_authorized(%s,%s,'memory.capture','write')",
                (enrollment.access_scope_id, enrollment.source_object_id),
            ).fetchone()
            self.assertEqual(row, (False,))
            visible = self.db.execute(
                "SELECT count(*) FROM memoriesql.source_objects WHERE source_object_id=%s",
                (enrollment.source_object_id,),
            ).fetchone()
            self.assertEqual(visible, (0,))
        with self.assertRaises(PermissionError):
            self.api().enroll(selection)
        with self.assertRaises(PermissionError):
            self.api().grant(grant)

    def test_no_direct_product_insert_or_unauthenticated_enrollment(self) -> None:
        selection = EnrollExactSource(
            request_id=uuid.uuid4(),
            source_system="fictional-orchard",
            object_kind="transcript",
            external_object_id="orchard/only-this-session.jsonl",
            source_schema_version=1,
            exact_source_confirmed=True,
        )
        with self.assertRaises(psycopg.Error):
            self.api(secret="0" * 64).enroll(selection)
        with self.assertRaises(ValidationError):
            EnrollExactSource.model_validate(
                selection.model_dump(mode="json") | {"installation_id": " \t "}
            )
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            with self.db.transaction():
                self.begin(self.owner_secret)
                self.db.execute(
                    "SELECT * FROM memoriesql.enroll_exact_source_v1(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        selection.request_id,
                        selection.source_system,
                        " \t ",
                        selection.object_kind,
                        selection.external_object_id,
                        selection.source_schema_version,
                        True,
                    ),
                )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.db.transaction():
                self.begin(self.owner_secret)
                self.db.execute(
                    "INSERT INTO memoriesql.protected_resources "
                    "(tenant_id,workspace_id,access_scope_id,resource_id,resource_kind,owner_user_id,status,created_by_principal_id,created_at) "
                    "VALUES (%s,%s,%s,%s,'source',%s,'active',%s,%s)",
                    (
                        self.tenant,
                        self.workspace,
                        uuid.uuid4(),
                        uuid.uuid4(),
                        self.user,
                        self.principal,
                        self.now,
                    ),
                )

    def test_fenced_source_writes_reject_expired_authority(self) -> None:
        selected = EnrollExactSource(
            request_id=uuid.uuid4(),
            source_system="fictional-orchard",
            object_kind="transcript",
            external_object_id="orchard/expiring-session.jsonl",
            source_schema_version=1,
            exact_source_confirmed=True,
        )
        enrollment = self.api().enroll(selected)
        new_source = selected.model_copy(
            update={
                "request_id": uuid.uuid4(),
                "external_object_id": "orchard/after-expiry.jsonl",
            }
        )
        grant_id = uuid.uuid4()
        revoke_id = uuid.uuid4()
        cases = (
            (
                "SELECT * FROM memoriesql.enroll_exact_source_v1(%s,%s,%s,%s,%s,%s,%s)",
                (
                    new_source.request_id,
                    new_source.source_system,
                    new_source.installation_id,
                    new_source.object_kind,
                    new_source.external_object_id,
                    new_source.source_schema_version,
                    True,
                ),
            ),
            (
                "SELECT * FROM memoriesql.grant_exact_source_v1(%s,%s,%s,%s,%s,%s)",
                (
                    grant_id,
                    enrollment.source_object_id,
                    self.principal,
                    ["read"],
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            ),
            (
                "SELECT * FROM memoriesql.revoke_exact_source_v1(%s,%s,%s)",
                (revoke_id, enrollment.source_object_id, "expired authority"),
            ),
        )
        for statement, parameters in cases:
            with self.subTest(statement=statement):
                ready = threading.Event()
                holder_errors: list[Exception] = []
                updated = self.db.execute(
                    "UPDATE memoriesql.authentication_credentials "
                    "SET expires_at = clock_timestamp() + interval '4 seconds' "
                    "WHERE tenant_id=%s AND credential_id=%s",
                    (self.tenant, self.credential),
                )
                self.assertEqual(updated.rowcount, 1)

                def hold_fence() -> None:
                    try:
                        with psycopg.connect(self.url) as locker:
                            locker.execute(
                                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                (str(self.tenant) + ":semantic_outcome_authority:",),
                            )
                            ready.set()
                            time.sleep(5)
                    except Exception as error:
                        holder_errors.append(error)
                        ready.set()

                holder = threading.Thread(target=hold_fence)
                holder.start()
                try:
                    self.assertTrue(ready.wait(5), "tenant fence was not acquired")
                    self.assertFalse(holder_errors)
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        with self.db.transaction():
                            self.begin(self.owner_secret)
                            self.db.execute(statement, parameters)
                finally:
                    holder.join(timeout=5)
                self.assertFalse(holder.is_alive())
                self.assertFalse(holder_errors)

        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.source_objects "
                "WHERE source_object_id=%s",
                (new_source.request_id,),
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.access_grants WHERE grant_id=%s",
                (grant_id,),
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT status FROM memoriesql.protected_resources "
                "WHERE resource_kind='source' AND resource_id=%s",
                (enrollment.source_object_id,),
            ).fetchone(),
            ("active",),
        )

    def test_fenced_source_grant_rejects_expired_scope(self) -> None:
        selection = EnrollExactSource(
            request_id=uuid.uuid4(),
            source_system="fictional-orchard",
            object_kind="transcript",
            external_object_id="orchard/expiring-grant.jsonl",
            source_schema_version=1,
            exact_source_confirmed=True,
        )
        enrollment = self.api().enroll(selection)
        owner_grant = self.db.execute(
            "SELECT grant_id FROM memoriesql.access_grants "
            "WHERE tenant_id=%s AND access_scope_id=%s "
            "AND target_principal_id=%s",
            (self.tenant, enrollment.access_scope_id, self.principal),
        ).fetchone()
        assert owner_grant is not None
        with self.db.transaction():
            self.begin(self.owner_secret)
            self.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,1,%s,'active',%s,"
                "clock_timestamp() + interval '4 seconds',clock_timestamp())",
                (owner_grant[0], ["read", "write", "share"], self.now),
            )

        ready = threading.Event()
        holder_errors: list[Exception] = []

        def hold_fence() -> None:
            try:
                with psycopg.connect(self.url) as locker:
                    locker.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (str(self.tenant) + ":semantic_outcome_authority:",),
                    )
                    ready.set()
                    time.sleep(5)
            except Exception as error:
                holder_errors.append(error)
                ready.set()

        holder = threading.Thread(target=hold_fence)
        holder.start()
        request_id = uuid.uuid4()
        try:
            self.assertTrue(ready.wait(5), "tenant fence was not acquired")
            self.assertFalse(holder_errors)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with self.db.transaction():
                    self.begin(self.owner_secret)
                    self.db.execute(
                        "SELECT * FROM memoriesql.grant_exact_source_v1(%s,%s,%s,%s,%s,%s)",
                        (
                            request_id,
                            enrollment.source_object_id,
                            self.principal,
                            ["read"],
                            self.now,
                            self.now + timedelta(hours=1),
                        ),
                    )
        finally:
            holder.join(timeout=5)
        self.assertFalse(holder.is_alive())
        self.assertFalse(holder_errors)
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM memoriesql.access_grants WHERE grant_id=%s",
                (request_id,),
            ).fetchone(),
            (0,),
        )
