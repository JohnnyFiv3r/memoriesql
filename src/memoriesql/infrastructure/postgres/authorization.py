from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from memoriesql.domain.authorization import (
    AuthorizationContext,
    AuthorizationDecision,
    PrincipalKind,
    ResourceKind,
    ResourceReference,
    ScopePermission,
)


class PostgresAuthorizationPort:
    """Transaction-bound adapter for the Postgres authorization kernel."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def begin_context(
        self, *, credential_sha256: str, requested_workspace_id: UUID
    ) -> AuthorizationContext:
        row = self._connection.execute(
            """
            SELECT
                tenant_id,
                workspace_id,
                principal_id,
                principal_kind,
                user_id,
                on_behalf_of_user_id,
                pairing_grant_id,
                membership_revision,
                pairing_grant_revision
            FROM memoriesql.begin_authorization_context(%s, %s)
            """,
            (credential_sha256, requested_workspace_id),
        ).fetchone()
        if row is None:
            raise PermissionError("authentication context is unavailable")
        return AuthorizationContext(
            tenant_id=UUID(str(row[0])),
            workspace_id=UUID(str(row[1])),
            principal_id=UUID(str(row[2])),
            principal_kind=PrincipalKind(str(row[3])),
            user_id=UUID(str(row[4])) if row[4] is not None else None,
            on_behalf_of_user_id=UUID(str(row[5])) if row[5] is not None else None,
            pairing_grant_id=UUID(str(row[6])) if row[6] is not None else None,
            membership_revision=int(row[7]),
            pairing_grant_revision=int(row[8]) if row[8] is not None else None,
        )

    def authorize_resource(
        self,
        *,
        context: AuthorizationContext,
        resource: ResourceReference,
        capability: str,
        permission: ScopePermission,
        request_id: UUID,
    ) -> AuthorizationDecision:
        allowed_row = self._connection.execute(
            """
            SELECT memoriesql.application_authorize_resource(%s, %s, %s, %s, %s)
            """,
            (
                resource.kind.value,
                resource.resource_id,
                capability,
                permission.value,
                request_id,
            ),
        ).fetchone()
        allowed = bool(allowed_row and allowed_row[0])
        return AuthorizationDecision(
            allowed=allowed,
            context=context,
            resource=ResourceReference(
                kind=ResourceKind(resource.kind),
                resource_id=resource.resource_id,
            ),
            capability=capability,
            permission=permission,
        )
