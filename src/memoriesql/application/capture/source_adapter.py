from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from memoriesql.application.capture.contracts import (
    CaptureEnvelope,
    CaptureIngressStatus,
    CaptureSubmissionPort,
    CaptureSubmissionReceipt,
    ConnectorCursor,
)
from memoriesql.application.capture.host_protocol import (
    HOST_HELPER_MAX_READ_BYTES,
    FileIdentity,
    HostReadRequest,
    HostReadResponse,
    HostReadStatus,
)
from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    canonical_sha256,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

SOURCE_ADAPTER_SDK_VERSION = 4
HOST_CAPTURE_MAX_PARTIAL_TAIL_BYTES = 65_536
HOST_CAPTURE_MAX_STORED_PARTIAL_TAIL_BYTES = HOST_HELPER_MAX_READ_BYTES
HOST_CAPTURE_MAX_STORED_PARTIAL_TAIL_BASE64_BYTES = 4 * (
    (HOST_CAPTURE_MAX_STORED_PARTIAL_TAIL_BYTES + 2) // 3
)
HOST_CAPTURE_MAX_RECORD_BYTES = 65_536
HOST_CAPTURE_MAX_BATCH_RECORDS = 64
HOST_CAPTURE_MAX_WINDOW_RECORDS = 1_024
HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS = 4_096
HOST_CAPTURE_MAX_PENDING_RANGES = 128
HOST_CAPTURE_MAX_ASSEMBLY_STATE_BYTES = 1_048_576
HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES = 65_536
# Source adapters must still opt into streaming assembly with their own smaller
# bound. This SDK ceiling does not change the default one-window behavior.
HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_BYTES = 8_388_608
HOST_CAPTURE_MAX_QUARANTINE = 32
HOST_CAPTURE_MAX_SCOPE_EXCLUSIONS = 1_024
HOST_CAPTURE_MAX_RETRY_ATTEMPTS = 8


class HostCaptureState(StrEnum):
    HEALTHY = "healthy"
    WAITING_FOR_COMPLETE_RECORD = "waiting_for_complete_record"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    PERMISSION_REVOKED = "permission_revoked"
    IDENTITY_CHANGED = "identity_changed"


class SourceStructuralRecordClass(StrEnum):
    """Closed, content-free record classes permitted in failure capsules."""

    EXTERNAL_USER = "user.external"
    TOOL_RESULT_USER = "user.tool-result"
    OTHER_USER = "user.other"
    THINKING_TERMINAL = "assistant.end-turn-thinking"
    VISIBLE_TERMINAL = "assistant.end-turn-visible"
    OTHER_ASSISTANT = "assistant.other"
    STOP_HOOK_SUMMARY = "system.stop-hook-summary"
    OTHER_SYSTEM = "system.other"
    ATTACHMENT = "attachment"
    PROGRESS = "progress"
    QUEUE_OPERATION = "queue-operation"
    OTHER = "other"


class SourceFailureCapsule(FrozenContractModel):
    """Bounded topology-only diagnostics; source values are never admitted."""

    capsule_version: Literal[1] = 1
    record_index: int = Field(ge=0)
    byte_offset: int = Field(ge=0)
    record_classification: SourceStructuralRecordClass
    parent_in_pending_records: bool
    parent_in_history_records: bool
    parent_in_parent_turns: bool
    pending_record_count: int = Field(ge=0, le=HOST_CAPTURE_MAX_WINDOW_RECORDS)
    history_record_count: int = Field(
        ge=0, le=HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS
    )
    lineage_map_count: int = Field(ge=0, le=HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS)
    logical_terminal_pending: bool
    active_turn_present: bool
    assembly_state_version: Literal[2, 3, 4]


class ScopeExclusionReason(StrEnum):
    OFF_DATE = "off_date"
    MISSING_SOURCE_TIME = "missing_source_time"
    CROSS_MIDNIGHT = "cross_midnight"


class ScopeExclusionDispositionKind(StrEnum):
    FINALIZED_TURN = "finalized_turn"
    SCOPE_START = "scope_start"


