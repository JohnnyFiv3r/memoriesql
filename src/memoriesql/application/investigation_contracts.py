"""Closed PR-05 wire identities and serialization, without execution or authority.

These internal mechanics bind explicit disclosure contexts. Validation does not
authorize a reference, acknowledge a result, consume a budget or run any SQL.
The trusted persistence/execution bridge must establish those facts separately.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    Field,
    StrictBool,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from memoriesql.application.agent_sql_catalog import (
    SqlAdmissionError,
    SqlCatalog,
    SqlParameter,
    _value,
)
from memoriesql.application.semantic_task_contracts import (
    FrozenContractModel,
    canonical_json_bytes,
)


def _text(value: str) -> str:
    value.encode("utf-8")
    if "\x00" in value:
        raise ValueError("text cannot contain NUL")
    return value


def _utc(value: str) -> str:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?Z",
        value,
    ):
        raise ValueError("UTC timestamp required")
    datetime.fromisoformat(value[:-1] + "+00:00")
    return value


def _int8(value: str) -> str:
    if not re.fullmatch(r"-?(0|[1-9][0-9]*)", value) or value == "-0":
        raise ValueError("canonical int8 required")
    if not -(2**63) <= int(value) < 2**63:
        raise ValueError("int8 out of range")
    return value


def _positive_int8(value: str) -> str:
    _int8(value)
    if int(value) < 1:
        raise ValueError("positive int8 required")
    return value


Text = Annotated[str, StringConstraints(strict=True), AfterValidator(_text)]
Ref = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ),
]
Hash = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
]
Timestamp = Annotated[Text, AfterValidator(_utc)]
PositiveInt8 = Annotated[Text, AfterValidator(_positive_int8)]
Control = Annotated[int, Field(strict=True, ge=0)]
Alias = Annotated[Text, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
SaveName = Annotated[Text, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Scalar = Text | StrictBool | None


def _bytes(value: str, maximum: int) -> None:
    if len(value.encode("utf-8")) > maximum:
        raise ValueError("text byte allocation exceeded")


class InvestigationRequestError(ValueError):
    """Safe classified error; never retain or echo caller text/identifiers."""

    def __init__(self, code: Literal["syntax", "binding", "type", "feature"]) -> None:
        self.code = code
        super().__init__(code)

    @property
    def outcome(self) -> Literal["invalid_request", "unsupported_query"]:
        return "unsupported_query" if self.code == "feature" else "invalid_request"


class ResultPin(FrozenContractModel):
    result_id: Ref
    content_digest: Hash


class CheckpointPin(FrozenContractModel):
    checkpoint_id: Ref
    manifest_digest: Hash


class AutomaticBasis(FrozenContractModel):
    kind: Literal["automatic"]


class NamedSaveBasis(FrozenContractModel):
    kind: Literal["named_save"]
    save_id: Ref
    revision: PositiveInt8


RetentionBasis = Annotated[AutomaticBasis | NamedSaveBasis, Field(discriminator="kind")]


class StandaloneAccess(FrozenContractModel):
    kind: Literal["standalone"]


class CheckpointResultAccess(FrozenContractModel):
    kind: Literal["checkpoint"]
    checkpoint_id: Ref
    manifest_digest: Hash
    root: ResultPin
    basis: RetentionBasis


AccessContext = Annotated[
    StandaloneAccess | CheckpointResultAccess, Field(discriminator="kind")
]


class CheckpointAccess(FrozenContractModel):
    checkpoint: CheckpointPin
    basis: RetentionBasis


class ResultAccess(FrozenContractModel):
    result: ResultPin
    access: AccessContext

    @model_validator(mode="after")
    def selected_root(self) -> ResultAccess:
        if isinstance(self.access, CheckpointResultAccess):
            if self.access.root != self.result:
                raise ValueError("context must bind this exact root pin")
        return self


class InputBinding(ResultAccess):
    alias: Alias


class InspectionResult(FrozenContractModel):
    pin: ResultPin
    access: AccessContext

    @model_validator(mode="after")
    def selected_root(self) -> InspectionResult:
        ResultAccess(result=self.pin, access=self.access)
        return self


class FactRef(FrozenContractModel):
    result_id: Ref
    row_ref: PositiveInt8


class ObservationEvidence(FrozenContractModel):
    kind: Literal["observation"]
    ref: Ref
    version_ref: Ref


class StatementEvidence(FrozenContractModel):
    kind: Literal["statement"]
    ref: Ref
    version_ref: Ref


class SourceEvidence(FrozenContractModel):
    kind: Literal["source"]
    ref: Ref
    unit_ref: Ref
    content_sha256: Hash


class ResultFactEvidence(FrozenContractModel):
    kind: Literal["result_fact"]
    fact: FactRef


EvidenceRef = Annotated[
    ObservationEvidence | StatementEvidence | SourceEvidence | ResultFactEvidence,
    Field(discriminator="kind"),
]


class WireParameter(FrozenContractModel):
    position: Annotated[int, Field(strict=True, ge=1, le=64)]
    type: Text
    value: Scalar | tuple[Text | StrictBool, ...]

    def sql_parameter(self, catalog: SqlCatalog) -> SqlParameter:
        array = self.type.endswith("[]")
        name = self.type[:-2] if array else self.type
        physical = catalog.reference_types.get(name, name)
        if physical not in {
            "uuid",
            "text",
            "bool",
            "int8",
            "numeric",
            "float8",
            "timestamptz",
        }:
            raise InvestigationRequestError("type")
        if array:
            if not isinstance(self.value, tuple):
                raise InvestigationRequestError("type")
            values: tuple[Any, ...] = self.value
        else:
            if isinstance(self.value, tuple):
                raise InvestigationRequestError("type")
            values = (self.value,)
        if len(values) > 64:
            raise InvestigationRequestError("type")
        for value in values:
            try:
                _value(physical, value)
            except SqlAdmissionError:
                raise InvestigationRequestError("type") from None
            if physical == "int8" and value is not None:
                _int8(str(value))
            if physical == "numeric" and value is not None:
                if encode_result_scalar(Decimal(str(value))) != value:
                    raise InvestigationRequestError("type")
        # No anchor admission is inferred here. The trusted bridge subsequently
        # calls catalog.parameters with independently authorized anchors.
        return SqlParameter(self.position, self.type, self.value)


class Scope(FrozenContractModel):
    source_refs: tuple[Ref, ...]
    known_at: Timestamp
    view: Literal["resolved", "historical"]


class RecursionRequest(FrozenContractModel):
    cte: Text
    depth_column: Text
    node_column: Text
    max_depth: Annotated[int, Field(strict=True, ge=1, le=8)]


class CandidateRequest(FrozenContractModel):
    surface: Literal["authored", "source", "both"]
    query: Annotated[Text, Field(min_length=1)]
    max_candidates: Annotated[int, Field(strict=True, ge=1, le=2000)]

    @model_validator(mode="after")
    def query_bytes(self) -> CandidateRequest:
        _bytes(self.query, 4096)
        return self


class Request(FrozenContractModel):
    contract_version: Literal[1]
    run_ref: Ref
    step_key: Ref

    @field_validator("contract_version", mode="before")
    @classmethod
    def version_integer(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("integer version required")
        return value


class QueryRequest(Request):
    kind: Literal["query"]
    catalog_hash: Hash
    sql: Annotated[Text, Field(min_length=1)]
    parameters: Annotated[tuple[WireParameter, ...], Field(max_length=64)]
    inputs: Annotated[tuple[InputBinding, ...], Field(max_length=8)]
    parents: Annotated[tuple[ResultAccess, ...], Field(max_length=8)]
    scope: Scope
    intent: Literal["discover", "enumerate", "refine", "expand", "refresh"]
    max_result_bytes: Annotated[int, Field(strict=True, ge=1, le=64 * 1024 * 1024)]
    page_size: Annotated[int, Field(strict=True, ge=1, le=50)]
    recursion: RecursionRequest | None = None
    candidate_profile_ref: Ref | None = None
    candidate_request: CandidateRequest | None = None

    @model_validator(mode="after")
    def composition(self) -> QueryRequest:
        _bytes(self.sql, 32768)
        if len({p.position for p in self.parameters}) != len(self.parameters):
            raise ValueError("duplicate parameter positions")
        if (
            sum(
                len(json.dumps(p.value, ensure_ascii=True).encode())
                for p in self.parameters
            )
            > 65536
        ):
            raise ValueError("parameter byte allocation exceeded")
        if len({i.alias for i in self.inputs}) != len(self.inputs):
            raise ValueError("duplicate input aliases")
        _unique_results(tuple(p.result for p in self.parents))
        if not all(
            i.result in tuple(p.result for p in self.parents) for i in self.inputs
        ):
            raise ValueError("all inputs require exact parent pins")
        if self.intent in {"discover", "enumerate"} and (self.inputs or self.parents):
            raise ValueError("live discovery cannot bind saved values")
        if self.intent == "refine" and not self.inputs:
            raise ValueError("refinement requires saved inputs")
        if self.intent in {"expand", "refresh"} and (self.inputs or not self.parents):
            raise ValueError("new live frames require parents without saved values")
        if (self.candidate_profile_ref is None) != (self.candidate_request is None):
            raise ValueError("candidate request requires explicit profile")
        return self


class ReuseRequest(Request, ResultAccess):
    kind: Literal["reuse_result"]
    page_size: Annotated[int, Field(strict=True, ge=1, le=50)]
    cursor: Text | None = None


class ObservationTarget(FrozenContractModel):
    bead_id: Ref
    bead_version_id: Ref


class InspectRequest(Request):
    kind: Literal["inspect"]
    observation_refs: tuple[ObservationTarget, ...]
    relation_refs: tuple[Ref, ...]
    result: InspectionResult | None = None
    provenance_ref: Ref | None = None
    facets: tuple[
        Literal["content", "provenance", "lifecycle", "evidence_metadata"], ...
    ]
    view: Literal["resolved", "historical"]
    known_at: Timestamp
    cursor: Text | None = None

    @model_validator(mode="after")
    def targets(self) -> InspectRequest:
        if len(self.observation_refs) + len(self.relation_refs) > 16:
            raise ValueError("inspection target allocation exceeded")
        if not (self.observation_refs or self.relation_refs or self.result):
            raise ValueError("inspection requires a target")
        if self.provenance_ref is not None and self.result is None:
            raise ValueError("provenance requires its visible result")
        return self


class HydrationSelection(FrozenContractModel):
    evidence_ref: Ref
    part_id: Ref
    lineage_ordinal: Control | None = None
    representation: Literal["raw_bytes", "normalized_text"]
    start: Control
    end: Control
    origin: ResultAccess | None = None

    @model_validator(mode="after")
    def interval(self) -> HydrationSelection:
        if self.end < self.start:
            raise ValueError("reversed half-open interval")
        if self.representation == "raw_bytes" and self.lineage_ordinal is None:
            raise ValueError("raw selection requires recorded lineage ordinal")
        if self.end - self.start > 16384:
            raise ValueError("selected span allocation exceeded")
        return self


class HydrateRequest(Request):
    kind: Literal["hydrate_source"]
    selections: Annotated[
        tuple[HydrationSelection, ...], Field(min_length=1, max_length=8)
    ]
    max_bytes: Annotated[int, Field(strict=True, ge=1, le=64 * 1024)]
    cursor: Text | None = None


class Progress(FrozenContractModel):
    status: Literal["investigating", "paused", "ready_for_pr06"]
    note: Text

    @model_validator(mode="after")
    def note_bytes(self) -> Progress:
        _bytes(self.note, 2048)
        return self


class Finding(FrozenContractModel):
    text: Text
    facts: tuple[FactRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]


def _unique_results(roots: tuple[ResultPin, ...]) -> None:
    if len({r.result_id for r in roots}) != len(roots):
        raise ValueError("duplicate result identities")


def _working_text(question: str, findings: tuple[Finding, ...]) -> None:
    _bytes(question, 4096)
    if sum(len(f.text.encode("utf-8")) for f in findings) > 8192:
        raise ValueError("finding text allocation exceeded")


class CheckpointRequest(Request):
    kind: Literal["checkpoint"]
    investigation_id: Ref | None = None
    branch_id: Ref | None = None
    expected_head: CheckpointPin | None = None
    question: Text
    progress: Progress
    findings: Annotated[tuple[Finding, ...], Field(max_length=16)]
    roots: Annotated[tuple[ResultAccess, ...], Field(max_length=32)]

    @model_validator(mode="after")
    def append(self) -> CheckpointRequest:
        count = sum(
            v is not None
            for v in (self.investigation_id, self.branch_id, self.expected_head)
        )
        if count not in {0, 3}:
            raise ValueError("append requires investigation, branch and compared head")
        _working_text(self.question, self.findings)
        _unique_results(tuple(r.result for r in self.roots))
        return self


class ReadCheckpointRequest(Request):
    kind: Literal["read_checkpoint"]
    access: CheckpointAccess


class SaveCheckpointRequest(Request):
    kind: Literal["save_checkpoint"]
    access: CheckpointAccess
    name: SaveName
    expected_save_revision: PositiveInt8 | None = None


class ResolveSaveRequest(Request):
    kind: Literal["resolve_save"]
    investigation_id: Ref
    name: SaveName


class ReleaseSaveRequest(Request):
    kind: Literal["release_save"]
    investigation_id: Ref
    name: SaveName
    expected_save_revision: PositiveInt8


class RestoreCheckpointRequest(Request):
    kind: Literal["restore_checkpoint", "branch_investigation"]
    access: CheckpointAccess
    selected_roots: Annotated[tuple[ResultPin, ...], Field(max_length=32)]

    @model_validator(mode="after")
    def selection(self) -> RestoreCheckpointRequest:
        _unique_results(self.selected_roots)
        return self


InvestigationRequest = Annotated[
    QueryRequest
    | ReuseRequest
    | InspectRequest
    | HydrateRequest
    | CheckpointRequest
    | ReadCheckpointRequest
    | SaveCheckpointRequest
    | ResolveSaveRequest
    | ReleaseSaveRequest
    | RestoreCheckpointRequest,
    Field(discriminator="kind"),
]
_REQUEST: TypeAdapter[InvestigationRequest] = TypeAdapter(InvestigationRequest)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvestigationRequestError("syntax")
        result[key] = value
    return result


def _number(value: str) -> Any:
    raise InvestigationRequestError("type")


def parse_investigation_request(data: bytes) -> InvestigationRequest:
    """Decode closed wire JSON; reference authorization remains trusted work."""
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_number,
            parse_constant=_number,
        )
        request = _REQUEST.validate_python(document)
        if isinstance(request, QueryRequest):
            catalog = SqlCatalog.installed()
            if request.catalog_hash != catalog.hash:
                raise InvestigationRequestError("feature")
            for parameter in request.parameters:
                parameter.sql_parameter(catalog)
        return request
    except InvestigationRequestError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise InvestigationRequestError("syntax") from None
    except (ValidationError, ValueError, TypeError, OverflowError, SqlAdmissionError):
        raise InvestigationRequestError("binding") from None


def request_fingerprint(request: InvestigationRequest) -> str:
    """Bind the full validated request, including explicit context and cursor.

    No authority, budget or runtime context is inferred. Optional-field presence
    is preserved; caller-supplied fields are neither ignored nor silently added.
    """
    return hashlib.sha256(
        result_json_bytes(request.model_dump(mode="json", exclude_unset=True))
    ).hexdigest()


def encode_result_scalar(value: Any) -> str | bool | None:
    """Native database scalar to the exact approved result-json-v1 wire value."""
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return _int8(str(value))
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("timestamp needs an offset")
        return (
            value.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("numeric must be finite")
        if value == 0:
            return "0"
        result = format(value, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("float8 must be finite")
        return value.hex()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, str):
        return _text(value)
    raise ValueError("unsupported result scalar")


def result_json_bytes(value: Any) -> bytes:
    """Canonical bytes of already typed wire data, never implicit SQL coercion."""
    # The standard encoder rejects circular containers before our scalar walk.
    encoded = canonical_json_bytes(value)
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            _text(item)
        elif item is None or type(item) in {bool, int}:
            pass
        elif isinstance(item, dict):
            if any(not isinstance(k, str) for k in item):
                raise ValueError("JSON object keys must be text")
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, tuple | list):
            pending.extend(item)
        else:
            raise ValueError("canonical JSON requires typed wire scalars")
    return encoded
