"""One-time personal-local owner initialization under operator database access."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from memoriesql.application.semantic_task_contracts import FrozenContractModel


class InitializePersonalLocal(FrozenContractModel):
    """Create the single personal-local owner, workspace and owner credential.

    Every identifier derives from `request_id`, so an identical replay after an
    unknown outcome resolves to the same owner instead of a second one.
    """

    contract_version: Literal[1] = 1
    request_id: UUID
    owner_display_name: str = Field(min_length=1, max_length=256)
    expires_at: AwareDatetime
    exact_initialization_confirmed: Literal[True]

    @model_validator(mode="after")
    def nonblank_owner(self) -> InitializePersonalLocal:
        if not self.owner_display_name.strip():
            raise ValueError("owner display name must not be blank")
        return self


class PersonalLocalInitialization(FrozenContractModel):
    contract_version: Literal[1] = 1
    tenant_id: UUID
    user_id: UUID
    principal_id: UUID
    workspace_id: UUID
    access_scope_id: UUID
    credential_id: UUID
    expires_at: AwareDatetime
    replayed: bool
