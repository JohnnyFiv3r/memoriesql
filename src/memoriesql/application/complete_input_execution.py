"""Explicit schema-18 execution of a schema-17 pin; no provider composition."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Final, Literal, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
)
from memoriesql.application.evidence_packages import (
    EvidenceInventoryEntry,
    PackageDeclaration,
)
from memoriesql.application.logical_unit_materialization import (
    LogicalEventDeclaration,
    SealedPackagePin,
)
from memoriesql.application.module_registry import BuiltInModuleRegistry
from memoriesql.application.observation_commands import (
    InitialBeadDraft,
    InitialObservationsPayload,
)
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    DispatchMode,
    EvidencePolicyContract,
    FrozenContractModel,
    ModelProfileReference,
    RunBudget,
    SemanticResultStatus,
    SemanticTaskDefinition,
    SemanticTaskInput,
    TypedModelContract,
    canonical_json_bytes,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry

EXECUTION_KIND: Final = "memory.semantic.author-complete-unit"
READER_PART_LIMIT = 8
READER_RESPONSE_BYTES = 4_194_304
WINDOW_CHARACTERS = 16_384
WINDOW_JSON_BYTES = 131_072
CHECKPOINT_CHARACTERS = 4_096


class CompleteExecutionPayload(FrozenContractModel):
    binding_task_id: UUID
    source_object_id: UUID
    event_id: UUID
    source_unit_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1)
    bead_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1)
    package: SealedPackagePin
    producer_policy_id: UUID
    dispatch_policy_id: UUID
    declaration: PackageDeclaration
    event_declaration: LogicalEventDeclaration
    parent_source_unit_id: UUID | None
    parent_resolution: Literal["canonical", "native_identity_only", "unknown"]
    required_execution: Literal["trusted_complete_input_exposure_v1"] = (
        "trusted_complete_input_exposure_v1"
    )


class CompleteExecutionInput(SemanticTaskInput[CompleteExecutionPayload]):
    task_kind: Literal["memory.semantic.author-complete-unit"] = EXECUTION_KIND
    contract_revision: Literal[2] = 2
    requested_effort_key: None = None
    requested_budget: None = None
    expected_target_revision: Literal[0] = 0

    @model_validator(mode="after")
    def exact_pin(self) -> CompleteExecutionInput:
        refs = self.evidence_manifest.references
        if (
            len(refs) != 1
            or refs[0].reference_id != str(self.payload.source_unit_ids[0])
            or refs[0].content_hash != self.payload.package.inventory_sha256
            or refs[0].declared_characters != self.payload.package.required_characters
            or self.target_reference != refs[0].reference_id
        ):
            raise ValueError("complete execution requires its exact unit and package")
        return self


class CompleteExecutionOutput(InitialObservationsPayload):
    annotations: tuple[InitialBeadDraft, ...] = Field(min_length=1, max_length=1)


class ActivateCompleteInput(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[18] = 18
    idempotency_key: str = Field(min_length=1, max_length=512)
    binding_task_id: UUID
    dispatch_policy_id: UUID


class CompleteInputActivationReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    binding_task_id: UUID
    execution_task_id: UUID
    idempotency_receipt_id: UUID
    enqueue_receipt_id: UUID
    package: SealedPackagePin
    replayed: bool


class ReadCompleteEvidence(FrozenContractModel):
    contract_version: Literal[2] = 2
    package_id: UUID
    inventory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    next_ordinal: int = Field(ge=0, lt=256)
    limit: int = Field(default=READER_PART_LIMIT, ge=1, le=READER_PART_LIMIT)


class CompleteEvidencePart(FrozenContractModel):
    inventory: EvidenceInventoryEntry
    content: str


class CompleteEvidenceBatch(FrozenContractModel):
    contract_version: Literal[2] = 2
    package_id: UUID
    inventory_sha256: str
    parts: tuple[CompleteEvidencePart, ...] = Field(max_length=READER_PART_LIMIT)
    next_ordinal: int | None


class EvidenceExposureSlice(FrozenContractModel):
    inventory: EvidenceInventoryEntry
    start_character: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=WINDOW_CHARACTERS)


class EvidenceExecutionWindow(FrozenContractModel):
    contract_version: Literal[1] = 1
    task_id: UUID
    attempt_id: UUID
    package: SealedPackagePin
    slices: tuple[EvidenceExposureSlice, ...] = Field(min_length=1, max_length=256)
    final: bool

    @model_validator(mode="after")
    def bounded(self) -> EvidenceExecutionWindow:
        if (
            sum(len(item.content) for item in self.slices) > WINDOW_CHARACTERS
            or len(canonical_json_bytes(self.model_dump(mode="json")))
            > WINDOW_JSON_BYTES
        ):
            raise ValueError("execution window exceeds the bounded dispatch contract")
        return self


class InspectionCheckpoint(FrozenContractModel):
    """Ephemeral model working notes. Never canonical output or exposure proof."""

    notes: str = Field(max_length=CHECKPOINT_CHARACTERS)


class CompleteInputAccess(Protocol):
    def windows(self) -> AsyncIterator[EvidenceExecutionWindow]: ...
    async def authorize_dispatch(self) -> None: ...


class EvidenceExposureRecorder(Protocol):
    """Trusted composition only; never exposed as a model tool or input field."""

    async def record_received(
        self,
        *,
        request_id: UUID,
        request_payload_hash: str,
        window: EvidenceExecutionWindow,
    ) -> None: ...


COMPLETE_EXECUTION_TASK = SemanticTaskDefinition(
    task_kind=EXECUTION_KIND,
    contract_revision=2,
    owning_module="memoriesql.kernel",
    input_contract=TypedModelContract(
        contract_id="memory.semantic.complete-input-execution.input",
        revision=1,
        model_type=CompleteExecutionInput,
    ),
    output_contract=TypedModelContract(
        contract_id="memory.semantic.complete-input-execution.output",
        revision=1,
        model_type=CompleteExecutionOutput,
    ),
    dispatch_mode=DispatchMode.DIRECT_LEAF,
    default_effort_key="standard",
    maximum_effort_key="standard",
    model_profile=ModelProfileReference(
        profile_key="quality-first.standard", revision=1, effort_key="standard"
    ),
    evidence_policy_key="complete-input-exposure-v1",
    # Eight bounded inspection windows. This ceiling is not disclosure approval
    # or a production model choice; explicit activation and composition are required.
    run_budget=RunBudget(
        wall_clock_seconds=300,
        request_limit=8,
        input_token_limit=524288,
        output_token_limit=32768,
        total_token_limit=557056,
        tool_call_limit=0,
        max_delegate_calls=0,
        max_parallel_delegates=0,
        evidence_item_limit=1,
        hydrated_character_limit=131072,
        output_retries=0,
        tool_retries=0,
    ),
    leaf_agent_key="memory.semantic.complete-unit-author",
)


def load_complete_input_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    task = COMPLETE_EXECUTION_TASK
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
        (EvidencePolicyContract(policy_key="complete-input-exposure-v1"),),
    )


class ApplyCompleteInput(FrozenContractModel):
    contract_version: Literal[3] = 3
    expected_schema_version: Literal[18] = 18
    idempotency_key: str = Field(min_length=1, max_length=512)
    tenant_id: UUID
    workspace_id: UUID
    access_scope_id: UUID
    task_id: UUID
    attempt_id: UUID
    lease_generation: int = Field(ge=1)
    task_kind: Literal["memory.semantic.author-complete-unit"] = EXECUTION_KIND
    contract_revision: Literal[2] = 2
    output_contract_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_result_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_payload_canonical_json: str = Field(min_length=2, max_length=12000)
    used_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=1)
    model_run_refs: tuple[str, ...] = Field(min_length=1, max_length=1)
    payload: CompleteExecutionOutput

    @model_validator(mode="after")
    def exact_result(self) -> ApplyCompleteInput:
        import hashlib

        data = canonical_json_bytes(self.payload.model_dump(mode="json"))
        if (
            self.semantic_payload_canonical_json.encode() != data
            or hashlib.sha256(data).hexdigest() != self.semantic_result_hash
        ):
            raise ValueError("semantic result hash mismatch")
        if (
            self.output_contract_hash
            != COMPLETE_EXECUTION_TASK.output_contract.schema_hash
        ):
            raise ValueError("complete input output contract mismatch")
        return self


class CompleteInputError(RuntimeError):
    def __init__(self, status: SemanticResultStatus, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
