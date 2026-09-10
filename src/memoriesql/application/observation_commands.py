"""Explicit, opt-in schema-15 commands; published v1 models remain unchanged."""

from __future__ import annotations

import hashlib
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.canonical_transactions import (
    CANONICAL_AUTHORING_EVIDENCE_JSON_BYTE_LIMIT,
    CANONICAL_AUTHORING_HYDRATED_CHARACTER_LIMIT,
    CANONICAL_AUTHORING_MAX_OBSERVATIONS,
    CANONICAL_AUTHORING_OUTPUT_JSON_BYTE_LIMIT,
    CANONICAL_CAPTURE_COMMAND_JSON_BYTE_LIMIT,
    ApplySemanticAnnotationsPayload,
    ApplySemanticAnnotationsReceipt,
    BeadAnnotationBundle,
    CanonicalSemanticAuthoringPayload,
    CanonicalSemanticTaskRequest,
    CaptureCheckpoint,
    SourceEventInput,
    SourceUnitInput,
    StatementKind,
    _validate_detail,
)
from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    canonical_json_bytes,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN


class AcceptSourceEventV2Command(FrozenContractModel):
    contract_version: Literal[2] = 2
    expected_schema_version: Literal[15] = 15
    tenant_id: UUID
    workspace_id: UUID
    access_scope_id: UUID
    source_object_id: UUID
    expected_source_object_schema_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=512)
    event: SourceEventInput
    units: tuple[SourceUnitInput, ...] = Field(min_length=1, max_length=256)
    checkpoint: CaptureCheckpoint | None = None
    semantic_task: CanonicalSemanticTaskRequest

    @model_validator(mode="after")
    def validate_event_units(self) -> AcceptSourceEventV2Command:
        ids = [unit.source_unit_id for unit in self.units]
        if len(ids) != len(set(ids)):
            raise ValueError("source_unit_id values must be unique")
        external_ids = [unit.external_unit_id for unit in self.units]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("external_unit_id values must be unique")
        known_ids = set(ids)
        for unit in self.units:
            if unit.parent_unit_id is not None and unit.parent_unit_id not in known_ids:
                raise ValueError("parent_unit_id must belong to the same event")
            _validate_detail(self.event.source_type, unit)
        observation_units = [unit for unit in self.units if unit.is_observation]
        if not observation_units:
            raise ValueError("at least one observation unit is required")
        if len(observation_units) > CANONICAL_AUTHORING_MAX_OBSERVATIONS:
            raise ValueError(
                "observation units exceed the canonical authoring output capacity"
            )
        if (
            sum(len(unit.content_text or "") for unit in observation_units)
            > CANONICAL_AUTHORING_HYDRATED_CHARACTER_LIMIT
        ):
            raise ValueError(
                "aggregate observation content exceeds the canonical authoring "
                "hydration limit"
            )
        if (
            sum(
                len(canonical_json_bytes(unit.content_text or "")) - 2
                for unit in observation_units
            )
            > CANONICAL_AUTHORING_EVIDENCE_JSON_BYTE_LIMIT
        ):
            raise ValueError(
                "escaped observation content exceeds the canonical authoring "
                "token envelope"
            )
        if (
            len(canonical_json_bytes(self.model_dump(mode="json", warnings="error")))
            > CANONICAL_CAPTURE_COMMAND_JSON_BYTE_LIMIT
        ):
            raise ValueError("capture command exceeds its canonical JSON byte limit")
        return self


class InitialBeadDraft(BeadAnnotationBundle):
    expected_bead_version: Literal[0] = 0

    @model_validator(mode="after")
    def reject_within_bead_corrections(self) -> Self:
        if any(s.statement_kind == StatementKind.CORRECTION for s in self.statements):
            raise ValueError("use bead supersession, not within-bead correction")
        return self


class AcceptedBeadPin(FrozenContractModel):
    bead_id: UUID
    bead_version_id: UUID


class InitialObservationsPayload(ApplySemanticAnnotationsPayload):
    annotations: tuple[InitialBeadDraft, ...] = Field(min_length=1, max_length=8)


