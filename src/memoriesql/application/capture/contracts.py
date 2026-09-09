from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.canonical_transactions import (
    AcceptSourceEventCommand,
    CanonicalSemanticTaskRequest,
    CaptureCheckpoint,
    ConversationTurnDetail,
    SourceEventInput,
    SourceTimePrecision,
    SourceType,
    SourceUnitInput,
)
from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    canonical_json_bytes,
    canonical_sha256,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

CAPTURE_CONTRACT_VERSION = 1
CAPTURE_SCHEMA_VERSION = 12
CAPTURE_ENVELOPE_MAX_BYTES = 1_048_576
CAPTURE_DIAGNOSTIC_LIMIT = 32
CAPTURE_OMISSION_LIMIT = 32


class CaptureSurface(StrEnum):
    ARTIFACT = "artifact"
    LIVE_HOOK = "live_hook"
    HARNESS = "harness"
    BROWSER = "browser"
    IMPORT = "import"
    EXPLICIT_CHECKPOINT = "explicit_checkpoint"
    SYNTHETIC = "synthetic"


class CaptureEventKind(StrEnum):
    SOURCE_OBSERVED = "source_observed"
    SOURCE_AMENDED = "source_amended"
    SOURCE_DELETED = "source_deleted"
    TURN_FINALIZED = "turn_finalized"
    TURN_AMENDED = "turn_amended"


class CompletionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ParserCoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    ERROR = "error"


class ParserDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CaptureIngressStatus(StrEnum):
    DETECTED = "detected"
    RECEIVED = "received"
    DUPLICATE = "duplicate"
    QUARANTINED = "quarantined"
    ACCEPTED = "accepted"
    AMENDED = "amended"
    DELETED = "deleted"


class MaterializationStatus(StrEnum):
    NONE = "none"
    THIN_BEAD = "thin_bead"
    READY = "ready"


class EnrichmentStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "enrichment_pending"
    FAILED = "enrichment_failed"
    READY = "ready"


class ParticipantKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


class SourcePosition(FrozenContractModel):
    byte_offset: int | None = Field(default=None, ge=0)
    record_index: int | None = Field(default=None, ge=0)
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    json_pointer: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_one_coordinate(self) -> SourcePosition:
        if all(
            value is None
            for value in (
                self.byte_offset,
                self.record_index,
                self.line,
                self.column,
                self.json_pointer,
            )
        ):
            raise ValueError("a source position requires at least one coordinate")
        return self


class ParserDiagnostic(FrozenContractModel):
    code: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    severity: ParserDiagnosticSeverity
    position: SourcePosition | None = None
    affected_items: int = Field(default=1, ge=1, le=1_000_000)


class ParserOmission(FrozenContractModel):
    code: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    affected_items: int = Field(ge=1, le=1_000_000)


class ParserCoverageReceipt(FrozenContractModel):
    parser_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    parser_version: str = Field(min_length=1, max_length=128)
    status: ParserCoverageStatus
    outcome_code: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    source_items_seen: int = Field(ge=0, le=1_000_000)
    source_items_emitted: int = Field(ge=0, le=1_000_000)
    source_items_quarantined: int = Field(ge=0, le=1_000_000)
    diagnostics_count: int = Field(ge=0, le=CAPTURE_DIAGNOSTIC_LIMIT)
    diagnostics: tuple[ParserDiagnostic, ...] = Field(
        default=(), max_length=CAPTURE_DIAGNOSTIC_LIMIT
    )
    first_source_position: SourcePosition | None = None
    last_source_position: SourcePosition | None = None
    known_omissions: tuple[ParserOmission, ...] = Field(
        default=(), max_length=CAPTURE_OMISSION_LIMIT
    )

    @model_validator(mode="after")
    def validate_coverage(self) -> ParserCoverageReceipt:
        if self.diagnostics_count != len(self.diagnostics):
            raise ValueError("diagnostics_count must equal bounded diagnostics")
        if self.source_items_emitted + self.source_items_quarantined > self.source_items_seen:
            raise ValueError("parser output counts cannot exceed source_items_seen")
        if self.status == ParserCoverageStatus.COMPLETE:
            if (
                self.source_items_emitted != self.source_items_seen
                or self.source_items_quarantined
                or self.known_omissions
            ):
                raise ValueError("complete parser coverage cannot omit or quarantine items")
            if any(
                item.severity == ParserDiagnosticSeverity.ERROR
                for item in self.diagnostics
            ):
                raise ValueError("complete parser coverage cannot contain errors")
        if self.status == ParserCoverageStatus.ERROR and self.outcome_code is None:
            raise ValueError("error parser coverage requires outcome_code")
        return self


