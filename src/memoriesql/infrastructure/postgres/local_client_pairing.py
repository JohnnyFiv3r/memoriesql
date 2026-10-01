"""Short, authenticated transactions for pairing and revoking one local client."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    InsufficientPrivilege,
    InvalidAuthorizationSpecification,
    NoDataFound,
    SerializationFailure,
    UniqueViolation,
)
from psycopg.pq import TransactionStatus

from memoriesql.application.local_client_pairing import (
    LocalClientPairing,
    LocalClientRevocation,
    PairLocalClient,
    RevokeLocalClient,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort

# Identities derive from the request so a blind retry cannot pair a second client.
_PAIRING_NAMESPACE = UUID("6d2f4f0e-6f4b-5d1f-9d8e-3c9a1b7e2a51")


class PairingRevisionConflict(RuntimeError):
    """The pairing grant changed after the caller read its revision."""


def pairing_identities(request_id: UUID) -> tuple[UUID, UUID, UUID, UUID]:
    """Return the principal, pairing, grant and credential IDs for one request."""

    principal, pairing, grant, credential = (
        uuid5(_PAIRING_NAMESPACE, f"{request_id}:{role}")
        for role in ("principal", "pairing", "pairing-grant", "credential")
    )
    return principal, pairing, grant, credential


class PostgresLocalClientPairing:
    """Database authority comes from the credential; times from the database."""

    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential_sha256 = credential_sha256
        self._workspace_id = workspace_id

    def _begin(self) -> None:
        self._connection.execute("SET LOCAL statement_timeout='2500ms'")
        self._connection.execute("SET LOCAL lock_timeout='500ms'")
        self._connection.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(self._connection).begin_context(
            credential_sha256=self._credential_sha256,
            requested_workspace_id=self._workspace_id,
        )

    def _require_idle(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("client pairing requires transaction ownership")

    def pair(
        self, request: PairLocalClient, *, client_secret_sha256: str
    ) -> LocalClientPairing:
        """Record only the secret's SHA-256; the caller alone holds the secret."""

        self._require_idle()
        command = PairLocalClient.model_validate(request.model_dump(mode="json"))
        principal_id, pairing_id, grant_id, credential_id = pairing_identities(
            command.request_id
        )
        try:
            with self._connection.transaction():
                self._begin()
                self._connection.execute(
                    "SELECT memoriesql.pair_local_client("
                    "%s,%s,%s,%s,'agent','paired_agent',%s,%s,%s,"
                    "pg_catalog.statement_timestamp(),%s)",
                    (
                        principal_id,
                        pairing_id,
                        grant_id,
                        credential_id,
                        list(command.capabilities),
                        list(command.access_scope_ids),
                        client_secret_sha256,
                        command.expires_at,
                    ),
                )
        except (
            InsufficientPrivilege,
            InvalidAuthorizationSpecification,
            CheckViolation,
            ForeignKeyViolation,
            UniqueViolation,
        ) as error:
            # Refusal never reveals which scope, capability, identity or
            # credential failed.
            raise PermissionError("client pairing is unavailable") from error
        return LocalClientPairing(
            principal_id=principal_id,
            pairing_id=pairing_id,
            pairing_grant_id=grant_id,
            credential_id=credential_id,
            capabilities=command.capabilities,
            access_scope_ids=command.access_scope_ids,
            expires_at=command.expires_at,
        )

    def revoke(self, request: RevokeLocalClient) -> LocalClientRevocation:
        self._require_idle()
        command = RevokeLocalClient.model_validate(request.model_dump(mode="json"))
        try:
            with self._connection.transaction():
                self._begin()
                row = self._connection.execute(
                    "SELECT memoriesql.revise_pairing_grant("
                    "%s,%s,%s,%s,'revoked',pg_catalog.statement_timestamp(),"
                    "pg_catalog.statement_timestamp() + interval '1 second',"
                    "pg_catalog.statement_timestamp()), "
                    "pg_catalog.statement_timestamp()",
                    (
                        command.pairing_grant_id,
                        command.expected_revision,
                        list(command.capabilities),
                        list(command.access_scope_ids),
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeError("pairing revocation returned no revision")
        except SerializationFailure as error:
            raise PairingRevisionConflict("pairing grant revision changed") from error
        except (
            InsufficientPrivilege,
            InvalidAuthorizationSpecification,
            CheckViolation,
            ForeignKeyViolation,
            NoDataFound,
        ) as error:
            raise PermissionError("client revocation is unavailable") from error
        return LocalClientRevocation(
            pairing_grant_id=command.pairing_grant_id,
            revision=row[0],
            revoked_at=row[1],
        )