class ObservationCorrectionPayload(ApplySemanticAnnotationsPayload):
    annotations: tuple[InitialBeadDraft, ...] = Field(min_length=1, max_length=1)
    supersedes: tuple[AcceptedBeadPin, ...] = Field(min_length=1, max_length=8)
    correction_reason: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        ids = [pin.bead_id for pin in self.supersedes]
        if len(ids) != len(set(ids)) or self.annotations[0].bead_id in ids:
            raise ValueError("supersession requires distinct prior beads")
        if not self.correction_reason.strip():
            raise ValueError("correction requires a reason")
        return self


class ObservationCorrectionInput(CanonicalSemanticAuthoringPayload):
    bead_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1)
    source_unit_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1)
    supersedes: tuple[AcceptedBeadPin, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        ids = [pin.bead_id for pin in self.supersedes]
        if len(ids) != len(set(ids)) or self.bead_ids[0] in ids:
            raise ValueError("supersession requires distinct prior beads")
        return self


class ObservationCommandV2[
    PayloadT: InitialObservationsPayload | ObservationCorrectionPayload
](FrozenContractModel):
    contract_version: Literal[2] = 2
    expected_schema_version: Literal[15] = 15
    idempotency_key: str = Field(min_length=1, max_length=512)
    tenant_id: UUID
    workspace_id: UUID
    access_scope_id: UUID
    task_id: UUID
    attempt_id: UUID
    lease_generation: int = Field(ge=1)
    task_kind: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    contract_revision: int = Field(ge=1)
    output_contract_hash: str = Field(pattern=SHA256_PATTERN)
    semantic_result_hash: str = Field(pattern=SHA256_PATTERN)
    semantic_payload_canonical_json: str = Field(
        min_length=2,
        max_length=CANONICAL_AUTHORING_OUTPUT_JSON_BYTE_LIMIT,
    )
    used_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=256)
    model_run_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    payload: PayloadT

    @model_validator(mode="after")
    def validate_result_references(self) -> Self:
        canonical_payload = canonical_json_bytes(
            self.payload.model_dump(mode="json", warnings="error")
        )
        if self.semantic_payload_canonical_json.encode("utf-8") != canonical_payload:
            raise ValueError(
                "semantic_payload_canonical_json must be the canonical payload"
            )
        if hashlib.sha256(canonical_payload).hexdigest() != self.semantic_result_hash:
            raise ValueError("semantic_result_hash must match the canonical payload")
        if len(self.used_evidence_refs) != len(set(self.used_evidence_refs)):
            raise ValueError("used evidence references must be unique")
        if len(self.model_run_refs) != len(set(self.model_run_refs)):
            raise ValueError("model run references must be unique")
        used_evidence = set(self.used_evidence_refs)
        model_runs = set(self.model_run_refs)
        for annotation in self.payload.annotations:
            for statement in annotation.statements:
                if statement.model_run_ref not in model_runs:
                    raise ValueError("statement run is outside the result run set")
                if not {
                    str(reference.source_unit_id) for reference in statement.evidence
                }.issubset(used_evidence):
                    raise ValueError(
                        "statement evidence is outside the used evidence set"
                    )
        return self


class AuthorInitialObservationsCommand(
    ObservationCommandV2[InitialObservationsPayload]
):
    task_kind: Literal["memory.semantic.author-observations"] = (
        "memory.semantic.author-observations"
    )
    contract_revision: Literal[2] = 2


class CorrectObservationCommand(ObservationCommandV2[ObservationCorrectionPayload]):
    task_kind: Literal["memory.semantic.correct-observation"] = (
        "memory.semantic.correct-observation"
    )
    contract_revision: Literal[2] = 2


class ObservationReceiptV2(ApplySemanticAnnotationsReceipt):
    contract_version: Literal[2] = 2
    operation: Literal[
        "initial_observations.apply.v2", "observation_correction.apply.v2"
    ]
    bead_ids: tuple[UUID, ...]
    supersedes: tuple[AcceptedBeadPin, ...]
