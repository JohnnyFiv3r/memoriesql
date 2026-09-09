from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    canonical_json_bytes,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

CANONICAL_TRANSACTION_CONTRACT_VERSION = 1
CANONICAL_AUTHORING_MAX_OBSERVATIONS = 8
CANONICAL_AUTHORING_HYDRATED_CHARACTER_LIMIT = 4_096
CANONICAL_AUTHORING_EVIDENCE_JSON_BYTE_LIMIT = 4_096
CANONICAL_AUTHORING_OUTPUT_JSON_BYTE_LIMIT = 12_000
CANONICAL_CAPTURE_COMMAND_JSON_BYTE_LIMIT = 1_048_576


class SourceType(StrEnum):
    TRANSCRIPT = "transcript"
    DOCUMENT = "document"
    MEDIA = "media"
    RELATIONAL = "relational"
    OPERATIONAL = "operational"


class SourceTimePrecision(StrEnum):
    INSTANT = "instant"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    INTERVAL = "interval"
    UNKNOWN = "unknown"


class CanonicalAttribute(FrozenContractModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: str = Field(max_length=512)


class ConversationTurnDetail(FrozenContractModel):
    detail_kind: Literal["conversation_turn"] = "conversation_turn"
    conversation_id: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=512)
    branch_id: str | None = Field(default=None, max_length=512)
    turn_id: str = Field(min_length=1, max_length=512)
    participant_id: str | None = Field(default=None, max_length=512)
    participant_role: str = Field(min_length=1, max_length=128)


class DocumentSegmentDetail(FrozenContractModel):
    detail_kind: Literal["document_segment"] = "document_segment"
    page_number: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    section_path: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_character_interval(self) -> DocumentSegmentDetail:
        if self.char_end is not None and self.char_start is None:
            raise ValueError("char_end requires char_start")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("char_end cannot precede char_start")
        return self


class MediaSegmentDetail(FrozenContractModel):
    detail_kind: Literal["media_segment"] = "media_segment"
    track_id: str | None = Field(default=None, max_length=512)
    speaker_ref: str | None = Field(default=None, max_length=512)
    transcript_language: str | None = Field(default=None, max_length=64)


class RecordEventDetail(FrozenContractModel):
    detail_kind: Literal["record_event"] = "record_event"
    record_system: str = Field(min_length=1, max_length=128)
    record_object_type: str = Field(min_length=1, max_length=128)
    record_object_key: str = Field(min_length=1, max_length=512)
    record_action: str = Field(min_length=1, max_length=128)
    effective_at: datetime | None = None
    changed_fields: tuple[CanonicalAttribute, ...] = ()


SourceUnitDetail = (
    ConversationTurnDetail
    | DocumentSegmentDetail
    | MediaSegmentDetail
    | RecordEventDetail
)


class DocumentRevision(FrozenContractModel):
    document_version: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=255)
    source_title: str | None = Field(default=None, max_length=1024)


class SourceEventInput(FrozenContractModel):
    event_id: UUID
    source_type: SourceType
    source_system: str = Field(min_length=1, max_length=128)
    installation_id: str | None = Field(default=None, max_length=512)
    external_event_id: str | None = Field(default=None, max_length=512)
    external_id_scope: str = Field(min_length=1, max_length=512)
    source_identity_key: str = Field(min_length=1, max_length=512)
    session_id: str | None = Field(default=None, max_length=512)
    actor_id: str = Field(min_length=1, max_length=512)
    actor_kind: str = Field(min_length=1, max_length=128)
    source_occurred_at: datetime | None = None
    source_occurred_end_at: datetime | None = None
    source_occurred_at_raw: str | None = Field(default=None, max_length=512)
    source_timezone: str | None = Field(default=None, max_length=128)
    source_time_precision: SourceTimePrecision | None = None
    source_sequence: int | None = Field(default=None, ge=0)
    source_revision_key: str | None = Field(default=None, max_length=512)
    parser_contract_version: str = Field(min_length=1, max_length=128)
    observation_unit_policy_version: str = Field(min_length=1, max_length=128)
    captured_at: datetime
    source_ref: str = Field(min_length=1, max_length=1024)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    attributes: tuple[CanonicalAttribute, ...] = ()
    document_revision: DocumentRevision | None = None

    @model_validator(mode="after")
    def validate_source_shape(self) -> SourceEventInput:
        if (
            self.source_occurred_at is not None
            and self.source_occurred_end_at is not None
            and self.source_occurred_end_at < self.source_occurred_at
        ):
            raise ValueError("source occurrence end cannot precede its start")
        if (self.source_type == SourceType.DOCUMENT) != (
            self.document_revision is not None
        ):
            raise ValueError("document_revision is required only for document events")
        _reject_duplicate_attributes(self.attributes)
        return self


