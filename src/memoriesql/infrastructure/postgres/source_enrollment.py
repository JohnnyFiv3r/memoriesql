"""Short, authenticated transactions for exact source identity and grants."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege
from psycopg.pq import TransactionStatus

from memoriesql.application.source_enrollment import (
    EnrollExactSource,
    ExactSourceEnrollment,
    ExactSourceGrant,
    ExactSourceRevocation,
    GrantExactSource,
    RevokeExactSource,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


class PostgresSourceEnrollment:
    """Database authority is obtained from a credential, never caller IDs."""

    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential_sha256 = credential_sha256
        self._workspace_id = workspace_id

    def _begin(self) -> None:
        self._connection.execute(
            "SELECT set_config('statement_timeout','2500',true), "
            "set_config('lock_timeout','500',true)"
        )
        self._connection.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(self._connection).begin_context(
            credential_sha256=self._credential_sha256,
            requested_workspace_id=self._workspace_id,
        )

    def _require_idle(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("source enrollment requires transaction ownership")

    def enroll(self, request: EnrollExactSource) -> ExactSourceEnrollment:
        self._require_idle()
        command = EnrollExactSource.model_validate(request.model_dump(mode="json"))
        try:
            with self._connection.transaction():
                self._begin()
                row = self._connection.execute(
                    "SELECT * FROM memoriesql.enroll_exact_source_v1(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        command.request_id,
                        command.source_system,
                        command.installation_id,
                        command.object_kind,
                        command.external_object_id,
                        command.source_schema_version,
                        command.exact_source_confirmed,
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeError("source enrollment returned no receipt")
                return ExactSourceEnrollment(
                    source_object_id=row[0],
                    access_scope_id=row[1],
                    policy_revision_id=row[2],
                    replayed=row[3],
                )
        except InsufficientPrivilege as error:
            raise PermissionError("source enrollment is unavailable") from error

    def grant(self, request: GrantExactSource) -> ExactSourceGrant:
        self._require_idle()
        command = GrantExactSource.model_validate(request.model_dump(mode="json"))
        try:
            with self._connection.transaction():
                self._begin()
                row = self._connection.execute(
                    "SELECT * FROM memoriesql.grant_exact_source_v1(%s,%s,%s,%s,%s,%s)",
                    (
                        command.request_id,
                        command.source_object_id,
                        command.target_principal_id,
                        list(command.permission_keys),
                        command.valid_from,
                        command.expires_at,
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeError("source grant returned no receipt")
                return ExactSourceGrant(grant_id=row[0], replayed=row[1])
        except (InsufficientPrivilege, ForeignKeyViolation) as error:
            raise PermissionError("source grant is unavailable") from error

    def revoke(self, request: RevokeExactSource) -> ExactSourceRevocation:
        self._require_idle()
        command = RevokeExactSource.model_validate(request.model_dump(mode="json"))
        try:
            with self._connection.transaction():
                self._begin()
                row = self._connection.execute(
                    "SELECT * FROM memoriesql.revoke_exact_source_v1(%s,%s,%s)",
                    (command.request_id, command.source_object_id, command.reason),
                ).fetchone()
                if row is None:
                    raise RuntimeError("source revocation returned no receipt")
                return ExactSourceRevocation(
                    request_id=command.request_id,
                    source_object_id=command.source_object_id,
                    replayed=bool(row[0]),
                    recorded_at=row[1],
                )
        except InsufficientPrivilege as error:
            raise PermissionError("source revocation is unavailable") from error