class SourceIdentity(FrozenContractModel):
    source_product: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    source_format_version: str = Field(min_length=1, max_length=128)
    installation_id: str | None = Field(default=None, min_length=1, max_length=512)
    device_id: str | None = Field(default=None, min_length=1, max_length=512)
    source_account_ref: str | None = Field(default=None, min_length=1, max_length=512)
    external_id_scope: str = Field(min_length=1, max_length=512)
    source_identity_key: str = Field(min_length=1, max_length=512)
    source_object_kind: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    source_object_stable_id: str = Field(min_length=1, max_length=512)


class ConversationIdentity(FrozenContractModel):
    conversation_id: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=512)
    branch_id: str | None = Field(default=None, min_length=1, max_length=512)
    turn_id: str = Field(min_length=1, max_length=512)
    parent_turn_id: str | None = Field(default=None, min_length=1, max_length=512)


class ParticipantIdentity(FrozenContractModel):
    participant_id: str = Field(min_length=1, max_length=512)
    kind: ParticipantKind
    role: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=256)


class ClientIdentity(FrozenContractModel):
    client_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    client_version: str = Field(min_length=1, max_length=128)
    agent_name: str | None = Field(default=None, min_length=1, max_length=256)
    model_name: str | None = Field(default=None, min_length=1, max_length=256)
    provider_name: str | None = Field(default=None, min_length=1, max_length=256)


class ProjectHint(FrozenContractModel):
    source_project_id: str | None = Field(default=None, min_length=1, max_length=512)
    repository_id: str | None = Field(default=None, min_length=1, max_length=512)
    worktree_id: str | None = Field(default=None, min_length=1, max_length=512)


class SourceLocator(FrozenContractModel):
    artifact_type: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    native_ref: str | None = Field(default=None, min_length=1, max_length=1024)
    byte_offset: int | None = Field(default=None, ge=0)
    record_index: int | None = Field(default=None, ge=0)
    line: int | None = Field(default=None, ge=1)
    row_id: str | None = Field(default=None, min_length=1, max_length=512)
    json_pointer: str | None = Field(default=None, min_length=1, max_length=512)


class CapturePolicyReferences(FrozenContractModel):
    capture_policy_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    access_policy_ref: str = Field(min_length=1, max_length=256)
    retention_policy_ref: str = Field(min_length=1, max_length=256)
    observation_unit_policy_version: str = Field(min_length=1, max_length=128)


class ConnectorCursor(FrozenContractModel):
    checkpoint_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    expected_sequence: int = Field(ge=0)
    next_sequence: int = Field(ge=1)
    checkpoint_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_contiguous_advance(self) -> ConnectorCursor:
        if self.next_sequence != self.expected_sequence + 1:
            raise ValueError("connector cursor must advance exactly one sequence")
        return self


