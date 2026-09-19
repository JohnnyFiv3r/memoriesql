"""Forward authored-local mention contract; no canonical identity resolution."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
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
