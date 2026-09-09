from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.capture.contracts import (
    CAPTURE_ENVELOPE_MAX_BYTES,
    ConversationEventEnvelope,
    KernelCaptureBinding,
)
from memoriesql.application.capture.host_protocol import FileIdentity
from memoriesql.application.capture.source_adapter import (
    HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES,
    HOST_CAPTURE_MAX_PENDING_RANGES,
    HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS,
    AssembledCaptureEnvelopeBuilder,
    FinalizedScopeExclusion,
    FramedSourceRecord,
    HostCaptureCursorState,
    HostCaptureState,
    ScopeExclusionReason,
    SourceRecordAssembler,
    SourceRecordParseError,
    VerifiedAssemblySpan,
)
from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    canonical_json_bytes,
    canonical_sha256,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

TRANSCRIPT_FOLD_CONTRACT_VERSION = 1
TRANSCRIPT_FOLD_SCHEMA_VERSION = 14
TRANSCRIPT_FOLD_CURSOR_VERSION = 3
TRANSCRIPT_FOLD_MAX_READ_BYTES = 8_388_608
TRANSCRIPT_FOLD_MAX_MIGRATION_BYTES = 67_108_864
TRANSCRIPT_FOLD_LEGACY_MIGRATION_CHUNK_BYTES = 1_000_000
TRANSCRIPT_FOLD_MAX_OUTCOMES = 256
TRANSCRIPT_FOLD_MAX_LINEAGE_RANGES = 1_024
TRANSCRIPT_FOLD_COMMAND_MAX_BYTES = 16_777_216


class TranscriptFoldOutcomeKind(StrEnum):
    EXACT_TURN = "exact_turn"
    TRANSCRIPT_SPAN = "transcript_span"
    POLICY_DISPOSITION = "policy_disposition"


class TranscriptFoldRunStatus(StrEnum):
    FOLDED = "folded"
    END_OF_DURABLE_RANGES = "end_of_durable_ranges"
    WAITING_FOR_COMPLETE_RECORD = "waiting_for_complete_record"
    WAITING_FOR_EXACT_TURN = "waiting_for_exact_turn"
    DEGRADED = "degraded"


class DurableTranscriptReadStatus(StrEnum):
    DATA = "data"
    END_OF_DURABLE_RANGES = "end_of_durable_ranges"


class TranscriptFoldSourceRange(FrozenContractModel):
    """One exact retained-range slice contributing to a fold outcome."""

    source_range_receipt_id: UUID
    receipt_byte_start: int = Field(ge=0)
    receipt_byte_end_exclusive: int = Field(gt=0)
    receipt_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_slice(self) -> TranscriptFoldSourceRange:
        if self.receipt_byte_end_exclusive <= self.receipt_byte_start:
            raise ValueError("source-range receipt bounds must be non-empty")
        if not (
            self.receipt_byte_start
            <= self.byte_start
            < self.byte_end_exclusive
            <= self.receipt_byte_end_exclusive
        ):
            raise ValueError("fold lineage must remain inside its retained receipt")
        return self


class DurableTranscriptReadRequest(FrozenContractModel):
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity_key: str = Field(min_length=1, max_length=256)
    byte_start: int = Field(ge=0)
    max_bytes: int = Field(ge=1, le=TRANSCRIPT_FOLD_MAX_MIGRATION_BYTES)
    confirmation: Literal["confirm_local_transcript_fold"] = (
        "confirm_local_transcript_fold"
    )


class DurableTranscriptRead(FrozenContractModel):
    """Owner-confirmed bytes reconstructed only from immutable range receipts."""

    status: DurableTranscriptReadStatus
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity: FileIdentity | None = None
    file_identity_key: str = Field(min_length=1, max_length=256)
    connector_id: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    observed_connector_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    observed_source_format_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(ge=0)
    durable_end_offset: int = Field(ge=0)
    payload_hex: str = Field(default="", pattern=r"^(?:[a-f0-9]{2})*$", repr=False)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    ranges: tuple[TranscriptFoldSourceRange, ...] = Field(
        default=(), max_length=TRANSCRIPT_FOLD_MAX_LINEAGE_RANGES
    )
    first_captured_at: datetime | None = None

    @model_validator(mode="after")
    def validate_read(self) -> DurableTranscriptRead:
        payload = bytes.fromhex(self.payload_hex)
        if self.byte_end_exclusive < self.byte_start:
            raise ValueError("durable fold read byte range is invalid")
        if self.durable_end_offset < self.byte_end_exclusive:
            raise ValueError("durable fold read cannot exceed retained bytes")
        if len(payload) != self.byte_end_exclusive - self.byte_start:
            raise ValueError("durable fold read bytes must match its range")
        if hashlib.sha256(payload).hexdigest() != self.payload_sha256:
            raise ValueError("durable fold read hash must bind its exact bytes")
        if self.status == DurableTranscriptReadStatus.DATA:
            if not payload or self.file_identity is None or not self.ranges:
                raise ValueError("durable fold data requires bytes and lineage")
            if self.first_captured_at is None or self.first_captured_at.tzinfo is None:
                raise ValueError("durable fold data requires a timezone-aware receipt time")
            expected_offset = self.byte_start
            for source_range in self.ranges:
                if source_range.byte_start != expected_offset:
                    raise ValueError("durable fold lineage must be byte contiguous")
                expected_offset = source_range.byte_end_exclusive
            if expected_offset != self.byte_end_exclusive:
                raise ValueError("durable fold lineage must cover all returned bytes")
        else:
            if payload or self.ranges or self.byte_end_exclusive != self.byte_start:
                raise ValueError("end-of-range reads cannot claim source bytes")
        return self

    @property
    def payload(self) -> bytes:
        return bytes.fromhex(self.payload_hex)


class ExactTurnFoldEvidence(FrozenContractModel):
    """The unchanged canonical exact-turn result, retained without applying it."""

    envelope_canonical_json: str = Field(
        min_length=2,
        max_length=CAPTURE_ENVELOPE_MAX_BYTES,
        repr=False,
    )
    envelope_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_envelope(self) -> ExactTurnFoldEvidence:
        envelope = ConversationEventEnvelope.model_validate_json(
            self.envelope_canonical_json
        )
        if envelope.canonical_bytes.decode("utf-8") != self.envelope_canonical_json:
            raise ValueError("exact-turn fold result must remain canonical")
        if envelope.canonical_hash != self.envelope_sha256:
            raise ValueError("exact-turn fold hash must bind its canonical bytes")
        return self

    @property
    def envelope(self) -> ConversationEventEnvelope:
        return ConversationEventEnvelope.model_validate_json(
            self.envelope_canonical_json
        )


