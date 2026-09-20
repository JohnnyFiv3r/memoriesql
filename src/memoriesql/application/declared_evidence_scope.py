"""Explicit source-local evidence scopes; no inferred native episode or meaning.

Package v1 already preserves unknown native boundaries and source completeness.
This opt-in materialization qualifies a declared scope, never those unknown facts.
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.canonical_transactions import SourceType
from memoriesql.application.evidence_packages import PackageStatus
from memoriesql.application.logical_unit_materialization import (
    LogicalUnitMaterializationReceipt,
)
from memoriesql.application.semantic_task_contracts import FrozenContractModel


class DeclaredEvidenceScope(FrozenContractModel):
    scope_id: UUID
    boundary_basis: str = Field(min_length=1, max_length=512)
    episode_completeness: Literal["unknown"] = "unknown"
    amends_source_unit_id: UUID | None = None
    amendment_reason: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def amendment(self) -> Self:
        if (self.amends_source_unit_id is None) != (self.amendment_reason is None):
            raise ValueError("scope amendment requires target and reason")
        if not self.boundary_basis.strip() or (
            self.amendment_reason is not None and not self.amendment_reason.strip()
        ):
            raise ValueError("scope basis and amendment reason must not be blank")
        return self

    @property
    def occurrence_key(self) -> str:
        return "declared-scope." + str(self.scope_id)


class ScopeCarryForward(FrozenContractModel):
    """Qualified producer assertion, not equivalence inferred from equal text."""

    original_package_id: UUID
    correspondence_basis: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def nonblank(self) -> Self:
        if not self.correspondence_basis.strip():
            raise ValueError("carry-forward requires an explicit correspondence basis")
        return self


class MaterializeDeclaredScope(FrozenContractModel):
    contract_version: Literal[3] = 3
    expected_schema_version: Literal[25] = 25
    idempotency_key: str = Field(min_length=1, max_length=512)
    package_id: UUID
    expected_inventory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    producer_policy_id: UUID
    expected_source_object_schema_version: int = Field(ge=1)
    source_type: SourceType
    scope: DeclaredEvidenceScope
    carry_forward: ScopeCarryForward | None = None


class DeclaredScopeMaterializationReceipt(LogicalUnitMaterializationReceipt):
    contract_version: Literal[3] = 3  # type: ignore[assignment]
    scope: DeclaredEvidenceScope
    carry_forward: ScopeCarryForward | None


class InspectScopedEvidencePackage(FrozenContractModel):
    contract_version: Literal[2] = 2
    operation: Literal["inspect"] = "inspect"
    package_id: UUID


class DeclaredScopeBinding(FrozenContractModel):
    scope: DeclaredEvidenceScope
    original_package_id: UUID
    source_unit_id: UUID
    initial_materialization_receipt_id: UUID
    identity_basis: Literal["declared_scope"] = "declared_scope"


class ScopedEvidencePackageStatus(FrozenContractModel):
    contract_version: Literal[2] = 2
    package: PackageStatus
    scope_binding: DeclaredScopeBinding | None
    # v1 readiness still describes native qualification. This field describes only
    # an actually persisted declared-scope binding, not native/semantic completion.
    scope_readiness: Literal["not_materialized", "materialized_declared_scope"]

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (self.scope_binding is None) != (self.scope_readiness == "not_materialized"):
            raise ValueError("scope readiness must reflect its persisted binding")
        return self
