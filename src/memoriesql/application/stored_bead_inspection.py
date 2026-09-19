"""Bounded stored-result inspection. No semantic interpretation or model calls."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.bead_classification import (
    BeadTypeDefinition,
    ClassificationContribution,
)
from memoriesql.application.canonical_transactions import AuthoredBeadRender
from memoriesql.application.logical_unit_materialization import SealedPackagePin
from memoriesql.application.semantic_task_contracts import FrozenContractModel
from memoriesql.application.source_revisiting import (
    ReadSourceEvidence,
    SourceEvidenceRead,
)


class InspectStoredBead(FrozenContractModel):
    contract_version: Literal[1] = 1
    bead_id: UUID


class UnitSupport(FrozenContractModel):
    """An actual unit-level link, never a sentence or span citation."""

    event_id: UUID
    source_unit_id: UUID
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class StoredStatement(FrozenContractModel):
    statement_id: UUID
    sequence: int = Field(ge=1)
    kind: Literal["observation", "context", "qualification", "correction"]
    text: str = Field(min_length=1, max_length=8192)
    supersedes_statement_id: UUID | None
    correction_reason: str | None = Field(max_length=1024)
    evidence: tuple[UnitSupport, ...] = Field(min_length=1, max_length=64)


class StoredResolution(FrozenContractModel):
    availability: Literal["available", "unavailable"]
    resolution_id: UUID | None = None
    status: Literal["unresolved", "resolved", "ambiguous", "rejected"] | None = None
    entity_ids: tuple[UUID, ...] | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def shape(self) -> StoredResolution:
        if self.availability != "available" and (
            self.resolution_id is not None
            or self.status is not None
            or self.entity_ids is not None
        ):
            raise ValueError("unavailable resolution must not disclose details")
        if self.availability == "available" and (
            self.resolution_id is None or self.status is None or self.entity_ids is None
        ):
            raise ValueError("available resolution requires its stored decision")
        return self


class StoredMention(FrozenContractModel):
    entity_mention_id: UUID
    surface_text: str = Field(min_length=1, max_length=1024)
    local_identity_state: Literal["unresolved", "ambiguous"] | None
    local_identity_reason: str | None = Field(max_length=1024)
    resolution: StoredResolution


class ContributionProvenance(FrozenContractModel):
    request_id: UUID
    task_id: UUID
    attempt_id: UUID
    model_id: str
    model_profile_key: str
    model_profile_revision: int
    authorization_policy_revision: int
    quality_policy_revision_id: UUID
    pricing_revision_id: UUID | None


class AcceptedMeaning(FrozenContractModel):
    bead_version_id: UUID
    receipt_id: UUID
    task_contract_key: str
    task_contract_version: int
    bead_type: BeadTypeDefinition
    render_contract_revision: int | None
    render: AuthoredBeadRender | None
    statements: tuple[StoredStatement, ...] = Field(max_length=32)
    # None means unsupported, never an authored empty collection.
    mentions: tuple[StoredMention, ...] | None = Field(max_length=32)
    classification: ClassificationContribution | None
    classification_vocabulary: tuple[BeadTypeDefinition, ...] | None = Field(
        max_length=32
    )
    classification_provenance: ContributionProvenance | None


class StoredBead(FrozenContractModel):
    bead_id: UUID
    event_id: UUID
    source_object_id: UUID
    source_unit_id: UUID
    parent_source_unit_id: UUID | None
    parent_resolution: Literal["canonical", "native_identity_only", "unknown"]
    package: SealedPackagePin | None
    binding_task_id: UUID | None
    execution_task_id: UUID | None
    execution_status: str | None
    lifecycle: Literal["accepted", "thin", "pending", "failed"]
    meaning: AcceptedMeaning | None

    @model_validator(mode="after")
    def accepted(self) -> StoredBead:
        if (self.lifecycle == "accepted") != (self.meaning is not None):
            raise ValueError("only persisted acceptance carries meaning")
        return self


class StoredBeadInspection(FrozenContractModel):
    contract_version: Literal[1] = 1
    outcome: Literal["available", "unavailable", "budget_exhausted"]
    bead: StoredBead | None = None

    @model_validator(mode="after")
    def disclosure(self) -> StoredBeadInspection:
        if (self.outcome == "available") != (self.bead is not None):
            raise ValueError("unavailable results must not disclose partial aggregates")
        return self


class ReadStoredBeadEvidence(FrozenContractModel):
    contract_version: Literal[1] = 1
    bead_id: UUID
    selection: ReadSourceEvidence


class StoredBeadEvidence(FrozenContractModel):
    contract_version: Literal[1] = 1
    outcome: Literal["available", "unavailable", "budget_exhausted"]
    evidence: SourceEvidenceRead | None = None

    @model_validator(mode="after")
    def disclosure(self) -> StoredBeadEvidence:
        if (self.outcome == "available") != (self.evidence is not None):
            raise ValueError("unavailable evidence must not disclose partial content")
        return self
