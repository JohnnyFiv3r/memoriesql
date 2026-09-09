from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from memoriesql.domain.authorization import (
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationOperation,
    ResourceKind,
    ResourceReference,
    ScopePermission,
)


class AuthenticationRequired(PermissionError):
    """Raised when a local request has no authenticated credential."""


class AuthorizationDenied(PermissionError):
    """Raised when authenticated authority does not cover a resource operation."""


@dataclass(frozen=True, slots=True)
class LocalCredential:
    _secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if len(self._secret) < 32:
            raise ValueError("local credentials must contain at least 32 characters")

    @classmethod
    def generate(cls) -> LocalCredential:
        return cls(secrets.token_urlsafe(32))

    def sha256(self) -> str:
        return hashlib.sha256(self._secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    resource: ResourceReference
    operation: AuthorizationOperation

    def __post_init__(self) -> None:
        expected_kind = _OPERATION_POLICIES[self.operation][0]
        if self.resource.kind is not expected_kind:
            raise ValueError("authorization operation does not match resource kind")


_OPERATION_POLICIES: dict[
    AuthorizationOperation, tuple[ResourceKind, str, ScopePermission]
] = {
    AuthorizationOperation.SOURCE_READ: (
        ResourceKind.SOURCE,
        "source.read",
        ScopePermission.READ,
    ),
    AuthorizationOperation.SOURCE_MANAGE: (
        ResourceKind.SOURCE,
        "source.manage",
        ScopePermission.WRITE,
    ),
    AuthorizationOperation.SOURCE_SHARE: (
        ResourceKind.SOURCE,
        "source.share",
        ScopePermission.SHARE,
    ),
    AuthorizationOperation.MODEL_READ: (
        ResourceKind.MODEL,
        "subject_model.read",
        ScopePermission.READ,
    ),
    AuthorizationOperation.MODEL_PROPOSE: (
        ResourceKind.MODEL,
        "subject_model.propose",
        ScopePermission.WRITE,
    ),
    AuthorizationOperation.MODEL_REVIEW: (
        ResourceKind.MODEL,
        "subject_model.review",
        ScopePermission.WRITE,
    ),
    AuthorizationOperation.MODEL_EXPORT: (
        ResourceKind.MODEL,
        "subject_model.export",
        ScopePermission.EXPORT,
    ),
    AuthorizationOperation.MODEL_DELETE: (
        ResourceKind.MODEL,
        "subject_model.delete",
        ScopePermission.DELETE,
    ),
    AuthorizationOperation.FILE_READ: (
        ResourceKind.FILE,
        "file.read",
        ScopePermission.READ,
    ),
    AuthorizationOperation.FILE_EXPORT: (
        ResourceKind.FILE,
        "file.export",
        ScopePermission.EXPORT,
    ),
    AuthorizationOperation.FILE_DELETE: (
        ResourceKind.FILE,
        "file.delete",
        ScopePermission.DELETE,
    ),
    AuthorizationOperation.RENDER_READ: (
        ResourceKind.RENDER,
        "file.read",
        ScopePermission.READ,
    ),
    AuthorizationOperation.RENDER_EXPORT: (
        ResourceKind.RENDER,
        "file.export",
        ScopePermission.EXPORT,
    ),
    AuthorizationOperation.RENDER_DELETE: (
        ResourceKind.RENDER,
        "file.delete",
        ScopePermission.DELETE,
    ),
}


class TrustedAuthorizationPort(Protocol):
    def begin_context(
        self, *, credential_sha256: str, requested_workspace_id: UUID
    ) -> AuthorizationContext: ...

    def authorize_resource(
        self,
        *,
        context: AuthorizationContext,
        resource: ResourceReference,
        capability: str,
        permission: ScopePermission,
        request_id: UUID,
    ) -> AuthorizationDecision: ...


class ApplicationAuthorization:
    """Build immutable authority from an authenticated secret and server state.

    Resource IDs may come from a caller. Effective principal, tenant, membership,
    pairing grant, access scope, and policy data never do.
    """

    def __init__(self, port: TrustedAuthorizationPort) -> None:
        self._port = port

    def authorize_local_request(
        self,
        *,
        credential: LocalCredential | None,
        requested_workspace_id: UUID,
        request: AuthorizationRequest,
        request_id: UUID,
    ) -> AuthorizationDecision:
        if credential is None:
            raise AuthenticationRequired(
                "local requests require authentication; loopback is not identity"
            )
        context = self._port.begin_context(
            credential_sha256=credential.sha256(),
            requested_workspace_id=requested_workspace_id,
        )
        _, capability, permission = _OPERATION_POLICIES[request.operation]
        decision = self._port.authorize_resource(
            context=context,
            resource=request.resource,
            capability=capability,
            permission=permission,
            request_id=request_id,
        )
        return decision

    @staticmethod
    def require_allowed(decision: AuthorizationDecision) -> AuthorizationDecision:
        """Raise only after the authorization transaction has committed its audit."""

        if not decision.allowed:
            raise AuthorizationDenied("resource is unavailable")
        return decision


def uuid7() -> UUID:
    """Return an application-generated UUIDv7 without a runtime dependency."""

    unix_milliseconds = time.time_ns() // 1_000_000
    if unix_milliseconds >= 1 << 48:
        raise RuntimeError("current time is outside the UUIDv7 timestamp range")
    random_tail = secrets.randbits(74)
    value = unix_milliseconds << 80
    value |= 0x7 << 76
    value |= ((random_tail >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_tail & ((1 << 62) - 1)
    return UUID(int=value)
