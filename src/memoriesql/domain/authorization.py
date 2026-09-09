from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class PrincipalKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"
    DEVICE = "device"


class AccessMode(StrEnum):
    OWNER_PRIVATE = "owner_private"
    EXPLICIT = "explicit"
    WORKSPACE = "workspace"


class ScopePermission(StrEnum):
    READ = "read"
    WRITE = "write"
    SHARE = "share"
    EXPORT = "export"
    DELETE = "delete"


class ResourceKind(StrEnum):
    SOURCE = "source"
    MODEL = "model"
    FILE = "file"
    RENDER = "render"


class AuthorizationOperation(StrEnum):
    SOURCE_READ = "source.read"
    SOURCE_MANAGE = "source.manage"
    SOURCE_SHARE = "source.share"
    MODEL_READ = "model.read"
    MODEL_PROPOSE = "model.propose"
    MODEL_REVIEW = "model.review"
    MODEL_EXPORT = "model.export"
    MODEL_DELETE = "model.delete"
    FILE_READ = "file.read"
    FILE_EXPORT = "file.export"
    FILE_DELETE = "file.delete"
    RENDER_READ = "render.read"
    RENDER_EXPORT = "render.export"
    RENDER_DELETE = "render.delete"


@dataclass(frozen=True, slots=True)
class ResourceReference:
    kind: ResourceKind
    resource_id: UUID


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    tenant_id: UUID
    workspace_id: UUID
    principal_id: UUID
    principal_kind: PrincipalKind
    user_id: UUID | None
    on_behalf_of_user_id: UUID | None
    pairing_grant_id: UUID | None
    membership_revision: int
    pairing_grant_revision: int | None


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    context: AuthorizationContext
    resource: ResourceReference
    capability: str
    permission: ScopePermission
