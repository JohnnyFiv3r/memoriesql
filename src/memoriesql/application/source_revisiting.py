"""Opt-in, task-scoped source revisiting. No production model or trust policy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, Protocol, cast, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
)
from memoriesql.application.complete_input_execution import (
    COMPLETE_EXECUTION_TASK,
    ApplyCompleteInput,
    CompleteExecutionOutput,
    EvidenceExposureSlice,
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
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    EvidencePolicyContract,
    FrozenContractModel,
    SemanticTaskDefinition,
    SemanticTaskInput,
    TypedModelContract,
    canonical_json_bytes,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry

TARGET_CHARACTERS = 131072
DELIVERY_UNITS = 262144
DISPATCH_JSON_BYTES = 262144


class AuthorizedContextPin(FrozenContractModel):
    package: SealedPackagePin
    source_object_id: UUID
    source_schema_version: int = Field(ge=1)


class RevisitingPayload(FrozenContractModel):
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
    authorized_context: tuple[AuthorizedContextPin, ...] = Field(max_length=4)
    required_execution: Literal["trusted_source_revisiting_v1"] = (
        "trusted_source_revisiting_v1"
    )


class RevisitingExecutionInput(SemanticTaskInput[RevisitingPayload]):
    task_kind: Literal["memory.semantic.author-complete-unit"] = (
        "memory.semantic.author-complete-unit"
    )
    contract_revision: Literal[3] = 3
    requested_effort_key: None = None
    requested_budget: None = None
    expected_target_revision: Literal[0] = 0

    @model_validator(mode="after")
    def exact_pin(self) -> RevisitingExecutionInput:
        if self.payload.package.required_characters > TARGET_CHARACTERS:
            raise ValueError("source_revisiting_target_budget")
        refs = self.evidence_manifest.references
        if (
            len(refs) != 1
            or refs[0].reference_id != str(self.payload.source_unit_ids[0])
            or refs[0].content_hash != self.payload.package.inventory_sha256
            or refs[0].declared_characters != self.payload.package.required_characters
            or self.target_reference != refs[0].reference_id
        ):
            raise ValueError("revisiting requires the exact mandatory target")
        ids = [
            self.payload.package.package_id,
            *(c.package.package_id for c in self.payload.authorized_context),
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("context pins must be distinct from each other and target")
        return self


class ActivateSourceRevisiting(FrozenContractModel):
    contract_version: Literal[2] = 2
    expected_schema_version: Literal[20] = 20
    idempotency_key: str = Field(min_length=1, max_length=512)
    binding_task_id: UUID
    dispatch_policy_id: UUID
    authorized_context: tuple[AuthorizedContextPin, ...] = Field(
        default=(), max_length=4
    )


class SourceRevisitingActivationReceipt(FrozenContractModel):
    contract_version: Literal[2] = 2
    binding_task_id: UUID
    execution_task_id: UUID
    idempotency_receipt_id: UUID
    enqueue_receipt_id: UUID
    package: SealedPackagePin
    authorized_context: tuple[AuthorizedContextPin, ...]
    replayed: bool


class ReadSourceEvidence(FrozenContractModel):
    """Ordinal identifies retained storage, never an invented semantic unit."""

    package_id: UUID
    inventory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    part_ordinal: int = Field(ge=0, lt=256)
    representation: Literal["normalized", "raw"] = "normalized"
    lineage_ordinal: int | None = Field(default=None, ge=0, lt=16)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=16384, ge=1, le=32768)

    @model_validator(mode="after")
    def representation_bounds(self) -> ReadSourceEvidence:
        if self.representation == "normalized":
            if self.lineage_ordinal is not None or self.limit > 16384:
                raise ValueError("normalized offsets are characters, bounded to 16384")
        elif self.lineage_ordinal is None:
            raise ValueError("raw reads select exactly one declared derivation range")
        return self


class SourceEvidenceRead(FrozenContractModel):
    request: ReadSourceEvidence
    inventory: EvidenceInventoryEntry
    content: str | None
    bytes_hex: str | None
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    next_offset: int | None


class RevisitingWindow(FrozenContractModel):
    contract_version: Literal[2] = 2
    task_id: UUID
    attempt_id: UUID
    package: SealedPackagePin
    slices: tuple[EvidenceExposureSlice, ...] = Field(min_length=1, max_length=256)
    final: bool

    @model_validator(mode="after")
    def bounded(self) -> RevisitingWindow:
        if (
            sum(len(s.content) for s in self.slices) > 65536
            or len(canonical_json_bytes(self.model_dump(mode="json"))) > 131072
        ):
            raise ValueError("revisiting context exceeds qualified dispatch bound")
        return self


class SourceDelivery(FrozenContractModel):
    contract_version: Literal[2] = 2
    task_id: UUID
    attempt_id: UUID
    package: SealedPackagePin
    window: RevisitingWindow | None = None
    read: SourceEvidenceRead | None = None

    @model_validator(mode="after")
    def bounded(self) -> SourceDelivery:
        if self.window is not None and self.read is not None:
            raise ValueError("one delivery selection per dispatch")
        if self.window is not None and (
            self.window.task_id != self.task_id
            or self.window.attempt_id != self.attempt_id
            or self.window.package != self.package
        ):
            raise ValueError("delivery must preserve window pins")
        if (
            len(canonical_json_bytes(self.model_dump(mode="json")))
            > DISPATCH_JSON_BYTES + 2048
        ):
            raise ValueError("source delivery bound")
        return self

    @property
    def delivered_units(self) -> int:
        if self.window is not None:
            return sum(len(s.content) for s in self.window.slices)
        if self.read is not None:
            return (
                len(self.read.content)
                if self.read.content is not None
                else len(bytes.fromhex(self.read.bytes_hex or ""))
            )
        return 0


class SourceAuthorStep(FrozenContractModel):
    """Single author's typed control output; notes have no canonical authority."""

    action: Literal["next", "read", "continue", "finish", "incomplete"]
    read: ReadSourceEvidence | None = None
    notes: str = Field(default="", max_length=4096)
    typed_output: CompleteExecutionOutput | None = None
    used_evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def selected_action(self) -> SourceAuthorStep:
        if (self.action == "read") != (self.read is not None):
            raise ValueError("read action requires exactly its typed selection")
        if (self.action == "finish") != (self.typed_output is not None):
            raise ValueError("only finish carries semantic output")
        if self.action != "finish" and self.used_evidence_refs:
            raise ValueError("only finish carries used evidence references")
        return self


