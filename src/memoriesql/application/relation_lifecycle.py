"""Governed claim/relation lifecycle, relation vocabulary governance and bounded reads.

Schema 27. Lifecycle records are append-only authority-bearing judgments; state is
derived from them, never stored as a mutable status, and neither recency nor write
order selects a winner. Vocabulary revisions are immutable; a custom type enters
only through an explicit proposal and an authorized decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.authored_relations import (
    RelationTypePin,
    SourceClock,
)
from memoriesql.application.semantic_task_contracts import FrozenContractModel

ClaimAction = Literal["reaffirm", "supersede", "retract", "dispute", "resolve_dispute"]
RelationAction = Literal["confirm", "dispute", "retract", "supersede"]
DerivedClaimState = Literal["current", "superseded", "retracted", "disputed"]
DerivedRelationState = Literal[
    "active", "superseded", "retracted", "disputed", "reassessment_pending"
]


def _nonblank(value: str | None, field: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field} must not be blank")


class LifecycleEvidence(FrozenContractModel):
    """An accepted statement and one of its exact supporting units."""

    statement_id: UUID
    source_unit_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RecordClaimEvent(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[27] = 27
    idempotency_key: str = Field(min_length=1, max_length=512)
    claim_id: UUID
    action: ClaimAction
    # supersede: the replacement; dispute: the competing claim;
    # resolve_dispute: the competing claim of the dispute being resolved.
    related_claim_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=1024)
    evidence: tuple[LifecycleEvidence, ...] = Field(default=(), max_length=8)
    effective_at: datetime | None = None
    # Operational compare-and-swap on this claim's latest visible event.
    expected_last_event_id: UUID | None = None

    @model_validator(mode="after")
    def shape(self) -> RecordClaimEvent:
        _nonblank(self.reason, "lifecycle reason")
        needs_related = self.action in ("supersede", "dispute", "resolve_dispute")
        if needs_related != (self.related_claim_id is not None):
            raise ValueError(f"{self.action} requires exactly the related claim it names")
        if self.related_claim_id == self.claim_id:
            raise ValueError("a claim cannot relate to itself")
        if len({e.statement_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("one evidence item per statement")
        return self


class ClaimEventReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    claim_event_id: UUID
    claim_id: UUID
    action: ClaimAction
    idempotency_receipt_id: UUID
    replayed: bool


class RecordRelationEvent(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[27] = 27
    idempotency_key: str = Field(min_length=1, max_length=512)
    relation_id: UUID
    action: RelationAction
    replacement_relation_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=1024)
    evidence: tuple[LifecycleEvidence, ...] = Field(default=(), max_length=8)
    effective_at: datetime | None = None
    expected_last_event_id: UUID | None = None

    @model_validator(mode="after")
    def shape(self) -> RecordRelationEvent:
        _nonblank(self.reason, "lifecycle reason")
        if (self.action == "supersede") != (self.replacement_relation_id is not None):
            raise ValueError("only supersede names its replacement relation")
        if self.replacement_relation_id == self.relation_id:
            raise ValueError("a relation cannot replace itself")
        if len({e.statement_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("one evidence item per statement")
        return self


class RelationEventReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    relation_event_id: UUID
    relation_id: UUID
    action: RelationAction
    idempotency_receipt_id: UUID
    replayed: bool


class ProposeRelationType(FrozenContractModel):
    """Propose a workspace relation type or a new revision of one.

    Built-in product definitions cannot be proposed, shadowed or redefined.
    """

    contract_version: Literal[1] = 1
    expected_schema_version: Literal[27] = 27
    idempotency_key: str = Field(min_length=1, max_length=512)
    # The access scope that records this workspace-wide decision's receipt.
    access_scope_id: UUID
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=128)
    definition: str = Field(min_length=1, max_length=4096)
    forward_reading: str = Field(min_length=1, max_length=128)
    inverse_reading: str = Field(min_length=1, max_length=128)
    symmetric: bool
    status: Literal["active", "inactive"] = "active"
    reason: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def shape(self) -> ProposeRelationType:
        for field in ("label", "definition", "forward_reading", "inverse_reading", "reason"):
            _nonblank(getattr(self, field), field)
        return self


class RelationTypeProposalReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    candidate_id: UUID
    key: str
    proposed_revision: int = Field(ge=1)
    idempotency_receipt_id: UUID
    replayed: bool


class DecideRelationType(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[27] = 27
    idempotency_key: str = Field(min_length=1, max_length=512)
    access_scope_id: UUID
    candidate_id: UUID
    decision: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def shape(self) -> DecideRelationType:
        _nonblank(self.reason, "decision reason")
        return self


class RelationTypeDecisionReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    candidate_id: UUID
    decision: Literal["accepted", "rejected"]
    relation_type: RelationTypePin | None
    relation_type_revision_id: UUID | None
    idempotency_receipt_id: UUID
    replayed: bool


class InspectBeadRelations(FrozenContractModel):
    """Read claims, relations and coverage of one accepted bead.

    `known_at` limits every record and lifecycle event to those recorded by then.
    """

    contract_version: Literal[1] = 1
    bead_id: UUID
    known_at: datetime | None = None


class InspectedStatement(FrozenContractModel):
    statement_id: UUID
    text: str


class InspectedLifecycleEvent(FrozenContractModel):
    """One lifecycle event recorded on `target_id`, naming `related_id` when it has one.

    A claim's history includes incoming disputes and resolutions recorded on the
    competing claim, so every derived state is explained by its listed events.
    """

    event_id: UUID
    target_id: UUID
    action: str
    related_id: UUID | None
    reason: str
    origin: Literal["authored", "governed"]
    authoring_bead_id: UUID | None
    effective_at: datetime | None
    recorded_at: datetime


class InspectedEvidence(FrozenContractModel):
    """One exact evidence pair and its derivation roots as known at the read time."""

    statement_id: UUID
    source_unit_id: UUID
    content_sha256: str
    derivation_root_ids: tuple[UUID, ...]


class InspectedClaim(FrozenContractModel):
    claim_id: UUID
    bead_id: UUID
    bead_version_id: UUID
    subject: str
    subject_mention_id: UUID | None
    slot: str
    value: str
    applicability: str | None
    statements: tuple[InspectedStatement, ...]
    source_time: SourceClock
    recorded_at: datetime
    state: DerivedClaimState
    superseded_by: tuple[UUID, ...]
    disputed_with: tuple[UUID, ...]
    origin_corrected_by: tuple[UUID, ...]
    events: tuple[InspectedLifecycleEvent, ...]


class InspectedRelationType(RelationTypePin):
    namespace: Literal["memoriesql", "workspace"]
    label: str
    forward_reading: str
    inverse_reading: str
    symmetric: bool


class InspectedRelation(FrozenContractModel):
    relation_id: UUID
    relation_type: InspectedRelationType
    direction: Literal["outgoing", "incoming"]
    source_bead_id: UUID
    source_bead_version_id: UUID
    target_bead_id: UUID
    target_bead_version_id: UUID
    authoring_bead_id: UUID
    basis: Literal["source_stated", "inferred"]
    rationale: str
    uncertainty: str | None
    author_confidence: float
    source_statements: tuple[InspectedStatement, ...]
    target_statements: tuple[InspectedStatement, ...]
    evidence: tuple[InspectedEvidence, ...]
    # Union of the evidence roots: shared roots count once, never as corroboration.
    independent_root_count: int = Field(ge=0)
    recorded_at: datetime
    state: DerivedRelationState
    superseded_by: tuple[UUID, ...]
    endpoint_corrected_by: tuple[UUID, ...]
    events: tuple[InspectedLifecycleEvent, ...]


class InspectedAssessment(FrozenContractModel):
    candidate_bead_id: UUID
    candidate_bead_version_id: UUID
    assessment: Literal["edge", "no_edge", "unassessed"]
    reason: str | None


class InspectedAuthoredClaimEvent(FrozenContractModel):
    """A lifecycle judgment this bead authored about another bead's claim."""

    event_id: UUID
    target_claim_id: UUID
    target_bead_id: UUID
    action: str
    related_claim_id: UUID | None
    reason: str


