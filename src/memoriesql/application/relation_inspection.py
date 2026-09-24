"""Bounded relation reads over both relation kinds (schema 29).

The v2 relations read returns revision-6 authored relations and assessed
relations together: endpoint and basis statements, attribution, evidence and
its independent roots, the specialist's judgment, pair coverage and the status
of the bead's relation tasks. Every relation type carries its pinned definition.
Every disclosed bead, statement and evidence item is reauthorized; any
unreadable dependency makes the whole read unavailable. The vocabulary read
lists the active revisions with their full definitions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.authored_relations import (
    RelationTypeDefinition,
    RelationTypePin,
)
from memoriesql.application.relation_assessment import Abstention, RelationJudgment
from memoriesql.application.relation_lifecycle import (
    InspectedAssessment,
    InspectedEvidence,
    InspectedLifecycleEvent,
)
from memoriesql.application.semantic_task_contracts import FrozenContractModel

AssertionState = Literal[
    "active", "superseded", "retracted", "disputed", "reassessment_pending", "not_accepted"
]


class InspectBeadRelationsV2(FrozenContractModel):
    """Read both relation kinds around one accepted bead as known at a time."""

    contract_version: Literal[2] = 2
    bead_id: UUID
    known_at: datetime | None = None


class InspectedStatementPin(FrozenContractModel):
    statement_id: UUID
    bead_id: UUID
    bead_version_id: UUID
    text: str


class InspectedAssertion(FrozenContractModel):
    """One relation assertion of either kind, read "source <forward reading> target".

    Authored assertions come from revision-6 authorship and have no basis
    statements, task or judgment. Assessed assertions come from a relation task;
    a proposal the specialist did not agree with stays `not_accepted` with both
    contributions visible and never counts as a relation.
    """

    kind: Literal["authored", "assessed"]
    relation_id: UUID
    # Its full pinned definition is in the inspection's relation types.
    relation_type: RelationTypePin
    # "basis": this bead supplies a basis statement but neither endpoint.
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
    author_confidence: float
    evidence: tuple[InspectedEvidence, ...]
    # Union of the evidence roots: shared roots count once, never as corroboration.
    independent_root_count: int = Field(ge=0)
    authoring_bead_id: UUID | None
    task_id: UUID | None
    author_run_ref: str
    specialist_run_ref: str | None
    judgment: RelationJudgment | None
    recorded_at: datetime
    state: AssertionState
    superseded_by: tuple[UUID, ...]
    endpoint_corrected_by: tuple[UUID, ...]
    events: tuple[InspectedLifecycleEvent, ...]

    @model_validator(mode="after")
    def kind_shape(self) -> InspectedAssertion:
        if self.kind == "authored":
            if (
                self.basis_statements
                or self.task_id is not None
                or self.judgment is not None
                or self.authoring_bead_id is None
                or self.state == "not_accepted"
            ):
                raise ValueError("an authored relation has its authoring bead and no judgment")
        elif self.task_id is None or self.judgment is None or self.specialist_run_ref is None:
            raise ValueError("an assessed relation names its task and judgment")
        return self


class InspectedPairDisposition(FrozenContractModel):
    task_id: UUID
    first_bead_id: UUID
    first_bead_version_id: UUID
    second_bead_id: UUID
    second_bead_version_id: UUID
    disposition: Literal["related", "not_related", "abstained", "not_assessed"]
    abstention: Abstention | None
    reason: str | None


class InspectedRelationTask(FrozenContractModel):
    """A relation task whose subject is this bead, with its visible status."""

    task_id: UUID
    subject_bead_version_id: UUID
    candidate_bead_ids: tuple[UUID, ...]
    reconsiders_task_id: UUID | None
    status: str
    status_reason: str | None
    activated_at: datetime
    completed_at: datetime | None


class BeadRelationsInspectionV2(FrozenContractModel):
    contract_version: Literal[2] = 2
    outcome: Literal["available", "unavailable", "budget_exhausted"]
    bead_id: UUID | None = None
    known_at: datetime | None = None
    # Every relation type revision a relation or judgment names, once, in full.
    relation_types: tuple[RelationTypeDefinition, ...] = ()
    relations: tuple[InspectedAssertion, ...] = ()
    pair_dispositions: tuple[InspectedPairDisposition, ...] = ()
    candidate_assessments: tuple[InspectedAssessment, ...] = ()
    relation_tasks: tuple[InspectedRelationTask, ...] = ()

    @model_validator(mode="after")
    def outcome_shape(self) -> BeadRelationsInspectionV2:
        if (self.outcome == "available") != (self.bead_id is not None):
            raise ValueError("only an available inspection names its bead")
        if self.outcome != "available" and (
            self.relation_types
            or self.relations
            or self.pair_dispositions
            or self.candidate_assessments
            or self.relation_tasks
        ):
            raise ValueError("an unavailable inspection discloses nothing")
        return self


class InspectRelationVocabulary(FrozenContractModel):
    contract_version: Literal[1] = 1


class RelationVocabularyInspection(FrozenContractModel):
    """Active relation type revisions with their full definitions."""

    contract_version: Literal[1] = 1
    outcome: Literal["available", "unavailable"]
    relation_types: tuple[RelationTypeDefinition, ...] = ()

    @model_validator(mode="after")
    def outcome_shape(self) -> RelationVocabularyInspection:
        if self.outcome != "available" and self.relation_types:
            raise ValueError("an unavailable inspection discloses nothing")
        return self


class RelationInspection(Protocol):
    def inspect_relations(self, request: InspectBeadRelationsV2) -> BeadRelationsInspectionV2: ...
    def inspect_vocabulary(
        self, request: InspectRelationVocabulary
    ) -> RelationVocabularyInspection: ...
