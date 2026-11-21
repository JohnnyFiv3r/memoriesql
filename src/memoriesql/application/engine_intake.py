"""Core Memory engine record intake: requests and receipts (schemas 55–56).

The engine delivers versioned records, one per deterministic chunk of a source
event and later one type decision for a chunk it first delivered as pending.
memoriesQL keeps them exactly as delivered, answers one outcome per record and
authors nothing. Each engine scope is one enrolled exact source with
``source_system`` ``core-memory`` and ``object_kind`` ``engine_scope``.
Delivering to a scope needs reading it too (schema 56), so no outcome tells a
deliverer anything about records it may not read. Schema 61 adds binding a
complete normalized event to accepted evidence; see ``docs/engine-intake.md``.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue

from memoriesql.application.semantic_task_contracts import FrozenContractModel

ENGINE_SOURCE_SYSTEM = "core-memory"
ENGINE_SCOPE_OBJECT_KIND = "engine_scope"
ENGINE_VOCABULARY_ID = "memoriesql.bead_types"
ENGINE_RECORD_CONTRACT = "core_memory.record.v1"


class AcceptEngineRecords(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[55] = 55
    records: tuple[dict[str, JsonValue], ...] = Field(min_length=1, max_length=256)


class EngineRecordAccepted(FrozenContractModel):
    outcome: Literal["accepted"]


class EngineRecordAlreadyAccepted(FrozenContractModel):
    outcome: Literal["already_accepted"]


class EngineRecordConflict(FrozenContractModel):
    """A different record already holds the key. It is kept; this one is not.

    `stored` is the held record. Every deliverer may read the engine scope.
    """

    outcome: Literal["conflict"]
    stored: dict[str, JsonValue]


class EngineRecordRejected(FrozenContractModel):
    """Final for this record: it is malformed, names an ungoverned type, or
    contradicts what memoriesQL already holds."""

    outcome: Literal["rejected"]
    reason: str = Field(min_length=1)


class EngineRecordRetryable(FrozenContractModel):
    """Configuration an operator can fix: the engine scope is not enrolled,
    or this credential may not both deliver to it and read it. Those cases are
    one answer, so it never shows whether a scope exists. This record and every
    later one in the batch are unread."""

    outcome: Literal["retryable"]
    retry_after_ms: int = Field(ge=0)
    reason: str = Field(min_length=1)


EngineRecordOutcome = Annotated[
    EngineRecordAccepted
    | EngineRecordAlreadyAccepted
    | EngineRecordConflict
    | EngineRecordRejected
    | EngineRecordRetryable,
    Field(discriminator="outcome"),
]


class EngineRecordResults(FrozenContractModel):
    contract_version: Literal[1] = 1
    results: tuple[EngineRecordOutcome, ...]


class ReadEngineRecords(FrozenContractModel):
    """`after` is a page's `next` cursor, or absent to start from the first record."""

    contract_version: Literal[1] = 1
    engine_scope: str = Field(min_length=1, max_length=4096, pattern=r"\S")
    after: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"\S")
    limit: int = Field(default=256, ge=1, le=1024)


class EngineRecordPage(FrozenContractModel):
    """One engine scope's records in acceptance order.

    `next` is an opaque cursor within this engine scope: pass it back as
    `after` to continue. It depends only on the scope's own records, and is
    null only while no record has been returned.
    """

    contract_version: Literal[1] = 1
    records: tuple[dict[str, JsonValue], ...]
    next: str | None


class EngineVocabularyType(FrozenContractModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    rev: int = Field(ge=1)
    definition: str = Field(min_length=1)


class EngineVocabulary(FrozenContractModel):
    """The governed bead types, in the engine's vocabulary shape."""

    id: Literal["memoriesql.bead_types"]
    types: tuple[EngineVocabularyType, ...] = Field(min_length=1)


EngineOffset = Annotated[int, Field(strict=True, ge=0, le=16 * 1024 * 1024)]
EngineDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
EngineWorkspace = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]


class EngineChunkSpan(FrozenContractModel):
    """An engine chunk's half-open UTF-8 byte and Unicode character intervals."""

    chunk_id: UUID
    byte_start: EngineOffset
    byte_end: EngineOffset
    char_start: EngineOffset
    char_end: EngineOffset
    chunk_sha256: EngineDigest


class BindEngineEventEvidence(FrozenContractModel):
    """Bind a complete normalized engine event to already accepted evidence."""

    source_object_id: UUID
    engine_workspace: EngineWorkspace
    engine_event_id: UUID
    event_sha256: EngineDigest
    source_unit_id: UUID
    statement_id: UUID
    chunks: tuple[EngineChunkSpan, ...] = Field(min_length=1, max_length=256)


class EngineChunkEvidence(EngineChunkSpan):
    """Canonical evidence identity and offsets, with no source text."""

    event_id: UUID
    source_unit_id: UUID
    content_sha256: EngineDigest