class SourceUnitInput(FrozenContractModel):
    source_unit_id: UUID
    parent_unit_id: UUID | None = None
    unit_kind: Literal[
        "turn", "section", "chunk", "element", "media_window", "record_change"
    ]
    is_observation: bool
    external_unit_id: str = Field(min_length=1, max_length=512)
    unit_ordinal: int = Field(ge=0)
    unit_source_occurred_at: datetime | None = None
    unit_source_occurred_end_at: datetime | None = None
    unit_time_precision: SourceTimePrecision | None = None
    time_start_seconds: float | None = Field(default=None, ge=0)
    time_end_seconds: float | None = Field(default=None, ge=0)
    content_text: str | None = None
    content_hash: str = Field(pattern=SHA256_PATTERN)
    hydration_ref: str | None = Field(default=None, max_length=1024)
    schema_version: int = Field(ge=1)
    structure: tuple[CanonicalAttribute, ...] = ()
    detail: SourceUnitDetail

    @model_validator(mode="after")
    def validate_unit_shape(self) -> SourceUnitInput:
        if self.parent_unit_id == self.source_unit_id:
            raise ValueError("a source unit cannot parent itself")
        if self.is_observation and self.unit_kind == "element":
            raise ValueError("element units cannot be observations")
        if (
            self.unit_source_occurred_at is not None
            and self.unit_source_occurred_end_at is not None
            and self.unit_source_occurred_end_at < self.unit_source_occurred_at
        ):
            raise ValueError("unit occurrence end cannot precede its start")
        if self.time_end_seconds is not None and self.time_start_seconds is None:
            raise ValueError("time_end_seconds requires time_start_seconds")
        if (
            self.time_start_seconds is not None
            and self.time_end_seconds is not None
            and self.time_end_seconds < self.time_start_seconds
        ):
            raise ValueError("time_end_seconds cannot precede time_start_seconds")
        if not self.content_text and not self.hydration_ref:
            raise ValueError("non-empty content_text or hydration_ref is required")
        if self.is_observation and not self.content_text:
            raise ValueError("observation units require non-empty content_text")
        if self.content_text is not None:
            actual_hash = hashlib.sha256(self.content_text.encode("utf-8")).hexdigest()
            if actual_hash != self.content_hash:
                raise ValueError("content_hash must match content_text")
        _reject_duplicate_attributes(self.structure)
        return self


class CaptureCheckpoint(FrozenContractModel):
    checkpoint_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    expected_sequence: int | None = Field(default=None, ge=0)
    next_sequence: int = Field(ge=1)
    checkpoint_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_progress(self) -> CaptureCheckpoint:
        if (
            self.expected_sequence is not None
            and self.next_sequence <= self.expected_sequence
        ):
            raise ValueError("checkpoint next_sequence must advance")
        return self


class CanonicalSemanticTaskRequest(FrozenContractModel):
    task_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=512)