class QuarantineEntry(FrozenContractModel):
    file_identity: FileIdentity
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    diagnostic_code: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    occurrences: int = Field(default=1, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def require_ordered_offsets(self) -> QuarantineEntry:
        if self.end_offset <= self.start_offset:
            raise ValueError("quarantine byte range must be non-empty")
        return self


class FinalizedScopeExclusion(FrozenContractModel):
    """Content-free reason returned instead of a bare finalized-turn filter."""

    approved_date: date
    reason: ScopeExclusionReason


class ScopeExclusionReasonCount(FrozenContractModel):
    reason: ScopeExclusionReason
    count: int = Field(ge=1, le=HOST_CAPTURE_MAX_SCOPE_EXCLUSIONS)


class ScopeExclusionEntry(FrozenContractModel):
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    file_identity: FileIdentity
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=1)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    approved_date: date
    reason: ScopeExclusionReason
    disposition_kind: ScopeExclusionDispositionKind = (
        ScopeExclusionDispositionKind.FINALIZED_TURN
    )
    excluded_logical_turn_count: int | None = Field(default=1, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def require_ordered_ranges(self) -> ScopeExclusionEntry:
        if self.end_offset <= self.start_offset:
            raise ValueError("scope exclusion byte range must be non-empty")
        if self.end_record_index <= self.start_record_index:
            raise ValueError("scope exclusion record range must be non-empty")
        return self


class PendingAssemblyRecord(FrozenContractModel):
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    record_index: int = Field(ge=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_ordered_offsets(self) -> PendingAssemblyRecord:
        if self.end_offset <= self.start_offset:
            raise ValueError("pending assembly record range must be non-empty")
        return self

    @classmethod
    def from_framed(cls, record: FramedSourceRecord) -> PendingAssemblyRecord:
        return cls(
            start_offset=record.start_offset,
            end_offset=record.end_offset,
            record_index=record.record_index,
            source_bytes_sha256=record.source_bytes_sha256,
        )

    def to_framed(self) -> FramedSourceRecord:
        return FramedSourceRecord(
            start_offset=self.start_offset,
            end_offset=self.end_offset,
            framed_bytes=b"",
            content_bytes=b"",
            oversized=False,
            record_index=self.record_index,
            framed_sha256=self.source_bytes_sha256,
        )


class PendingAssemblyRange(FrozenContractModel):
    """One independently verifiable acquisition range for cursor v2."""

    range_version: Literal[1] = 1
    file_identity: FileIdentity
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=1)
    byte_count: int = Field(ge=1, le=HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_BYTES)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_ordered_ranges(self) -> PendingAssemblyRange:
        if self.end_offset <= self.start_offset:
            raise ValueError("pending assembly range bytes must be non-empty")
        if self.end_record_index <= self.start_record_index:
            raise ValueError("pending assembly range records must be non-empty")
        if self.byte_count != self.end_offset - self.start_offset:
            raise ValueError("pending assembly range byte count must match its span")
        return self

    @classmethod
    def from_records(
        cls,
        records: tuple[FramedSourceRecord, ...],
        *,
        file_identity: FileIdentity,
    ) -> PendingAssemblyRange:
        if not records:
            raise ValueError("pending assembly range requires records")
        for previous, current in zip(records, records[1:], strict=False):
            if previous.end_offset != current.start_offset:
                raise ValueError("pending assembly records must be byte contiguous")
            if previous.record_index + 1 != current.record_index:
                raise ValueError("pending assembly records must be index contiguous")
        source_bytes = b"".join(item.framed_bytes for item in records)
        return cls(
            file_identity=file_identity,
            start_offset=records[0].start_offset,
            end_offset=records[-1].end_offset,
            start_record_index=records[0].record_index,
            end_record_index=records[-1].record_index + 1,
            byte_count=len(source_bytes),
            source_bytes_sha256=hashlib.sha256(source_bytes).hexdigest(),
        )


class VerifiedAssemblySpan(FrozenContractModel):
    """Content-free binding passed to SDK-v4 compact envelope builders."""

    span_version: Literal[1] = 1
    file_identity: FileIdentity
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    start_record_index: int = Field(ge=0)
    end_record_index: int = Field(ge=1)
    byte_count: int = Field(ge=1, le=HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_BYTES)
    record_count: int = Field(ge=1, le=HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS)
    range_count: int = Field(ge=1, le=HOST_CAPTURE_MAX_PENDING_RANGES)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_ordered_span(self) -> VerifiedAssemblySpan:
        if self.end_offset <= self.start_offset:
            raise ValueError("verified assembly span bytes must be non-empty")
        if self.end_record_index <= self.start_record_index:
            raise ValueError("verified assembly span records must be non-empty")
        if self.byte_count != self.end_offset - self.start_offset:
            raise ValueError("verified assembly byte count must match its span")
        if self.record_count != self.end_record_index - self.start_record_index:
            raise ValueError("verified assembly record count must match its span")
        return self


class ProposedSourceCursor(FrozenContractModel):
    proposal_version: Literal[1] = 1
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    file_identity: FileIdentity
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    envelope_hash: str = Field(pattern=SHA256_PATTERN)
    expected_checkpoint_sequence: int = Field(ge=0)
    next_checkpoint_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def require_contiguous_proposal(self) -> ProposedSourceCursor:
        if self.end_offset <= self.start_offset:
            raise ValueError("proposed source cursor must advance bytes")
        if self.next_checkpoint_sequence != self.expected_checkpoint_sequence + 1:
            raise ValueError("proposed checkpoint must advance exactly once")
        return self

    @property
    def canonical_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", warnings="error"))


class DurableCursorAcknowledgement(FrozenContractModel):
    proposal: ProposedSourceCursor
    operation_id: str = Field(min_length=1, max_length=512)
    receipt_request_hash: str = Field(pattern=SHA256_PATTERN)
    receipt_event_id: UUID
    receipt_delivery_id: UUID
    authority_principal_id: UUID
    replayed: bool


class HostCaptureCursorState(FrozenContractModel):
    cursor_version: Literal[1, 2] = 2
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    revision: int = Field(default=0, ge=0)
    file_identity: FileIdentity | None = None
    acknowledged_offset: int = Field(default=0, ge=0)
    acknowledged_record_index: int = Field(default=0, ge=0)
    checkpoint_sequence: int = Field(default=0, ge=0)
    partial_tail_base64: str = Field(
        default="", max_length=HOST_CAPTURE_MAX_STORED_PARTIAL_TAIL_BASE64_BYTES
    )
    partial_tail_sha256: str = Field(
        default=hashlib.sha256(b"").hexdigest(), pattern=SHA256_PATTERN
    )
    pending_assembly_bytes: int = Field(
        default=0, ge=0, le=HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_BYTES
    )
    pending_assembly_sha256: str = Field(
        default=hashlib.sha256(b"").hexdigest(), pattern=SHA256_PATTERN
    )
    pending_assembly_records: tuple[PendingAssemblyRecord, ...] = Field(
        default=(), max_length=HOST_CAPTURE_MAX_WINDOW_RECORDS
    )
    pending_assembly_ranges: tuple[PendingAssemblyRange, ...] = Field(
        default=(), max_length=HOST_CAPTURE_MAX_PENDING_RANGES
    )
    extractor_state_base64: str = Field(default="", max_length=87_384)
    extractor_state_sha256: str = Field(
        default=hashlib.sha256(b"").hexdigest(), pattern=SHA256_PATTERN
    )
    pending_capture_recorded_at: datetime | None = None
    source_state: HostCaptureState = HostCaptureState.HEALTHY
    observed_size: int = Field(default=0, ge=0)
    retry_attempts: int = Field(default=0, ge=0, le=HOST_CAPTURE_MAX_RETRY_ATTEMPTS)
    diagnostic_code: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    quarantine: tuple[QuarantineEntry, ...] = Field(
        default=(), max_length=HOST_CAPTURE_MAX_QUARANTINE
    )
    quarantine_overflow_count: int = Field(default=0, ge=0, le=1_000_000)
    scope_exclusions: tuple[ScopeExclusionEntry, ...] = Field(
        default=(), max_length=HOST_CAPTURE_MAX_SCOPE_EXCLUSIONS
    )
    last_acknowledgement: DurableCursorAcknowledgement | None = None

    @model_validator(mode="after")
    def validate_cursor_state(self) -> HostCaptureCursorState:
        try:
            tail = base64.b64decode(self.partial_tail_base64, validate=True)
        except ValueError as exc:
            raise ValueError("partial tail must be canonical base64") from exc
        if len(tail) > HOST_CAPTURE_MAX_STORED_PARTIAL_TAIL_BYTES:
            raise ValueError("partial tail exceeds its preservation bound")
        if hashlib.sha256(tail).hexdigest() != self.partial_tail_sha256:
            raise ValueError("partial tail hash does not match its bytes")
        try:
            extractor_state = base64.b64decode(
                self.extractor_state_base64, validate=True
            )
        except ValueError as exc:
            raise ValueError("extractor state must be canonical base64") from exc
        if len(extractor_state) > HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES:
            raise ValueError("extractor continuation state exceeds its bound")
        if hashlib.sha256(extractor_state).hexdigest() != self.extractor_state_sha256:
            raise ValueError("extractor state hash does not match its bytes")
        if (
            self.pending_capture_recorded_at is not None
            and self.pending_capture_recorded_at.tzinfo is None
        ):
            raise ValueError("pending capture time must be timezone-aware")
        if self.cursor_version == 1 and self.pending_assembly_ranges:
            raise ValueError("cursor v1 cannot contain compact pending ranges")
        if self.cursor_version == 2 and self.pending_assembly_records:
            raise ValueError("cursor v2 cannot contain legacy pending records")
        if self.pending_assembly_records:
            expected_start = self.acknowledged_offset
            expected_record_index = self.acknowledged_record_index
            for record in self.pending_assembly_records:
                if record.start_offset != expected_start:
                    raise ValueError("pending assembly record bytes must be contiguous")
                if record.record_index != expected_record_index:
                    raise ValueError(
                        "pending assembly record indexes must be contiguous"
                    )
                expected_start = record.end_offset
                expected_record_index += 1
            if expected_start - self.acknowledged_offset != self.pending_assembly_bytes:
                raise ValueError("pending assembly byte count must match its records")
            expected_digest = canonical_sha256(
                [
                    item.model_dump(mode="json", warnings="error")
                    for item in self.pending_assembly_records
                ]
            )
            if self.pending_assembly_sha256 != expected_digest:
                raise ValueError(
                    "pending assembly record hash does not match its records"
                )
        elif self.pending_assembly_ranges:
            expected_start = self.acknowledged_offset
            expected_record_index = self.acknowledged_record_index
            for pending_range in self.pending_assembly_ranges:
                if pending_range.file_identity != self.file_identity:
                    raise ValueError(
                        "pending assembly range must bind the cursor file identity"
                    )
                if pending_range.start_offset != expected_start:
                    raise ValueError("pending assembly range bytes must be contiguous")
                if pending_range.start_record_index != expected_record_index:
                    raise ValueError(
                        "pending assembly range indexes must be contiguous"
                    )
                expected_start = pending_range.end_offset
                expected_record_index = pending_range.end_record_index
            if expected_start - self.acknowledged_offset != self.pending_assembly_bytes:
                raise ValueError("pending assembly byte count must match its ranges")
            if (
                expected_record_index - self.acknowledged_record_index
                > HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS
            ):
                raise ValueError("pending assembly record span exceeds its bound")
            expected_digest = canonical_sha256(
                [
                    item.model_dump(mode="json", warnings="error")
                    for item in self.pending_assembly_ranges
                ]
            )
            if self.pending_assembly_sha256 != expected_digest:
                raise ValueError(
                    "pending assembly range hash does not match its ranges"
                )
        elif not self.pending_assembly_bytes:
            if self.pending_assembly_sha256 != hashlib.sha256(b"").hexdigest():
                raise ValueError("empty pending assembly must use the empty SHA-256")
        elif self.pending_assembly_bytes > HOST_HELPER_MAX_READ_BYTES:
            raise ValueError("raw pending assembly exceeds the host read window")
        if self.acknowledged_offset > self.observed_size:
            raise ValueError("acknowledged offset cannot exceed observed source size")
        if (
            tail
            and self.pending_assembly_bytes
            and not self.pending_assembly_records
            and not self.pending_assembly_ranges
        ):
            raise ValueError("partial tail and pending assembly are mutually exclusive")
        if self.file_identity is None and (
            self.acknowledged_offset
            or tail
            or self.pending_assembly_bytes
            or self.pending_assembly_records
            or self.pending_assembly_ranges
            or extractor_state
            or self.pending_capture_recorded_at is not None
            or self.scope_exclusions
            or self.last_acknowledgement is not None
        ):
            raise ValueError("cursor progress requires stable file identity")
        exclusion_ranges: set[tuple[int, int, int, int]] = set()
        for exclusion in self.scope_exclusions:
            if exclusion.capability_id != self.capability_id:
                raise ValueError("scope exclusion must bind the cursor capability")
            if exclusion.file_identity != self.file_identity:
                raise ValueError("scope exclusion must bind the cursor file identity")
            exclusion_range = (
                exclusion.start_offset,
                exclusion.end_offset,
                exclusion.start_record_index,
                exclusion.end_record_index,
            )
            if exclusion_range in exclusion_ranges:
                raise ValueError("scope exclusion ranges must be unique")
            exclusion_ranges.add(exclusion_range)
        if self.last_acknowledgement is not None:
            proposal = self.last_acknowledgement.proposal
            if proposal.end_offset != self.acknowledged_offset:
                raise ValueError("last receipt must bind the acknowledged byte offset")
            if proposal.next_checkpoint_sequence != self.checkpoint_sequence:
                raise ValueError("last receipt must bind the checkpoint sequence")
            if proposal.file_identity != self.file_identity:
                raise ValueError("last receipt must bind the current file identity")
        return self

    @property
    def pending_end_offset(self) -> int:
        if self.pending_assembly_ranges:
            return self.pending_assembly_ranges[-1].end_offset
        if self.pending_assembly_records:
            return self.pending_assembly_records[-1].end_offset
        return self.acknowledged_offset

    @property
    def pending_end_record_index(self) -> int:
        if self.pending_assembly_ranges:
            return self.pending_assembly_ranges[-1].end_record_index
        if self.pending_assembly_records:
            return self.pending_assembly_records[-1].record_index + 1
        return self.acknowledged_record_index

    @property
    def partial_tail(self) -> bytes:
        return base64.b64decode(self.partial_tail_base64, validate=True)

    @property
    def extractor_state(self) -> bytes:
        return base64.b64decode(self.extractor_state_base64, validate=True)

    @classmethod
    def initial(cls, capability_id: str) -> HostCaptureCursorState:
        return cls(capability_id=capability_id)


class HostCaptureHealth(FrozenContractModel):
    health_version: Literal[1] = 1
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    source_state: HostCaptureState
    file_identity: FileIdentity | None
    acknowledged_offset: int = Field(ge=0)
    acknowledged_record_index: int = Field(ge=0)
    checkpoint_sequence: int = Field(ge=0)
    pending_partial_tail_bytes: int = Field(
        ge=0, le=HOST_CAPTURE_MAX_STORED_PARTIAL_TAIL_BYTES
    )
    pending_assembly_bytes: int = Field(
        ge=0, le=HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_BYTES
    )
    observed_size: int = Field(ge=0)
    lag_bytes: int = Field(ge=0)
    retry_attempts: int = Field(ge=0, le=HOST_CAPTURE_MAX_RETRY_ATTEMPTS)
    quarantine_count: int = Field(ge=0, le=HOST_CAPTURE_MAX_QUARANTINE)
    quarantine_overflow_count: int = Field(ge=0)
    scope_exclusion_count: int = Field(
        default=0, ge=0, le=HOST_CAPTURE_MAX_SCOPE_EXCLUSIONS
    )
    scope_exclusion_reason_counts: tuple[ScopeExclusionReasonCount, ...] = Field(
        default=(), max_length=3
    )
    diagnostic_code: str | None = None
    failure_capsule: SourceFailureCapsule | None = None
    last_acknowledgement: DurableCursorAcknowledgement | None = None

    @model_validator(mode="after")
    def validate_scope_exclusion_counts(self) -> HostCaptureHealth:
        reasons = [item.reason for item in self.scope_exclusion_reason_counts]
        if len(set(reasons)) != len(reasons):
            raise ValueError("scope exclusion reason counts must be unique")
        if sum(item.count for item in self.scope_exclusion_reason_counts) != (
            self.scope_exclusion_count
        ):
            raise ValueError("scope exclusion reason counts must equal their total")
        return self

    def scope_exclusion_count_for(self, reason: ScopeExclusionReason) -> int:
        return next(
            (
                item.count
                for item in self.scope_exclusion_reason_counts
                if item.reason == reason
            ),
            0,
        )


@dataclass(frozen=True)
class FramedSourceRecord:
    start_offset: int
    end_offset: int
    framed_bytes: bytes
    content_bytes: bytes
    oversized: bool
    record_index: int = 0
    framed_sha256: str | None = None

    @property
    def source_bytes_sha256(self) -> str:
        return self.framed_sha256 or hashlib.sha256(self.framed_bytes).hexdigest()


@dataclass(frozen=True)
class FramedRecordBatch:
    records: tuple[FramedSourceRecord, ...]
    partial_tail: bytes
    has_unprocessed_records: bool
    partial_tail_overflow: bool


class NewlineRecordFramer:
    """Frame complete newline-delimited records without interpreting source content."""

    def __init__(
        self,
        *,
        max_record_bytes: int = HOST_CAPTURE_MAX_RECORD_BYTES,
        max_partial_tail_bytes: int = HOST_CAPTURE_MAX_PARTIAL_TAIL_BYTES,
        max_batch_records: int = HOST_CAPTURE_MAX_BATCH_RECORDS,
    ) -> None:
        if not 1 <= max_record_bytes <= HOST_HELPER_MAX_READ_BYTES:
            raise ValueError("record bound must fit the host read window")
        if not 1 <= max_partial_tail_bytes <= HOST_HELPER_MAX_READ_BYTES:
            raise ValueError("partial-tail bound must fit the host read window")
        if not 1 <= max_batch_records <= HOST_CAPTURE_MAX_WINDOW_RECORDS:
            raise ValueError("record-count bound exceeds the SDK maximum")
        self.max_record_bytes = max_record_bytes
        self.max_partial_tail_bytes = max_partial_tail_bytes
        self.max_batch_records = max_batch_records

    def frame(
        self,
        payload: bytes,
        *,
        base_offset: int,
        base_record_index: int = 0,
    ) -> FramedRecordBatch:
        if len(payload) > HOST_HELPER_MAX_READ_BYTES + self.max_partial_tail_bytes:
            raise ValueError("framing input exceeds the bounded acquisition window")
        records: list[FramedSourceRecord] = []
        cursor = 0
        while len(records) < self.max_batch_records:
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
                    oversized=len(framed) > self.max_record_bytes,
                    record_index=base_record_index + len(records),
                )
            )
            cursor = end
        has_unprocessed = payload.find(b"\n", cursor) >= 0
        partial = b"" if has_unprocessed else payload[cursor:]
        return FramedRecordBatch(
            records=tuple(records),
            partial_tail=(
                partial if len(partial) <= self.max_partial_tail_bytes else b""
            ),
            has_unprocessed_records=has_unprocessed,
            partial_tail_overflow=len(partial) > self.max_partial_tail_bytes,
        )


