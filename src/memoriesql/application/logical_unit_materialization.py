"""Canonical materialization only; complete-input execution is unavailable."""

from __future__ import annotations

from typing import Final, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.canonical_transactions import SourceType
from memoriesql.application.evidence_packages import NativeFacts, digest
from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    SemanticTaskInput,
    canonical_json_bytes,
)

MATERIALIZATION_COMMAND_MAX_BYTES = 16_384
MATERIALIZATION_METADATA_MAX_BYTES = 8_192
COMPLETE_UNIT_TASK_KIND: Final = "memory.semantic.author-complete-unit"
COMPLETE_UNIT_EXECUTION_UNAVAILABLE: Final = "complete_input_executor_unavailable"


class LogicalEventDeclaration(FrozenContractModel):
    """Producer-asserted genuine event identity, not a batching window."""

    event_key: str = Field(min_length=1, max_length=256)
    identity_basis: Literal["native", "producer_assigned"]
    source_type: SourceType
    native: NativeFacts
    expected_units: int | None = Field(default=None, ge=1, le=2_147_483_647)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.identity_basis == "native" and not self.native.native_id:
            raise ValueError("native event identity requires its native identifier")
        if (
            len(canonical_json_bytes(self.model_dump(mode="json")))
            > MATERIALIZATION_METADATA_MAX_BYTES
        ):
            raise ValueError("event declaration exceeds metadata bound")
        return self


class SealedPackagePin(FrozenContractModel):
    package_id: UUID
    sealed_receipt_id: UUID
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    required_parts: int = Field(ge=1, le=256)
    required_characters: int = Field(ge=1, le=16_777_216)
    required_utf8_bytes: int = Field(ge=1, le=16_777_216)
    coverage: Literal["entire_sealed_inventory"] = "entire_sealed_inventory"


class CompleteUnitPayload(FrozenContractModel):
    source_object_id: UUID
    event_id: UUID
    source_unit_id: UUID
    bead_id: UUID
    materialization_receipt_id: UUID
    producer_policy_id: UUID
    package: SealedPackagePin
    required_execution: Literal["trusted_complete_input_exposure_validation"] = (
        "trusted_complete_input_exposure_validation"
    )
    execution_availability: Literal["unavailable"] = "unavailable"


class CompleteUnitTaskInput(SemanticTaskInput[CompleteUnitPayload]):
    task_kind: Literal["memory.semantic.author-complete-unit"] = COMPLETE_UNIT_TASK_KIND
    contract_revision: Literal[1] = 1
    expected_target_revision: Literal[0] = 0
    requested_effort_key: None = None
    requested_budget: None = None

    @model_validator(mode="after")
    def bind_entire_inventory(self) -> Self:
        refs = self.evidence_manifest.references
        pin = self.payload.package
        if (
            len(refs) != 1
            or self.evidence_manifest.revision != 1
            or refs[0].reference_id != "evidence-package." + str(pin.package_id)
            or refs[0].content_hash != pin.inventory_sha256
            or refs[0].declared_characters != pin.required_characters
            or self.target_reference != str(self.payload.source_unit_id)
        ):
            raise ValueError("task input must bind the complete sealed package")
        return self


# This is an admission/binding identity, not an executable SemanticTaskDefinition.
# No agent, provider, output model, budgets or executable registry are fabricated.
COMPLETE_UNIT_BINDING_SPEC = {
    "task_kind": COMPLETE_UNIT_TASK_KIND,
    "contract_revision": 1,
    "input_schema": CompleteUnitTaskInput.model_json_schema(),
    "execution_available": False,
    "required_execution": "trusted_complete_input_exposure_validation",
}
COMPLETE_UNIT_TASK_CONTRACT_HASH = digest(
    canonical_json_bytes(COMPLETE_UNIT_BINDING_SPEC)
)
COMPLETE_UNIT_REGISTRY_HASH = (
    "complete-unit-bindings-v1:" + COMPLETE_UNIT_TASK_CONTRACT_HASH
)


class MaterializeLogicalUnit(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[17] = 17
    idempotency_key: str = Field(min_length=1, max_length=512)
    package_id: UUID
    expected_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    producer_policy_id: UUID
    expected_source_object_schema_version: int = Field(ge=1)
    event: LogicalEventDeclaration
    parent_source_unit_id: UUID | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (
            len(canonical_json_bytes(self.model_dump(mode="json")))
            > MATERIALIZATION_COMMAND_MAX_BYTES
        ):
            raise ValueError("materialization command exceeds byte bound")
        return self


class LogicalEventProgress(FrozenContractModel):
    event_id: UUID
    declared_units: int | None
    materialized_units: int
    remaining_declared_units: int | None
    inventory_state: Literal[
        "partial", "declared_inventory_materialized", "total_unknown"
    ]
    independently_proven_source_complete: Literal[False] = False


class LogicalUnitMaterializationReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    idempotency_receipt_id: UUID
    initial_materialization_receipt_id: UUID
    task_enqueue_receipt_id: UUID
    submitted_package_id: UUID
    bound_package: SealedPackagePin
    event_id: UUID
    source_unit_id: UUID
    bead_id: UUID
    task_id: UUID
    status: Literal["materialized", "already_exists"]
    replayed: bool
    execution_availability: Literal["unavailable"] = "unavailable"
    execution_reason: Literal["complete_input_executor_unavailable"] = (
        COMPLETE_UNIT_EXECUTION_UNAVAILABLE
    )
    # Frozen receipt-time accounting; inspect the event for current progress.
    event_progress_at_commit: LogicalEventProgress


class InspectLogicalEvent(FrozenContractModel):
    contract_version: Literal[1] = 1
    event_id: UUID
