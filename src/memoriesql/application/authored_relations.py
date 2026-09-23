"""Optional authored claims and bead-to-bead relations through revision-6 authorship.

Schema 27 lets the primary author of one complete unit propose tracked claims,
relations to explicitly pinned candidate beads, lifecycle judgments about the
candidates' claims and an assessment of every pinned candidate. Code validates
identity, pins, evidence and integrity; it never infers a relation, a conflict
or a winner. The 0-1 author confidence is a diagnostic, never authority.
No specialist relation judgment is part of this revision.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
)
from memoriesql.application.canonical_transactions import (
    StatementEvidence,
    StatementKind,
)
from memoriesql.application.complete_input_execution import ApplyCompleteInput
from memoriesql.application.local_entity_mentions import (
    MENTION_EXECUTION_TASK,
    MentionAuthorStep,
    MentionExecutionInput,
    MentionExecutionOutput,
)
from memoriesql.application.module_registry import BuiltInModuleRegistry
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    EvidencePolicyContract,
    FrozenContractModel,
    SemanticTaskDefinition,
    TypedModelContract,
    canonical_json_bytes,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry
from memoriesql.application.source_revisiting import (
    ActivateSourceRevisiting,
    RevisitingPayload,
    SourceRevisitingActivationReceipt,
)

RELATED_OUTPUT_BYTES = 32768
CANDIDATE_PACKET_BYTES = 65536
MAX_CANDIDATES = 8
MAX_RELATIONS = 16
MAX_CLAIMS = 16
MAX_CLAIM_UPDATES = 16

ClaimState = Literal["current", "superseded", "retracted", "disputed"]


def _nonblank(value: str | None, field: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field} must not be blank")


class RelationTypePin(FrozenContractModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    revision: int = Field(ge=1)


class RelationTypeDefinition(RelationTypePin):
    """An exact active vocabulary revision pinned at activation."""

    namespace: Literal["memoriesql", "workspace"]
    label: str = Field(min_length=1, max_length=128)
    definition: str = Field(min_length=1, max_length=4096)
    forward_reading: str = Field(min_length=1, max_length=128)
    inverse_reading: str = Field(min_length=1, max_length=128)
    symmetric: bool


class BeadTypeReference(FrozenContractModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    revision: int = Field(ge=1)


class CandidateStatement(FrozenContractModel):
    statement_id: UUID
    statement_kind: StatementKind
    statement_text: str = Field(min_length=1, max_length=8192)
    evidence: tuple[StatementEvidence, ...] = Field(min_length=1, max_length=64)


class CandidateMention(FrozenContractModel):
    entity_mention_id: UUID
    surface_text: str = Field(min_length=1, max_length=1024)
    local_identity_state: Literal["unresolved", "ambiguous"] | None = None


class CandidateClaim(FrozenContractModel):
    claim_id: UUID
    statement_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)
    subject: str = Field(min_length=1, max_length=256)
    slot: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=1024)
    applicability: str | None = Field(default=None, min_length=1, max_length=1024)
    # Derived at activation from canonical lifecycle; informational only.
    state_at_activation: ClaimState


class SourceClock(FrozenContractModel):
    """Inherited source clocks. A capture-time basis is never occurrence time.

    Unit time wins over event time; precision and order are exactly as stored.
    """

    event_id: UUID
    source_unit_id: UUID
    source_object_id: UUID
    occurred_at: datetime | None
    occurred_end_at: datetime | None
    time_precision: str | None = Field(default=None, max_length=64)
    time_basis: Literal["unit_source_time", "event_source_time", "capture_time"]
    unit_ordinal: int | None = None
    event_sequence: int | None = None
    recorded_at: datetime


class RelationCandidate(FrozenContractModel):
    """One explicitly supplied accepted bead. Supplied coverage, not relevance."""

    bead_id: UUID
    bead_version_id: UUID
    origin_kind: Literal["initial", "correction"]
    bead_type: BeadTypeReference
    title: str | None = Field(default=None, max_length=2048)
    summary: tuple[str, ...] = Field(default=(), max_length=3)
    source: SourceClock
    statements: tuple[CandidateStatement, ...] = Field(min_length=1, max_length=32)
    mentions: tuple[CandidateMention, ...] = Field(default=(), max_length=32)
    claims: tuple[CandidateClaim, ...] = Field(default=(), max_length=MAX_CLAIMS)
    superseded_by: tuple[UUID, ...] = Field(default=(), max_length=8)


class RelatedPayload(RevisitingPayload):
    relation_candidates: tuple[RelationCandidate, ...] = Field(
        default=(), max_length=MAX_CANDIDATES
    )
    relation_vocabulary: tuple[RelationTypeDefinition, ...] = Field(
        min_length=1, max_length=32
    )

    @model_validator(mode="after")
    def pins(self) -> RelatedPayload:
        keys = [d.key for d in self.relation_vocabulary]
        if len(keys) != len(set(keys)):
            raise ValueError("one explicit revision per relation vocabulary key")
        beads = [c.bead_id for c in self.relation_candidates]
        if len(beads) != len(set(beads)) or self.bead_ids[0] in beads:
            raise ValueError("candidates must be distinct accepted beads, not the target")
        if (
            len(
                canonical_json_bytes(
                    [c.model_dump(mode="json") for c in self.relation_candidates]
                )
            )
            > CANDIDATE_PACKET_BYTES
        ):
            raise ValueError(
                f"relation candidates exceed {CANDIDATE_PACKET_BYTES} bytes; supply fewer"
            )
        return self


class RelatedExecutionInput(MentionExecutionInput):
    contract_revision: Literal[6] = 6  # type: ignore[assignment]
    payload: RelatedPayload


class RelationEvidenceReference(FrozenContractModel):
    source_unit_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class AuthoredRelation(FrozenContractModel):
    """A proposed evidence-backed connection between the authored bead and one candidate."""

    relation_id: UUID
    candidate_bead_id: UUID
    # from_authored: authored bead is the source; the stored row reads
    # "source <forward_reading> target".
    direction: Literal["from_authored", "to_authored"]
    relation_type: RelationTypePin
    basis: Literal["source_stated", "inferred"]
    authored_statement_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)
    candidate_statement_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)
    evidence: tuple[RelationEvidenceReference, ...] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=1024)
    uncertainty: str | None = Field(default=None, min_length=1, max_length=1024)
    # Diagnostic 0-1 author confidence with at most two decimals; never authority.
    author_confidence: float = Field(ge=0, le=1, multiple_of=0.01, allow_inf_nan=False)

    @model_validator(mode="after")
    def shape(self) -> AuthoredRelation:
        _nonblank(self.rationale, "relation rationale")
        _nonblank(self.uncertainty, "relation uncertainty")
        # Two decimals keep Python and PostgreSQL canonical JSON byte-identical.
        if round(self.author_confidence, 2) != self.author_confidence:
            raise ValueError("author confidence uses at most two decimal places")
        for ids in (self.authored_statement_ids, self.candidate_statement_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("endpoint statement IDs must be unique")
        pins = [(e.source_unit_id, e.content_hash) for e in self.evidence]
        if len(pins) != len(set(pins)):
            raise ValueError("relation evidence references must be unique")
        return self


class CandidateAssessment(FrozenContractModel):
    """Every supplied candidate is assessed with an edge, without one, or not at all."""

    candidate_bead_id: UUID
    assessment: Literal["edge", "no_edge", "unassessed"]
    reason: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def reason_for_unassessed(self) -> CandidateAssessment:
        _nonblank(self.reason, "assessment reason")
        if self.assessment == "unassessed" and self.reason is None:
            raise ValueError("an unassessed candidate requires its reason")
        return self


class AuthoredClaim(FrozenContractModel):
    """An optional tracked proposition of the authored bead, bound to its statements."""

    claim_id: UUID
    statement_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)
    subject: str = Field(min_length=1, max_length=256)
    subject_mention_id: UUID | None = None
    slot: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=1024)
    applicability: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def shape(self) -> AuthoredClaim:
        for field in ("subject", "slot", "value", "applicability"):
            _nonblank(getattr(self, field), f"claim {field}")
        if len(self.statement_ids) != len(set(self.statement_ids)):
            raise ValueError("claim statement IDs must be unique")
        return self


class ClaimUpdate(FrozenContractModel):
    """An authored lifecycle judgment about one pinned candidate's claim."""

    target_claim_id: UUID
    action: Literal["supersede", "dispute", "reaffirm"]
    related_claim_id: UUID | None = None
    basis_statement_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def shape(self) -> ClaimUpdate:
        _nonblank(self.reason, "claim update reason")
        if (self.action == "reaffirm") != (self.related_claim_id is None):
            raise ValueError(
                "supersede and dispute name a claim of this bundle; reaffirm does not"
            )
        if len(self.basis_statement_ids) != len(set(self.basis_statement_ids)):
            raise ValueError("basis statement IDs must be unique")
        return self


