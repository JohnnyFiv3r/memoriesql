"""Pinned model-runtime adapters.

The production registries are intentionally empty. Domain-owned task families and
durable worker integrations land in later delivery lanes.
"""

from memoriesql.infrastructure.models.pydanticai_executor import (
    PYDANTIC_AI_SOURCE_COMMIT,
    PYDANTIC_AI_SOURCE_TAG,
    PYDANTIC_AI_VERSION,
    PYDANTIC_AI_WHEEL_SHA256,
    RUNTIME_ADAPTER_VERSION,
    AuthoredSemanticOutput,
    ConductorAgentSpec,
    DelegationOutcome,
    DelegationRequest,
    EvidenceToolResult,
    LeafAgentSpec,
    ModelProfileBinding,
    PydanticAIAgentRegistry,
    PydanticAIModelProfileRegistry,
    PydanticAISemanticExecutor,
    behavior_freeze_hash,
)

__all__ = (
    "PYDANTIC_AI_SOURCE_COMMIT",
    "PYDANTIC_AI_SOURCE_TAG",
    "PYDANTIC_AI_VERSION",
    "PYDANTIC_AI_WHEEL_SHA256",
    "RUNTIME_ADAPTER_VERSION",
    "AuthoredSemanticOutput",
    "ConductorAgentSpec",
    "DelegationOutcome",
    "DelegationRequest",
    "EvidenceToolResult",
    "LeafAgentSpec",
    "ModelProfileBinding",
    "PydanticAIAgentRegistry",
    "PydanticAIModelProfileRegistry",
    "PydanticAISemanticExecutor",
    "behavior_freeze_hash",
)