class SourceEventEnvelope(FrozenContractModel):
    envelope_kind: Literal["source_event", "conversation_event"] = "source_event"
    schema_version: Literal[1] = 1
    connector_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    connector_version: str = Field(min_length=1, max_length=128)
    capture_surface: CaptureSurface
    source_identity: SourceIdentity
    project_hint: ProjectHint | None = None
    event_id: UUID
    external_event_id: str | None = Field(default=None, min_length=1, max_length=512)
    event_kind: CaptureEventKind
    supersedes_source_identity_key: str | None = Field(
        default=None, min_length=1, max_length=512
    )
    source_occurred_at: datetime | None = None
    source_occurred_end_at: datetime | None = None
    source_occurred_at_raw: str | None = Field(default=None, min_length=1, max_length=512)
    source_timezone: str | None = Field(default=None, min_length=1, max_length=128)
    source_time_precision: SourceTimePrecision | None = None
    source_sequence: int | None = Field(default=None, ge=0)
    source_revision_key: str | None = Field(default=None, min_length=1, max_length=512)
    captured_at: datetime
    source_type: SourceType
    content_hash: str = Field(pattern=SHA256_PATTERN)
    source_locator: SourceLocator | None = None
    parser_receipt: ParserCoverageReceipt
    policies: CapturePolicyReferences
    units: tuple[SourceUnitInput, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def validate_source_event(self) -> SourceEventEnvelope:
        if self.event_id.version != 7:
            raise ValueError("capture event_id must be UUIDv7")
        if type(self) is SourceEventEnvelope and self.envelope_kind != "source_event":
            raise ValueError("source-event envelope kind is invalid")
        if self.source_occurred_at and self.source_occurred_end_at:
            if self.source_occurred_end_at < self.source_occurred_at:
                raise ValueError("source occurrence end cannot precede start")
        is_control = self.event_kind == CaptureEventKind.SOURCE_DELETED
        parser_error = self.parser_receipt.status == ParserCoverageStatus.ERROR
        if is_control or parser_error:
            if self.units:
                raise ValueError("deleted or parser-error envelopes cannot contain units")
        elif not any(unit.is_observation for unit in self.units):
            raise ValueError("accepted envelopes require an observation unit")
        if self.event_kind in (
            CaptureEventKind.SOURCE_AMENDED,
            CaptureEventKind.TURN_AMENDED,
            CaptureEventKind.SOURCE_DELETED,
        ) and self.supersedes_source_identity_key is None:
            raise ValueError("amendment and deletion require a superseded identity")
        if self.event_kind not in (
            CaptureEventKind.SOURCE_AMENDED,
            CaptureEventKind.TURN_AMENDED,
            CaptureEventKind.SOURCE_DELETED,
        ) and self.supersedes_source_identity_key is not None:
            raise ValueError("only amendment or deletion may supersede source identity")
        if len(canonical_json_bytes(self.model_dump(mode="json", warnings="error"))) > CAPTURE_ENVELOPE_MAX_BYTES:
            raise ValueError("capture envelope exceeds its canonical byte limit")
        return self

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json", warnings="error"))

    @property
    def canonical_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", warnings="error"))


class ConversationEventEnvelope(SourceEventEnvelope):
    envelope_kind: Literal["conversation_event"] = "conversation_event"
    source_type: Literal[SourceType.TRANSCRIPT] = SourceType.TRANSCRIPT
    event_kind: Literal[
        CaptureEventKind.TURN_FINALIZED,
        CaptureEventKind.TURN_AMENDED,
    ]
    completion_status: CompletionStatus
    conversation: ConversationIdentity
    participants: tuple[ParticipantIdentity, ...] = Field(min_length=1, max_length=64)
    client: ClientIdentity

    @model_validator(mode="after")
    def validate_conversation_units(self) -> ConversationEventEnvelope:
        participant_ids = [item.participant_id for item in self.participants]
        if len(participant_ids) != len(set(participant_ids)):
            raise ValueError("conversation participant identities must be unique")
        observation_units = [unit for unit in self.units if unit.is_observation]
        if len(observation_units) != 1:
            raise ValueError("conversation envelopes require exactly one observation unit")
        observation_unit = observation_units[0]
        for unit in self.units:
            detail = unit.detail
            if not isinstance(detail, ConversationTurnDetail):
                raise ValueError("conversation envelopes require conversation-turn units")
            if (
                detail.conversation_id != self.conversation.conversation_id
                or detail.session_id != self.conversation.session_id
                or detail.branch_id != self.conversation.branch_id
            ):
                raise ValueError("conversation unit identity must match its envelope")
            if unit.is_observation and detail.turn_id != self.conversation.turn_id:
                raise ValueError("conversation observation must match its envelope turn")
            if not unit.is_observation and (
                unit.unit_kind != "turn"
                or unit.parent_unit_id != observation_unit.source_unit_id
                or detail.turn_id == self.conversation.turn_id
            ):
                raise ValueError(
                    "conversation evidence must be a distinct subordinate turn unit"
                )
            if detail.participant_id not in participant_ids:
                raise ValueError("conversation unit participant must be declared")
        return self


CaptureEnvelope = SourceEventEnvelope | ConversationEventEnvelope


class KernelCaptureBinding(FrozenContractModel):
    """Authority resolved by the kernel, never from adapter envelope fields."""

    tenant_id: UUID
    workspace_id: UUID
    access_scope_id: UUID
    source_object_id: UUID
    expected_source_object_schema_version: int = Field(ge=1)


class CaptureSubmissionCommand(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[12] = 12
    idempotency_key: str = Field(min_length=1, max_length=512)
    envelope: CaptureEnvelope
    envelope_canonical_json: str = Field(min_length=2, max_length=CAPTURE_ENVELOPE_MAX_BYTES)
    envelope_hash: str = Field(pattern=SHA256_PATTERN)
    cursor: ConnectorCursor | None = None
    canonical_accept_command: AcceptSourceEventCommand | None = None
    binding: KernelCaptureBinding

    @model_validator(mode="after")
    def validate_submission(self) -> CaptureSubmissionCommand:
        rendered = self.envelope.canonical_bytes.decode("utf-8")
        if self.envelope_canonical_json != rendered:
            raise ValueError("envelope_canonical_json must be canonical")
        if self.envelope_hash != self.envelope.canonical_hash:
            raise ValueError("envelope_hash must match the canonical envelope")
        control = (
            self.envelope.parser_receipt.status == ParserCoverageStatus.ERROR
            or self.envelope.event_kind == CaptureEventKind.SOURCE_DELETED
        )
        if control:
            if self.canonical_accept_command is not None or self.cursor is not None:
                raise ValueError("quarantine and deletion cannot advance a connector cursor")
        elif self.canonical_accept_command is None:
            raise ValueError("accepted capture requires the canonical transaction command")
        return self


class CaptureSubmissionReceipt(FrozenContractModel):
    delivery_id: UUID
    event_id: UUID | None = None
    idempotency_receipt_id: UUID
    operation_id: str = Field(min_length=1, max_length=512)
    request_hash: str = Field(pattern=SHA256_PATTERN)
    checkpoint_sequence: int | None = Field(default=None, ge=1)
    ingress_status: CaptureIngressStatus
    authority_principal_id: UUID
    replayed: bool
    durable: Literal[True] = True


class CaptureInboxItem(FrozenContractModel):
    delivery_id: UUID
    event_id: UUID | None = None
    source_object_id: UUID
    envelope_kind: Literal["source_event", "conversation_event"]
    source_product: str
    source_identity_key: str
    ingress_status: CaptureIngressStatus
    materialization_status: MaterializationStatus
    enrichment_status: EnrichmentStatus
    checkpoint_sequence: int | None = None
    delivered_at: datetime


class CaptureStatusEvent(FrozenContractModel):
    delivery_id: UUID
    status_sequence: int = Field(ge=1)
    ingress_status: CaptureIngressStatus
    occurred_at: datetime


class CaptureDeliveryDetail(FrozenContractModel):
    delivery_id: UUID
    event_kind: CaptureEventKind
    supersedes_event_id: UUID | None = None
    source_locator: SourceLocator | None = None
    parser_receipt: ParserCoverageReceipt
    source_identity: SourceIdentity
    conversation: ConversationIdentity | None = None
    participants: tuple[ParticipantIdentity, ...] = ()
    client: ClientIdentity | None = None
    project_hint: ProjectHint | None = None
    policies: CapturePolicyReferences


class CaptureSubmissionPort(Protocol):
    def submit(
        self,
        envelope: CaptureEnvelope,
        *,
        idempotency_key: str,
        cursor: ConnectorCursor | None,
        recorded_at: datetime,
    ) -> CaptureSubmissionReceipt: ...


def compile_capture_submission(
    envelope: CaptureEnvelope,
    *,
    binding: KernelCaptureBinding,
    idempotency_key: str,
    semantic_task_id: UUID | None,
    cursor: ConnectorCursor | None,
) -> CaptureSubmissionCommand:
    """Compile an adapter envelope only after the kernel resolves authority."""

    if isinstance(envelope, ConversationEventEnvelope):
        envelope = ConversationEventEnvelope.model_validate(
            envelope.model_dump(mode="json", warnings="error")
        )
    else:
        envelope = SourceEventEnvelope.model_validate(
            envelope.model_dump(mode="json", warnings="error")
        )
    control = (
        envelope.parser_receipt.status == ParserCoverageStatus.ERROR
        or envelope.event_kind == CaptureEventKind.SOURCE_DELETED
    )
    canonical_command: AcceptSourceEventCommand | None = None
    if not control:
        if semantic_task_id is None:
            raise ValueError("accepted capture requires a kernel semantic task ID")
        if semantic_task_id.version != 7:
            raise ValueError("kernel semantic task ID must be UUIDv7")
        actor: ParticipantIdentity | None = None
        if isinstance(envelope, ConversationEventEnvelope):
            observation_detail = next(
                unit.detail for unit in envelope.units if unit.is_observation
            )
            if not isinstance(observation_detail, ConversationTurnDetail):
                raise ValueError("conversation observation detail is unavailable")
            actor = next(
                participant
                for participant in envelope.participants
                if participant.participant_id == observation_detail.participant_id
            )
        actor_id = actor.participant_id if actor else "unknown"
        actor_kind = actor.kind if actor else "unknown"
        source_ref = (
            f"source:{envelope.source_identity.source_product}:"
            f"{envelope.source_identity.source_object_stable_id}"
        )
        checkpoint = None
        if cursor is not None:
            checkpoint = CaptureCheckpoint(
                checkpoint_key=cursor.checkpoint_key,
                expected_sequence=cursor.expected_sequence,
                next_sequence=cursor.next_sequence,
                checkpoint_hash=cursor.checkpoint_hash,
            )
        canonical_command = AcceptSourceEventCommand(
            tenant_id=binding.tenant_id,
            workspace_id=binding.workspace_id,
            access_scope_id=binding.access_scope_id,
            source_object_id=binding.source_object_id,
            expected_source_object_schema_version=(
                binding.expected_source_object_schema_version
            ),
            idempotency_key=idempotency_key,
            event=SourceEventInput(
                event_id=envelope.event_id,
                source_type=envelope.source_type,
                source_system=envelope.source_identity.source_product,
                installation_id=envelope.source_identity.installation_id,
                external_event_id=envelope.external_event_id,
                external_id_scope=envelope.source_identity.external_id_scope,
                source_identity_key=envelope.source_identity.source_identity_key,
                session_id=(
                    envelope.conversation.session_id
                    if isinstance(envelope, ConversationEventEnvelope)
                    else None
                ),
                actor_id=actor_id,
                actor_kind=str(actor_kind),
                source_occurred_at=envelope.source_occurred_at,
                source_occurred_end_at=envelope.source_occurred_end_at,
                source_occurred_at_raw=envelope.source_occurred_at_raw,
                source_timezone=envelope.source_timezone,
                source_time_precision=envelope.source_time_precision,
                source_sequence=envelope.source_sequence,
                source_revision_key=envelope.source_revision_key,
                parser_contract_version=(
                    f"{envelope.parser_receipt.parser_id}."
                    f"{envelope.parser_receipt.parser_version}"
                ),
                observation_unit_policy_version=(
                    envelope.policies.observation_unit_policy_version
                ),
                captured_at=envelope.captured_at,
                source_ref=source_ref,
                content_hash=envelope.content_hash,
            ),
            units=envelope.units,
            checkpoint=checkpoint,
            semantic_task=CanonicalSemanticTaskRequest(
                task_id=semantic_task_id,
                idempotency_key=f"capture.semantic:{idempotency_key}",
            ),
        )
    canonical_json = envelope.canonical_bytes.decode("utf-8")
    return CaptureSubmissionCommand(
        idempotency_key=idempotency_key,
        envelope=envelope,
        envelope_canonical_json=canonical_json,
        envelope_hash=envelope.canonical_hash,
        cursor=cursor,
        canonical_accept_command=canonical_command,
        binding=binding,
    )