class SourceRecordParseError(ValueError):
    def __init__(
        self,
        diagnostic_code: str,
        *,
        failure_capsule: SourceFailureCapsule | None = None,
    ) -> None:
        super().__init__(diagnostic_code)
        self.diagnostic_code = diagnostic_code
        self.failure_capsule = failure_capsule


class CursorWriteConflict(RuntimeError):
    pass


class ReceiptBindingError(RuntimeError):
    pass


class HostAcquisitionPort(Protocol):
    def read(self, request: HostReadRequest) -> HostReadResponse: ...


class SourceRecordExtractor[TExtracted](Protocol):
    def extract(self, record: FramedSourceRecord) -> TExtracted: ...


@dataclass(frozen=True)
class SourceAssemblyStep[TExtracted]:
    """One pure assembly transition over an already-opened source record."""

    state: bytes
    finalized: TExtracted | None = None


@dataclass(frozen=True)
class SourceScopeSeekResult:
    file_identity: FileIdentity
    observed_size: int
    start_offset: int
    end_offset: int
    start_record_index: int
    end_record_index: int
    source_bytes_sha256: str
    approved_date: date
    reason: ScopeExclusionReason
    excluded_logical_turn_count: int | None = None

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("scope seek byte range is invalid")
        if (
            self.start_record_index < 0
            or self.end_record_index < self.start_record_index
        ):
            raise ValueError("scope seek record range is invalid")
        if self.observed_size < self.end_offset:
            raise ValueError("scope seek range exceeds the observed source")
        if self.excluded_logical_turn_count is not None and not (
            0 <= self.excluded_logical_turn_count <= 1_000_000
        ):
            raise ValueError("scope seek logical-turn count is invalid")
        if len(self.source_bytes_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.source_bytes_sha256
        ):
            raise ValueError("scope seek hash is invalid")


class SourceScopeSeeker(Protocol):
    def seek(
        self,
        *,
        acquisition: HostAcquisitionPort,
        capability_id: str,
        capability_credential: str,
        expected_identity: FileIdentity | None,
        start_offset: int,
        start_record_index: int,
        read_size: int,
    ) -> SourceScopeSeekResult: ...


class SourceRecordAssembler[TExtracted](Protocol):
    def advance(
        self,
        record: FramedSourceRecord,
        *,
        state: bytes,
    ) -> SourceAssemblyStep[TExtracted]: ...


class ScopeAwareSourceRecordAssembler[TExtracted](
    SourceRecordAssembler[TExtracted], Protocol
):
    def begin_after_scope_start(self, *, state: bytes) -> bytes: ...


class CaptureEnvelopeBuilder[TExtracted](Protocol):
    def build(
        self,
        extracted: TExtracted,
        *,
        record: FramedSourceRecord,
        file_identity: FileIdentity,
        recorded_at: datetime,
    ) -> CaptureEnvelope: ...


class AssembledCaptureEnvelopeBuilder[TExtracted](Protocol):
    def build(
        self,
        extracted: TExtracted,
        *,
        records: tuple[FramedSourceRecord, ...],
        file_identity: FileIdentity,
        recorded_at: datetime,
    ) -> CaptureEnvelope: ...


class CompactAssembledCaptureEnvelopeBuilder[TExtracted](Protocol):
    def build_from_source_span(
        self,
        extracted: TExtracted,
        *,
        source_span: VerifiedAssemblySpan,
        recorded_at: datetime,
    ) -> CaptureEnvelope: ...


class CaptureCursorStore(Protocol):
    def load(self, capability_id: str) -> HostCaptureCursorState | None: ...

    def commit(
        self,
        *,
        expected_revision: int,
        state: HostCaptureCursorState,
    ) -> None: ...