class SourceRevisitingAccess(Protocol):
    def revisiting_windows(self) -> AsyncIterator[RevisitingWindow]: ...
    async def revisit(self, request: ReadSourceEvidence) -> SourceEvidenceRead: ...
    async def authorize_delivery(self, delivery: SourceDelivery) -> None: ...


@runtime_checkable
class SourceDeliveryRecorder(Protocol):
    async def record_delivery(
        self, *, request_id: UUID, request_payload_hash: str, delivery: SourceDelivery
    ) -> None: ...


SOURCE_REVISITING_TASK = SemanticTaskDefinition(
    task_kind=COMPLETE_EXECUTION_TASK.task_kind,
    contract_revision=3,
    owning_module=COMPLETE_EXECUTION_TASK.owning_module,
    input_contract=TypedModelContract(
        contract_id="memory.semantic.source-revisiting.input",
        revision=1,
        model_type=RevisitingExecutionInput,
    ),
    output_contract=COMPLETE_EXECUTION_TASK.output_contract,
    dispatch_mode=COMPLETE_EXECUTION_TASK.dispatch_mode,
    default_effort_key="standard",
    maximum_effort_key="standard",
    model_profile=COMPLETE_EXECUTION_TASK.model_profile,
    evidence_policy_key="source-revisiting-v1",
    leaf_agent_key="memory.semantic.source-revisiting-author",
    run_budget=COMPLETE_EXECUTION_TASK.run_budget.model_copy(
        update={"request_limit": 12}
    ),
)


def load_source_revisiting_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    task = SOURCE_REVISITING_TASK
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


# Preserve the existing canonical output model/hash; only application routing is new.
class ApplySourceRevisiting(ApplyCompleteInput):
    contract_version: Literal[4] = 4  # type: ignore[assignment]
    expected_schema_version: Literal[20] = 20  # type: ignore[assignment]
    contract_revision: Literal[3] = 3  # type: ignore[assignment]