class RelatedExecutionOutput(MentionExecutionOutput):
    """Whole canonical JSON output is limited to 32,768 UTF-8 bytes."""

    relations: tuple[AuthoredRelation, ...] = Field(max_length=MAX_RELATIONS)
    candidate_assessments: tuple[CandidateAssessment, ...] = Field(
        max_length=MAX_CANDIDATES
    )
    claims: tuple[AuthoredClaim, ...] = Field(max_length=MAX_CLAIMS)
    claim_updates: tuple[ClaimUpdate, ...] = Field(max_length=MAX_CLAIM_UPDATES)

    @model_validator(mode="after")
    def coherent_bundle(self) -> RelatedExecutionOutput:
        bead = self.annotations[0]
        statements = {s.statement_id for s in bead.statements}
        mentions = {m.entity_mention_id for m in bead.mentions}
        relation_ids = [r.relation_id for r in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("relation IDs must be unique")
        labels = [
            (r.candidate_bead_id, r.direction, r.relation_type.key) for r in self.relations
        ]
        if len(labels) != len(set(labels)):
            raise ValueError("one relation per candidate, direction and type")
        claims = {c.claim_id for c in self.claims}
        if len(claims) != len(self.claims):
            raise ValueError("claim IDs must be unique")
        for relation in self.relations:
            if not set(relation.authored_statement_ids) <= statements:
                raise ValueError("relation must cite statements of the authored bead")
        for claim in self.claims:
            if not set(claim.statement_ids) <= statements:
                raise ValueError("claim must bind statements of the authored bead")
            if (
                claim.subject_mention_id is not None
                and claim.subject_mention_id not in mentions
            ):
                raise ValueError("claim subject mention must belong to the authored bead")
        updates = [(u.target_claim_id, u.action, u.related_claim_id) for u in self.claim_updates]
        if len(updates) != len(set(updates)):
            raise ValueError("claim updates must be unique")
        for update in self.claim_updates:
            if not set(update.basis_statement_ids) <= statements:
                raise ValueError("claim update basis must be authored statements")
            if update.related_claim_id is not None and update.related_claim_id not in claims:
                raise ValueError("claim update must name a claim of this bundle")
            if update.target_claim_id in claims:
                raise ValueError("claim updates target a pinned candidate's claim")
        assessed = [a.candidate_bead_id for a in self.candidate_assessments]
        if len(assessed) != len(set(assessed)):
            raise ValueError("one assessment per supplied candidate")
        edges = {r.candidate_bead_id for r in self.relations}
        for assessment in self.candidate_assessments:
            if (assessment.assessment == "edge") != (
                assessment.candidate_bead_id in edges
            ):
                raise ValueError(
                    "an edge assessment requires, and only it permits, a relation"
                )
        if not edges <= set(assessed):
            raise ValueError("every related candidate requires its assessment")
        return self

    @model_validator(mode="after")
    def reject_duplicate_beads(self) -> RelatedExecutionOutput:
        # Replaces the inherited check by name: identical duplicate rules, while
        # the whole-output envelope is the revision-6 bound below.
        bead_ids = [annotation.bead_id for annotation in self.annotations]
        if len(bead_ids) != len(set(bead_ids)):
            raise ValueError("each bead may appear once per semantic result")
        statement_ids = [
            statement.statement_id
            for annotation in self.annotations
            for statement in annotation.statements
        ]
        if len(statement_ids) != len(set(statement_ids)):
            raise ValueError("statement IDs must be unique")
        return self

    @model_validator(mode="after")
    def aggregate_output_bound(self) -> RelatedExecutionOutput:
        # Replaces the inherited 12,000-byte revision-4 bound by name.
        if (
            len(canonical_json_bytes(self.model_dump(mode="json", warnings="error")))
            > RELATED_OUTPUT_BYTES
        ):
            raise ValueError(
                f"related output exceeds {RELATED_OUTPUT_BYTES} canonical UTF-8 bytes"
            )
        return self


class RelatedAuthorStep(MentionAuthorStep):
    typed_output: RelatedExecutionOutput | None = None


class ActivateRelatedAuthorship(ActivateSourceRevisiting):
    """Explicit candidates and vocabulary; an empty candidate set is valid."""

    contract_version: Literal[5] = 5  # type: ignore[assignment]
    expected_schema_version: Literal[27] = 27  # type: ignore[assignment]
    relation_candidates: tuple[UUID, ...] = Field(default=(), max_length=MAX_CANDIDATES)
    relation_vocabulary: tuple[RelationTypePin, ...] = Field(
        min_length=1, max_length=32
    )

    @model_validator(mode="after")
    def distinct(self) -> ActivateRelatedAuthorship:
        if len(set(self.relation_candidates)) != len(self.relation_candidates):
            raise ValueError("candidate beads must be distinct")
        keys = [p.key for p in self.relation_vocabulary]
        if len(keys) != len(set(keys)):
            raise ValueError("one explicit revision per relation vocabulary key")
        return self


class RelatedActivationReceipt(SourceRevisitingActivationReceipt):
    contract_version: Literal[5] = 5  # type: ignore[assignment]
    relation_candidates: tuple[UUID, ...]


RELATED_EXECUTION_TASK = replace(
    MENTION_EXECUTION_TASK,
    contract_revision=6,
    input_contract=TypedModelContract(
        contract_id="memory.semantic.related-authorship.input",
        revision=1,
        model_type=RelatedExecutionInput,
    ),
    output_contract=TypedModelContract(
        contract_id="memory.semantic.related-authorship.output",
        revision=1,
        model_type=RelatedExecutionOutput,
    ),
    leaf_agent_key="memory.semantic.related-author",
)


def load_related_authorship_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    task = RELATED_EXECUTION_TASK
    return SemanticTaskRegistry._from_source_controlled(
        module_registry,
        (cast(SemanticTaskDefinition[BaseModel, BaseModel], task),),
        (
            AgentContract(
                agent_key=cast(str, task.leaf_agent_key),
                input_contract=task.input_contract.reference,
                output_contract=task.output_contract.reference,
                maximum_effort_key="standard",
            ),
        ),
        (),
        BUILTIN_EFFORT_PROFILES,
        BUILTIN_MODEL_PROFILE_REFERENCES,
        (EvidencePolicyContract(policy_key="source-revisiting-v1"),),
    )


class ApplyRelatedAuthorship(ApplyCompleteInput):
    contract_version: Literal[7] = 7  # type: ignore[assignment]
    expected_schema_version: Literal[27] = 27  # type: ignore[assignment]
    contract_revision: Literal[6] = 6  # type: ignore[assignment]
    semantic_payload_canonical_json: str = Field(
        min_length=2, max_length=RELATED_OUTPUT_BYTES
    )
    payload: RelatedExecutionOutput

    @model_validator(mode="after")
    def exact_result(self) -> ApplyRelatedAuthorship:
        data = canonical_json_bytes(self.payload.model_dump(mode="json"))
        if (
            self.semantic_payload_canonical_json.encode() != data
            or hashlib.sha256(data).hexdigest() != self.semantic_result_hash
        ):
            raise ValueError("semantic result hash mismatch")
        if self.output_contract_hash != RELATED_EXECUTION_TASK.output_contract.schema_hash:
            raise ValueError("related authorship output contract mismatch")
        return self
