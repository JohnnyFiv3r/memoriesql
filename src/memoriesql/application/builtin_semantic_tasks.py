from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from memoriesql.application.canonical_transactions import (
    CANONICAL_AUTHORING_HYDRATED_CHARACTER_LIMIT,
    CANONICAL_AUTHORING_MAX_OBSERVATIONS,
    ApplySemanticAnnotationsPayload,
    CanonicalSemanticAuthoringPayload,
)
from memoriesql.application.module_registry import BuiltInModuleRegistry
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    DispatchMode,
    EffortProfile,
    EvidencePolicyContract,
    ModelProfileReference,
    RunBudget,
    SemanticTaskDefinition,
    SemanticTaskInput,
    ToolContract,
    TypedModelContract,
)
from memoriesql.application.semantic_task_registry import (
    KERNEL_SEMANTIC_TASK_OWNER,
    SemanticTaskRegistry,
)

CANONICAL_AUTHORING_INPUT_CONTRACT = TypedModelContract(
    contract_id="memory.semantic.author-observations.input",
    revision=1,
    model_type=SemanticTaskInput[CanonicalSemanticAuthoringPayload],
)
CANONICAL_AUTHORING_OUTPUT_CONTRACT = TypedModelContract(
    contract_id="memory.semantic.author-observations.output",
    revision=1,
    model_type=ApplySemanticAnnotationsPayload,
)

CANONICAL_AUTHORING_TASK = SemanticTaskDefinition(
    task_kind="memory.semantic.author-observations",
    contract_revision=1,
    owning_module=KERNEL_SEMANTIC_TASK_OWNER,
    input_contract=CANONICAL_AUTHORING_INPUT_CONTRACT,
    output_contract=CANONICAL_AUTHORING_OUTPUT_CONTRACT,
    dispatch_mode=DispatchMode.DIRECT_LEAF,
    default_effort_key="frontier",
    maximum_effort_key="frontier",
    model_profile=ModelProfileReference(
        profile_key="quality-first.frontier",
        revision=1,
        effort_key="frontier",
    ),
    evidence_policy_key="manifest-only",
    run_budget=RunBudget(
        wall_clock_seconds=300,
        request_limit=8,
        input_token_limit=32000,
        output_token_limit=10000,
        total_token_limit=42000,
        tool_call_limit=0,
        cost_safety_limit_microusd=None,
        max_delegate_calls=0,
        max_parallel_delegates=0,
        evidence_item_limit=CANONICAL_AUTHORING_MAX_OBSERVATIONS,
        hydrated_character_limit=CANONICAL_AUTHORING_HYDRATED_CHARACTER_LIMIT,
        output_retries=2,
        tool_retries=0,
    ),
    leaf_agent_key="memory.semantic.observation-author",
)

BUILTIN_SEMANTIC_TASKS: tuple[SemanticTaskDefinition[BaseModel, BaseModel], ...] = (
    cast(
        SemanticTaskDefinition[BaseModel, BaseModel],
        CANONICAL_AUTHORING_TASK,
    ),
)
BUILTIN_SEMANTIC_AGENTS: tuple[AgentContract, ...] = (
    AgentContract(
        agent_key="memory.semantic.observation-author",
        input_contract=CANONICAL_AUTHORING_INPUT_CONTRACT.reference,
        output_contract=CANONICAL_AUTHORING_OUTPUT_CONTRACT.reference,
        maximum_effort_key="frontier",
    ),
)
BUILTIN_SEMANTIC_TOOLS: tuple[ToolContract, ...] = ()

BUILTIN_EFFORT_PROFILES = (
    EffortProfile(key="economy", rank=0),
    EffortProfile(key="standard", rank=1),
    EffortProfile(key="frontier", rank=2),
)

BUILTIN_MODEL_PROFILE_REFERENCES = (
    ModelProfileReference(
        profile_key="quality-first.economy",
        revision=1,
        effort_key="economy",
    ),
    ModelProfileReference(
        profile_key="quality-first.standard",
        revision=1,
        effort_key="standard",
    ),
    ModelProfileReference(
        profile_key="quality-first.frontier",
        revision=1,
        effort_key="frontier",
    ),
)

BUILTIN_EVIDENCE_POLICIES = (EvidencePolicyContract(policy_key="manifest-only"),)


def load_builtin_semantic_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    """Resolve canonical tasks against caller-owned static composition."""

    return SemanticTaskRegistry._from_source_controlled(
        module_registry,
        BUILTIN_SEMANTIC_TASKS,
        BUILTIN_SEMANTIC_AGENTS,
        BUILTIN_SEMANTIC_TOOLS,
        BUILTIN_EFFORT_PROFILES,
        BUILTIN_MODEL_PROFILE_REFERENCES,
        BUILTIN_EVIDENCE_POLICIES,
    )