class HostCaptureLoop[TExtracted]:
    """One bounded acquire, parse, envelope, submit, and receipt-ack cycle."""

    def __init__(
        self,
        *,
        acquisition: HostAcquisitionPort,
        extractor: SourceRecordExtractor[TExtracted] | None = None,
        envelope_builder: CaptureEnvelopeBuilder[TExtracted] | None = None,
        assembler: SourceRecordAssembler[TExtracted] | None = None,
        assembled_envelope_builder: AssembledCaptureEnvelopeBuilder[TExtracted]
        | None = None,
        submission: CaptureSubmissionPort,
        cursor_store: CaptureCursorStore,
        capability_id: str,
        capability_credential: str,
        checkpoint_key: str,
        read_size: int = HOST_HELPER_MAX_READ_BYTES,
        framer: NewlineRecordFramer | None = None,
        finalized_scope_exclusion: Callable[
            [TExtracted], FinalizedScopeExclusion | None
        ]
        | None = None,
        scope_seeker: SourceScopeSeeker | None = None,
        max_streaming_assembly_bytes: int | None = None,
    ) -> None:
        configured_framer = framer or NewlineRecordFramer()
        if (
            not configured_framer.max_record_bytes
            <= read_size
            <= HOST_HELPER_MAX_READ_BYTES
        ):
            raise ValueError(
                "host capture read_size must hold one complete valid record"
            )
        single_record_mode = extractor is not None or envelope_builder is not None
        assembly_mode = assembler is not None or assembled_envelope_builder is not None
        if single_record_mode == assembly_mode:
            raise ValueError("configure exactly one source extraction mode")
        if single_record_mode and (extractor is None or envelope_builder is None):
            raise ValueError("single-record extraction requires its envelope builder")
        if assembly_mode and (assembler is None or assembled_envelope_builder is None):
            raise ValueError("assembly extraction requires its envelope builder")
        if max_streaming_assembly_bytes is not None and (
            not assembly_mode
            or not 1
            <= max_streaming_assembly_bytes
            <= HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_BYTES
        ):
            raise ValueError("streaming assembly bound is invalid")
        if max_streaming_assembly_bytes is not None and not callable(
            getattr(assembled_envelope_builder, "build_from_source_span", None)
        ):
            raise ValueError(
                "streaming assembly requires an SDK-v4 compact envelope builder"
            )
        if scope_seeker is not None and not assembly_mode:
            raise ValueError("scope seeking requires assembly extraction")
        self._acquisition = acquisition
        self._extractor = extractor
        self._envelope_builder = envelope_builder
        self._assembler = assembler
        self._assembled_envelope_builder = assembled_envelope_builder
        self._submission = submission
        self._cursor_store = cursor_store
        self._capability_id = capability_id
        self._capability_credential = capability_credential
        self._checkpoint_key = checkpoint_key
        self._read_size = read_size
        self._framer = configured_framer
        self._finalized_scope_exclusion = finalized_scope_exclusion
        self._scope_seeker = scope_seeker
        self._max_streaming_assembly_bytes = max_streaming_assembly_bytes

    def run_once(self, *, recorded_at: datetime) -> HostCaptureHealth:
        if recorded_at.tzinfo is None:
            raise ValueError("host capture recorded_at must be timezone-aware")
        state = self._load_state()
        if self._max_streaming_assembly_bytes is not None:
            try:
                state = self._upgrade_streaming_cursor(state)
                if state.pending_assembly_ranges:
                    self._source_ranges_sha256(state.pending_assembly_ranges)
            except ReceiptBindingError:
                previous_revision = state.revision
                state = self._updated(
                    state,
                    source_state=HostCaptureState.IDENTITY_CHANGED,
                    observed_size=state.observed_size,
                    retry_attempts=min(
                        state.retry_attempts + 1, HOST_CAPTURE_MAX_RETRY_ATTEMPTS
                    ),
                    diagnostic_code="host.pending-range-drift",
                )
                self._commit(previous_revision=previous_revision, state=state)
                return self._health(state)
        if (
            self._scope_seeker is not None
            and state.acknowledged_offset == 0
            and state.acknowledged_record_index == 0
            and state.checkpoint_sequence == 0
            and not state.scope_exclusions
        ):
            try:
                state = self._seek_scope_start(state)
            except SourceRecordParseError as exc:
                previous_revision = state.revision
                state = self._updated(
                    state,
                    source_state=HostCaptureState.DEGRADED,
                    retry_attempts=min(
                        state.retry_attempts + 1, HOST_CAPTURE_MAX_RETRY_ATTEMPTS
                    ),
                    diagnostic_code=exc.diagnostic_code,
                )
                self._commit(previous_revision=previous_revision, state=state)
                return self._health(state, failure_capsule=exc.failure_capsule)
        read_offset = state.acknowledged_offset
        if self._max_streaming_assembly_bytes is not None:
            read_offset = state.pending_end_offset
        request = HostReadRequest(
            request_id=uuid4(),
            capability_id=self._capability_id,
            capability_credential=self._capability_credential,
            expected_identity=state.file_identity,
            byte_offset=read_offset,
            max_bytes=self._read_size,
        )
        response = self._acquisition.read(request)
        if response.request_id != request.request_id:
            raise ReceiptBindingError("host response request identity mismatch")
        if response.start_offset != request.byte_offset:
            raise ReceiptBindingError("host response byte offset mismatch")
        if len(response.data) > request.max_bytes:
            raise ReceiptBindingError("host response exceeds the requested byte bound")
        if response.status not in {HostReadStatus.DATA, HostReadStatus.END_OF_FILE}:
            state = self._commit_host_failure(state, response)
            return self._health(state)
        assert response.file_identity is not None
        if (
            state.file_identity is not None
            and state.file_identity != response.file_identity
        ):
            state = self._updated(
                state,
                source_state=HostCaptureState.IDENTITY_CHANGED,
                diagnostic_code="host.identity-changed",
                observed_size=response.observed_size,
            )
            self._commit(previous_revision=state.revision - 1, state=state)
            return self._health(state)

        source_identity = response.file_identity
        capture_recorded_at = state.pending_capture_recorded_at or recorded_at
        if (
            state.pending_assembly_bytes
            and not state.pending_assembly_records
            and not state.pending_assembly_ranges
        ):
            pending_prefix = response.data[: state.pending_assembly_bytes]
            if (
                len(pending_prefix) != state.pending_assembly_bytes
                or hashlib.sha256(pending_prefix).hexdigest()
                != state.pending_assembly_sha256
            ):
                previous_revision = state.revision
                state = self._updated(
                    state,
                    source_state=HostCaptureState.IDENTITY_CHANGED,
                    observed_size=max(
                        response.observed_size, state.acknowledged_offset
                    ),
                    retry_attempts=min(
                        state.retry_attempts + 1, HOST_CAPTURE_MAX_RETRY_ATTEMPTS
                    ),
                    diagnostic_code="host.pending-assembly-drift",
                )
                self._commit(previous_revision=previous_revision, state=state)
                return self._health(state)
        if state.partial_tail and not response.data.startswith(state.partial_tail):
            previous_revision = state.revision
            state = self._updated(
                state,
                source_state=HostCaptureState.IDENTITY_CHANGED,
                observed_size=max(response.observed_size, state.acknowledged_offset),
                retry_attempts=min(
                    state.retry_attempts + 1, HOST_CAPTURE_MAX_RETRY_ATTEMPTS
                ),
                diagnostic_code="host.partial-tail-drift",
            )
            self._commit(previous_revision=previous_revision, state=state)
            return self._health(state)
        combined = response.data
        batch = self._framer.frame(
            combined,
            base_offset=request.byte_offset,
            base_record_index=(
                state.pending_end_record_index
                if self._max_streaming_assembly_bytes is not None
                else state.acknowledged_record_index
                + len(state.pending_assembly_records)
            ),
        )
        if batch.partial_tail_overflow:
            state = self._quarantine(
                state,
                file_identity=source_identity,
                start_offset=request.byte_offset,
                end_offset=request.byte_offset + len(combined),
                source_bytes=combined,
                diagnostic_code="framing.partial-tail-limit",
                observed_size=response.observed_size,
            )
            return self._health(state)

        assembly_state = state.extractor_state
        assembly_records: list[FramedSourceRecord] = (
            []
            if self._max_streaming_assembly_bytes is not None
            else [item.to_framed() for item in state.pending_assembly_records]
        )
        assembly_ranges = list(state.pending_assembly_ranges)
        committed_terminal_in_batch = False
        for record in batch.records:
            if record.oversized:
                state = self._quarantine(
                    state,
                    file_identity=source_identity,
                    start_offset=record.start_offset,
                    end_offset=record.end_offset,
                    source_bytes=record.framed_bytes,
                    diagnostic_code="framing.record-size-limit",
                    observed_size=response.observed_size,
                )
                return self._health(state)
            assembly_records.append(record)
            try:
                finalized: TExtracted | None
                if self._assembler is None:
                    assert self._extractor is not None
                    finalized = self._extractor.extract(record)
                else:
                    step = self._assembler.advance(record, state=assembly_state)
                    if len(step.state) > HOST_CAPTURE_MAX_ASSEMBLY_STATE_BYTES:
                        raise SourceRecordParseError("assembly.state-size-limit")
                    assembly_state = step.state
                    finalized = step.finalized
            except SourceRecordParseError as exc:
                if self._max_streaming_assembly_bytes is not None:
                    candidate_ranges = self._ranges_with_records(
                        tuple(assembly_ranges),
                        tuple(assembly_records),
                        file_identity=source_identity,
                    )
                    source_bytes = b""
                    source_digest = self._source_ranges_sha256(candidate_ranges)
                    quarantine_start = candidate_ranges[0].start_offset
                else:
                    source_bytes = b"".join(
                        item.framed_bytes for item in assembly_records
                    )
                    source_digest = hashlib.sha256(source_bytes).hexdigest()
                    quarantine_start = assembly_records[0].start_offset
                state = self._quarantine(
                    state,
                    file_identity=source_identity,
                    start_offset=quarantine_start,
                    end_offset=record.end_offset,
                    source_bytes=source_bytes,
                    source_bytes_sha256=source_digest,
                    diagnostic_code=exc.diagnostic_code,
                    observed_size=response.observed_size,
                )
                return self._health(state, failure_capsule=exc.failure_capsule)
            if finalized is None:
                continue
            if len(assembly_state) > HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES:
                if self._max_streaming_assembly_bytes is not None:
                    candidate_ranges = self._ranges_with_records(
                        tuple(assembly_ranges),
                        tuple(assembly_records),
                        file_identity=source_identity,
                    )
                    source_bytes = b""
                    source_digest = self._source_ranges_sha256(candidate_ranges)
                    quarantine_start = candidate_ranges[0].start_offset
                else:
                    source_bytes = b"".join(
                        item.framed_bytes for item in assembly_records
                    )
                    source_digest = hashlib.sha256(source_bytes).hexdigest()
                    quarantine_start = assembly_records[0].start_offset
                state = self._quarantine(
                    state,
                    file_identity=source_identity,
                    start_offset=quarantine_start,
                    end_offset=record.end_offset,
                    source_bytes=source_bytes,
                    source_bytes_sha256=source_digest,
                    diagnostic_code="assembly.continuation-state-size-limit",
                    observed_size=response.observed_size,
                )
                return self._health(state)
            verified_span: VerifiedAssemblySpan | None = None
            if self._max_streaming_assembly_bytes is not None:
                candidate_ranges = self._ranges_with_records(
                    tuple(assembly_ranges),
                    tuple(assembly_records),
                    file_identity=source_identity,
                )
                limit_diagnostic = self._streaming_limit_diagnostic(candidate_ranges)
                if limit_diagnostic is not None:
                    source_digest = self._source_ranges_sha256(candidate_ranges)
                    state = self._quarantine(
                        state,
                        file_identity=source_identity,
                        start_offset=candidate_ranges[0].start_offset,
                        end_offset=candidate_ranges[-1].end_offset,
                        source_bytes=b"",
                        source_bytes_sha256=source_digest,
                        diagnostic_code=limit_diagnostic,
                        observed_size=response.observed_size,
                    )
                    return self._health(state)
                source_digest = self._source_ranges_sha256(candidate_ranges)
                verified_span = self._verified_span(
                    candidate_ranges, source_bytes_sha256=source_digest
                )
                assembly_start_offset = verified_span.start_offset
                assembly_start_record_index = verified_span.start_record_index
            else:
                source_digest = self._assembly_source_sha256(
                    tuple(assembly_records), file_identity=source_identity
                )
                assembly_start_offset = assembly_records[0].start_offset
                assembly_start_record_index = assembly_records[0].record_index
            scope_exclusion = (
                self._finalized_scope_exclusion(finalized)
                if self._finalized_scope_exclusion is not None
                else None
            )
            if scope_exclusion is not None:
                if not isinstance(scope_exclusion, FinalizedScopeExclusion):
                    raise TypeError(
                        "finalized scope exclusion must be a structured disposition"
                    )
                if len(state.scope_exclusions) >= HOST_CAPTURE_MAX_SCOPE_EXCLUSIONS:
                    raise RuntimeError("scope exclusion capacity exceeded")
                exclusion = ScopeExclusionEntry(
                    capability_id=self._capability_id,
                    file_identity=source_identity,
                    start_offset=assembly_start_offset,
                    end_offset=record.end_offset,
                    start_record_index=assembly_start_record_index,
                    end_record_index=record.record_index + 1,
                    source_bytes_sha256=source_digest,
                    approved_date=scope_exclusion.approved_date,
                    reason=scope_exclusion.reason,
                )
                previous_revision = state.revision
                state = self._updated(
                    state,
                    file_identity=source_identity,
                    acknowledged_offset=record.end_offset,
                    acknowledged_record_index=record.record_index + 1,
                    partial_tail=b"",
                    pending_assembly=b"",
                    pending_assembly_records=(),
                    pending_assembly_ranges=(),
                    extractor_state=assembly_state,
                    source_state=HostCaptureState.HEALTHY,
                    observed_size=response.observed_size,
                    retry_attempts=0,
                    diagnostic_code=None,
                    scope_exclusions=(*state.scope_exclusions, exclusion),
                    clear_last_acknowledgement=True,
                    clear_pending_capture_recorded_at=True,
                )
                self._commit(previous_revision=previous_revision, state=state)
                committed_terminal_in_batch = True
                capture_recorded_at = recorded_at
                assembly_records.clear()
                assembly_ranges.clear()
                continue
            if state.pending_capture_recorded_at is None:
                previous_revision = state.revision
                state = self._updated(
                    state,
                    file_identity=source_identity,
                    pending_capture_recorded_at=capture_recorded_at,
                    observed_size=response.observed_size,
                )
                self._commit(previous_revision=previous_revision, state=state)
            if self._assembler is None:
                assert self._envelope_builder is not None
                envelope = self._envelope_builder.build(
                    finalized,
                    record=record,
                    file_identity=source_identity,
                    recorded_at=capture_recorded_at,
                )
            else:
                assert self._assembled_envelope_builder is not None
                if verified_span is not None:
                    compact_build = getattr(
                        self._assembled_envelope_builder,
                        "build_from_source_span",
                    )
                    envelope = compact_build(
                        finalized,
                        source_span=verified_span,
                        recorded_at=capture_recorded_at,
                    )
                else:
                    envelope = self._assembled_envelope_builder.build(
                        finalized,
                        records=tuple(assembly_records),
                        file_identity=source_identity,
                        recorded_at=capture_recorded_at,
                    )
            proposal = ProposedSourceCursor(
                capability_id=self._capability_id,
                file_identity=source_identity,
                start_offset=assembly_start_offset,
                end_offset=record.end_offset,
                source_bytes_sha256=source_digest,
                envelope_hash=envelope.canonical_hash,
                expected_checkpoint_sequence=state.checkpoint_sequence,
                next_checkpoint_sequence=state.checkpoint_sequence + 1,
            )
            operation_id = f"host-capture.v1:{self._capability_id}:{envelope.event_id}"
            receipt = self._submission.submit(
                envelope,
                idempotency_key=operation_id,
                cursor=ConnectorCursor(
                    checkpoint_key=self._checkpoint_key,
                    expected_sequence=proposal.expected_checkpoint_sequence,
                    next_sequence=proposal.next_checkpoint_sequence,
                    checkpoint_hash=proposal.canonical_hash,
                ),
                recorded_at=recorded_at,
            )
            self._validate_receipt(
                receipt,
                operation_id=operation_id,
                proposal=proposal,
                event_id=envelope.event_id,
            )
            acknowledgement = DurableCursorAcknowledgement(
                proposal=proposal,
                operation_id=receipt.operation_id,
                receipt_request_hash=receipt.request_hash,
                receipt_event_id=envelope.event_id,
                receipt_delivery_id=receipt.delivery_id,
                authority_principal_id=receipt.authority_principal_id,
                replayed=receipt.replayed,
            )
            previous_revision = state.revision
            state = self._updated(
                state,
                file_identity=source_identity,
                acknowledged_offset=record.end_offset,
                acknowledged_record_index=record.record_index + 1,
                checkpoint_sequence=proposal.next_checkpoint_sequence,
                partial_tail=b"",
                pending_assembly=b"",
                pending_assembly_records=(),
                pending_assembly_ranges=(),
                extractor_state=assembly_state,
                source_state=HostCaptureState.HEALTHY,
                observed_size=response.observed_size,
                retry_attempts=0,
                diagnostic_code=None,
                last_acknowledgement=acknowledgement,
                clear_pending_capture_recorded_at=True,
            )
            self._commit(previous_revision=previous_revision, state=state)
            committed_terminal_in_batch = True
            # A pinned time belongs only to the exact envelope being replayed.
            # Any later envelope discovered in this same poll is new capture work.
            capture_recorded_at = recorded_at
            assembly_records.clear()
            assembly_ranges.clear()

        if (
            self._assembler is not None
            and assembly_records
            and not committed_terminal_in_batch
            and self._max_streaming_assembly_bytes is None
            and len(response.data) == request.max_bytes
            and response.observed_size > request.byte_offset + len(response.data)
        ):
            state = self._quarantine(
                state,
                file_identity=source_identity,
                start_offset=request.byte_offset,
                end_offset=request.byte_offset + len(combined),
                source_bytes=combined,
                diagnostic_code="assembly.acquisition-window-limit",
                observed_size=response.observed_size,
            )
            return self._health(state)

        if (
            self._assembler is not None
            and assembly_records
            and batch.has_unprocessed_records
            and not committed_terminal_in_batch
            and self._max_streaming_assembly_bytes is None
        ):
            source_bytes = b"".join(item.framed_bytes for item in assembly_records)
            state = self._quarantine(
                state,
                file_identity=source_identity,
                start_offset=assembly_records[0].start_offset,
                end_offset=assembly_records[-1].end_offset,
                source_bytes=source_bytes,
                diagnostic_code="assembly.record-count-limit",
                observed_size=response.observed_size,
            )
            return self._health(state)

        if (
            self._assembler is not None
            and self._max_streaming_assembly_bytes is not None
        ):
            pending_ranges = self._ranges_with_records(
                tuple(assembly_ranges),
                tuple(assembly_records),
                file_identity=source_identity,
            )
            pending_bytes = (
                pending_ranges[-1].end_offset - state.acknowledged_offset
                if pending_ranges
                else 0
            )
            limit_diagnostic = self._streaming_limit_diagnostic(pending_ranges)
            if limit_diagnostic is not None:
                source_digest = self._source_ranges_sha256(pending_ranges)
                state = self._quarantine(
                    state,
                    file_identity=source_identity,
                    start_offset=pending_ranges[0].start_offset,
                    end_offset=pending_ranges[-1].end_offset,
                    source_bytes=b"",
                    source_bytes_sha256=source_digest,
                    diagnostic_code=limit_diagnostic,
                    observed_size=response.observed_size,
                )
                return self._health(state)
            if len(assembly_state) > HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES:
                source_digest = self._source_ranges_sha256(pending_ranges)
                state = self._quarantine(
                    state,
                    file_identity=source_identity,
                    start_offset=pending_ranges[0].start_offset,
                    end_offset=pending_ranges[-1].end_offset,
                    source_bytes=b"",
                    source_bytes_sha256=source_digest,
                    diagnostic_code="assembly.continuation-state-size-limit",
                    observed_size=response.observed_size,
                )
                return self._health(state)
            desired_state = (
                HostCaptureState.WAITING_FOR_COMPLETE_RECORD
                if pending_ranges or batch.partial_tail or batch.has_unprocessed_records
                else HostCaptureState.HEALTHY
            )
            pending_digest = (
                canonical_sha256(
                    [
                        item.model_dump(mode="json", warnings="error")
                        for item in pending_ranges
                    ]
                )
                if pending_ranges
                else hashlib.sha256(b"").hexdigest()
            )
            if (
                state.partial_tail != batch.partial_tail
                or state.cursor_version != 2
                or state.pending_assembly_records
                or state.pending_assembly_ranges != pending_ranges
                or state.pending_assembly_bytes != pending_bytes
                or state.pending_assembly_sha256 != pending_digest
                or state.extractor_state != assembly_state
                or state.file_identity != source_identity
                or state.source_state != desired_state
                or state.observed_size != response.observed_size
                or state.retry_attempts
                or state.diagnostic_code is not None
            ):
                previous_revision = state.revision
                state = self._updated(
                    state,
                    file_identity=source_identity,
                    partial_tail=batch.partial_tail,
                    pending_assembly_records=(),
                    pending_assembly_ranges=pending_ranges,
                    extractor_state=assembly_state,
                    source_state=desired_state,
                    observed_size=response.observed_size,
                    retry_attempts=0,
                    diagnostic_code=None,
                )
                self._commit(previous_revision=previous_revision, state=state)
            return self._health(state)

        if not batch.has_unprocessed_records or committed_terminal_in_batch:
            pending_assembly = b""
            if self._assembler is not None:
                pending_start = (
                    assembly_records[0].start_offset
                    if assembly_records
                    else state.acknowledged_offset
                )
                relative_start = pending_start - request.byte_offset
                pending_assembly = combined[relative_start:]
            desired_state = (
                HostCaptureState.WAITING_FOR_COMPLETE_RECORD
                if batch.partial_tail
                or pending_assembly
                or batch.has_unprocessed_records
                else HostCaptureState.HEALTHY
            )
            desired_partial_tail = (
                b"" if self._assembler is not None else batch.partial_tail
            )
            if (
                state.partial_tail != desired_partial_tail
                or state.pending_assembly_bytes != len(pending_assembly)
                or state.pending_assembly_sha256
                != hashlib.sha256(pending_assembly).hexdigest()
                or state.file_identity != source_identity
                or state.source_state != desired_state
                or state.observed_size != response.observed_size
                or state.retry_attempts
                or state.diagnostic_code is not None
            ):
                previous_revision = state.revision
                state = self._updated(
                    state,
                    file_identity=source_identity,
                    partial_tail=desired_partial_tail,
                    pending_assembly=pending_assembly,
                    source_state=desired_state,
                    observed_size=response.observed_size,
                    retry_attempts=0,
                    diagnostic_code=None,
                )
                self._commit(previous_revision=previous_revision, state=state)
        return self._health(state)

    def health(self) -> HostCaptureHealth:
        return self._health(self._load_state())

    def _load_state(self) -> HostCaptureCursorState:
        state = self._cursor_store.load(self._capability_id)
        return state or HostCaptureCursorState.initial(self._capability_id)

    def _seek_scope_start(
        self, state: HostCaptureCursorState
    ) -> HostCaptureCursorState:
        assert self._scope_seeker is not None
        result = self._scope_seeker.seek(
            acquisition=self._acquisition,
            capability_id=self._capability_id,
            capability_credential=self._capability_credential,
            expected_identity=state.file_identity,
            start_offset=state.acknowledged_offset,
            start_record_index=state.acknowledged_record_index,
            read_size=self._read_size,
        )
        if result.start_offset != state.acknowledged_offset:
            raise ReceiptBindingError("scope seek start offset mismatch")
        if result.start_record_index != state.acknowledged_record_index:
            raise ReceiptBindingError("scope seek start record mismatch")
        if (
            state.file_identity is not None
            and result.file_identity != state.file_identity
        ):
            raise ReceiptBindingError("scope seek file identity mismatch")
        if result.end_offset == result.start_offset:
            return state
        if result.end_record_index == result.start_record_index:
            raise ReceiptBindingError("scope seek advanced bytes without records")
        if len(state.scope_exclusions) >= HOST_CAPTURE_MAX_SCOPE_EXCLUSIONS:
            raise RuntimeError("scope exclusion capacity exceeded")
        exclusion = ScopeExclusionEntry(
            capability_id=self._capability_id,
            file_identity=result.file_identity,
            start_offset=result.start_offset,
            end_offset=result.end_offset,
            start_record_index=result.start_record_index,
            end_record_index=result.end_record_index,
            source_bytes_sha256=result.source_bytes_sha256,
            approved_date=result.approved_date,
            reason=result.reason,
            disposition_kind=ScopeExclusionDispositionKind.SCOPE_START,
            excluded_logical_turn_count=result.excluded_logical_turn_count,
        )
        assembly_state = state.extractor_state
        begin_after_scope_start = (
            getattr(self._assembler, "begin_after_scope_start", None)
            if self._assembler is not None
            else None
        )
        if callable(begin_after_scope_start):
            assembly_state = begin_after_scope_start(state=assembly_state)
        previous_revision = state.revision
        updated = self._updated(
            state,
            file_identity=result.file_identity,
            acknowledged_offset=result.end_offset,
            acknowledged_record_index=result.end_record_index,
            partial_tail=b"",
            pending_assembly=b"",
            pending_assembly_records=(),
            pending_assembly_ranges=(),
            extractor_state=assembly_state,
            source_state=HostCaptureState.HEALTHY,
            observed_size=result.observed_size,
            retry_attempts=0,
            diagnostic_code=None,
            scope_exclusions=(*state.scope_exclusions, exclusion),
            clear_last_acknowledgement=True,
            clear_pending_capture_recorded_at=True,
        )
        self._commit(previous_revision=previous_revision, state=updated)
        return updated

    def _assembly_source_sha256(
        self,
        records: tuple[FramedSourceRecord, ...],
        *,
        file_identity: FileIdentity,
    ) -> str:
        if self._max_streaming_assembly_bytes is not None:
            return self._source_range_sha256(records, file_identity=file_identity)
        return hashlib.sha256(
            b"".join(item.framed_bytes for item in records)
        ).hexdigest()

    def _upgrade_streaming_cursor(
        self, state: HostCaptureCursorState
    ) -> HostCaptureCursorState:
        if state.cursor_version == 2:
            return state
        if state.pending_assembly_records:
            if state.file_identity is None:
                raise ReceiptBindingError("legacy pending records lack file identity")
            legacy_records = tuple(
                item.to_framed() for item in state.pending_assembly_records
            )
            source_digest = self._source_range_sha256(
                legacy_records, file_identity=state.file_identity
            )
            pending_range = PendingAssemblyRange(
                file_identity=state.file_identity,
                start_offset=legacy_records[0].start_offset,
                end_offset=legacy_records[-1].end_offset,
                start_record_index=legacy_records[0].record_index,
                end_record_index=legacy_records[-1].record_index + 1,
                byte_count=(
                    legacy_records[-1].end_offset - legacy_records[0].start_offset
                ),
                source_bytes_sha256=source_digest,
            )
            previous_revision = state.revision
            state = self._updated(
                state,
                pending_assembly_records=(),
                pending_assembly_ranges=(pending_range,),
                source_state=state.source_state,
                retry_attempts=state.retry_attempts,
                diagnostic_code=state.diagnostic_code,
            )
            self._commit(previous_revision=previous_revision, state=state)
            return state
        if state.pending_assembly_bytes:
            # Cursor v1 raw-window assembly remains byte-for-byte compatible. The
            # next successful write has the bytes needed to persist cursor v2.
            return state
        previous_revision = state.revision
        state = self._updated(
            state,
            source_state=state.source_state,
            retry_attempts=state.retry_attempts,
            diagnostic_code=state.diagnostic_code,
        )
        self._commit(previous_revision=previous_revision, state=state)
        return state

    @staticmethod
    def _ranges_with_records(
        ranges: tuple[PendingAssemblyRange, ...],
        records: tuple[FramedSourceRecord, ...],
        *,
        file_identity: FileIdentity,
    ) -> tuple[PendingAssemblyRange, ...]:
        if not records:
            return ranges
        pending_range = PendingAssemblyRange.from_records(
            records, file_identity=file_identity
        )
        if ranges:
            previous = ranges[-1]
            if previous.file_identity != pending_range.file_identity:
                raise ReceiptBindingError("pending assembly range identity drift")
            if previous.end_offset != pending_range.start_offset:
                raise ReceiptBindingError("pending assembly range byte gap")
            if previous.end_record_index != pending_range.start_record_index:
                raise ReceiptBindingError("pending assembly range record gap")
        return (*ranges, pending_range)

    def _streaming_limit_diagnostic(
        self, ranges: tuple[PendingAssemblyRange, ...]
    ) -> str | None:
        if not ranges:
            return None
        if len(ranges) > HOST_CAPTURE_MAX_PENDING_RANGES:
            return "assembly.streaming-range-limit"
        record_count = ranges[-1].end_record_index - ranges[0].start_record_index
        if record_count > HOST_CAPTURE_MAX_STREAMING_ASSEMBLY_RECORDS:
            return "assembly.streaming-record-limit"
        byte_count = ranges[-1].end_offset - ranges[0].start_offset
        if (
            self._max_streaming_assembly_bytes is not None
            and byte_count > self._max_streaming_assembly_bytes
        ):
            return "assembly.streaming-span-limit"
        return None

    @staticmethod
    def _verified_span(
        ranges: tuple[PendingAssemblyRange, ...],
        *,
        source_bytes_sha256: str,
    ) -> VerifiedAssemblySpan:
        if not ranges:
            raise ValueError("verified assembly span requires ranges")
        return VerifiedAssemblySpan(
            file_identity=ranges[0].file_identity,
            start_offset=ranges[0].start_offset,
            end_offset=ranges[-1].end_offset,
            start_record_index=ranges[0].start_record_index,
            end_record_index=ranges[-1].end_record_index,
            byte_count=ranges[-1].end_offset - ranges[0].start_offset,
            record_count=(ranges[-1].end_record_index - ranges[0].start_record_index),
            range_count=len(ranges),
            source_bytes_sha256=source_bytes_sha256,
        )

    def _source_ranges_sha256(self, ranges: tuple[PendingAssemblyRange, ...]) -> str:
        if not ranges:
            raise ValueError("source range hashing requires ranges")
        span_hasher = hashlib.sha256()
        expected_offset = ranges[0].start_offset
        expected_record_index = ranges[0].start_record_index
        identity = ranges[0].file_identity
        for pending_range in ranges:
            if pending_range.file_identity != identity:
                raise ReceiptBindingError("source range file identity mismatch")
            if pending_range.start_offset != expected_offset:
                raise ReceiptBindingError("source range byte continuity mismatch")
            if pending_range.start_record_index != expected_record_index:
                raise ReceiptBindingError("source range record continuity mismatch")
            range_hasher = hashlib.sha256()
            offset = pending_range.start_offset
            while offset < pending_range.end_offset:
                request = HostReadRequest(
                    request_id=uuid4(),
                    capability_id=self._capability_id,
                    capability_credential=self._capability_credential,
                    expected_identity=identity,
                    byte_offset=offset,
                    max_bytes=min(self._read_size, pending_range.end_offset - offset),
                )
                response = self._acquisition.read(request)
                if response.request_id != request.request_id:
                    raise ReceiptBindingError("source range response identity mismatch")
                if response.start_offset != offset:
                    raise ReceiptBindingError("source range response offset mismatch")
                if response.status not in {
                    HostReadStatus.DATA,
                    HostReadStatus.END_OF_FILE,
                }:
                    raise ReceiptBindingError("source range became unavailable")
                if response.file_identity != identity:
                    raise ReceiptBindingError("source range file identity mismatch")
                data = response.data
                remaining = pending_range.end_offset - offset
                if not data or len(data) > remaining:
                    raise ReceiptBindingError("source range read length mismatch")
                range_hasher.update(data)
                span_hasher.update(data)
                offset += len(data)
            if range_hasher.hexdigest() != pending_range.source_bytes_sha256:
                raise ReceiptBindingError("source range content drift")
            expected_offset = pending_range.end_offset
            expected_record_index = pending_range.end_record_index
        return span_hasher.hexdigest()

    def _source_range_sha256(
        self,
        records: tuple[FramedSourceRecord, ...],
        *,
        file_identity: FileIdentity,
    ) -> str:
        if not records:
            raise ValueError("source range hashing requires records")
        span_hasher = hashlib.sha256()
        for record in records:
            record_hasher = hashlib.sha256()
            offset = record.start_offset
            while offset < record.end_offset:
                request = HostReadRequest(
                    request_id=uuid4(),
                    capability_id=self._capability_id,
                    capability_credential=self._capability_credential,
                    expected_identity=file_identity,
                    byte_offset=offset,
                    max_bytes=min(self._read_size, record.end_offset - offset),
                )
                response = self._acquisition.read(request)
                if response.request_id != request.request_id:
                    raise ReceiptBindingError("source range response identity mismatch")
                if response.start_offset != offset:
                    raise ReceiptBindingError("source range response offset mismatch")
                if response.status not in {
                    HostReadStatus.DATA,
                    HostReadStatus.END_OF_FILE,
                }:
                    raise ReceiptBindingError("source range became unavailable")
                if response.file_identity != file_identity:
                    raise ReceiptBindingError("source range file identity mismatch")
                data = response.data
                remaining = record.end_offset - offset
                if not data or len(data) > remaining:
                    raise ReceiptBindingError("source range read length mismatch")
                record_hasher.update(data)
                span_hasher.update(data)
                offset += len(data)
            if record_hasher.hexdigest() != record.source_bytes_sha256:
                raise ReceiptBindingError("source range content drift")
        return span_hasher.hexdigest()

    def _commit_host_failure(
        self,
        state: HostCaptureCursorState,
        response: HostReadResponse,
    ) -> HostCaptureCursorState:
        mapped_state = HostCaptureState.DEGRADED
        if response.status == HostReadStatus.PERMISSION_REVOKED:
            mapped_state = HostCaptureState.PERMISSION_REVOKED
        elif response.status in {
            HostReadStatus.ROTATED_OR_REPLACED,
            HostReadStatus.IDENTITY_DRIFT,
            HostReadStatus.TRUNCATED,
        }:
            mapped_state = HostCaptureState.IDENTITY_CHANGED
        previous_revision = state.revision
        updated = self._updated(
            state,
            source_state=mapped_state,
            observed_size=max(response.observed_size, state.acknowledged_offset),
            retry_attempts=min(
                state.retry_attempts + 1, HOST_CAPTURE_MAX_RETRY_ATTEMPTS
            ),
            diagnostic_code=response.diagnostic_code or f"host.{response.status}",
        )
        self._commit(previous_revision=previous_revision, state=updated)
        return updated

    def _quarantine(
        self,
        state: HostCaptureCursorState,
        *,
        file_identity: FileIdentity,
        start_offset: int,
        end_offset: int,
        source_bytes: bytes,
        source_bytes_sha256: str | None = None,
        diagnostic_code: str,
        observed_size: int,
    ) -> HostCaptureCursorState:
        digest = source_bytes_sha256 or hashlib.sha256(source_bytes).hexdigest()
        entries = list(state.quarantine)
        overflow = state.quarantine_overflow_count
        for index, existing in enumerate(entries):
            if (
                existing.file_identity == file_identity
                and existing.start_offset == start_offset
                and existing.end_offset == end_offset
                and existing.source_bytes_sha256 == digest
                and existing.diagnostic_code == diagnostic_code
            ):
                entries[index] = existing.model_copy(
                    update={"occurrences": min(existing.occurrences + 1, 1_000_000)}
                )
                break
        else:
            if len(entries) < HOST_CAPTURE_MAX_QUARANTINE:
                entries.append(
                    QuarantineEntry(
                        file_identity=file_identity,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        source_bytes_sha256=digest,
                        diagnostic_code=diagnostic_code,
                    )
                )
            else:
                overflow = min(overflow + 1, 1_000_000)
        previous_revision = state.revision
        updated = self._updated(
            state,
            file_identity=file_identity,
            source_state=HostCaptureState.QUARANTINED,
            observed_size=max(observed_size, state.acknowledged_offset),
            diagnostic_code=diagnostic_code,
            quarantine=tuple(entries),
            quarantine_overflow_count=overflow,
        )
        self._commit(previous_revision=previous_revision, state=updated)
        return updated

    def _updated(
        self,
        state: HostCaptureCursorState,
        *,
        file_identity: FileIdentity | None = None,
        acknowledged_offset: int | None = None,
        acknowledged_record_index: int | None = None,
        checkpoint_sequence: int | None = None,
        partial_tail: bytes | None = None,
        pending_assembly: bytes | None = None,
        pending_assembly_records: tuple[PendingAssemblyRecord, ...] | None = None,
        pending_assembly_ranges: tuple[PendingAssemblyRange, ...] | None = None,
        extractor_state: bytes | None = None,
        source_state: HostCaptureState | None = None,
        observed_size: int | None = None,
        retry_attempts: int | None = None,
        diagnostic_code: str | None = None,
        quarantine: tuple[QuarantineEntry, ...] | None = None,
        quarantine_overflow_count: int | None = None,
        scope_exclusions: tuple[ScopeExclusionEntry, ...] | None = None,
        last_acknowledgement: DurableCursorAcknowledgement | None = None,
        clear_last_acknowledgement: bool = False,
        pending_capture_recorded_at: datetime | None = None,
        clear_pending_capture_recorded_at: bool = False,
    ) -> HostCaptureCursorState:
        if (
            pending_capture_recorded_at is not None
            and clear_pending_capture_recorded_at
        ):
            raise ValueError("pending capture time cannot be set and cleared together")
        if last_acknowledgement is not None and clear_last_acknowledgement:
            raise ValueError("last acknowledgement cannot be set and cleared together")
        configured_pending_forms = sum(
            value is not None and bool(value)
            for value in (
                pending_assembly,
                pending_assembly_records,
                pending_assembly_ranges,
            )
        )
        if configured_pending_forms > 1:
            raise ValueError("pending assembly representations are mutually exclusive")
        payload = state.model_dump(mode="json", warnings="error")
        payload["cursor_version"] = 2
        payload["revision"] = state.revision + 1
        if file_identity is not None:
            payload["file_identity"] = file_identity.model_dump(mode="json")
        if acknowledged_offset is not None:
            payload["acknowledged_offset"] = acknowledged_offset
        if acknowledged_record_index is not None:
            payload["acknowledged_record_index"] = acknowledged_record_index
        if checkpoint_sequence is not None:
            payload["checkpoint_sequence"] = checkpoint_sequence
        if partial_tail is not None:
            payload["partial_tail_base64"] = base64.b64encode(partial_tail).decode(
                "ascii"
            )
            payload["partial_tail_sha256"] = hashlib.sha256(partial_tail).hexdigest()
        if pending_assembly is not None:
            payload["pending_assembly_bytes"] = len(pending_assembly)
            payload["pending_assembly_sha256"] = hashlib.sha256(
                pending_assembly
            ).hexdigest()
            payload["pending_assembly_records"] = []
            payload["pending_assembly_ranges"] = []
        if pending_assembly_records is not None:
            payload["pending_assembly_records"] = [
                item.model_dump(mode="json", warnings="error")
                for item in pending_assembly_records
            ]
            payload["pending_assembly_bytes"] = (
                pending_assembly_records[-1].end_offset
                - pending_assembly_records[0].start_offset
                if pending_assembly_records
                else 0
            )
            payload["pending_assembly_sha256"] = (
                canonical_sha256(payload["pending_assembly_records"])
                if pending_assembly_records
                else hashlib.sha256(b"").hexdigest()
            )
            payload["pending_assembly_ranges"] = []
        if pending_assembly_ranges is not None:
            payload["pending_assembly_records"] = []
            payload["pending_assembly_ranges"] = [
                item.model_dump(mode="json", warnings="error")
                for item in pending_assembly_ranges
            ]
            payload["pending_assembly_bytes"] = (
                pending_assembly_ranges[-1].end_offset
                - pending_assembly_ranges[0].start_offset
                if pending_assembly_ranges
                else 0
            )
            payload["pending_assembly_sha256"] = (
                canonical_sha256(payload["pending_assembly_ranges"])
                if pending_assembly_ranges
                else hashlib.sha256(b"").hexdigest()
            )
        if extractor_state is not None:
            if len(extractor_state) > HOST_CAPTURE_MAX_CONTINUATION_STATE_BYTES:
                raise ValueError("extractor continuation state exceeds its bound")
            payload["extractor_state_base64"] = base64.b64encode(
                extractor_state
            ).decode("ascii")
            payload["extractor_state_sha256"] = hashlib.sha256(
                extractor_state
            ).hexdigest()
        if source_state is not None:
            payload["source_state"] = source_state
        if observed_size is not None:
            payload["observed_size"] = observed_size
        if retry_attempts is not None:
            payload["retry_attempts"] = retry_attempts
        payload["diagnostic_code"] = diagnostic_code
        if quarantine is not None:
            payload["quarantine"] = [
                item.model_dump(mode="json") for item in quarantine
            ]
        if quarantine_overflow_count is not None:
            payload["quarantine_overflow_count"] = quarantine_overflow_count
        if scope_exclusions is not None:
            payload["scope_exclusions"] = [
                item.model_dump(mode="json") for item in scope_exclusions
            ]
        if last_acknowledgement is not None:
            payload["last_acknowledgement"] = last_acknowledgement.model_dump(
                mode="json"
            )
        elif clear_last_acknowledgement:
            payload["last_acknowledgement"] = None
        if pending_capture_recorded_at is not None:
            payload["pending_capture_recorded_at"] = pending_capture_recorded_at
        elif clear_pending_capture_recorded_at:
            payload["pending_capture_recorded_at"] = None
        return HostCaptureCursorState.model_validate(payload)

    def _commit(
        self,
        *,
        previous_revision: int,
        state: HostCaptureCursorState,
    ) -> None:
        self._cursor_store.commit(
            expected_revision=previous_revision,
            state=state,
        )

    @staticmethod
    def _validate_receipt(
        receipt: CaptureSubmissionReceipt,
        *,
        operation_id: str,
        proposal: ProposedSourceCursor,
        event_id: UUID,
    ) -> None:
        if receipt.operation_id != operation_id:
            raise ReceiptBindingError("durable receipt operation identity mismatch")
        if receipt.event_id != event_id:
            raise ReceiptBindingError("durable receipt event identity mismatch")
        if receipt.checkpoint_sequence != proposal.next_checkpoint_sequence:
            raise ReceiptBindingError("durable receipt checkpoint mismatch")
        if receipt.ingress_status not in {
            CaptureIngressStatus.ACCEPTED,
            CaptureIngressStatus.DUPLICATE,
            CaptureIngressStatus.AMENDED,
        }:
            raise ReceiptBindingError(
                "durable receipt did not accept the source record"
            )

    @staticmethod
    def _health(
        state: HostCaptureCursorState,
        *,
        failure_capsule: SourceFailureCapsule | None = None,
    ) -> HostCaptureHealth:
        exclusion_reason_counts: dict[ScopeExclusionReason, int] = {}
        for exclusion in state.scope_exclusions:
            exclusion_reason_counts[exclusion.reason] = (
                exclusion_reason_counts.get(exclusion.reason, 0) + 1
            )
        return HostCaptureHealth(
            capability_id=state.capability_id,
            source_state=state.source_state,
            file_identity=state.file_identity,
            acknowledged_offset=state.acknowledged_offset,
            acknowledged_record_index=state.acknowledged_record_index,
            checkpoint_sequence=state.checkpoint_sequence,
            pending_partial_tail_bytes=len(state.partial_tail),
            pending_assembly_bytes=state.pending_assembly_bytes,
            observed_size=state.observed_size,
            lag_bytes=max(0, state.observed_size - state.acknowledged_offset),
            retry_attempts=state.retry_attempts,
            quarantine_count=len(state.quarantine),
            quarantine_overflow_count=state.quarantine_overflow_count,
            scope_exclusion_count=len(state.scope_exclusions),
            scope_exclusion_reason_counts=tuple(
                ScopeExclusionReasonCount(reason=reason, count=count)
                for reason, count in exclusion_reason_counts.items()
            ),
            diagnostic_code=state.diagnostic_code,
            failure_capsule=failure_capsule,
            last_acknowledgement=state.last_acknowledgement,
        )
