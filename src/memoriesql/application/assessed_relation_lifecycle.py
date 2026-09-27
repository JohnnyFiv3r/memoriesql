"""Approved schema-30 human governance and canonical relation projection contracts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import Field, TypeAdapter, field_validator, model_validator

from memoriesql.application.authored_relations import (
    RelationTypeDefinition,
    RelationTypePin,
)
from memoriesql.application.relation_assessment import RelationJudgment
from memoriesql.application.relation_inspection import (
    AssertionState,
    InspectedPairDisposition,
    InspectedStatementPin,
)
from memoriesql.application.relation_lifecycle import (
    InspectedAssessment,
    LifecycleEvidence,
)
from memoriesql.application.semantic_task_contracts import (
    FrozenContractModel,
    canonical_json_bytes,
)

Action = Literal["confirm", "dispute", "retract"]
RootStatus = Literal["qualified", "indeterminate", "unsupported"]
Error = Literal[
    "invalid_request",
    "schema_mismatch",
    "unavailable",
    "idempotency_conflict",
    "head_conflict",
    "transition_invalid",
    "reassessment_required",
    "receipt_incomplete",
    "budget_exhausted",
]
_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
)


def normalized_time(value: Any) -> Any:
    if isinstance(value, str) and not _TIME.fullmatch(value):
        raise ValueError(
            "finite timezone-aware timestamp with at most six fractional digits required"
        )
    if value is not None:
        if not isinstance(value, str | datetime):
            raise ValueError('timestamp must be a string or aware datetime')
        value = TypeAdapter(datetime).validate_python(value)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        value = value.astimezone(UTC)
    return value


class RecordAssessedRelationEvent(FrozenContractModel):
    contract_version: Literal[1]
    expected_schema_version: Literal[30]
    idempotency_key: str = Field(min_length=1, max_length=512)
    relation_id: UUID
    action: Action
    reason: str = Field(min_length=1, max_length=1024)
    evidence: tuple[LifecycleEvidence, ...] = Field(max_length=8)
    effective_at: datetime | None
    expected_head_token: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("effective_at", mode="before")
    @classmethod
    def timestamp(cls, value: Any) -> Any:
        return normalized_time(value)

    @model_validator(mode="after")
    def shape(self) -> RecordAssessedRelationEvent:
        if not self.reason.strip() or not self.idempotency_key.strip():
            raise ValueError("reason and key must not be blank")
        if self.action == "confirm" and not self.evidence:
            raise ValueError("confirmation requires evidence")
        pairs = [(e.statement_id, e.source_unit_id) for e in self.evidence]
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate evidence pair")
        object.__setattr__(
            self,
            "evidence",
            tuple(
                sorted(self.evidence, key=lambda e: (e.statement_id, e.source_unit_id))
            ),
        )
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 16384:
            raise ValueError("normalized command exceeds byte bound")
        return self


class AssessedRelationEventReceipt(FrozenContractModel):
    contract_version: Literal[1]
    relation_event_id: UUID
    relation_id: UUID
    action: Action
    event_number: int = Field(ge=1)
    previous_event_id: UUID | None
    recorded_at: datetime
    effective_at: datetime | None
    idempotency_receipt_id: UUID
    head_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: AssertionState
    support_eligible: bool
    replayed: bool


class AssessedRelationEventRefusal(FrozenContractModel):
    contract_version: Literal[1] = 1
    outcome: Literal["refused"] = "refused"
    error: Error


EventResult = AssessedRelationEventReceipt | AssessedRelationEventRefusal


class InspectBeadRelationsV3(FrozenContractModel):
    contract_version: Literal[3]
    bead_id: UUID
    known_at: datetime | None

    @field_validator("known_at", mode="before")
    @classmethod
    def timestamp(cls, value: Any) -> Any:
        return normalized_time(value)


class RootGap(FrozenContractModel):
    kind: Literal["authored", "assessed"]
    relation_id: UUID
    reason: Literal[
        "disputed",
        "corrected",
        "withdrawn",
        "replacement_gap",
        "statement_roots_unsupported",
    ]


class QualifiedEvidence(FrozenContractModel):
    statement_id: UUID
    source_unit_id: UUID
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    derivation_root_ids: tuple[UUID, ...] | None
    roots_status: RootStatus
    roots_gap_relation_ids: tuple[RootGap, ...]

    @model_validator(mode="after")
    def qualified_shape(self) -> QualifiedEvidence:
        if (self.roots_status == "qualified") != (self.derivation_root_ids is not None):
            raise ValueError("only qualified evidence has roots")
        if (self.roots_status == "qualified") != (not self.roots_gap_relation_ids):
            raise ValueError("unqualified evidence requires complete gaps")
        return self


class ProjectedEvent(FrozenContractModel):
    event_id: UUID
    target_id: UUID
    action: Literal["confirm", "dispute", "retract", "supersede", "retire"]
    related_id: UUID | None
    reason: str
    origin: Literal["authored", "governed"]
    authoring_bead_id: UUID | None
    effective_at: datetime | None
    recorded_at: datetime
    event_number: int | None = Field(ge=1)
    previous_event_id: UUID | None
    recorded_by_principal_id: UUID
    recorded_by_user_id: UUID | None
    idempotency_receipt_id: UUID
    evidence: tuple[LifecycleEvidence, ...]


class ProjectedAssertion(FrozenContractModel):
    kind: Literal["authored", "assessed"]
    relation_id: UUID
    relation_type: RelationTypePin
    direction: Literal["outgoing", "incoming", "internal", "basis"]
    source_bead_id: UUID
    source_bead_version_id: UUID
    target_bead_id: UUID
    target_bead_version_id: UUID
    source_statements: tuple[InspectedStatementPin, ...]
    target_statements: tuple[InspectedStatementPin, ...]
    basis_statements: tuple[InspectedStatementPin, ...]
    basis: Literal["source_stated", "agent_inferred"]
    rationale: str
    qualification: str | None
    author_confidence: float = Field(ge=0, le=1, multiple_of=0.01)
    evidence: tuple[QualifiedEvidence, ...]
    independent_root_count: int | None = Field(ge=0)
    authoring_bead_id: UUID | None
    task_id: UUID | None
    author_run_ref: str
    specialist_run_ref: str | None
    judgment: RelationJudgment | None
    recorded_at: datetime
    state: AssertionState
    superseded_by: tuple[UUID, ...]
    endpoint_corrected_by: tuple[UUID, ...]
    basis_corrected_by: tuple[UUID, ...]
    events: tuple[ProjectedEvent, ...]
    acceptance: Literal["accepted", "not_accepted"]
    acceptance_receipt_id: UUID
    head_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    support_eligible: bool
    support_reason: (
        Literal["not_accepted", "disputed", "corrected", "retracted", "superseded"]
        | None
    )
    correction_pending: bool
    roots_status: RootStatus

    @model_validator(mode="after")
    def projection_shape(self) -> ProjectedAssertion:
        if self.support_eligible != (
            self.state == "active" and not self.correction_pending
        ):
            raise ValueError("eligibility must follow canonical state")
        if self.support_eligible != (self.support_reason is None):
            raise ValueError("ineligible assertions require a reason")
        if (self.roots_status == "qualified") != (
            self.independent_root_count is not None
        ):
            raise ValueError("only qualified assertions have an independent count")
        return self


class ProjectionFrame(FrozenContractModel):
    known_at: datetime
    snapshot_at: datetime
    dependency_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProjectedRelationTask(FrozenContractModel):
    task_id: UUID
    subject_bead_version_id: UUID
    candidate_bead_ids: tuple[UUID, ...]
    reconsiders_task_id: UUID | None
    status: str
    status_reason: str | None
    activated_at: datetime
    completed_at: datetime | None
    status_known_at: datetime


class BeadRelationsInspectionV3(FrozenContractModel):
    contract_version: Literal[3]
    outcome: Literal["available"]
    bead_id: UUID
    known_at: datetime
    frame: ProjectionFrame
    relation_types: tuple[RelationTypeDefinition, ...]
    relations: tuple[ProjectedAssertion, ...]
    pair_dispositions: tuple[InspectedPairDisposition, ...]
    candidate_assessments: tuple[InspectedAssessment, ...]
    relation_tasks: tuple[ProjectedRelationTask, ...]


class RelationInspectionUnavailable(FrozenContractModel):
    contract_version: Literal[3] = 3
    outcome: Literal["unavailable", "budget_exhausted"]


class RelationInspectionRefusal(FrozenContractModel):
    contract_version: Literal[3] = 3
    outcome: Literal["refused"] = "refused"
    error: Literal["invalid_request", "schema_mismatch"]


InspectionResult = (
    BeadRelationsInspectionV3
    | RelationInspectionUnavailable
    | RelationInspectionRefusal
)


class AssessedRelationLifecycle(Protocol):
    def record_event(self, command: RecordAssessedRelationEvent) -> EventResult: ...
    def inspect_relations(
        self, request: InspectBeadRelationsV3
    ) -> InspectionResult: ...
