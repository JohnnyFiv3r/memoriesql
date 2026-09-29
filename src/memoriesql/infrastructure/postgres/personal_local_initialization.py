"""Bind the one-shot personal-local bootstrap to a deterministic, replayable request."""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.errors import InsufficientPrivilege, ObjectNotInPrerequisiteState
from psycopg.pq import TransactionStatus

from memoriesql.application.personal_local_initialization import (
    InitializePersonalLocal,
    PersonalLocalInitialization,
)

_INITIALIZATION_NAMESPACE = UUID("1f3c4b2a-8d7e-5f60-9a1b-2c3d4e5f6a7b")
_ROLES = (
    "tenant",
    "user",
    "identity",
    "principal",
    "workspace",
    "access-scope",
    "policy-revision",
    "credential",
    "session",
)


class AlreadyInitialized(RuntimeError):
    """Another initialization owns this database; nothing was created."""


class InitializationReplayUnverifiable(RuntimeError):
    """This login cannot read the records a replay must match; nothing was created."""


def initialization_identities(request_id: UUID) -> dict[str, UUID]:
    return {role: uuid5(_INITIALIZATION_NAMESPACE, f"{request_id}:{role}") for role in _ROLES}


class PostgresPersonalLocalInitialization:
    """Operator-only: the login must be able to assume the application role."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def initialize(
        self, request: InitializePersonalLocal, *, session_secret_sha256: str
    ) -> PersonalLocalInitialization:
        """Record only the secret's SHA-256; the caller alone holds the secret."""

        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("initialization requires transaction ownership")
        command = InitializePersonalLocal.model_validate(request.model_dump(mode="json"))
        ids = initialization_identities(command.request_id)
        try:
            with self._connection.transaction():
                self._connection.execute(
                    "SELECT set_config('statement_timeout','2500',true), "
                    "set_config('lock_timeout','500',true)"
                )
                self._connection.execute("SET LOCAL ROLE memoriesql_application")
                self._connection.execute(
                    "SELECT memoriesql.bootstrap_personal_local("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,'memoriesql.local',%s,%s,%s,"
                    "pg_catalog.statement_timestamp(),%s)",
                    (
                        ids["tenant"],
                        ids["user"],
                        ids["identity"],
                        ids["principal"],
                        ids["workspace"],
                        ids["access-scope"],
                        ids["policy-revision"],
                        ids["credential"],
                        ids["session"],
                        str(ids["identity"]),
                        command.owner_display_name,
                        session_secret_sha256,
                        command.expires_at,
                    ),
                )
            replayed = False
        except ObjectNotInPrerequisiteState as error:
            owned = self._owns_existing(ids, command)
            if owned is None:
                raise InitializationReplayUnverifiable(
                    "personal-local replay cannot be verified by this login"
                ) from error
            if not owned:
                raise AlreadyInitialized("personal-local authority exists") from error
            replayed = True
        except InsufficientPrivilege as error:
            raise PermissionError("initialization is unavailable") from error
        return PersonalLocalInitialization(
            tenant_id=ids["tenant"],
            user_id=ids["user"],
            principal_id=ids["principal"],
            workspace_id=ids["workspace"],
            access_scope_id=ids["access-scope"],
            credential_id=ids["credential"],
            expires_at=command.expires_at,
            replayed=replayed,
        )

    def _owns_existing(
        self, ids: dict[str, UUID], command: InitializePersonalLocal
    ) -> bool | None:
        """True only for an identical replay of this exact request.

        The active workspace, owner name and credential expiry must all match
        the request's derived records. None means this login cannot read those
        records unfiltered, or the read failed, so the replay can be neither
        confirmed nor ruled out.
        """

        try:
            # An empty read proves nothing under row security: only a login
            # that bypasses it (a superuser or BYPASSRLS role) can rule out.
            unrestricted = self._connection.execute(
                "SELECT NOT (pg_catalog.row_security_active('memoriesql.workspaces') "
                "OR pg_catalog.row_security_active('memoriesql.users') "
                "OR pg_catalog.row_security_active("
                "'memoriesql.authentication_credentials'))"
            ).fetchone()
            if not (unrestricted and unrestricted[0]):
                return None
            row = self._connection.execute(
                "SELECT u.display_name, c.expires_at "
                "FROM memoriesql.workspaces w "
                "JOIN memoriesql.users u ON u.tenant_id=w.tenant_id AND u.user_id=%s "
                "JOIN memoriesql.authentication_credentials c "
                "ON c.tenant_id=w.tenant_id AND c.credential_id=%s "
                "WHERE w.tenant_id=%s AND w.workspace_id=%s AND w.status='active'",
                (ids["user"], ids["credential"], ids["tenant"], ids["workspace"]),
            ).fetchone()
        except Exception:
            # The bootstrap was already refused, so nothing this run sent can
            # commit: any failure here, a lost connection included, only leaves
            # the replay unverifiable.
            with contextlib.suppress(Exception):
                if self._connection.info.transaction_status in (
                    TransactionStatus.INTRANS,
                    TransactionStatus.INERROR,
                ):
                    self._connection.rollback()
            return None
        return (
            row is not None
            and row[0] == command.owner_display_name
            and row[1] == command.expires_at
        )
