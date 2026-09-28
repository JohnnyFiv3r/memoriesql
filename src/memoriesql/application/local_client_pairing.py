"""Provider-neutral pairing of one local agent client under current human authority."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from memoriesql.application.semantic_task_contracts import FrozenContractModel

_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def _distinct_authority(
    capabilities: tuple[str, ...], access_scope_ids: tuple[UUID, ...]
) -> None:
    if any(_CAPABILITY.fullmatch(capability) is None for capability in capabilities):
        raise ValueError("capabilities must be registered capability keys")
    if len(set(capabilities)) != len(capabilities):
        raise ValueError("capabilities must be distinct")
    if len(set(access_scope_ids)) != len(access_scope_ids):
        raise ValueError("access scopes must be distinct")


class PairLocalClient(FrozenContractModel):
    """Pair one agent client with explicit capabilities, owned scopes and expiry.

    The database derives tenant, workspace and the pairing human from the
    authenticated credential. The paired client's authority is the intersection
    of this pairing grant, its role and each paired scope's policy. An
    owner-private scope admits the client on the pairing human's behalf; an
    explicit scope, such as an enrolled exact source's, also requires a current
    access grant to the client's principal.
    """

    contract_version: Literal[1] = 1
    request_id: UUID
    principal_kind: Literal["agent"] = "agent"
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=32)
    access_scope_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    expires_at: AwareDatetime
    exact_pairing_confirmed: Literal[True]

    @model_validator(mode="after")
    def bounded_authority(self) -> PairLocalClient:
        _distinct_authority(self.capabilities, self.access_scope_ids)
        return self


class LocalClientPairing(FrozenContractModel):
    contract_version: Literal[1] = 1
    principal_id: UUID
    pairing_id: UUID
    pairing_grant_id: UUID
    pairing_grant_revision: Literal[1] = 1
    credential_id: UUID
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=32)
    access_scope_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    expires_at: AwareDatetime


class RevokeLocalClient(FrozenContractModel):
    """Terminally revoke one pairing grant at its exact current revision."""

    contract_version: Literal[1] = 1
    pairing_grant_id: UUID
    expected_revision: int = Field(ge=1)
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=32)
    access_scope_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    exact_revocation_confirmed: Literal[True]

    @model_validator(mode="after")
    def bounded_authority(self) -> RevokeLocalClient:
        _distinct_authority(self.capabilities, self.access_scope_ids)
        return self


class LocalClientRevocation(FrozenContractModel):
    contract_version: Literal[1] = 1
    pairing_grant_id: UUID
    revision: int = Field(ge=2)
    revoked_at: AwareDatetime