class BeadRelationsInspection(FrozenContractModel):
    contract_version: Literal[1] = 1
    outcome: Literal["available", "unavailable", "budget_exhausted"]
    bead_id: UUID | None = None
    known_at: datetime | None = None
    # False when the bead was accepted by a task revision that could not author them.
    relations_capable: bool = False
    claims: tuple[InspectedClaim, ...] = ()
    relations: tuple[InspectedRelation, ...] = ()
    candidate_assessments: tuple[InspectedAssessment, ...] = ()
    authored_claim_events: tuple[InspectedAuthoredClaimEvent, ...] = ()

    @model_validator(mode="after")
    def outcome_shape(self) -> BeadRelationsInspection:
        if (self.outcome == "available") != (self.bead_id is not None):
            raise ValueError("only an available inspection names its bead")
        if self.outcome != "available" and (
            self.claims or self.relations or self.candidate_assessments
            or self.authored_claim_events
        ):
            raise ValueError("an unavailable inspection discloses nothing")
        return self


class RelationLifecycle(Protocol):
    def record_claim_event(self, command: RecordClaimEvent) -> ClaimEventReceipt: ...
    def record_relation_event(self, command: RecordRelationEvent) -> RelationEventReceipt: ...
    def propose_relation_type(
        self, command: ProposeRelationType
    ) -> RelationTypeProposalReceipt: ...
    def decide_relation_type(
        self, command: DecideRelationType
    ) -> RelationTypeDecisionReceipt: ...
    def inspect_relations(self, request: InspectBeadRelations) -> BeadRelationsInspection: ...
