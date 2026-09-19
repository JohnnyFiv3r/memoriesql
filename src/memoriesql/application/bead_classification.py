"""Versioned, provider-neutral contribution to one immutable authored bead."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Literal, Protocol, cast, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
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
    ModelProfileReference,
    SemanticTaskDefinition,
    TypedModelContract,
    canonical_json_bytes,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry
from memoriesql.application.source_revisiting import (
    ActivateSourceRevisiting,
    ReadSourceEvidence,
    RevisitingPayload,
    SourceEvidenceRead,
    SourceRevisitingActivationReceipt,
)


def digest(value: BaseModel) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value.model_dump(mode="json"))
    ).hexdigest()


class BeadTypePin(FrozenContractModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    revision: int = Field(ge=1)


class BeadTypeDefinition(BeadTypePin):
    definition: str = Field(min_length=1, max_length=4096)


class ClassificationDecision(FrozenContractModel):
    contract_version: Literal[1] = 1
    outcome: Literal["selected", "no_fit", "insufficient_evidence", "ambiguous"]
    selected_type: BeadTypePin | None = None
    proposal_alignment: Literal["consistent", "conflicting", "uncertain"]
    rationale: str | None = Field(default=None, min_length=1, max_length=2048)
    # Optional diagnostics, never author confidence, truth or an acceptance threshold.
    distribution: tuple[ClassificationScore, ...] = Field(default=(), max_length=35)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    alignment_distribution: tuple[AlignmentScore, ...] = Field(default=(), max_length=3)
    alignment_confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def shape(self) -> ClassificationDecision:
        if (self.outcome == "selected") != (self.selected_type is not None):
            raise ValueError("only selected outcomes carry an accepted candidate")
        if self.rationale is not None and not self.rationale.strip():
            raise ValueError("classification rationale must not be blank")
        pins = [
            x.type if isinstance(x.type, str) else (x.type.key, x.type.revision)
            for x in self.distribution
        ]
        if len(pins) != len(set(pins)):
            raise ValueError("diagnostic labels must be unique")
        if len({x.alignment for x in self.alignment_distribution}) != len(
            self.alignment_distribution
        ):
            raise ValueError("alignment diagnostic labels must be unique")
        return self


class ClassificationScore(FrozenContractModel):
    type: BeadTypePin | Literal["no_fit", "insufficient_evidence", "ambiguous"]
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class AlignmentScore(FrozenContractModel):
    alignment: Literal["consistent", "conflicting", "uncertain"]
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


ClassificationDecision.model_rebuild()


class ClassificationPayload(RevisitingPayload):
    classification_vocabulary: tuple[BeadTypeDefinition, ...] = Field(
        min_length=1, max_length=32
    )

    @model_validator(mode="after")
    def vocabulary_unique(self) -> ClassificationPayload:
        if len({d.key for d in self.classification_vocabulary}) != len(
            self.classification_vocabulary
        ):
            raise ValueError("one explicit revision per vocabulary key")
        return self


class ClassificationExecutionInput(MentionExecutionInput):
    contract_revision: Literal[5] = 5  # type: ignore[assignment]
    payload: ClassificationPayload


class ClassificationAuthorStep(MentionAuthorStep):
    supporting_selections: tuple[ReadSourceEvidence, ...] = Field(
        default=(), max_length=8
    )

    @model_validator(mode="after")
    def support_required(self) -> ClassificationAuthorStep:
        if (self.action == "finish") != bool(self.supporting_selections):
            raise ValueError("finish requires explicit supporting evidence selections")
        return self


class ClassificationPacket(FrozenContractModel):
    contract_version: Literal[1] = 1
    task_id: UUID
    attempt_id: UUID
    author_run_id: str = Field(min_length=1, max_length=512)
    proposal: MentionExecutionOutput
    vocabulary: tuple[BeadTypeDefinition, ...] = Field(min_length=1, max_length=32)
    evidence: tuple[SourceEvidenceRead, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounds(self) -> ClassificationPacket:
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 262144:
            raise ValueError(
                "classification packet exceeds 262144 UTF-8 bytes; do not truncate"
            )
        return self


class ClassificationEvidenceReference(FrozenContractModel):
    selection: ReadSourceEvidence
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ClassificationContribution(FrozenContractModel):
    """One accepted contribution; request_id is its stable opaque identity."""

    contract_version: Literal[1] = 1
    request_id: UUID
    model_run_ref: str = Field(min_length=1, max_length=512)
    author_run_ref: str = Field(min_length=1, max_length=512)
    packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    vocabulary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: tuple[ClassificationEvidenceReference, ...] = Field(
        min_length=1, max_length=8
    )
    decision: ClassificationDecision

    @model_validator(mode="after")
    def accepted_only(self) -> ClassificationContribution:
        if (
            self.decision.outcome != "selected"
            or self.decision.proposal_alignment != "consistent"
        ):
            raise ValueError("an abstention is not an accepted contribution")
        if self.model_run_ref == self.author_run_ref:
            raise ValueError(
                "specialist and primary must have distinct attributable runs"
            )
        return self


class ClassifiedExecutionOutput(MentionExecutionOutput):
    classification: ClassificationContribution

    @model_validator(mode="after")
    def same_authored_bundle(self) -> ClassifiedExecutionOutput:
        proposal = MentionExecutionOutput(annotations=self.annotations)
        bead = self.annotations[0]
        selected = self.classification.decision.selected_type
        if selected != BeadTypePin(
            key=bead.bead_type_key, revision=bead.bead_type_revision
        ):
            raise ValueError(
                "classification disagreement requires authored reconsideration or non-acceptance"
            )
        if digest(proposal) != self.classification.proposal_sha256:
            raise ValueError(
                "classification must bind the exact substantive author proposal"
            )
        return self


CLASSIFIER_PROFILE = ModelProfileReference(
    profile_key="classification.standard", revision=1, effort_key="standard"
)
CLASSIFIER_INPUT = TypedModelContract(
    contract_id="memory.semantic.bead-classification.packet",
    revision=1,
    model_type=ClassificationPacket,
)
CLASSIFIER_OUTPUT = TypedModelContract(
    contract_id="memory.semantic.bead-classification.decision",
    revision=1,
    model_type=ClassificationDecision,
)
CLASSIFIER_KEY = "memory.semantic.bead-type-classifier"
CLASSIFIER_AGENT = AgentContract(
    agent_key=CLASSIFIER_KEY,
    input_contract=CLASSIFIER_INPUT.reference,
    output_contract=CLASSIFIER_OUTPUT.reference,
    maximum_effort_key="standard",
)
CLASSIFICATION_TASK = replace(
    MENTION_EXECUTION_TASK,
    contract_revision=5,
    input_contract=TypedModelContract(
        contract_id="memory.semantic.classified-authorship.input",
        revision=1,
        model_type=ClassificationExecutionInput,
    ),
    output_contract=TypedModelContract(
        contract_id="memory.semantic.classified-authorship.output",
        revision=1,
        model_type=ClassifiedExecutionOutput,
    ),
    leaf_agent_key="memory.semantic.classified-author",
    allowed_delegate_keys=(CLASSIFIER_KEY,),
    run_budget=MENTION_EXECUTION_TASK.run_budget.model_copy(
        update={"max_delegate_calls": 1, "max_parallel_delegates": 1}
    ),
)


def load_classification_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    t = CLASSIFICATION_TASK
    return SemanticTaskRegistry._from_source_controlled(
        module_registry,
        (cast(SemanticTaskDefinition[BaseModel, BaseModel], t),),
        (
            AgentContract(
                agent_key=cast(str, t.leaf_agent_key),
                input_contract=t.input_contract.reference,
                output_contract=t.output_contract.reference,
                maximum_effort_key="standard",
            ),
            CLASSIFIER_AGENT,
        ),
        (),
        BUILTIN_EFFORT_PROFILES,
        (*BUILTIN_MODEL_PROFILE_REFERENCES, CLASSIFIER_PROFILE),
        (EvidencePolicyContract(policy_key="source-revisiting-v1"),),
        leaf_contracts=(
            cast(TypedModelContract[BaseModel], CLASSIFIER_INPUT),
            cast(TypedModelContract[BaseModel], CLASSIFIER_OUTPUT),
        ),
    )


class ActivateClassifiedAuthorship(ActivateSourceRevisiting):
    contract_version: Literal[4] = 4  # type: ignore[assignment]
    expected_schema_version: Literal[23] = 23  # type: ignore[assignment]
    classification_vocabulary: tuple[BeadTypePin, ...] = Field(
        min_length=1, max_length=32
    )


class ClassificationActivationReceipt(SourceRevisitingActivationReceipt):
    contract_version: Literal[4] = 4  # type: ignore[assignment]


class ApplyClassifiedAuthorship(ApplyCompleteInput):
    contract_version: Literal[6] = 6  # type: ignore[assignment]
    expected_schema_version: Literal[23] = 23  # type: ignore[assignment]
    contract_revision: Literal[5] = 5  # type: ignore[assignment]
    model_run_refs: tuple[str, ...] = Field(min_length=2, max_length=2)
    payload: ClassifiedExecutionOutput

    @model_validator(mode="after")
    def exact_result(self) -> ApplyClassifiedAuthorship:
        data = canonical_json_bytes(self.payload.model_dump(mode="json"))
        if (
            self.semantic_payload_canonical_json.encode() != data
            or hashlib.sha256(data).hexdigest() != self.semantic_result_hash
        ):
            raise ValueError("semantic result hash mismatch")
        if self.output_contract_hash != CLASSIFICATION_TASK.output_contract.schema_hash:
            raise ValueError("classification output contract mismatch")
        c = self.payload.classification
        if set(self.model_run_refs) != {c.author_run_ref, c.model_run_ref}:
            raise ValueError("contribution must bind this exact run tree")
        return self


@runtime_checkable
class ClassificationRecorder(Protocol):
    async def record_classification(
        self,
        *,
        request_id: UUID,
        request_payload_hash: str,
        packet: ClassificationPacket,
        decision: ClassificationDecision,
    ) -> None: ...