class AcceptSourceEventCommand(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[11] = 11
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
    def validate_event_units(self) -> AcceptSourceEventCommand:
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


class StatementKind(StrEnum):
    OBSERVATION = "observation"
    CONTEXT = "context"
    QUALIFICATION = "qualification"
    CORRECTION = "correction"


class StatementEvidence(FrozenContractModel):
    source_unit_id: UUID
    content_hash: str = Field(pattern=SHA256_PATTERN)


class SemanticStatementDraft(FrozenContractModel):
    statement_id: UUID
    statement_kind: StatementKind
    statement_text: str = Field(min_length=1, max_length=8192)
    evidence: tuple[StatementEvidence, ...] = Field(min_length=1, max_length=64)
    context_source_ids: tuple[UUID, ...] = ()
    model_run_ref: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    supersedes_statement_id: UUID | None = None
    correction_reason: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_statement_lifecycle(self) -> SemanticStatementDraft:
        if len(self.evidence) != len({item.source_unit_id for item in self.evidence}):
            raise ValueError("statement evidence references must be unique")
        if len(self.context_source_ids) != len(set(self.context_source_ids)):
            raise ValueError("context source references must be unique")
        if self.statement_kind == StatementKind.CORRECTION:
            if self.supersedes_statement_id is None or self.correction_reason is None:
                raise ValueError("correction requires supersession target and reason")
        elif (
            self.supersedes_statement_id is not None
            or self.correction_reason is not None
        ):
            raise ValueError("only correction may carry supersession metadata")
        return self


class AuthoredRenderClause(FrozenContractModel):
    text: str = Field(min_length=1, max_length=2048)
    statement_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_statement_ids(self) -> AuthoredRenderClause:
        if not self.text.strip():
            raise ValueError("render clause text must contain non-whitespace text")
        if len(self.statement_ids) != len(set(self.statement_ids)):
            raise ValueError("render clause statement IDs must be unique")
        return self


class AuthoredRenderOmission(FrozenContractModel):
    statement_id: UUID
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_reason(self) -> AuthoredRenderOmission:
        if not self.reason.strip():
            raise ValueError("render omission reason must contain non-whitespace text")
        return self


class AuthoredBeadRender(FrozenContractModel):
    title: AuthoredRenderClause
    summary: tuple[AuthoredRenderClause, ...] = Field(min_length=1, max_length=3)
    detail: tuple[AuthoredRenderClause, ...] = Field(default=(), max_length=8)
    omissions: tuple[AuthoredRenderOmission, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_coverage_shape(self) -> AuthoredBeadRender:
        if len(self.title.text) > 240:
            raise ValueError("render title exceeds 240 characters")
        if len(self.summary_text) > 2048:
            raise ValueError("render summary exceeds 2048 characters")
        detail_text = self.detail_text
        if detail_text is not None and len(detail_text) > 8192:
            raise ValueError("render detail exceeds 8192 characters")
        omitted_ids = [item.statement_id for item in self.omissions]
        if len(omitted_ids) != len(set(omitted_ids)):
            raise ValueError("render omission statement IDs must be unique")
        if set(omitted_ids) & self.included_statement_ids:
            raise ValueError("render statements cannot be included and omitted")
        return self

    @property
    def title_text(self) -> str:
        return self.title.text

    @property
    def summary_text(self) -> str:
        return " ".join(clause.text for clause in self.summary)

    @property
    def detail_text(self) -> str | None:
        if not self.detail:
            return None
        return "\n\n".join(clause.text for clause in self.detail)

    @property
    def included_statement_ids(self) -> set[UUID]:
        clauses = (self.title, *self.summary, *self.detail)
        return {
            statement_id
            for clause in clauses
            for statement_id in clause.statement_ids
        }

    @property
    def classified_statement_ids(self) -> set[UUID]:
        return self.included_statement_ids | {
            omission.statement_id for omission in self.omissions
        }


class BeadAnnotationBundle(FrozenContractModel):
    bead_id: UUID
    event_id: UUID
    source_unit_id: UUID
    bead_version_id: UUID
    expected_bead_version: int = Field(ge=0)
    bead_type_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    bead_type_revision: int = Field(ge=1)
    statements: tuple[SemanticStatementDraft, ...] = Field(min_length=1, max_length=64)
    render: AuthoredBeadRender

    @model_validator(mode="after")
    def validate_initial_semantics(self) -> BeadAnnotationBundle:
        statement_ids = [statement.statement_id for statement in self.statements]
        if len(statement_ids) != len(set(statement_ids)):
            raise ValueError("statement IDs must be unique within a bead bundle")
        statement_id_set = set(statement_ids)
        if not statement_id_set.issubset(self.render.classified_statement_ids):
            raise ValueError("every authored statement must be rendered or omitted")
        if (
            self.expected_bead_version == 0
            and self.render.classified_statement_ids != statement_id_set
        ):
            raise ValueError("initial render may reference only its authored statements")
        if self.expected_bead_version == 0:
            observations = [
                statement
                for statement in self.statements
                if statement.statement_kind == StatementKind.OBSERVATION
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


class CanonicalSemanticAuthoringPayload(FrozenContractModel):
    event_id: UUID
    bead_ids: tuple[UUID, ...] = Field(
        min_length=1, max_length=CANONICAL_AUTHORING_MAX_OBSERVATIONS
    )
    source_unit_ids: tuple[UUID, ...] = Field(
        min_length=1, max_length=CANONICAL_AUTHORING_MAX_OBSERVATIONS
    )

    @model_validator(mode="after")
    def validate_cardinality(self) -> CanonicalSemanticAuthoringPayload:
        if len(self.bead_ids) != len(self.source_unit_ids):
            raise ValueError("each observation unit requires one bead")
        if len(self.bead_ids) != len(set(self.bead_ids)):
            raise ValueError("bead IDs must be unique")
        if len(self.source_unit_ids) != len(set(self.source_unit_ids)):
            raise ValueError("source unit IDs must be unique")
        return self


class ApplySemanticAnnotationsPayload(FrozenContractModel):
    annotations: tuple[BeadAnnotationBundle, ...] = Field(
        min_length=1, max_length=CANONICAL_AUTHORING_MAX_OBSERVATIONS
    )

    @model_validator(mode="after")
    def reject_duplicate_beads(self) -> ApplySemanticAnnotationsPayload:
        bead_ids = [annotation.bead_id for annotation in self.annotations]
        if len(bead_ids) != len(set(bead_ids)):
            raise ValueError("each bead may appear once per semantic result")
        statement_ids = [
            statement.statement_id
            for annotation in self.annotations
            for statement in annotation.statements
        ]
        if len(statement_ids) != len(set(statement_ids)):
            raise ValueError("statement IDs must be unique")
        if (
            len(canonical_json_bytes(self.model_dump(mode="json", warnings="error")))
            > CANONICAL_AUTHORING_OUTPUT_JSON_BYTE_LIMIT
        ):
            raise ValueError(
                "semantic annotations exceed the canonical authoring output envelope"
            )
        return self


class ApplySemanticAnnotationsCommand(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[11] = 11
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
    payload: ApplySemanticAnnotationsPayload

    @model_validator(mode="after")
    def validate_result_references(self) -> ApplySemanticAnnotationsCommand:
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


class AcceptSourceEventReceipt(FrozenContractModel):
    event_id: UUID
    source_unit_ids: tuple[UUID, ...]
    bead_ids: tuple[UUID, ...]
    semantic_task_id: UUID
    idempotency_receipt_id: UUID
    checkpoint_sequence: int | None
    status: Literal["accepted", "already_exists"]
    replayed: bool


class ApplySemanticAnnotationsReceipt(FrozenContractModel):
    task_id: UUID
    attempt_id: UUID
    idempotency_receipt_id: UUID
    statement_ids: tuple[UUID, ...]
    bead_version_ids: tuple[UUID, ...]
    task_status: Literal["succeeded"]
    replayed: bool


def _reject_duplicate_attributes(attributes: tuple[CanonicalAttribute, ...]) -> None:
    keys = [attribute.key for attribute in attributes]
    if len(keys) != len(set(keys)):
        raise ValueError("attribute keys must be unique")


def _validate_detail(source_type: SourceType, unit: SourceUnitInput) -> None:
    detail = unit.detail
    if source_type == SourceType.TRANSCRIPT:
        valid = unit.unit_kind == "turn" and isinstance(detail, ConversationTurnDetail)
    elif source_type == SourceType.DOCUMENT:
        valid = unit.unit_kind in ("section", "chunk", "element") and isinstance(
            detail, DocumentSegmentDetail
        )
    elif source_type == SourceType.MEDIA:
        valid = unit.unit_kind in ("media_window", "element") and isinstance(
            detail, MediaSegmentDetail
        )
        if valid and (unit.time_start_seconds is None or unit.time_end_seconds is None):
            valid = False
    else:
        valid = unit.unit_kind == "record_change" and isinstance(
            detail, RecordEventDetail
        )
    if not valid:
        raise ValueError("source unit detail does not match the event source type")
