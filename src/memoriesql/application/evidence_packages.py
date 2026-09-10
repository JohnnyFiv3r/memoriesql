"""Version-1 evidence inventories, without observation or execution authority."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    canonical_json_bytes,
)

PACKAGE_MAX_PARTS = 256
PART_MAX_UTF8_BYTES = 65_536
PACKAGE_MAX_UTF8_BYTES = PACKAGE_MAX_PARTS * PART_MAX_UTF8_BYTES
PART_MAX_LINEAGE = 16
PART_MAX_SOURCE_BYTES = 262_144
METADATA_MAX_JSON_BYTES = 8_192
COMMAND_MAX_JSON_BYTES = 524_288
READ_MAX_CHARACTERS = 1_024
INVENTORY_PAGE_MAX_PARTS = 4
RESPONSE_MAX_JSON_BYTES = 65_536
OPERATION_TIMEOUT_MS = 2_000
LOCK_TIMEOUT_MS = 500


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class NativeFacts(FrozenContractModel):
    """Null means unknown, never a fabricated native identifier."""

    native_id: str | None = Field(default=None, max_length=256)
    parent_native_id: str | None = Field(default=None, max_length=256)
    session_native_id: str | None = Field(default=None, max_length=256)
    branch_native_id: str | None = Field(default=None, max_length=256)
    participant_native_id: str | None = Field(default=None, max_length=256)
    role: str | None = Field(default=None, max_length=128)
    source_order: int | None = Field(default=None, ge=0)
    occurred_at: datetime | None = None
    occurred_at_raw: str | None = Field(default=None, max_length=256)
    time_precision: str | None = Field(default=None, max_length=64)


class SourceQualification(FrozenContractModel):
    """Producer assertions. Core does not qualify source formats or semantics."""

    qualification_ref: str = Field(min_length=1, max_length=256)
    boundary: Literal["qualified_native_unit", "unresolved"]
    boundary_basis: str = Field(min_length=1, max_length=512)
    physical_records: Literal["complete", "pending_tail"]
    topology: Literal["known", "partially_known", "unknown"]
    normalized_input: Literal["complete", "incomplete"]
    source_completeness: Literal["producer_attested", "unresolved"]
    exclusions: tuple[str, ...] = Field(default=(), max_length=16)
    unresolved_coverage: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def bounded_reasons(self) -> Self:
        if any(
            not item or len(item) > 256
            for item in self.exclusions + self.unresolved_coverage
        ):
            raise ValueError("coverage reasons must contain 1-256 characters")
        return self


class PackageDeclaration(FrozenContractModel):
    source_object_id: UUID
    source_revision_key: str = Field(min_length=1, max_length=512)
    occurrence_key: str = Field(min_length=1, max_length=256)
    occurrence_identity_basis: Literal["native", "producer_assigned"]
    normalization_policy_version: str = Field(min_length=1, max_length=128)
    package_revision: int = Field(default=1, ge=1)
    native: NativeFacts
    qualification: SourceQualification
    expected_parts: int = Field(ge=1, le=PACKAGE_MAX_PARTS)
    expected_characters: int = Field(ge=1, le=PACKAGE_MAX_UTF8_BYTES)
    expected_utf8_bytes: int = Field(ge=1, le=PACKAGE_MAX_UTF8_BYTES)
    expected_inventory_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def bounded_declaration(self) -> Self:
        if self.occurrence_identity_basis == "native" and not self.native.native_id:
            raise ValueError("native occurrence identity requires a native identifier")
        if self.expected_characters > self.expected_utf8_bytes:
            raise ValueError("character total exceeds UTF-8 bytes")
        if (
            len(canonical_json_bytes(self.model_dump(mode="json")))
            > METADATA_MAX_JSON_BYTES
        ):
            raise ValueError("declaration exceeds metadata bound")
        return self


class RawEvidenceSlice(FrozenContractModel):
    source_range_receipt_id: UUID
    byte_start: int = Field(ge=0)
    byte_end_exclusive: int = Field(gt=0)
    source_bytes_sha256: str = Field(pattern=SHA256_PATTERN)
    fold_receipt_id: UUID | None = None
    fold_outcome_ordinal: int | None = Field(default=None, ge=0, le=255)

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not 0 < self.byte_end_exclusive - self.byte_start <= PART_MAX_SOURCE_BYTES:
            raise ValueError("invalid retained evidence interval")
        if (self.fold_receipt_id is None) != (self.fold_outcome_ordinal is None):
            raise ValueError("fold receipt and ordinal must be supplied together")
        return self


class EvidencePart(FrozenContractModel):
    part_id: UUID
    ordinal: int = Field(ge=0, lt=PACKAGE_MAX_PARTS)
    component_key: str = Field(min_length=1, max_length=256)
    component_offset: int = Field(default=0, ge=0)
    parent_component_key: str | None = Field(default=None, max_length=256)
    kind: str = Field(min_length=1, max_length=64)
    native: NativeFacts
    derivation: Literal["identity_utf8", "producer_normalized"]
    lineage: tuple[RawEvidenceSlice, ...] = Field(
        min_length=1, max_length=PART_MAX_LINEAGE
    )
    content: str = Field(min_length=1, max_length=PART_MAX_UTF8_BYTES, repr=False)
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def integrity(self) -> Self:
        encoded = self.content.encode("utf-8")
        if "\x00" in self.content or len(encoded) > PART_MAX_UTF8_BYTES:
            raise ValueError("content exceeds UTF-8 storage bound or contains NUL")
        if digest(encoded) != self.content_sha256:
            raise ValueError("content hash mismatch")
        if (
            sum(item.byte_end_exclusive - item.byte_start for item in self.lineage)
            > PART_MAX_SOURCE_BYTES
        ):
            raise ValueError("source lineage exceeds per-append work bound")
        if len(canonical_json_bytes(self.inventory_entry())) > METADATA_MAX_JSON_BYTES:
            raise ValueError("part inventory metadata exceeds bound")
        return self

    def inventory_entry(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content"}) | {
            "characters": len(self.content),
            "utf8_bytes": len(self.content.encode("utf-8")),
        }


def inventory_digest(parts: tuple[EvidencePart, ...]) -> str:
    """Digest ordered entry hashes; read-page boundaries never enter identity."""
    return digest(
        "".join(
            digest(canonical_json_bytes(part.inventory_entry())) for part in parts
        ).encode("ascii")
    )


class CreateEvidencePackage(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["create"] = "create"
    idempotency_key: str = Field(min_length=1, max_length=512)
    declaration: PackageDeclaration


class AppendEvidencePart(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["append"] = "append"
    idempotency_key: str = Field(min_length=1, max_length=512)
    package_id: UUID
    part: EvidencePart


class SealEvidencePackage(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["seal"] = "seal"
    idempotency_key: str = Field(min_length=1, max_length=512)
    package_id: UUID


class PackageReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    package_id: UUID
    idempotency_receipt_id: UUID
    operation: Literal["create", "append", "seal"]
    replayed: bool
    already_exists: bool
    sealed: bool
    appended_parts: int


class InspectEvidencePackage(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["inspect"] = "inspect"
    package_id: UUID


class EvidenceContinuation(FrozenContractModel):
    package_id: UUID
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    part_id: UUID | None = None
    position: int = Field(ge=0)


class PageEvidenceInventory(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["inventory"] = "inventory"
    package_id: UUID
    continuation: EvidenceContinuation | None = None
    limit: int = Field(
        default=INVENTORY_PAGE_MAX_PARTS, ge=1, le=INVENTORY_PAGE_MAX_PARTS
    )


class ReadEvidencePart(FrozenContractModel):
    contract_version: Literal[1] = 1
    operation: Literal["read"] = "read"
    package_id: UUID
    part_id: UUID
    continuation: EvidenceContinuation | None = None
    max_characters: int = Field(
        default=READ_MAX_CHARACTERS, ge=1, le=READ_MAX_CHARACTERS
    )


class PackageStatus(FrozenContractModel):
    package_id: UUID
    declaration: PackageDeclaration
    producer_principal_id: UUID
    appended_parts: int
    appended_characters: int
    appended_utf8_bytes: int
    sealed: bool
    inventory_sha256: str | None
    readiness: Literal[
        "assembling", "pending_source_qualification", "ready_producer_attested"
    ]
    independently_proven_source_complete: Literal[False] = False
    currently_authorized: Literal[True] = True


class EvidenceInventoryEntry(FrozenContractModel):
    part_id: UUID
    ordinal: int = Field(ge=0, lt=PACKAGE_MAX_PARTS)
    component_key: str = Field(min_length=1, max_length=256)
    component_offset: int = Field(ge=0)
    parent_component_key: str | None = Field(max_length=256)
    kind: str = Field(min_length=1, max_length=64)
    native: NativeFacts
    derivation: Literal["identity_utf8", "producer_normalized"]
    lineage: tuple[RawEvidenceSlice, ...] = Field(
        min_length=1, max_length=PART_MAX_LINEAGE
    )
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    characters: int = Field(ge=1, le=PART_MAX_UTF8_BYTES)
    utf8_bytes: int = Field(ge=1, le=PART_MAX_UTF8_BYTES)


class InventoryPage(FrozenContractModel):
    package_id: UUID
    inventory_sha256: str
    entries: tuple[EvidenceInventoryEntry, ...] = Field(
        min_length=1, max_length=INVENTORY_PAGE_MAX_PARTS
    )
    continuation: EvidenceContinuation | None
    terminal: bool


class EvidencePartPage(FrozenContractModel):
    package_id: UUID
    inventory_sha256: str
    part_id: UUID
    part_sha256: str
    character_start: int
    character_end_exclusive: int
    content: str
    content_sha256: str
    utf8_bytes: int
    continuation: EvidenceContinuation | None
    terminal: bool
