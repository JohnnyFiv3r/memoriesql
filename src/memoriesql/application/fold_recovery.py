"""Bounded recovery of immutable fold evidence, without qualification or execution."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.capture.host_protocol import FileIdentity
from memoriesql.application.capture.transcript_fold import (
    TranscriptFoldOutcomeKind,
    TranscriptFoldSourceRange,
    TranscriptPolicyDisposition,
    TranscriptSpanEvidence,
)
from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
)

DISCOVERY_LIMIT = 32
LINEAGE_LIMIT = 16
READ_BYTES = 32_768
REQUEST_BYTES = 8_192
RESPONSE_BYTES = 131_072
OPERATION_TIMEOUT_MS = 2_000
LOCK_TIMEOUT_MS = 500


class FoldOutcomeKey(FrozenContractModel):
    source_object_id: UUID
    fold_receipt_id: UUID
    outcome_ordinal: int = Field(ge=0, le=255)


class FoldDiscoveryCursor(FrozenContractModel):
    """An immutable source-scoped high water mark and last delivered outcome."""

    through_receipt_id: UUID
    after: FoldOutcomeKey


class DiscoverFoldOutcomes(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["discover"] = "discover"
    source_object_id: UUID
    continuation: FoldDiscoveryCursor | None = None
    after: FoldOutcomeKey | None = None
    limit: int = Field(default=DISCOVERY_LIMIT, ge=1, le=DISCOVERY_LIMIT)

    @model_validator(mode="after")
    def consistent_cursor(self) -> Self:
        if self.continuation is not None and self.after is not None:
            raise ValueError("use continuation or a fresh discovery after an outcome")
        anchor = self.continuation.after if self.continuation else self.after
        if anchor and anchor.source_object_id != self.source_object_id:
            raise ValueError("discovery cursor belongs to another source")
        return self


class InspectFoldOutcome(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["inspect"] = "inspect"
    key: FoldOutcomeKey


class PageFoldLineage(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["lineage"] = "lineage"
    key: FoldOutcomeKey
    next_ordinal: int = Field(default=0, ge=0, le=1023)
    limit: int = Field(default=LINEAGE_LIMIT, ge=1, le=LINEAGE_LIMIT)


class ReadFoldEvidence(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["read"] = "read"
    key: FoldOutcomeKey
    evidence: Literal["exact_envelope", "raw_lineage"]
    lineage_ordinal: int | None = Field(default=None, ge=0, le=1023)
    expected_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_offset: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=READ_BYTES, ge=1, le=READ_BYTES)

    @model_validator(mode="after")
    def selected_evidence(self) -> Self:
        if (self.evidence == "raw_lineage") != (self.lineage_ordinal is not None):
            raise ValueError("only raw lineage reads require a lineage ordinal")
        return self


class FoldOutcomeSummary(FrozenContractModel):
    key: FoldOutcomeKey
    outcome_kind: TranscriptFoldOutcomeKind
    byte_start: int
    byte_end_exclusive: int
    start_record_index: int
    end_record_index: int
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)


class FoldDiscoveryPage(FrozenContractModel):
    source_object_id: UUID
    through_receipt_id: UUID | None
    outcomes: tuple[FoldOutcomeSummary, ...] = Field(max_length=DISCOVERY_LIMIT)
    continuation: FoldDiscoveryCursor | None
    resume_after: FoldOutcomeKey | None


class FoldOutcomeDetail(FoldOutcomeSummary):
    source_revision_key: str
    file_identity_key: str
    file_identity: FileIdentity
    connector_id: str
    capability_id: str
    adapter_profile_version: str | None
    observed_source_format_version: str | None
    folded_by_principal_id: UUID
    folded_at: datetime
    idempotency_receipt_id: UUID
    outcomes_sha256: str = Field(pattern=SHA256_PATTERN)
    exact_envelope_sha256: str | None
    exact_envelope_bytes: int | None
    transcript_span: TranscriptSpanEvidence | None
    policy_disposition: TranscriptPolicyDisposition | None
    lineage_count: int = Field(ge=1, le=1024)
    source_qualification: Literal["not_established"] = "not_established"


class RecoveredFoldLineage(TranscriptFoldSourceRange):
    lineage_ordinal: int = Field(ge=0, le=1023)


class FoldLineagePage(FrozenContractModel):
    key: FoldOutcomeKey
    ranges: tuple[RecoveredFoldLineage, ...] = Field(
        min_length=1, max_length=LINEAGE_LIMIT
    )
    next_ordinal: int | None


class FoldEvidencePage(FrozenContractModel):
    key: FoldOutcomeKey
    evidence: Literal["exact_envelope", "raw_lineage"]
    lineage_ordinal: int | None
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    total_bytes: int = Field(gt=0)
    byte_offset: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    content_hex: str = Field(
        pattern=r"^(?:[a-f0-9]{2})+$", max_length=READ_BYTES * 2, repr=False
    )
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    next_byte_offset: int | None

    @model_validator(mode="after")
    def exact_bytes(self) -> Self:
        content = bytes.fromhex(self.content_hex)
        if (
            len(content) != self.byte_end_exclusive - self.byte_offset
            or self.byte_end_exclusive > self.total_bytes
            or hashlib.sha256(content).hexdigest() != self.content_sha256
            or self.next_byte_offset
            != (
                self.byte_end_exclusive
                if self.byte_end_exclusive < self.total_bytes
                else None
            )
        ):
            raise ValueError("fold evidence page integrity mismatch")
        return self

    @property
    def content(self) -> bytes:
        return bytes.fromhex(self.content_hex)
