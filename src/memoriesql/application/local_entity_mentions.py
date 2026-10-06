"""Forward authored-local mention contract; no canonical identity resolution."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from enum import StrEnum
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
)
from memoriesql.application.canonical_transactions import (
    AuthoredBeadRender,
    SemanticStatementDraft,
)
from memoriesql.application.complete_input_execution import (
    ApplyCompleteInput,
    CompleteExecutionOutput,
)
from memoriesql.application.module_registry import BuiltInModuleRegistry
from memoriesql.application.observation_commands import InitialBeadDraft
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
    SOURCE_REVISITING_TASK,
    ActivateSourceRevisiting,
    RevisitingExecutionInput,
    SourceAuthorStep,
    SourceRevisitingActivationReceipt,
)


class LocalEntityMention(FrozenContractModel):
    entity_mention_id: UUID
    surface_text: str = Field(min_length=1, max_length=1024)
    local_identity_state: Literal["unresolved", "ambiguous"]
    local_identity_reason: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def local_uncertainty(self) -> LocalEntityMention:
        if not self.surface_text.strip():
            raise ValueError("mention surface text must not be blank")
        if self.local_identity_reason is not None and not self.local_identity_reason.strip():
            raise ValueError("authored uncertainty reason must not be blank")
        if self.local_identity_state == "ambiguous" and self.local_identity_reason is None:
            raise ValueError("local ambiguity requires an authored reason")
        return self


class MentionBeadDraft(InitialBeadDraft):
    # Required even when empty; the accepted version's own receipt proves support.
    mentions: tuple[LocalEntityMention, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_mentions(self) -> MentionBeadDraft:
        if len({m.entity_mention_id for m in self.mentions}) != len(self.mentions):
            raise ValueError("local mention IDs must be unique within the authored bead")
        return self


class MentionExecutionOutput(CompleteExecutionOutput):
    """Whole canonical JSON output is limited to 12,000 UTF-8 bytes."""

    annotations: tuple[MentionBeadDraft, ...] = Field(min_length=1, max_length=1)


    @model_validator(mode="after")
    def aggregate_output_bound(self) -> MentionExecutionOutput:
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 12000:
            raise ValueError("local mention output exceeds 12000 canonical UTF-8 bytes")
        return self


class MentionExecutionInput(RevisitingExecutionInput):
    contract_revision: Literal[4] = 4  # type: ignore[assignment]


class MentionAuthorStep(SourceAuthorStep):
    typed_output: MentionExecutionOutput | None = None


class ActivateMentionAuthorship(ActivateSourceRevisiting):
    contract_version: Literal[3] = 3  # type: ignore[assignment]
    expected_schema_version: Literal[22] = 22  # type: ignore[assignment]


class MentionActivationReceipt(SourceRevisitingActivationReceipt):
    contract_version: Literal[3] = 3  # type: ignore[assignment]


MENTION_EXECUTION_TASK = replace(
    SOURCE_REVISITING_TASK,
    contract_revision=4,
    input_contract=TypedModelContract(
        contract_id="memory.semantic.local-mentions.input", revision=1,
        model_type=MentionExecutionInput,
    ),
    output_contract=TypedModelContract(
        contract_id="memory.semantic.local-mentions.output", revision=1,
        model_type=MentionExecutionOutput,
    ),
    leaf_agent_key="memory.semantic.local-mentions-author",
)


def load_local_mentions_task_registry(module_registry: BuiltInModuleRegistry) -> SemanticTaskRegistry:
    task = MENTION_EXECUTION_TASK
    return SemanticTaskRegistry._from_source_controlled(
        module_registry,
        (cast(SemanticTaskDefinition[BaseModel, BaseModel], task),),
        (AgentContract(
            agent_key=cast(str, task.leaf_agent_key),
            input_contract=task.input_contract.reference,
            output_contract=task.output_contract.reference,
            maximum_effort_key="standard",
        ),), (), BUILTIN_EFFORT_PROFILES, BUILTIN_MODEL_PROFILE_REFERENCES,
        (EvidencePolicyContract(policy_key="source-revisiting-v1"),),
    )


class ApplyLocalMentions(ApplyCompleteInput):
    contract_version: Literal[5] = 5  # type: ignore[assignment]
    expected_schema_version: Literal[22] = 22  # type: ignore[assignment]
    contract_revision: Literal[4] = 4  # type: ignore[assignment]
    payload: MentionExecutionOutput

    @model_validator(mode="after")
    def exact_result(self) -> ApplyLocalMentions:
        data = canonical_json_bytes(self.payload.model_dump(mode="json"))
        if (self.semantic_payload_canonical_json.encode() != data
            or hashlib.sha256(data).hexdigest() != self.semantic_result_hash):
            raise ValueError("semantic result hash mismatch")
        if self.output_contract_hash != MENTION_EXECUTION_TASK.output_contract.schema_hash:
            raise ValueError("local mentions output contract mismatch")
        return self


# Revision 7. Revision 4's response schema offers the statement kind
# `correction`, but no apply path accepts it for a new note, so an author that
# chose it lost the note. In revision 7 a correction supersedes an earlier
# statement of the same note, which keeps the prior meaning, and the render
# covers exactly the statements no correction supersedes. Inputs and render
# semantics are otherwise revision 4's.

CORRECTION_WITHIN_NOTE = (
    "A correction of something this note states supersedes that earlier "
    "statement: it names the statement in supersedes_statement_id and gives the "
    "reason in correction_reason. The superseded statement keeps the prior "
    "meaning."
)
EVERY_CURRENT_STATEMENT_RENDERED = (
    "Every statement that no correction supersedes appears in the note's "
    "rendered text or in its omissions, and a superseded statement appears in "
    "neither."
)


class AuthoredStatementKind(StrEnum):
    """The kinds a new note's statements may take."""

    OBSERVATION = "observation"
    CONTEXT = "context"
    QUALIFICATION = "qualification"
    CORRECTION = "correction"


