"""Core Memory engine record intake: requests and receipts (schema 55).

The engine delivers versioned records, one per deterministic chunk of a source
event and later one type decision for a chunk it first delivered as pending.
memoriesQL keeps them exactly as delivered, answers one outcome per record and
authors nothing. Each engine scope is one enrolled exact source with
``source_system`` ``core-memory`` and ``object_kind`` ``engine_scope``.
"""

from __future__ import annotations

from typing import Annotated, Literal

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

    `stored` is the held record, present only when the caller may read the
    engine scope (`source.read` and `read`).
    """

    outcome: Literal["conflict"]
    stored: dict[str, JsonValue] | None = None


class EngineRecordRejected(FrozenContractModel):
    """Final for this record: it is malformed, names an ungoverned type, or
    contradicts what memoriesQL already holds."""

    outcome: Literal["rejected"]
    reason: str = Field(min_length=1)


class EngineRecordRetryable(FrozenContractModel):
    """Configuration an operator can fix, such as an unenrolled or ungranted
    engine scope. This record and every later one in the batch are unread."""

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
    contract_version: Literal[1] = 1
    engine_scope: str = Field(min_length=1, max_length=4096, pattern=r"\S")
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=256, ge=1, le=1024)


class EngineRecordPage(FrozenContractModel):
    contract_version: Literal[1] = 1
    records: tuple[dict[str, JsonValue], ...]
    next: int = Field(ge=0)


class EngineVocabularyType(FrozenContractModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    rev: int = Field(ge=1)
    definition: str = Field(min_length=1)


class EngineVocabulary(FrozenContractModel):
    """The governed bead types, in the engine's vocabulary shape."""

    id: Literal["memoriesql.bead_types"]
    types: tuple[EngineVocabularyType, ...] = Field(min_length=1)
