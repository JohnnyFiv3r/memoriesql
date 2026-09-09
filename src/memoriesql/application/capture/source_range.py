from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from memoriesql.application.capture.contracts import (
    CaptureSurface,
    KernelCaptureBinding,
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
    canonical_json_bytes,
    canonical_sha256,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

SOURCE_RANGE_CAPTURE_CONTRACT_VERSION = 1
SOURCE_RANGE_CAPTURE_SCHEMA_VERSION = 13
SOURCE_RANGE_CHUNK_MAX_BYTES = 65_536
SOURCE_RANGE_TRANSACTION_MAX_BYTES = HOST_HELPER_MAX_READ_BYTES
SOURCE_RANGE_TRANSACTION_MAX_CHUNKS = 256
SOURCE_RANGE_COMMAND_MAX_BYTES = 786_432


class SourceRangeInterpretationStatus(StrEnum):
    PENDING = "pending_interpretation"


class RawSourceCaptureStatus(StrEnum):
    CAPTURED = "captured"
    END_OF_FILE = "end_of_file"
    PAUSED = "paused"
    PERMISSION_REVOKED = "permission_revoked"
    SOURCE_UNAVAILABLE = "source_unavailable"
    IDENTITY_CHANGED = "identity_changed"


class SourceRangePolicyReferences(FrozenContractModel):
    capture_policy_id: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    retention_policy_ref: str = Field(min_length=1, max_length=256)


class CapturedSourceRangeChunk(FrozenContractModel):
    chunk_ordinal: int = Field(ge=0, le=SOURCE_RANGE_TRANSACTION_MAX_CHUNKS - 1)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    payload_byte_count: int = Field(ge=1, le=SOURCE_RANGE_CHUNK_MAX_BYTES)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    payload_hex: str = Field(
        min_length=2,
        max_length=SOURCE_RANGE_CHUNK_MAX_BYTES * 2,
        pattern=r"^(?:[a-f0-9]{2})+$",
        repr=False,
    )

    @model_validator(mode="after")
    def validate_exact_payload(self) -> CapturedSourceRangeChunk:
        payload = bytes.fromhex(self.payload_hex)
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("source-range chunk must have a non-empty byte range")
        if self.byte_end_exclusive - self.byte_start != self.payload_byte_count:
            raise ValueError("source-range chunk byte count must match its range")
        if len(payload) != self.payload_byte_count:
            raise ValueError("source-range chunk byte count must match its payload")
        if hashlib.sha256(payload).hexdigest() != self.payload_sha256:
            raise ValueError("source-range chunk hash must match its exact payload")
        return self

    @property
    def payload(self) -> bytes:
        return bytes.fromhex(self.payload_hex)


class SourceByteCheckpoint(FrozenContractModel):
    checkpoint_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    expected_sequence: int = Field(ge=0)
    next_sequence: int = Field(ge=1)
    expected_byte_offset: int = Field(ge=0)
    next_byte_offset: int = Field(gt=0)
    file_identity_key: str = Field(min_length=1, max_length=256)
    checkpoint_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> SourceByteCheckpoint:
        if self.next_sequence != self.expected_sequence + 1:
            raise ValueError("source-byte checkpoint must advance exactly once")
        if self.next_byte_offset <= self.expected_byte_offset:
            raise ValueError("source-byte checkpoint must advance bytes")
        return self


class CaptureSourceRangeCommand(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[13] = 13
    operation: Literal["capture_source_range"] = "capture_source_range"
    idempotency_key: str = Field(min_length=1, max_length=512)
    binding: KernelCaptureBinding
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    connector_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    observed_connector_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    capture_surface: CaptureSurface
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity: FileIdentity
    file_identity_key: str = Field(min_length=1, max_length=256)
    observed_source_format_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    payload_byte_count: int = Field(
        ge=1,
        le=SOURCE_RANGE_TRANSACTION_MAX_BYTES,
    )
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    chunks: tuple[CapturedSourceRangeChunk, ...] = Field(
        min_length=1,
        max_length=SOURCE_RANGE_TRANSACTION_MAX_CHUNKS,
    )
    policies: SourceRangePolicyReferences
    checkpoint: SourceByteCheckpoint

    @model_validator(mode="after")
    def validate_range(self) -> CaptureSourceRangeCommand:
        if self.file_identity_key != self.file_identity.stable_key:
            raise ValueError("source-range file identity key must be mechanical")
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("source range must be non-empty")
        if self.byte_end_exclusive - self.byte_start != self.payload_byte_count:
            raise ValueError("source-range byte count must match its range")
        if self.checkpoint.expected_byte_offset != self.byte_start:
            raise ValueError("source-range checkpoint must begin at the range start")
        if self.checkpoint.next_byte_offset != self.byte_end_exclusive:
            raise ValueError("source-range checkpoint must end at the range end")
        if self.checkpoint.file_identity_key != self.file_identity_key:
            raise ValueError("source-range checkpoint must bind file identity")
        expected_checkpoint_hash = hashlib.sha256(
            (
                f"{self.checkpoint.checkpoint_key}:"
                f"{self.checkpoint.expected_sequence}:"
                f"{self.checkpoint.next_sequence}:{self.byte_start}:"
                f"{self.byte_end_exclusive}:{self.file_identity_key}:"
                f"{self.payload_sha256}"
            ).encode()
        ).hexdigest()
        if self.checkpoint.checkpoint_hash != expected_checkpoint_hash:
            raise ValueError("source-range checkpoint hash must bind its range")
        expected_start = self.byte_start
        payload_hasher = hashlib.sha256()
        for expected_ordinal, chunk in enumerate(self.chunks):
            if chunk.chunk_ordinal != expected_ordinal:
                raise ValueError("source-range chunks must use contiguous ordinals")
            if chunk.byte_start != expected_start:
                raise ValueError("source-range chunks must be byte contiguous")
            expected_start = chunk.byte_end_exclusive
            payload_hasher.update(chunk.payload)
        if expected_start != self.byte_end_exclusive:
            raise ValueError("source-range chunks must cover the declared range")
        if sum(chunk.payload_byte_count for chunk in self.chunks) != (
            self.payload_byte_count
        ):
            raise ValueError("source-range chunks must cover the declared byte count")
        if payload_hasher.hexdigest() != self.payload_sha256:
            raise ValueError("source-range hash must match its exact chunk payloads")
        if len(self.canonical_bytes) > SOURCE_RANGE_COMMAND_MAX_BYTES:
            raise ValueError("source-range command exceeds its canonical byte bound")
        return self

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json", warnings="error"))

    @property
    def canonical_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", warnings="error"))


class CaptureSourceRangeReceipt(FrozenContractModel):
    source_range_receipt_id: UUID
    idempotency_receipt_id: UUID
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity_key: str = Field(min_length=1, max_length=256)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    payload_byte_count: int = Field(ge=1, le=SOURCE_RANGE_TRANSACTION_MAX_BYTES)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_count: int = Field(ge=1, le=SOURCE_RANGE_TRANSACTION_MAX_CHUNKS)
    checkpoint_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    checkpoint_sequence: int = Field(ge=1)
    interpretation_status: Literal[SourceRangeInterpretationStatus.PENDING] = (
        SourceRangeInterpretationStatus.PENDING
    )
    authority_principal_id: UUID
    captured_at: datetime
    already_exists: bool
    replayed: bool
    durable: Literal[True] = True

    @model_validator(mode="after")
    def validate_receipt(self) -> CaptureSourceRangeReceipt:
        if self.byte_end_exclusive - self.byte_start != self.payload_byte_count:
            raise ValueError("source-range receipt byte count must match its range")
        if self.captured_at.tzinfo is None:
            raise ValueError("source-range receipt capture time must be timezone-aware")
        return self


class DurableSourceRangeAcknowledgement(FrozenContractModel):
    source_range_receipt_id: UUID
    idempotency_receipt_id: UUID
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity_key: str = Field(min_length=1, max_length=256)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    checkpoint_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    checkpoint_sequence: int = Field(ge=1)
    authority_principal_id: UUID
    already_exists: bool
    replayed: bool


class RawSourceCursorState(FrozenContractModel):
    cursor_version: Literal[1] = 1
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    revision: int = Field(default=0, ge=0)
    file_identity: FileIdentity | None = None
    acknowledged_byte_offset: int = Field(default=0, ge=0)
    checkpoint_sequence: int = Field(default=0, ge=0)
    last_acknowledgement: DurableSourceRangeAcknowledgement | None = None

    @model_validator(mode="after")
    def validate_cursor(self) -> RawSourceCursorState:
        if self.file_identity is None and (
            self.checkpoint_sequence or self.last_acknowledgement is not None
        ):
            raise ValueError("raw-source cursor progress requires file identity")
        if self.last_acknowledgement is not None:
            acknowledgement = self.last_acknowledgement
            if acknowledgement.byte_end_exclusive != self.acknowledged_byte_offset:
                raise ValueError("raw-source cursor must bind its last durable receipt")
            if acknowledgement.checkpoint_sequence != self.checkpoint_sequence:
                raise ValueError("raw-source cursor checkpoint must bind its receipt")
            assert self.file_identity is not None
            if acknowledgement.file_identity_key != self.file_identity.stable_key:
                raise ValueError(
                    "raw-source cursor file identity must bind its receipt"
                )
        return self

    @classmethod
    def initial(
        cls,
        capability_id: str,
        *,
        byte_offset: int = 0,
    ) -> RawSourceCursorState:
        return cls(
            capability_id=capability_id,
            acknowledged_byte_offset=byte_offset,
        )


class RawSourceCaptureResult(FrozenContractModel):
    status: RawSourceCaptureStatus
    acknowledged_byte_offset: int = Field(ge=0)
    checkpoint_sequence: int = Field(ge=0)
    observed_size: int = Field(ge=0)
    diagnostic_code: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    receipt: CaptureSourceRangeReceipt | None = None

    @model_validator(mode="after")
    def validate_result(self) -> RawSourceCaptureResult:
        if self.status == RawSourceCaptureStatus.CAPTURED:
            if self.receipt is None:
                raise ValueError("captured raw-source result requires its receipt")
            if (
                self.receipt.byte_end_exclusive != self.acknowledged_byte_offset
                or self.receipt.checkpoint_sequence != self.checkpoint_sequence
            ):
                raise ValueError("captured raw-source result must bind its receipt")
        elif self.receipt is not None:
            raise ValueError("non-captured raw-source result cannot claim a receipt")
        return self


class SourceRangeInspection(FrozenContractModel):
    source_range_receipt_id: UUID
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    file_identity_key: str = Field(min_length=1, max_length=256)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    payload_byte_count: int = Field(ge=1, le=SOURCE_RANGE_TRANSACTION_MAX_BYTES)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_count: int = Field(ge=1, le=SOURCE_RANGE_TRANSACTION_MAX_CHUNKS)
    connector_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    observed_connector_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    observed_source_format_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    parser_profile_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    diagnostic_code: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    recognized_record_type: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    failing_field_name: str | None = Field(default=None, min_length=1, max_length=256)
    json_pointer: str | None = Field(default=None, min_length=1, max_length=512)
    byte_offset: int | None = Field(default=None, ge=0)
    record_index: int | None = Field(default=None, ge=0)
    interpretation_status: SourceRangeInterpretationStatus
    raw_bytes_available: bool


class SourceRangeRawDetail(FrozenContractModel):
    source_range_receipt_id: UUID
    chunk_ordinal: int = Field(ge=0)
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    payload_hex: str = Field(
        min_length=2,
        max_length=SOURCE_RANGE_CHUNK_MAX_BYTES * 2,
        pattern=r"^(?:[a-f0-9]{2})+$",
        repr=False,
    )

    @model_validator(mode="after")
    def validate_exact_payload(self) -> SourceRangeRawDetail:
        payload = bytes.fromhex(self.payload_hex)
        if self.byte_end_exclusive <= self.byte_start:
            raise ValueError("raw source-range detail must be non-empty")
        if len(payload) != self.byte_end_exclusive - self.byte_start:
            raise ValueError("raw source-range detail must bind its byte range")
        if hashlib.sha256(payload).hexdigest() != self.payload_sha256:
            raise ValueError("raw source-range detail must bind its exact payload")
        return self

    @property
    def payload(self) -> bytes:
        return bytes.fromhex(self.payload_hex)


class SourceRangeCapturePort(Protocol):
    def capture(
        self,
        command: CaptureSourceRangeCommand,
        *,
        recorded_at: datetime,
    ) -> CaptureSourceRangeReceipt: ...


class RawSourceCursorStore(Protocol):
    def load(self, capability_id: str) -> RawSourceCursorState | None: ...

    def commit(
        self,
        *,
        expected_revision: int,
        state: RawSourceCursorState,
    ) -> None: ...


class RawSourceAcquisitionPort(Protocol):
    def read(self, request: HostReadRequest) -> HostReadResponse: ...


def build_capture_source_range_command(
    payload: bytes,
    *,
    binding: KernelCaptureBinding,
    capability_id: str,
    connector_id: str,
    observed_connector_version: str | None,
    capture_surface: CaptureSurface,
    source_revision_key: str,
    file_identity: FileIdentity,
    observed_source_format_version: str | None,
    byte_start: int,
    checkpoint_key: str,
    expected_checkpoint_sequence: int,
    policies: SourceRangePolicyReferences,
    chunk_size: int = SOURCE_RANGE_CHUNK_MAX_BYTES,
) -> CaptureSourceRangeCommand:
    if not 1 <= len(payload) <= SOURCE_RANGE_TRANSACTION_MAX_BYTES:
        raise ValueError("source-range payload exceeds its transaction bound")
    if not 1 <= chunk_size <= SOURCE_RANGE_CHUNK_MAX_BYTES:
        raise ValueError("source-range chunk size exceeds its configured bound")
    chunks = tuple(
        CapturedSourceRangeChunk(
            chunk_ordinal=ordinal,
            byte_start=byte_start + relative_start,
            byte_end_exclusive=byte_start + relative_start + len(chunk),
            payload_byte_count=len(chunk),
            payload_sha256=hashlib.sha256(chunk).hexdigest(),
            payload_hex=chunk.hex(),
        )
        for ordinal, relative_start in enumerate(range(0, len(payload), chunk_size))
        if (chunk := payload[relative_start : relative_start + chunk_size])
    )
    byte_end = byte_start + len(payload)
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    file_identity_key = file_identity.stable_key
    checkpoint_hash = hashlib.sha256(
        (
            f"{checkpoint_key}:{expected_checkpoint_sequence}:"
            f"{expected_checkpoint_sequence + 1}:{byte_start}:{byte_end}:"
            f"{file_identity_key}:{payload_sha256}"
        ).encode()
    ).hexdigest()
    operation_hash = canonical_sha256(
        {
            "source_object_id": str(binding.source_object_id),
            "source_revision_key": source_revision_key,
            "file_identity_key": file_identity_key,
            "byte_start": byte_start,
            "byte_end_exclusive": byte_end,
            "payload_sha256": payload_sha256,
        }
    )
    return CaptureSourceRangeCommand(
        idempotency_key=f"source-range:{operation_hash}",
        binding=binding,
        capability_id=capability_id,
        connector_id=connector_id,
        observed_connector_version=observed_connector_version,
        capture_surface=capture_surface,
        source_revision_key=source_revision_key,
        file_identity=file_identity,
        file_identity_key=file_identity_key,
        observed_source_format_version=observed_source_format_version,
        byte_start=byte_start,
        byte_end_exclusive=byte_end,
        payload_byte_count=len(payload),
        payload_sha256=payload_sha256,
        chunks=chunks,
        policies=policies,
        checkpoint=SourceByteCheckpoint(
            checkpoint_key=checkpoint_key,
            expected_sequence=expected_checkpoint_sequence,
            next_sequence=expected_checkpoint_sequence + 1,
            expected_byte_offset=byte_start,
            next_byte_offset=byte_end,
            file_identity_key=file_identity_key,
            checkpoint_hash=checkpoint_hash,
        ),
    )


class RawSourceCaptureLoop:
    """Durably capture one approved bounded read before any interpretation."""

    def __init__(
        self,
        *,
        acquisition: RawSourceAcquisitionPort,
        submission: SourceRangeCapturePort,
        cursor_store: RawSourceCursorStore,
        binding: KernelCaptureBinding,
        capability_id: str,
        capability_credential: str,
        checkpoint_key: str,
        connector_id: str,
        observed_connector_version: str | None,
        capture_surface: CaptureSurface,
        source_revision_key: str,
        observed_source_format_version: str | None,
        policies: SourceRangePolicyReferences,
        initial_byte_offset: int = 0,
        read_size: int = SOURCE_RANGE_TRANSACTION_MAX_BYTES,
    ) -> None:
        if not 1 <= read_size <= SOURCE_RANGE_TRANSACTION_MAX_BYTES:
            raise ValueError("raw-source read size exceeds its transaction bound")
        self._acquisition = acquisition
        self._submission = submission
        self._cursor_store = cursor_store
        self._binding = binding
        self._capability_id = capability_id
        self._capability_credential = capability_credential
        self._checkpoint_key = checkpoint_key
        self._connector_id = connector_id
        self._observed_connector_version = observed_connector_version
        self._capture_surface = capture_surface
        self._source_revision_key = source_revision_key
        self._observed_source_format_version = observed_source_format_version
        self._policies = policies
        self._initial_byte_offset = initial_byte_offset
        self._read_size = read_size

    def run_once(self, *, recorded_at: datetime) -> RawSourceCaptureResult:
        if recorded_at.tzinfo is None:
            raise ValueError("raw-source recorded_at must be timezone-aware")
        state = self._cursor_store.load(self._capability_id) or (
            RawSourceCursorState.initial(
                self._capability_id,
                byte_offset=self._initial_byte_offset,
            )
        )
        request = HostReadRequest(
            request_id=uuid4(),
            capability_id=self._capability_id,
            capability_credential=self._capability_credential,
            expected_identity=state.file_identity,
            byte_offset=state.acknowledged_byte_offset,
            max_bytes=self._read_size,
        )
        response = self._acquisition.read(request)
        if response.request_id != request.request_id:
            raise ValueError("raw-source host response request identity mismatch")
        if response.start_offset != request.byte_offset:
            raise ValueError("raw-source host response byte offset mismatch")
        if response.status == HostReadStatus.END_OF_FILE:
            return RawSourceCaptureResult(
                status=RawSourceCaptureStatus.END_OF_FILE,
                acknowledged_byte_offset=state.acknowledged_byte_offset,
                checkpoint_sequence=state.checkpoint_sequence,
                observed_size=response.observed_size,
            )
        if response.status != HostReadStatus.DATA:
            status = {
                HostReadStatus.PERMISSION_REVOKED: (
                    RawSourceCaptureStatus.PERMISSION_REVOKED
                ),
                HostReadStatus.PERMISSION_DENIED: (
                    RawSourceCaptureStatus.PERMISSION_REVOKED
                ),
                HostReadStatus.ROTATED_OR_REPLACED: (
                    RawSourceCaptureStatus.IDENTITY_CHANGED
                ),
                HostReadStatus.IDENTITY_DRIFT: (
                    RawSourceCaptureStatus.IDENTITY_CHANGED
                ),
            }.get(response.status, RawSourceCaptureStatus.SOURCE_UNAVAILABLE)
            return RawSourceCaptureResult(
                status=status,
                acknowledged_byte_offset=state.acknowledged_byte_offset,
                checkpoint_sequence=state.checkpoint_sequence,
                observed_size=response.observed_size,
                diagnostic_code=response.diagnostic_code,
            )
        assert response.file_identity is not None
        if (
            state.file_identity is not None
            and state.file_identity != response.file_identity
        ):
            return RawSourceCaptureResult(
                status=RawSourceCaptureStatus.IDENTITY_CHANGED,
                acknowledged_byte_offset=state.acknowledged_byte_offset,
                checkpoint_sequence=state.checkpoint_sequence,
                observed_size=response.observed_size,
                diagnostic_code="host.identity-changed",
            )
        command = build_capture_source_range_command(
            response.data,
            binding=self._binding,
            capability_id=self._capability_id,
            connector_id=self._connector_id,
            observed_connector_version=self._observed_connector_version,
            capture_surface=self._capture_surface,
            source_revision_key=self._source_revision_key,
            file_identity=response.file_identity,
            observed_source_format_version=self._observed_source_format_version,
            byte_start=response.start_offset,
            checkpoint_key=self._checkpoint_key,
            expected_checkpoint_sequence=state.checkpoint_sequence,
            policies=self._policies,
        )
        receipt = self._submission.capture(command, recorded_at=recorded_at)
        if (
            receipt.source_object_id != self._binding.source_object_id
            or receipt.source_revision_key != self._source_revision_key
            or receipt.file_identity_key != response.file_identity.stable_key
            or receipt.byte_start != command.byte_start
            or receipt.byte_end_exclusive != command.byte_end_exclusive
            or receipt.payload_sha256 != command.payload_sha256
            or (
                not receipt.replayed
                and not receipt.already_exists
                and receipt.chunk_count != len(command.chunks)
            )
            or receipt.checkpoint_key != self._checkpoint_key
            or receipt.checkpoint_sequence != state.checkpoint_sequence + 1
            or not receipt.durable
        ):
            raise ValueError("raw-source durable receipt does not bind its request")
        acknowledgement = DurableSourceRangeAcknowledgement(
            source_range_receipt_id=receipt.source_range_receipt_id,
            idempotency_receipt_id=receipt.idempotency_receipt_id,
            source_object_id=receipt.source_object_id,
            source_revision_key=receipt.source_revision_key,
            file_identity_key=receipt.file_identity_key,
            payload_sha256=receipt.payload_sha256,
            byte_start=receipt.byte_start,
            byte_end_exclusive=receipt.byte_end_exclusive,
            checkpoint_key=receipt.checkpoint_key,
            checkpoint_sequence=receipt.checkpoint_sequence,
            authority_principal_id=receipt.authority_principal_id,
            already_exists=receipt.already_exists,
            replayed=receipt.replayed,
        )
        next_state = RawSourceCursorState(
            capability_id=state.capability_id,
            revision=state.revision + 1,
            file_identity=response.file_identity,
            acknowledged_byte_offset=receipt.byte_end_exclusive,
            checkpoint_sequence=receipt.checkpoint_sequence,
            last_acknowledgement=acknowledgement,
        )
        self._cursor_store.commit(
            expected_revision=state.revision,
            state=next_state,
        )
        return RawSourceCaptureResult(
            status=RawSourceCaptureStatus.CAPTURED,
            acknowledged_byte_offset=next_state.acknowledged_byte_offset,
            checkpoint_sequence=next_state.checkpoint_sequence,
            observed_size=response.observed_size,
            receipt=receipt,
        )

    def run_until_pause(
        self,
        *,
        recorded_at: datetime,
        max_transactions: int,
    ) -> tuple[RawSourceCaptureResult, ...]:
        if not 1 <= max_transactions <= 1_024:
            raise ValueError("raw-source transaction budget is invalid")
        results: list[RawSourceCaptureResult] = []
        for _ in range(max_transactions):
            result = self.run_once(recorded_at=recorded_at)
            results.append(result)
            if result.status != RawSourceCaptureStatus.CAPTURED:
                return tuple(results)
        last = results[-1]
        results.append(
            RawSourceCaptureResult(
                status=RawSourceCaptureStatus.PAUSED,
                acknowledged_byte_offset=last.acknowledged_byte_offset,
                checkpoint_sequence=last.checkpoint_sequence,
                observed_size=last.observed_size,
                diagnostic_code="source-range.transaction-budget-reached",
            )
        )
        return tuple(results)