class AuthoredStatementDraft(SemanticStatementDraft):
    statement_kind: AuthoredStatementKind = Field(  # type: ignore[assignment]
        description=CORRECTION_WITHIN_NOTE
    )

    @model_validator(mode="after")
    def nonblank_correction_reason(self) -> Self:
        if self.correction_reason is not None and not self.correction_reason.strip():
            raise ValueError("a correction reason must not be blank")
        return self


class AuthoredMentionBeadDraft(MentionBeadDraft):
    statements: tuple[AuthoredStatementDraft, ...] = Field(min_length=1, max_length=64)
    render: AuthoredBeadRender = Field(description=EVERY_CURRENT_STATEMENT_RENDERED)

    @model_validator(mode="after")
    def reject_within_bead_corrections(self) -> Self:
        # Replaces InitialBeadDraft's refusal: a correction supersedes an
        # earlier statement of this note, and a statement has at most one.
        earlier: set[UUID] = set()
        superseded: set[UUID] = set()
        for statement in self.statements:
            target = statement.supersedes_statement_id
            if target is not None:
                if target not in earlier:
                    raise ValueError(
                        "a correction supersedes an earlier statement of its note"
                    )
                if target in superseded:
                    raise ValueError("a statement has at most one correction")
                superseded.add(target)
            earlier.add(statement.statement_id)
        return self

    @model_validator(mode="after")
    def validate_initial_semantics(self) -> Self:
        # Replaces BeadAnnotationBundle's rule: the render classifies exactly
        # the statements no correction supersedes, as the database requires.
        ids = [statement.statement_id for statement in self.statements]
        if len(ids) != len(set(ids)):
            raise ValueError("statement IDs must be unique within a bead bundle")
        superseded = {
            statement.supersedes_statement_id
            for statement in self.statements
            if statement.supersedes_statement_id is not None
        }
        if self.render.classified_statement_ids != set(ids) - superseded:
            raise ValueError(
                "the render classifies exactly the statements no correction supersedes"
            )
        observations = [
            statement
            for statement in self.statements
            if statement.statement_kind == AuthoredStatementKind.OBSERVATION
        ]
        if not observations:
            raise ValueError("initial bead semantics require an observation")
        if any(
            self.source_unit_id
            not in {evidence.source_unit_id for evidence in statement.evidence}
            for statement in observations
        ):
            raise ValueError(
                "initial observations require evidence from their own source unit"
            )
        return self


class AuthoredMentionOutput(MentionExecutionOutput):
    """Whole canonical JSON output is limited to 12,000 UTF-8 bytes."""

    annotations: tuple[AuthoredMentionBeadDraft, ...] = Field(
        min_length=1, max_length=1
    )


class AuthoredMentionInput(RevisitingExecutionInput):
    contract_revision: Literal[7] = 7  # type: ignore[assignment]


class AuthoredMentionStep(SourceAuthorStep):
    typed_output: AuthoredMentionOutput | None = None


class ActivateAuthoredMentions(ActivateSourceRevisiting):
    contract_version: Literal[6] = 6  # type: ignore[assignment]
    expected_schema_version: Literal[41] = 41  # type: ignore[assignment]


class AuthoredMentionActivationReceipt(SourceRevisitingActivationReceipt):
    contract_version: Literal[6] = 6  # type: ignore[assignment]


AUTHORED_MENTION_TASK = replace(
    MENTION_EXECUTION_TASK,
    contract_revision=7,
    input_contract=TypedModelContract(
        contract_id="memory.semantic.local-mentions.input", revision=2,
        model_type=AuthoredMentionInput,
    ),
    output_contract=TypedModelContract(
        contract_id="memory.semantic.local-mentions.output", revision=2,
        model_type=AuthoredMentionOutput,
    ),
    leaf_agent_key="memory.semantic.local-mentions-author.v2",
)


def load_authored_mentions_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    task = AUTHORED_MENTION_TASK
    return SemanticTaskRegistry._from_source_controlled(
        module_registry,
        (cast(SemanticTaskDefinition[BaseModel, BaseModel], task),),
        (AgentContract(
            agent_key=cast(str, task.leaf_agent_key),
            input_contract=task.input_contract.reference,
            output_contract=task.output_contract.reference,
            maximum_effort_key="standard",
        ),), (), BUILTIN_EFFORT_PROFILES, BUILTIN_MODEL_PROFILE_REFERENCES,
        (EvidencePolicyContract(policy_key="source-revisiting-v1"),),
    )


class ApplyAuthoredMentions(ApplyCompleteInput):
    contract_version: Literal[8] = 8  # type: ignore[assignment]
    expected_schema_version: Literal[41] = 41  # type: ignore[assignment]
    contract_revision: Literal[7] = 7  # type: ignore[assignment]
    payload: AuthoredMentionOutput

    @model_validator(mode="after")
    def exact_result(self) -> ApplyAuthoredMentions:
        data = canonical_json_bytes(self.payload.model_dump(mode="json"))
        if (self.semantic_payload_canonical_json.encode() != data
            or hashlib.sha256(data).hexdigest() != self.semantic_result_hash):
            raise ValueError("semantic result hash mismatch")
        if self.output_contract_hash != AUTHORED_MENTION_TASK.output_contract.schema_hash:
            raise ValueError("authored mentions output contract mismatch")
        return self
