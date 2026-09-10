"""Opt-in immutable observation tasks; legacy registry identity is unchanged."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_EVIDENCE_POLICIES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
    CANONICAL_AUTHORING_TASK,
)
from memoriesql.application.canonical_transactions import (
    CanonicalSemanticAuthoringPayload,
)
from memoriesql.application.module_registry import BuiltInModuleRegistry
from memoriesql.application.observation_commands import (
    InitialObservationsPayload,
    ObservationCorrectionInput,
    ObservationCorrectionPayload,
)
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    SemanticTaskDefinition,
    SemanticTaskInput,
    TypedModelContract,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry


def _definition[InputT: BaseModel, OutputT: BaseModel](
    kind: str, agent: str, payload: type[InputT], output: type[OutputT]
) -> SemanticTaskDefinition[InputT, OutputT]:
    return SemanticTaskDefinition(
        task_kind=kind,
        contract_revision=2,
        owning_module=CANONICAL_AUTHORING_TASK.owning_module,
        input_contract=TypedModelContract(
            contract_id=kind + ".input",
            revision=2,
            model_type=SemanticTaskInput[payload],  # type: ignore[valid-type]
        ),
        output_contract=TypedModelContract(
            contract_id=kind + ".output",
            revision=2,
            model_type=output,
        ),
        dispatch_mode=CANONICAL_AUTHORING_TASK.dispatch_mode,
        default_effort_key=CANONICAL_AUTHORING_TASK.default_effort_key,
        maximum_effort_key=CANONICAL_AUTHORING_TASK.maximum_effort_key,
        model_profile=CANONICAL_AUTHORING_TASK.model_profile,
        evidence_policy_key=CANONICAL_AUTHORING_TASK.evidence_policy_key,
        run_budget=CANONICAL_AUTHORING_TASK.run_budget,
        leaf_agent_key=agent,
    )


INITIAL_OBSERVATIONS_TASK = _definition(
    "memory.semantic.author-observations",
    "memory.semantic.initial-observation-author",
    CanonicalSemanticAuthoringPayload,
    InitialObservationsPayload,
)
OBSERVATION_CORRECTION_TASK = _definition(
    "memory.semantic.correct-observation",
    "memory.semantic.observation-corrector",
    ObservationCorrectionInput,
    ObservationCorrectionPayload,
)


def load_observation_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    definitions = tuple(
        cast(SemanticTaskDefinition[BaseModel, BaseModel], item)
        for item in (INITIAL_OBSERVATIONS_TASK, OBSERVATION_CORRECTION_TASK)
    )
    agents = tuple(
        AgentContract(
            agent_key=cast(str, item.leaf_agent_key),
            input_contract=item.input_contract.reference,
            output_contract=item.output_contract.reference,
            maximum_effort_key=item.maximum_effort_key,
        )
        for item in definitions
    )
    return SemanticTaskRegistry._from_source_controlled(
        module_registry,
        definitions,
        agents,
        (),
        BUILTIN_EFFORT_PROFILES,
        BUILTIN_MODEL_PROFILE_REFERENCES,
        BUILTIN_EVIDENCE_POLICIES,
    )
