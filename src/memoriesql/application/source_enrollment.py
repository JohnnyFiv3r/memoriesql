"""Provider-neutral identity and authority requests for one selected source."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from memoriesql.application.semantic_task_contracts import FrozenContractModel


class EnrollExactSource(FrozenContractModel):
    contract_version: Literal[1] = 1
    request_id: UUID
    source_system: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    installation_id: str | None = Field(default=None, min_length=1, max_length=1024)
    object_kind: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    external_object_id: str = Field(min_length=1, max_length=4096)
    source_schema_version: int = Field(ge=1, le=65535)
    exact_source_confirmed: Literal[True]

    @model_validator(mode="after")
    def nonblank_identity(self) -> EnrollExactSource:
        if not self.external_object_id.strip():
            raise ValueError("external object identity must not be blank")
        return self


class ExactSourceEnrollment(FrozenContractModel):
    contract_version: Literal[1] = 1
    source_object_id: UUID
    access_scope_id: UUID
    policy_revision_id: UUID
    replayed: bool


class GrantExactSource(FrozenContractModel):
    contract_version: Literal[1] = 1
    request_id: UUID
    source_object_id: UUID
    target_principal_id: UUID
    permission_keys: tuple[Literal["read", "write"], ...] = Field(
        min_length=1, max_length=2
    )
    valid_from: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_grant(self) -> GrantExactSource:
        if self.permission_keys not in (("read",), ("write",), ("read", "write")):
            raise ValueError("source grants require canonical read/write permissions")
        if self.expires_at <= self.valid_from:
            raise ValueError("source grant expiry must follow its start")
        return self


class ExactSourceGrant(FrozenContractModel):
    contract_version: Literal[1] = 1
    grant_id: UUID
    replayed: bool


class RevokeExactSource(FrozenContractModel):
    contract_version: Literal[1] = 1
    request_id: UUID
    source_object_id: UUID
    reason: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def nonblank_reason(self) -> RevokeExactSource:
        if not self.reason.strip():
            raise ValueError("source revocation requires a reason")
        return self


class ExactSourceRevocation(FrozenContractModel):
    contract_version: Literal[1] = 1
    request_id: UUID
    source_object_id: UUID
    replayed: bool
    recorded_at: AwareDatetime