class TranscriptSpanEvidence(FrozenContractModel):
    """Minimal evidence record for complete records with unknown topology."""

    transcript_span_version: Literal[1] = 1
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity_key: str = Field(min_length=1, max_length=256)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(gt=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    topology_status: Literal["unknown"] = "unknown"
    content_storage: Literal["retained_source_ranges"] = "retained_source_ranges"

    @model_validator(mode="after")
    def validate_span(self) -> TranscriptSpanEvidence:
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("transcript span bytes must be non-empty")
        if self.end_record_index <= self.start_record_index:
            raise ValueError("transcript span records must be non-empty")
        return self


class TranscriptPolicyDisposition(FrozenContractModel):
    disposition_code: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    scope_reason: ScopeExclusionReason | None = None


class TranscriptFoldOutcome(FrozenContractModel):
    outcome_kind: TranscriptFoldOutcomeKind
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    lineage: tuple[TranscriptFoldSourceRange, ...] = Field(
        min_length=1,
        max_length=TRANSCRIPT_FOLD_MAX_LINEAGE_RANGES,
    )
    exact_turn: ExactTurnFoldEvidence | None = None
    transcript_span: TranscriptSpanEvidence | None = None
    policy_disposition: TranscriptPolicyDisposition | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> TranscriptFoldOutcome:
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("fold outcome bytes must be non-empty")
        if self.end_record_index < self.start_record_index:
            raise ValueError("fold outcome record range is invalid")
        details = (
            self.exact_turn,
            self.transcript_span,
            self.policy_disposition,
        )
        if sum(item is not None for item in details) != 1:
            raise ValueError("fold outcome requires exactly one typed result")
        expected_detail = {
            TranscriptFoldOutcomeKind.EXACT_TURN: self.exact_turn,
            TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN: self.transcript_span,
            TranscriptFoldOutcomeKind.POLICY_DISPOSITION: self.policy_disposition,
        }[self.outcome_kind]
        if expected_detail is None:
            raise ValueError("fold outcome kind must match its typed result")
        if self.outcome_kind != TranscriptFoldOutcomeKind.POLICY_DISPOSITION and (
            self.end_record_index <= self.start_record_index
        ):
            raise ValueError("exact turns and transcript spans require records")
        expected_offset = self.byte_start
        for source_range in self.lineage:
            if source_range.byte_start != expected_offset:
                raise ValueError("fold outcome lineage must be byte contiguous")
            expected_offset = source_range.byte_end_exclusive
        if expected_offset != self.byte_end_exclusive:
            raise ValueError("fold outcome lineage must cover its exact bytes")
        if self.transcript_span is not None:
            span = self.transcript_span
            if (
                span.byte_start,
                span.byte_end_exclusive,
                span.start_record_index,
                span.end_record_index,
                span.source_bytes_sha256,
            ) != (
                self.byte_start,
                self.byte_end_exclusive,
                self.start_record_index,
                self.end_record_index,
                self.source_bytes_sha256,
            ):
                raise ValueError("transcript span must bind its fold outcome")
        return self


class LegacyAdapterCursorBinding(FrozenContractModel):
    cursor_version: Literal[1, 2]
    cursor_sha256: str = Field(pattern=SHA256_PATTERN)
    acknowledged_byte_offset: int = Field(gt=0)
    acknowledged_record_index: int = Field(ge=0)
    checkpoint_sequence: int = Field(ge=0)
    last_receipt_event_id: UUID | None = None


class TranscriptFoldCheckpoint(FrozenContractModel):
    checkpoint_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    expected_sequence: int = Field(ge=0)
    next_sequence: int = Field(ge=1)
    checkpoint_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> TranscriptFoldCheckpoint:
        if self.next_sequence != self.expected_sequence + 1:
            raise ValueError("transcript fold checkpoint must advance exactly once")
        return self


class CommitTranscriptFoldCommand(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[14] = 14
    operation: Literal["commit_transcript_fold"] = "commit_transcript_fold"
    idempotency_key: str = Field(min_length=1, max_length=512)
    binding: KernelCaptureBinding
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    connector_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    adapter_profile_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    observed_source_format_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity: FileIdentity
    file_identity_key: str = Field(min_length=1, max_length=256)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=0)
    complete_record_count: int = Field(ge=0, le=1_000_000)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    outcomes_sha256: str = Field(pattern=SHA256_PATTERN)
    outcomes: tuple[TranscriptFoldOutcome, ...] = Field(
        min_length=1, max_length=TRANSCRIPT_FOLD_MAX_OUTCOMES
    )
    checkpoint: TranscriptFoldCheckpoint
    legacy_cursor: LegacyAdapterCursorBinding | None = None

    @model_validator(mode="after")
    def validate_fold(self) -> CommitTranscriptFoldCommand:
        if self.file_identity_key != self.file_identity.stable_key:
            raise ValueError("transcript fold file identity key must be mechanical")
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("transcript fold bytes must be non-empty")
        if self.end_record_index < self.start_record_index:
            raise ValueError("transcript fold record range is invalid")
        if self.complete_record_count != (
            self.end_record_index - self.start_record_index
        ):
            raise ValueError("transcript fold record count must match its range")
        expected_byte = self.byte_start
        expected_record = self.start_record_index
        for outcome in self.outcomes:
            if outcome.byte_start != expected_byte:
                raise ValueError("transcript fold outcomes must be byte contiguous")
            if outcome.start_record_index != expected_record:
                raise ValueError("transcript fold outcomes must be record contiguous")
            expected_byte = outcome.byte_end_exclusive
            expected_record = outcome.end_record_index
        if expected_byte != self.byte_end_exclusive:
            raise ValueError("transcript fold outcomes must cover its exact bytes")
        if expected_record != self.end_record_index:
            raise ValueError("transcript fold outcomes must cover every complete record")
        if any(
            outcome.outcome_kind == TranscriptFoldOutcomeKind.EXACT_TURN
            for outcome in self.outcomes
        ) and (
            self.adapter_profile_version is None
            or self.observed_source_format_version is None
        ):
            raise ValueError(
                "exact transcript folds require a qualified adapter profile"
            )
        rendered_outcomes = [
            item.model_dump(mode="json", warnings="error") for item in self.outcomes
        ]
        if canonical_sha256(rendered_outcomes) != self.outcomes_sha256:
            raise ValueError("transcript fold outcome hash must bind every outcome")
        expected_checkpoint_hash = _fold_checkpoint_sha256(
            checkpoint_key=self.checkpoint.checkpoint_key,
            expected_sequence=self.checkpoint.expected_sequence,
            next_sequence=self.checkpoint.next_sequence,
            source_object_id=self.binding.source_object_id,
            source_revision_key=self.source_revision_key,
            file_identity_key=self.file_identity_key,
            byte_start=self.byte_start,
            byte_end=self.byte_end_exclusive,
            start_record=self.start_record_index,
            end_record=self.end_record_index,
            source_bytes_sha256=self.source_bytes_sha256,
            outcomes_sha256=self.outcomes_sha256,
        )
        if self.checkpoint.checkpoint_hash != expected_checkpoint_hash:
            raise ValueError("transcript fold checkpoint must bind its exact result")
        if self.legacy_cursor is not None:
            if self.byte_start != 0:
                raise ValueError("legacy cursor migration must begin at byte zero")
            if (
                self.legacy_cursor.acknowledged_byte_offset < self.byte_end_exclusive
                or self.legacy_cursor.acknowledged_record_index
                < self.end_record_index
                or self.legacy_cursor.checkpoint_sequence
                != self.checkpoint.expected_sequence
            ):
                raise ValueError("legacy cursor migration must bind prior progress")
            if len(self.outcomes) != 1 or (
                self.outcomes[0].outcome_kind
                != TranscriptFoldOutcomeKind.POLICY_DISPOSITION
            ):
                raise ValueError("legacy cursor migration requires one disposition")
        if len(self.canonical_bytes) > TRANSCRIPT_FOLD_COMMAND_MAX_BYTES:
            raise ValueError("transcript fold command exceeds its canonical bound")
        return self

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json", warnings="error"))

    @property
    def canonical_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", warnings="error"))


class CommitTranscriptFoldReceipt(FrozenContractModel):
    transcript_fold_receipt_id: UUID
    idempotency_receipt_id: UUID
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity_key: str = Field(min_length=1, max_length=256)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=0)
    complete_record_count: int = Field(ge=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    outcomes_sha256: str = Field(pattern=SHA256_PATTERN)
    exact_turn_count: int = Field(ge=0)
    transcript_span_count: int = Field(ge=0)
    policy_disposition_count: int = Field(ge=0)
    checkpoint_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    checkpoint_sequence: int = Field(ge=1)
    authority_principal_id: UUID
    folded_at: datetime
    replayed: bool
    durable: Literal[True] = True

    @model_validator(mode="after")
    def validate_receipt(self) -> CommitTranscriptFoldReceipt:
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("transcript fold receipt bytes must be non-empty")
        if self.end_record_index - self.start_record_index != self.complete_record_count:
            raise ValueError("transcript fold receipt must bind its record count")
        if self.folded_at.tzinfo is None:
            raise ValueError("transcript fold receipt time must be timezone-aware")
        if (
            self.exact_turn_count
            + self.transcript_span_count
            + self.policy_disposition_count
            < 1
        ):
            raise ValueError("transcript fold receipt requires an outcome")
        return self


class DurableTranscriptFoldAcknowledgement(FrozenContractModel):
    transcript_fold_receipt_id: UUID
    idempotency_receipt_id: UUID
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    outcomes_sha256: str = Field(pattern=SHA256_PATTERN)
    checkpoint_sequence: int = Field(ge=1)
    authority_principal_id: UUID
    replayed: bool


class TranscriptFoldPendingRange(FrozenContractModel):
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(gt=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_pending(self) -> TranscriptFoldPendingRange:
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("pending fold bytes must be non-empty")
        if self.end_record_index <= self.start_record_index:
            raise ValueError("pending fold records must be non-empty")
        return self


class TranscriptFoldCursorState(FrozenContractModel):
    """Cursor-v3 replacement for the existing exact-turn adapter cursor."""

    cursor_version: Literal[3] = 3
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    revision: int = Field(default=0, ge=0)
    file_identity: FileIdentity | None = None
    acknowledged_offset: int = Field(default=0, ge=0)
    acknowledged_record_index: int = Field(default=0, ge=0)
    checkpoint_sequence: int = Field(default=0, ge=0)
    pending_ranges: tuple[TranscriptFoldPendingRange, ...] = Field(
        default=(), max_length=HOST_CAPTURE_MAX_PENDING_RANGES
    )
    extractor_state_base64: str = Field(default="", max_length=87_384, repr=False)
    extractor_state_sha256: str = Field(
        default=hashlib.sha256(b"").hexdigest(), pattern=SHA256_PATTERN
    )
    pending_tail_byte_count: int = Field(default=0, ge=0)
    pending_tail_sha256: str = Field(
        default=hashlib.sha256(b"").hexdigest(), pattern=SHA256_PATTERN
    )
    source_state: HostCaptureState = HostCaptureState.HEALTHY
    durable_end_offset: int = Field(default=0, ge=0)
    diagnostic_code: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    last_acknowledgement: DurableTranscriptFoldAcknowledgement | None = None

    @model_validator(mode="after")
    def validate_cursor(self) -> TranscriptFoldCursorState:
        try:
            extractor_state = base64.b64decode(
                self.extractor_state_base64, validate=True
            )
        except ValueError as exc:
            raise ValueError("fold extractor state must be canonical base64") from exc
        if len(extractor_state) > HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES:
            raise ValueError("fold extractor state exceeds its bound")
        if hashlib.sha256(extractor_state).hexdigest() != self.extractor_state_sha256:
            raise ValueError("fold extractor state hash must bind its bytes")
        expected_byte = self.acknowledged_offset
        expected_record = self.acknowledged_record_index
        for pending in self.pending_ranges:
            if pending.byte_start != expected_byte:
                raise ValueError("pending fold ranges must be byte contiguous")
            if pending.start_record_index != expected_record:
                raise ValueError("pending fold ranges must be record contiguous")
            expected_byte = pending.byte_end_exclusive
            expected_record = pending.end_record_index
        if expected_record - self.acknowledged_record_index > (
            HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS
        ):
            raise ValueError("pending fold records exceed the adapter SDK bound")
        if self.pending_tail_byte_count == 0 and self.pending_tail_sha256 != (
            hashlib.sha256(b"").hexdigest()
        ):
            raise ValueError("empty fold tail must use the empty SHA-256")
        if self.acknowledged_offset > self.durable_end_offset:
            raise ValueError("fold cursor cannot exceed retained source bytes")
        if self.file_identity is None and (
            self.acknowledged_offset
            or self.pending_ranges
            or extractor_state
            or self.pending_tail_byte_count
            or self.last_acknowledgement is not None
        ):
            raise ValueError("fold cursor progress requires file identity")
        if self.last_acknowledgement is not None:
            acknowledgement = self.last_acknowledgement
            if (
                acknowledgement.byte_end_exclusive != self.acknowledged_offset
                or acknowledgement.end_record_index != self.acknowledged_record_index
                or acknowledgement.checkpoint_sequence != self.checkpoint_sequence
            ):
                raise ValueError("fold cursor must bind its last durable receipt")
        return self

    @property
    def extractor_state(self) -> bytes:
        return base64.b64decode(self.extractor_state_base64, validate=True)

    @property
    def pending_end_offset(self) -> int:
        return (
            self.pending_ranges[-1].byte_end_exclusive
            if self.pending_ranges
            else self.acknowledged_offset
        )

    @property
    def pending_end_record_index(self) -> int:
        return (
            self.pending_ranges[-1].end_record_index
            if self.pending_ranges
            else self.acknowledged_record_index
        )


class TranscriptFoldHealth(FrozenContractModel):
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    status: TranscriptFoldRunStatus
    acknowledged_offset: int = Field(ge=0)
    acknowledged_record_index: int = Field(ge=0)
    checkpoint_sequence: int = Field(ge=0)
    durable_end_offset: int = Field(ge=0)
    pending_complete_record_count: int = Field(ge=0)
    pending_tail_byte_count: int = Field(ge=0)
    exact_turn_count: int = Field(default=0, ge=0)
    transcript_span_count: int = Field(default=0, ge=0)
    policy_disposition_count: int = Field(default=0, ge=0)
    diagnostic_code: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    receipt: CommitTranscriptFoldReceipt | None = None


class TranscriptFoldInboxItem(FrozenContractModel):
    transcript_fold_receipt_id: UUID
    outcome_ordinal: int = Field(ge=0)
    source_object_id: UUID
    outcome_kind: TranscriptFoldOutcomeKind
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    exact_envelope_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    disposition_code: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    adapter_profile_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    folded_at: datetime

    @model_validator(mode="after")
    def validate_item(self) -> TranscriptFoldInboxItem:
        if self.folded_at.tzinfo is None:
            raise ValueError("transcript fold inbox time must be timezone-aware")
        if self.outcome_kind == TranscriptFoldOutcomeKind.EXACT_TURN:
            if self.exact_envelope_sha256 is None or self.disposition_code is not None:
                raise ValueError("exact-turn inbox outcome must expose only its hash")
        elif self.outcome_kind == TranscriptFoldOutcomeKind.POLICY_DISPOSITION:
            if self.disposition_code is None or self.exact_envelope_sha256 is not None:
                raise ValueError("policy inbox outcome must expose its disposition")
        elif self.exact_envelope_sha256 is not None or self.disposition_code is not None:
            raise ValueError("transcript-span inbox outcome exposes bounds only")
        return self


class DurableTranscriptReadPort(Protocol):
    def read(self, request: DurableTranscriptReadRequest) -> DurableTranscriptRead: ...


class TranscriptFoldSubmissionPort(Protocol):
    def commit(
        self,
        command: CommitTranscriptFoldCommand,
        *,
        recorded_at: datetime,
    ) -> CommitTranscriptFoldReceipt: ...


class TranscriptFoldCursorStore(Protocol):
    def load(
        self, capability_id: str
    ) -> HostCaptureCursorState | TranscriptFoldCursorState | None: ...

    def commit(
        self,
        *,
        expected_revision: int,
        state: TranscriptFoldCursorState,
    ) -> None: ...


@dataclass(frozen=True)
class QualifiedTranscriptFoldAdapter:
    profile_version: str
    connector_id: str
    source_format_version: str
    assembler: SourceRecordAssembler[Any]
    envelope_builder: AssembledCaptureEnvelopeBuilder[Any]
    max_record_bytes: int
    max_span_bytes: int
    max_turn_records: int
    topology_fallback_codes: frozenset[str]
    finalized_scope_exclusion: Callable[[Any], FinalizedScopeExclusion | None] | None = (
        None
    )

    def __post_init__(self) -> None:
        if not self.profile_version or len(self.profile_version) > 128:
            raise ValueError("fold adapter profile version is invalid")
        if not 1 <= self.max_record_bytes <= 262_144:
            raise ValueError("fold adapter record bound is invalid")
        if not 1 <= self.max_span_bytes <= TRANSCRIPT_FOLD_MAX_READ_BYTES:
            raise ValueError("fold adapter span bound is invalid")
        if not 1 <= self.max_turn_records <= HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS:
            raise ValueError("fold adapter record-count bound is invalid")


def _fold_checkpoint_sha256(
    *,
    checkpoint_key: str,
    expected_sequence: int,
    next_sequence: int,
    source_object_id: UUID,
    source_revision_key: str,
    file_identity_key: str,
    byte_start: int,
    byte_end: int,
    start_record: int,
    end_record: int,
    source_bytes_sha256: str,
    outcomes_sha256: str,
) -> str:
    return hashlib.sha256(
        (
            f"{checkpoint_key}:{expected_sequence}:{next_sequence}:"
            f"{source_object_id}:{source_revision_key}:{file_identity_key}:"
            f"{byte_start}:{byte_end}:{start_record}:{end_record}:"
            f"{source_bytes_sha256}:{outcomes_sha256}"
        ).encode()
    ).hexdigest()


def build_transcript_fold_command(
    *,
    binding: KernelCaptureBinding,
    capability_id: str,
    connector_id: str,
    adapter_profile_version: str | None,
    observed_source_format_version: str | None,
    source_revision_key: str,
    file_identity: FileIdentity,
    outcomes: tuple[TranscriptFoldOutcome, ...],
    source_bytes: bytes,
    checkpoint_key: str,
    expected_checkpoint_sequence: int,
    legacy_cursor: LegacyAdapterCursorBinding | None = None,
) -> CommitTranscriptFoldCommand:
    if not outcomes:
        raise ValueError("transcript fold command requires outcomes")
    byte_start = outcomes[0].byte_start
    byte_end = outcomes[-1].byte_end_exclusive
    if len(source_bytes) != byte_end - byte_start:
        raise ValueError("transcript fold command bytes must match its outcomes")
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    rendered_outcomes = [
        item.model_dump(mode="json", warnings="error") for item in outcomes
    ]
    outcomes_hash = canonical_sha256(rendered_outcomes)
    start_record = outcomes[0].start_record_index
    end_record = outcomes[-1].end_record_index
    next_sequence = expected_checkpoint_sequence + 1
    checkpoint_hash = _fold_checkpoint_sha256(
        checkpoint_key=checkpoint_key,
        expected_sequence=expected_checkpoint_sequence,
        next_sequence=next_sequence,
        source_object_id=binding.source_object_id,
        source_revision_key=source_revision_key,
        file_identity_key=file_identity.stable_key,
        byte_start=byte_start,
        byte_end=byte_end,
        start_record=start_record,
        end_record=end_record,
        source_bytes_sha256=source_hash,
        outcomes_sha256=outcomes_hash,
    )
    operation_hash = canonical_sha256(
        {
            "source_object_id": str(binding.source_object_id),
            "source_revision_key": source_revision_key,
            "file_identity_key": file_identity.stable_key,
            "byte_start": byte_start,
            "byte_end_exclusive": byte_end,
            "source_bytes_sha256": source_hash,
            "outcomes_sha256": outcomes_hash,
            "legacy_cursor_sha256": (
                legacy_cursor.cursor_sha256 if legacy_cursor is not None else None
            ),
        }
    )
    return CommitTranscriptFoldCommand(
        idempotency_key=f"transcript-fold:{operation_hash}",
        binding=binding,
        capability_id=capability_id,
        connector_id=connector_id,
        adapter_profile_version=adapter_profile_version,
        observed_source_format_version=observed_source_format_version,
        source_revision_key=source_revision_key,
        file_identity=file_identity,
        file_identity_key=file_identity.stable_key,
        byte_start=byte_start,
        byte_end_exclusive=byte_end,
        start_record_index=start_record,
        end_record_index=end_record,
        complete_record_count=end_record - start_record,
        source_bytes_sha256=source_hash,
        outcomes_sha256=outcomes_hash,
        outcomes=outcomes,
        checkpoint=TranscriptFoldCheckpoint(
            checkpoint_key=checkpoint_key,
            expected_sequence=expected_checkpoint_sequence,
            next_sequence=next_sequence,
            checkpoint_hash=checkpoint_hash,
        ),
        legacy_cursor=legacy_cursor,
    )


class DurableTranscriptFoldLoop:
    """Exact-turn-first fold over acknowledged canonical source ranges."""

    def __init__(
        self,
        *,
        source: DurableTranscriptReadPort,
        submission: TranscriptFoldSubmissionPort,
        cursor_store: TranscriptFoldCursorStore,
        binding: KernelCaptureBinding,
        capability_id: str,
        checkpoint_key: str,
        connector_id: str,
        source_revision_key: str,
        file_identity_key: str,
        observed_source_format_version: str | None,
        adapter: QualifiedTranscriptFoldAdapter | None,
        source_format_disposition_code: str | None = None,
        sealed_byte_end_exclusive: int | None = None,
        read_size: int = TRANSCRIPT_FOLD_MAX_READ_BYTES,
    ) -> None:
        if not 1 <= read_size <= TRANSCRIPT_FOLD_MAX_READ_BYTES:
            raise ValueError("transcript fold read size is invalid")
        if adapter is not None and adapter.connector_id != connector_id:
            raise ValueError("transcript fold adapter must bind the connector")
        if source_format_disposition_code is not None and (
            adapter is not None
            or IDENTIFIER_PATTERN.fullmatch(source_format_disposition_code) is None
            or len(source_format_disposition_code) > 128
        ):
            raise ValueError("source-format disposition configuration is invalid")
        if sealed_byte_end_exclusive is not None and sealed_byte_end_exclusive <= 0:
            raise ValueError("sealed transcript fold byte end must be positive")
        self._source = source
        self._submission = submission
        self._cursor_store = cursor_store
        self._binding = binding
        self._capability_id = capability_id
        self._checkpoint_key = checkpoint_key
        self._connector_id = connector_id
        self._source_revision_key = source_revision_key
        self._file_identity_key = file_identity_key
        self._observed_source_format_version = observed_source_format_version
        self._adapter = adapter
        self._source_format_disposition_code = source_format_disposition_code
        self._sealed_byte_end_exclusive = sealed_byte_end_exclusive
        self._read_size = read_size

    def run_once(self, *, recorded_at: datetime) -> TranscriptFoldHealth:
        if recorded_at.tzinfo is None:
            raise ValueError("transcript fold time must be timezone-aware")
        loaded = self._cursor_store.load(self._capability_id)
        if isinstance(loaded, HostCaptureCursorState):
            state = self._migrate_legacy_cursor(loaded, recorded_at=recorded_at)
        elif loaded is None:
            state = TranscriptFoldCursorState(capability_id=self._capability_id)
        else:
            state = loaded
        read_start = state.pending_end_offset
        read = self._source.read(
            DurableTranscriptReadRequest(
                source_object_id=self._binding.source_object_id,
                source_revision_key=self._source_revision_key,
                file_identity_key=self._file_identity_key,
                byte_start=read_start,
                max_bytes=self._read_size,
            )
        )
        self._validate_read(read, state=state)
        if read.status == DurableTranscriptReadStatus.END_OF_DURABLE_RANGES:
            if state.pending_ranges and self._is_sealed_read(read):
                return self._commit_sealed_pending_span(
                    state,
                    file_identity=state.file_identity,
                    pending_tail=b"",
                    durable_end_offset=read.durable_end_offset,
                    observed_source_format_version=(
                        self._observed_source_format_version
                    ),
                    adapter_profile_version=None,
                    recorded_at=recorded_at,
                )
            status = (
                TranscriptFoldRunStatus.WAITING_FOR_EXACT_TURN
                if state.pending_ranges
                else TranscriptFoldRunStatus.WAITING_FOR_COMPLETE_RECORD
                if state.pending_tail_byte_count
                else TranscriptFoldRunStatus.END_OF_DURABLE_RANGES
            )
            return self._health(state, status=status)
        assert read.file_identity is not None
        adapter = self._qualified_adapter(read)
        max_record_bytes = adapter.max_record_bytes if adapter is not None else 262_144
        records, partial_tail = _frame_complete_records(
            read.payload,
            base_offset=read.byte_start,
            base_record_index=state.pending_end_record_index,
            max_record_bytes=max_record_bytes,
        )
        if not records:
            if state.pending_ranges and self._is_sealed_read(read):
                return self._commit_sealed_pending_span(
                    state,
                    file_identity=read.file_identity,
                    pending_tail=partial_tail,
                    durable_end_offset=read.durable_end_offset,
                    observed_source_format_version=(
                        read.observed_source_format_version
                    ),
                    adapter_profile_version=(
                        adapter.profile_version if adapter is not None else None
                    ),
                    recorded_at=recorded_at,
                )
            updated = self._updated_state(
                state,
                file_identity=read.file_identity,
                pending_ranges=state.pending_ranges,
                extractor_state=state.extractor_state,
                pending_tail=partial_tail,
                source_state=HostCaptureState.WAITING_FOR_COMPLETE_RECORD,
                durable_end_offset=read.durable_end_offset,
                diagnostic_code=None,
            )
            self._commit_state(previous_revision=state.revision, state=updated)
            return self._health(
                updated,
                status=TranscriptFoldRunStatus.WAITING_FOR_COMPLETE_RECORD,
            )

        outcomes: list[TranscriptFoldOutcome] = []
        assembly_state = state.extractor_state
        outcome_start_offset = state.acknowledged_offset
        outcome_start_record = state.acknowledged_record_index
        pending_records_start = 0
        consumed_record_count = 0
        fallback_code: str | None = None
        fallback_kind: TranscriptFoldOutcomeKind | None = None
        outcome_limit_reached = False

        if adapter is None:
            fallback_code = (
                self._source_format_disposition_code
                or "fold.unqualified-source-format"
            )
            fallback_kind = (
                TranscriptFoldOutcomeKind.POLICY_DISPOSITION
                if self._source_format_disposition_code is not None
                else TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN
            )
        else:
            for index, record in enumerate(records):
                if len(outcomes) == TRANSCRIPT_FOLD_MAX_OUTCOMES:
                    pending_records_start = index
                    outcome_limit_reached = True
                    break
                if record.oversized:
                    fallback_code = "fold.record-size-limit"
                    fallback_kind = TranscriptFoldOutcomeKind.POLICY_DISPOSITION
                    pending_records_start = index
                    break
                if record.record_index + 1 - outcome_start_record > (
                    adapter.max_turn_records
                ):
                    fallback_code = "fold.turn-record-limit"
                    fallback_kind = TranscriptFoldOutcomeKind.POLICY_DISPOSITION
                    pending_records_start = index
                    break
                if record.end_offset - outcome_start_offset > adapter.max_span_bytes:
                    fallback_code = "fold.span-byte-limit"
                    fallback_kind = TranscriptFoldOutcomeKind.POLICY_DISPOSITION
                    pending_records_start = index
                    break
                try:
                    step = adapter.assembler.advance(record, state=assembly_state)
                except SourceRecordParseError as exc:
                    fallback_code = exc.diagnostic_code
                    fallback_kind = (
                        TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN
                        if exc.diagnostic_code in adapter.topology_fallback_codes
                        else TranscriptFoldOutcomeKind.POLICY_DISPOSITION
                    )
                    pending_records_start = index
                    break
                if len(step.state) > HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES:
                    fallback_code = "fold.continuation-state-size-limit"
                    fallback_kind = TranscriptFoldOutcomeKind.POLICY_DISPOSITION
                    pending_records_start = index
                    break
                assembly_state = step.state
                consumed_record_count = index + 1
                if step.finalized is None:
                    continue
                exact_end_record = record.record_index + 1
                exact_read = self._read_exact(
                    outcome_start_offset,
                    record.end_offset,
                )
                source_span = VerifiedAssemblySpan(
                    file_identity=read.file_identity,
                    start_offset=outcome_start_offset,
                    end_offset=record.end_offset,
                    start_record_index=outcome_start_record,
                    end_record_index=exact_end_record,
                    byte_count=record.end_offset - outcome_start_offset,
                    record_count=exact_end_record - outcome_start_record,
                    range_count=len(exact_read.ranges),
                    source_bytes_sha256=exact_read.payload_sha256,
                )
                scope_exclusion = (
                    adapter.finalized_scope_exclusion(step.finalized)
                    if adapter.finalized_scope_exclusion is not None
                    else None
                )
                if scope_exclusion is not None:
                    outcomes.append(
                        _policy_outcome(
                            byte_start=outcome_start_offset,
                            byte_end=record.end_offset,
                            start_record=outcome_start_record,
                            end_record=exact_end_record,
                            source_read=exact_read,
                            code=f"fold.scope-{scope_exclusion.reason}",
                            scope_reason=ScopeExclusionReason(
                                scope_exclusion.reason
                            ),
                        )
                    )
                else:
                    envelope = _build_exact_envelope(
                        adapter.envelope_builder,
                        step.finalized,
                        source_span=source_span,
                        recorded_at=exact_read.first_captured_at or recorded_at,
                    )
                    outcomes.append(
                        TranscriptFoldOutcome(
                            outcome_kind=TranscriptFoldOutcomeKind.EXACT_TURN,
                            byte_start=outcome_start_offset,
                            byte_end_exclusive=record.end_offset,
                            start_record_index=outcome_start_record,
                            end_record_index=exact_end_record,
                            source_bytes_sha256=exact_read.payload_sha256,
                            lineage=exact_read.ranges,
                            exact_turn=ExactTurnFoldEvidence(
                                envelope_canonical_json=envelope.canonical_bytes.decode(
                                    "utf-8"
                                ),
                                envelope_sha256=envelope.canonical_hash,
                            ),
                        )
                    )
                outcome_start_offset = record.end_offset
                outcome_start_record = exact_end_record
                pending_records_start = index + 1

        if (
            adapter is not None
            and fallback_kind is None
            and not outcome_limit_reached
            and outcome_start_offset < records[-1].end_offset
            and self._is_sealed_read(read)
        ):
            fallback_code = "fold.sealed-evidence-boundary"
            fallback_kind = TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN

        if fallback_kind is not None:
            last_record = records[-1]
            fallback_read = self._read_exact(
                outcome_start_offset,
                last_record.end_offset,
            )
            if fallback_kind == TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN:
                outcomes.append(
                    _span_outcome(
                        binding=self._binding,
                        source_revision_key=self._source_revision_key,
                        file_identity_key=self._file_identity_key,
                        byte_start=outcome_start_offset,
                        byte_end=last_record.end_offset,
                        start_record=outcome_start_record,
                        end_record=last_record.record_index + 1,
                        source_read=fallback_read,
                    )
                )
            else:
                outcomes.append(
                    _policy_outcome(
                        byte_start=outcome_start_offset,
                        byte_end=last_record.end_offset,
                        start_record=outcome_start_record,
                        end_record=last_record.record_index + 1,
                        source_read=fallback_read,
                        code=fallback_code or "fold.unsupported-record",
                    )
                )
            outcome_start_offset = last_record.end_offset
            outcome_start_record = last_record.record_index + 1
            consumed_record_count = len(records)
            pending_records_start = len(records)
            assembly_state = b""

        receipt: CommitTranscriptFoldReceipt | None = None
        previous_revision = state.revision
        next_state = state
        if outcomes:
            command_read = self._read_exact(
                outcomes[0].byte_start,
                outcomes[-1].byte_end_exclusive,
            )
            command = build_transcript_fold_command(
                binding=self._binding,
                capability_id=self._capability_id,
                connector_id=self._connector_id,
                adapter_profile_version=(
                    adapter.profile_version if adapter is not None else None
                ),
                observed_source_format_version=(
                    read.observed_source_format_version
                ),
                source_revision_key=self._source_revision_key,
                file_identity=read.file_identity,
                outcomes=tuple(outcomes),
                source_bytes=command_read.payload,
                checkpoint_key=self._checkpoint_key,
                expected_checkpoint_sequence=state.checkpoint_sequence,
            )
            receipt = self._submission.commit(command, recorded_at=recorded_at)
            self._validate_receipt(receipt, command=command)
            next_state = TranscriptFoldCursorState(
                capability_id=state.capability_id,
                revision=state.revision,
                file_identity=read.file_identity,
                acknowledged_offset=receipt.byte_end_exclusive,
                acknowledged_record_index=receipt.end_record_index,
                checkpoint_sequence=receipt.checkpoint_sequence,
                source_state=HostCaptureState.HEALTHY,
                durable_end_offset=read.durable_end_offset,
                last_acknowledgement=_acknowledgement(receipt),
            )
            outcome_start_offset = receipt.byte_end_exclusive
            outcome_start_record = receipt.end_record_index

        remaining = () if outcome_limit_reached else records[pending_records_start:]
        if outcome_limit_reached:
            partial_tail = b""
        pending_ranges: tuple[TranscriptFoldPendingRange, ...] = ()
        if remaining:
            pending_bytes = b"".join(item.framed_bytes for item in remaining)
            pending_ranges = (
                TranscriptFoldPendingRange(
                    byte_start=remaining[0].start_offset,
                    byte_end_exclusive=remaining[-1].end_offset,
                    start_record_index=remaining[0].record_index,
                    end_record_index=remaining[-1].record_index + 1,
                    source_bytes_sha256=hashlib.sha256(pending_bytes).hexdigest(),
                ),
            )
        elif not outcomes and state.pending_ranges:
            pending_ranges = state.pending_ranges
        if not outcomes and adapter is not None and consumed_record_count == len(records):
            if records:
                new_pending = TranscriptFoldPendingRange(
                    byte_start=records[0].start_offset,
                    byte_end_exclusive=records[-1].end_offset,
                    start_record_index=records[0].record_index,
                    end_record_index=records[-1].record_index + 1,
                    source_bytes_sha256=hashlib.sha256(
                        b"".join(item.framed_bytes for item in records)
                    ).hexdigest(),
                )
                pending_ranges = (*state.pending_ranges, new_pending)

        waiting = bool(pending_ranges or partial_tail)
        final_state = self._updated_state(
            next_state,
            file_identity=read.file_identity,
            pending_ranges=pending_ranges,
            extractor_state=assembly_state if adapter is not None else b"",
            pending_tail=partial_tail,
            source_state=(
                HostCaptureState.WAITING_FOR_COMPLETE_RECORD
                if waiting
                else HostCaptureState.HEALTHY
            ),
            durable_end_offset=read.durable_end_offset,
            diagnostic_code=fallback_code,
            keep_acknowledgement=receipt is not None,
        )
        self._commit_state(previous_revision=previous_revision, state=final_state)
        if receipt is not None:
            return self._health(
                final_state,
                status=TranscriptFoldRunStatus.FOLDED,
                receipt=receipt,
            )
        return self._health(
            final_state,
            status=(
                TranscriptFoldRunStatus.WAITING_FOR_EXACT_TURN
                if pending_ranges
                else TranscriptFoldRunStatus.WAITING_FOR_COMPLETE_RECORD
            ),
        )

    def _is_sealed_read(self, read: DurableTranscriptRead) -> bool:
        sealed_end = self._sealed_byte_end_exclusive
        return sealed_end is not None and (
            read.durable_end_offset == sealed_end
            and read.byte_end_exclusive == sealed_end
        )

    def _commit_sealed_pending_span(
        self,
        state: TranscriptFoldCursorState,
        *,
        file_identity: FileIdentity | None,
        pending_tail: bytes,
        durable_end_offset: int,
        observed_source_format_version: str | None,
        adapter_profile_version: str | None,
        recorded_at: datetime,
    ) -> TranscriptFoldHealth:
        if file_identity is None or not state.pending_ranges:
            raise ValueError("sealed transcript span requires pending source evidence")
        pending_read = self._read_exact(
            state.acknowledged_offset,
            state.pending_end_offset,
        )
        outcome = _span_outcome(
            binding=self._binding,
            source_revision_key=self._source_revision_key,
            file_identity_key=self._file_identity_key,
            byte_start=state.acknowledged_offset,
            byte_end=state.pending_end_offset,
            start_record=state.acknowledged_record_index,
            end_record=state.pending_end_record_index,
            source_read=pending_read,
        )
        command = build_transcript_fold_command(
            binding=self._binding,
            capability_id=self._capability_id,
            connector_id=self._connector_id,
            adapter_profile_version=adapter_profile_version,
            observed_source_format_version=observed_source_format_version,
            source_revision_key=self._source_revision_key,
            file_identity=file_identity,
            outcomes=(outcome,),
            source_bytes=pending_read.payload,
            checkpoint_key=self._checkpoint_key,
            expected_checkpoint_sequence=state.checkpoint_sequence,
        )
        receipt = self._submission.commit(command, recorded_at=recorded_at)
        self._validate_receipt(receipt, command=command)
        acknowledged = TranscriptFoldCursorState(
            capability_id=state.capability_id,
            revision=state.revision,
            file_identity=file_identity,
            acknowledged_offset=receipt.byte_end_exclusive,
            acknowledged_record_index=receipt.end_record_index,
            checkpoint_sequence=receipt.checkpoint_sequence,
            source_state=HostCaptureState.HEALTHY,
            durable_end_offset=durable_end_offset,
            last_acknowledgement=_acknowledgement(receipt),
        )
        updated = self._updated_state(
            acknowledged,
            file_identity=file_identity,
            pending_ranges=(),
            extractor_state=b"",
            pending_tail=pending_tail,
            source_state=(
                HostCaptureState.WAITING_FOR_COMPLETE_RECORD
                if pending_tail
                else HostCaptureState.HEALTHY
            ),
            durable_end_offset=durable_end_offset,
            diagnostic_code="fold.sealed-evidence-boundary",
        )
        self._commit_state(previous_revision=state.revision, state=updated)
        return self._health(
            updated,
            status=TranscriptFoldRunStatus.FOLDED,
            receipt=receipt,
        )

    def _qualified_adapter(
        self, source_read: DurableTranscriptRead
    ) -> QualifiedTranscriptFoldAdapter | None:
        adapter = self._adapter
        if adapter is None:
            return None
        if (
            source_read.connector_id != adapter.connector_id
            or source_read.observed_source_format_version
            != adapter.source_format_version
            or self._observed_source_format_version != adapter.source_format_version
        ):
            return None
        return adapter

    def _migrate_legacy_cursor(
        self,
        legacy: HostCaptureCursorState,
        *,
        recorded_at: datetime,
    ) -> TranscriptFoldCursorState:
        if legacy.capability_id != self._capability_id:
            raise ValueError("legacy adapter cursor capability mismatch")
        if legacy.acknowledged_offset == 0:
            migrated = TranscriptFoldCursorState(
                capability_id=legacy.capability_id,
                revision=legacy.revision + 1,
                file_identity=legacy.file_identity,
                durable_end_offset=legacy.acknowledged_offset,
            )
            self._commit_state(previous_revision=legacy.revision, state=migrated)
            return migrated
        legacy_payload = legacy.model_dump(mode="json", warnings="error")
        legacy_binding = LegacyAdapterCursorBinding(
            cursor_version=legacy.cursor_version,
            cursor_sha256=canonical_sha256(legacy_payload),
            acknowledged_byte_offset=legacy.acknowledged_offset,
            acknowledged_record_index=legacy.acknowledged_record_index,
            checkpoint_sequence=legacy.checkpoint_sequence,
            last_receipt_event_id=(
                legacy.last_acknowledgement.receipt_event_id
                if legacy.last_acknowledgement is not None
                else None
            ),
        )
        next_offset = 0
        next_record = 0
        expected_checkpoint_sequence = legacy.checkpoint_sequence
        receipt: CommitTranscriptFoldReceipt | None = None
        source_read: DurableTranscriptRead | None = None
        while next_offset < legacy.acknowledged_offset:
            requested_end = min(
                legacy.acknowledged_offset,
                next_offset + TRANSCRIPT_FOLD_LEGACY_MIGRATION_CHUNK_BYTES,
            )
            source_read = self._read_exact(next_offset, requested_end)
            assert source_read.file_identity is not None
            if legacy.file_identity != source_read.file_identity:
                raise ValueError("legacy adapter cursor file identity mismatch")
            chunk_end = requested_end
            if requested_end < legacy.acknowledged_offset:
                last_complete_record = source_read.payload.rfind(b"\n") + 1
                if last_complete_record == 0:
                    raise ValueError(
                        "legacy adapter cursor record exceeds the migration window"
                    )
                chunk_end = next_offset + last_complete_record
                if chunk_end != requested_end:
                    source_read = self._read_exact(next_offset, chunk_end)
            elif not source_read.payload.endswith(b"\n"):
                raise ValueError(
                    "legacy adapter cursor does not end at a complete record"
                )
            record_count = source_read.payload.count(b"\n")
            if record_count == 0:
                raise ValueError("legacy adapter cursor migration made no progress")
            chunk_record_end = next_record + record_count
            if chunk_record_end > legacy.acknowledged_record_index:
                raise ValueError("legacy adapter cursor record count mismatch")
            file_identity = source_read.file_identity
            assert file_identity is not None
            outcome = _policy_outcome(
                byte_start=next_offset,
                byte_end=chunk_end,
                start_record=next_record,
                end_record=chunk_record_end,
                source_read=source_read,
                code="fold.legacy-cursor-receipt-migration",
            )
            command = build_transcript_fold_command(
                binding=self._binding,
                capability_id=self._capability_id,
                connector_id=self._connector_id,
                adapter_profile_version=(
                    self._adapter.profile_version if self._adapter is not None else None
                ),
                observed_source_format_version=(
                    source_read.observed_source_format_version
                ),
                source_revision_key=self._source_revision_key,
                file_identity=file_identity,
                outcomes=(outcome,),
                source_bytes=source_read.payload,
                checkpoint_key=self._checkpoint_key,
                expected_checkpoint_sequence=expected_checkpoint_sequence,
                legacy_cursor=legacy_binding if next_offset == 0 else None,
            )
            receipt = self._submission.commit(command, recorded_at=recorded_at)
            self._validate_receipt(receipt, command=command)
            next_offset = receipt.byte_end_exclusive
            next_record = receipt.end_record_index
            expected_checkpoint_sequence = receipt.checkpoint_sequence
        if (
            receipt is None
            or source_read is None
            or next_offset != legacy.acknowledged_offset
            or next_record != legacy.acknowledged_record_index
        ):
            raise ValueError("legacy adapter cursor migration coverage mismatch")
        migrated = TranscriptFoldCursorState(
            capability_id=legacy.capability_id,
            revision=legacy.revision + 1,
            file_identity=source_read.file_identity,
            acknowledged_offset=receipt.byte_end_exclusive,
            acknowledged_record_index=receipt.end_record_index,
            checkpoint_sequence=receipt.checkpoint_sequence,
            extractor_state_base64=(
                legacy.extractor_state_base64
                if not (
                    legacy.pending_assembly_bytes
                    or legacy.pending_assembly_records
                    or legacy.pending_assembly_ranges
                    or legacy.partial_tail
                )
                else ""
            ),
            extractor_state_sha256=(
                legacy.extractor_state_sha256
                if not (
                    legacy.pending_assembly_bytes
                    or legacy.pending_assembly_records
                    or legacy.pending_assembly_ranges
                    or legacy.partial_tail
                )
                else hashlib.sha256(b"").hexdigest()
            ),
            source_state=HostCaptureState.HEALTHY,
            durable_end_offset=source_read.durable_end_offset,
            last_acknowledgement=_acknowledgement(receipt),
        )
        self._commit_state(previous_revision=legacy.revision, state=migrated)
        return migrated

    def _read_exact(
        self,
        byte_start: int,
        byte_end: int,
        *,
        max_bytes: int | None = None,
    ) -> DurableTranscriptRead:
        if byte_end <= byte_start:
            raise ValueError("exact durable fold read must be non-empty")
        length = byte_end - byte_start
        read = self._source.read(
            DurableTranscriptReadRequest(
                source_object_id=self._binding.source_object_id,
                source_revision_key=self._source_revision_key,
                file_identity_key=self._file_identity_key,
                byte_start=byte_start,
                max_bytes=max_bytes or length,
            )
        )
        if (
            read.status != DurableTranscriptReadStatus.DATA
            or read.byte_start != byte_start
            or read.byte_end_exclusive < byte_end
        ):
            raise ValueError("durable source ranges do not cover the fold result")
        if read.byte_end_exclusive != byte_end:
            payload = read.payload[:length]
            ranges = _slice_lineage(
                read.ranges,
                payload=read.payload,
                payload_byte_start=read.byte_start,
                byte_start=byte_start,
                byte_end=byte_end,
            )
            return DurableTranscriptRead(
                status=DurableTranscriptReadStatus.DATA,
                source_object_id=read.source_object_id,
                source_revision_key=read.source_revision_key,
                file_identity=read.file_identity,
                file_identity_key=read.file_identity_key,
                connector_id=read.connector_id,
                observed_connector_version=read.observed_connector_version,
                observed_source_format_version=read.observed_source_format_version,
                byte_start=byte_start,
                byte_end_exclusive=byte_end,
                durable_end_offset=read.durable_end_offset,
                payload_hex=payload.hex(),
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                ranges=ranges,
                first_captured_at=read.first_captured_at,
            )
        return read

    def _validate_read(
        self,
        source_read: DurableTranscriptRead,
        *,
        state: TranscriptFoldCursorState,
    ) -> None:
        if source_read.source_object_id != self._binding.source_object_id:
            raise ValueError("durable fold source object mismatch")
        if source_read.source_revision_key != self._source_revision_key:
            raise ValueError("durable fold source revision mismatch")
        if source_read.file_identity_key != self._file_identity_key:
            raise ValueError("durable fold file identity key mismatch")
        if source_read.byte_start != state.pending_end_offset:
            raise ValueError("durable fold source read is not cursor contiguous")
        if (
            state.file_identity is not None
            and source_read.file_identity is not None
            and source_read.file_identity != state.file_identity
        ):
            raise ValueError("durable fold source identity changed")

    @staticmethod
    def _validate_receipt(
        receipt: CommitTranscriptFoldReceipt,
        *,
        command: CommitTranscriptFoldCommand,
    ) -> None:
        if (
            receipt.source_object_id != command.binding.source_object_id
            or receipt.source_revision_key != command.source_revision_key
            or receipt.file_identity_key != command.file_identity_key
            or receipt.byte_start != command.byte_start
            or receipt.byte_end_exclusive != command.byte_end_exclusive
            or receipt.start_record_index != command.start_record_index
            or receipt.end_record_index != command.end_record_index
            or receipt.source_bytes_sha256 != command.source_bytes_sha256
            or receipt.outcomes_sha256 != command.outcomes_sha256
            or receipt.checkpoint_key != command.checkpoint.checkpoint_key
            or receipt.checkpoint_sequence != command.checkpoint.next_sequence
            or not receipt.durable
        ):
            raise ValueError("durable transcript fold receipt does not bind its command")

    @staticmethod
    def _updated_state(
        state: TranscriptFoldCursorState,
        *,
        file_identity: FileIdentity,
        pending_ranges: tuple[TranscriptFoldPendingRange, ...],
        extractor_state: bytes,
        pending_tail: bytes,
        source_state: HostCaptureState,
        durable_end_offset: int,
        diagnostic_code: str | None,
        keep_acknowledgement: bool = True,
    ) -> TranscriptFoldCursorState:
        return TranscriptFoldCursorState(
            capability_id=state.capability_id,
            revision=state.revision + 1,
            file_identity=file_identity,
            acknowledged_offset=state.acknowledged_offset,
            acknowledged_record_index=state.acknowledged_record_index,
            checkpoint_sequence=state.checkpoint_sequence,
            pending_ranges=pending_ranges,
            extractor_state_base64=base64.b64encode(extractor_state).decode("ascii"),
            extractor_state_sha256=hashlib.sha256(extractor_state).hexdigest(),
            pending_tail_byte_count=len(pending_tail),
            pending_tail_sha256=hashlib.sha256(pending_tail).hexdigest(),
            source_state=source_state,
            durable_end_offset=durable_end_offset,
            diagnostic_code=diagnostic_code,
            last_acknowledgement=(
                state.last_acknowledgement if keep_acknowledgement else None
            ),
        )

    def _commit_state(
        self,
        *,
        previous_revision: int,
        state: TranscriptFoldCursorState,
    ) -> None:
        self._cursor_store.commit(expected_revision=previous_revision, state=state)

    @staticmethod
    def _health(
        state: TranscriptFoldCursorState,
        *,
        status: TranscriptFoldRunStatus,
        receipt: CommitTranscriptFoldReceipt | None = None,
    ) -> TranscriptFoldHealth:
        return TranscriptFoldHealth(
            capability_id=state.capability_id,
            status=status,
            acknowledged_offset=state.acknowledged_offset,
            acknowledged_record_index=state.acknowledged_record_index,
            checkpoint_sequence=state.checkpoint_sequence,
            durable_end_offset=state.durable_end_offset,
            pending_complete_record_count=(
                state.pending_end_record_index - state.acknowledged_record_index
            ),
            pending_tail_byte_count=state.pending_tail_byte_count,
            exact_turn_count=receipt.exact_turn_count if receipt is not None else 0,
            transcript_span_count=(
                receipt.transcript_span_count if receipt is not None else 0
            ),
            policy_disposition_count=(
                receipt.policy_disposition_count if receipt is not None else 0
            ),
            diagnostic_code=state.diagnostic_code,
            receipt=receipt,
        )


def _build_exact_envelope(
    builder: AssembledCaptureEnvelopeBuilder[Any],
    finalized: Any,
    *,
    source_span: VerifiedAssemblySpan,
    recorded_at: datetime,
) -> ConversationEventEnvelope:
    compact_build = getattr(builder, "build_from_source_span", None)
    if not callable(compact_build):
        raise ValueError("durable fold requires a compact exact-turn builder")
    envelope = compact_build(
        finalized,
        source_span=source_span,
        recorded_at=recorded_at,
    )
    if not isinstance(envelope, ConversationEventEnvelope):
        raise TypeError("durable fold exact result must be a conversation envelope")
    return envelope


def _frame_complete_records(
    payload: bytes,
    *,
    base_offset: int,
    base_record_index: int,
    max_record_bytes: int,
) -> tuple[tuple[FramedSourceRecord, ...], bytes]:
    records: list[FramedSourceRecord] = []
    cursor = 0
    while True:
        delimiter = payload.find(b"\n", cursor)
        if delimiter < 0:
            break
        end = delimiter + 1
        framed = payload[cursor:end]
        content = framed[:-1]
        if content.endswith(b"\r"):
            content = content[:-1]
        records.append(
            FramedSourceRecord(
                start_offset=base_offset + cursor,
                end_offset=base_offset + end,
                framed_bytes=framed,
                content_bytes=content,
                oversized=len(framed) > max_record_bytes,
                record_index=base_record_index + len(records),
            )
        )
        cursor = end
    return tuple(records), payload[cursor:]


def _slice_lineage(
    ranges: tuple[TranscriptFoldSourceRange, ...],
    *,
    payload: bytes,
    payload_byte_start: int,
    byte_start: int,
    byte_end: int,
) -> tuple[TranscriptFoldSourceRange, ...]:
    result: list[TranscriptFoldSourceRange] = []
    for source_range in ranges:
        start = max(byte_start, source_range.byte_start)
        end = min(byte_end, source_range.byte_end_exclusive)
        if end <= start:
            continue
        if start == source_range.byte_start and end == source_range.byte_end_exclusive:
            result.append(source_range)
        else:
            relative_start = start - payload_byte_start
            relative_end = end - payload_byte_start
            source_bytes_sha256 = hashlib.sha256(
                payload[relative_start:relative_end]
            ).hexdigest()
            result.append(
                source_range.model_copy(
                    update={
                        "byte_start": start,
                        "byte_end_exclusive": end,
                        "source_bytes_sha256": source_bytes_sha256,
                    }
                )
            )
    return tuple(result)


def _span_outcome(
    *,
    binding: KernelCaptureBinding,
    source_revision_key: str,
    file_identity_key: str,
    byte_start: int,
    byte_end: int,
    start_record: int,
    end_record: int,
    source_read: DurableTranscriptRead,
) -> TranscriptFoldOutcome:
    return TranscriptFoldOutcome(
        outcome_kind=TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN,
        byte_start=byte_start,
        byte_end_exclusive=byte_end,
        start_record_index=start_record,
        end_record_index=end_record,
        source_bytes_sha256=source_read.payload_sha256,
        lineage=source_read.ranges,
        transcript_span=TranscriptSpanEvidence(
            source_object_id=binding.source_object_id,
            source_revision_key=source_revision_key,
            file_identity_key=file_identity_key,
            byte_start=byte_start,
            byte_end_exclusive=byte_end,
            start_record_index=start_record,
            end_record_index=end_record,
            source_bytes_sha256=source_read.payload_sha256,
        ),
    )


def _policy_outcome(
    *,
    byte_start: int,
    byte_end: int,
    start_record: int,
    end_record: int,
    source_read: DurableTranscriptRead,
    code: str,
    scope_reason: ScopeExclusionReason | None = None,
) -> TranscriptFoldOutcome:
    return TranscriptFoldOutcome(
        outcome_kind=TranscriptFoldOutcomeKind.POLICY_DISPOSITION,
        byte_start=byte_start,
        byte_end_exclusive=byte_end,
        start_record_index=start_record,
        end_record_index=end_record,
        source_bytes_sha256=source_read.payload_sha256,
        lineage=source_read.ranges,
        policy_disposition=TranscriptPolicyDisposition(
            disposition_code=code,
            scope_reason=scope_reason,
        ),
    )


def _acknowledgement(
    receipt: CommitTranscriptFoldReceipt,
) -> DurableTranscriptFoldAcknowledgement:
    return DurableTranscriptFoldAcknowledgement(
        transcript_fold_receipt_id=receipt.transcript_fold_receipt_id,
        idempotency_receipt_id=receipt.idempotency_receipt_id,
        byte_start=receipt.byte_start,
        byte_end_exclusive=receipt.byte_end_exclusive,
        start_record_index=receipt.start_record_index,
        end_record_index=receipt.end_record_index,
        source_bytes_sha256=receipt.source_bytes_sha256,
        outcomes_sha256=receipt.outcomes_sha256,
        checkpoint_sequence=receipt.checkpoint_sequence,
        authority_principal_id=receipt.authority_principal_id,
        replayed=receipt.replayed,
    )
