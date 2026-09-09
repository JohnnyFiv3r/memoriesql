from __future__ import annotations

import base64
import hashlib
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    canonical_json_bytes,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

HOST_HELPER_PROTOCOL_VERSION = 1
HOST_HELPER_MAX_REQUEST_BYTES = 16_384
HOST_HELPER_MAX_RESPONSE_BYTES = 393_216
HOST_HELPER_MAX_READ_BYTES = 262_144
HOST_HELPER_MAX_CAPABILITIES = 32


class HostReadStatus(StrEnum):
    DATA = "data"
    END_OF_FILE = "end_of_file"
    PERMISSION_REVOKED = "permission_revoked"
    PERMISSION_DENIED = "permission_denied"
    SOURCE_UNAVAILABLE = "source_unavailable"
    TRUNCATED = "truncated"
    ROTATED_OR_REPLACED = "rotated_or_replaced"
    IDENTITY_DRIFT = "identity_drift"


class HostHelperState(StrEnum):
    READY = "ready"
    STOPPING = "stopping"


class FileIdentity(FrozenContractModel):
    platform: str = Field(min_length=1, max_length=32)
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    birth_time_ns: int | None = Field(default=None, ge=0)

    @property
    def stable_key(self) -> str:
        birth = self.birth_time_ns if self.birth_time_ns is not None else "unknown"
        return f"{self.platform}:{self.device}:{self.inode}:{birth}"


class ApprovedSourceGrant(FrozenContractModel):
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    approved_root: str = Field(min_length=1, max_length=4096)
    relative_source: str = Field(min_length=1, max_length=4096)
    credential_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_approved_path(self) -> ApprovedSourceGrant:
        if "\x00" in self.approved_root or "\x00" in self.relative_source:
            raise ValueError("approved source paths cannot contain NUL")
        approved_root = Path(self.approved_root)
        if not approved_root.is_absolute():
            raise ValueError("approved_root must be absolute on the helper host")
        if str(approved_root) != self.approved_root or ".." in approved_root.parts:
            raise ValueError("approved_root must use normalized absolute syntax")
        if "\\" in self.relative_source:
            raise ValueError("relative_source must use protocol forward slashes")
        relative = PurePosixPath(self.relative_source)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise ValueError("relative_source must stay beneath its approved root")
        if relative.as_posix() != self.relative_source:
            raise ValueError("relative_source must use normalized protocol syntax")
        return self


class HostHelperBootstrapRequest(FrozenContractModel):
    protocol_version: Literal[1] = 1
    operation: Literal["initialize"] = "initialize"
    request_id: UUID
    session_id: UUID
    capabilities: tuple[ApprovedSourceGrant, ...] = Field(
        min_length=1, max_length=HOST_HELPER_MAX_CAPABILITIES
    )

    @model_validator(mode="after")
    def require_unique_capabilities(self) -> HostHelperBootstrapRequest:
        capability_ids = [grant.capability_id for grant in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("host helper capability IDs must be unique")
        return self


class HostReadRequest(FrozenContractModel):
    protocol_version: Literal[1] = 1
    operation: Literal["read_append"] = "read_append"
    request_id: UUID
    capability_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    capability_credential: str = Field(min_length=32, max_length=256, repr=False)
    expected_identity: FileIdentity | None = None
    byte_offset: int = Field(ge=0)
    max_bytes: int = Field(ge=1, le=HOST_HELPER_MAX_READ_BYTES)


class HostHealthRequest(FrozenContractModel):
    protocol_version: Literal[1] = 1
    operation: Literal["health"] = "health"
    request_id: UUID


class HostShutdownRequest(FrozenContractModel):
    protocol_version: Literal[1] = 1
    operation: Literal["shutdown"] = "shutdown"
    request_id: UUID


class HostReadResponse(FrozenContractModel):
    protocol_version: Literal[1] = 1
    response_kind: Literal["read"] = "read"
    request_id: UUID
    status: HostReadStatus
    file_identity: FileIdentity | None = None
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    observed_size: int = Field(ge=0)
    data_base64: str = Field(max_length=349_528)
    data_sha256: str = Field(pattern=SHA256_PATTERN)
    end_of_file: bool
    diagnostic_code: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )

    @model_validator(mode="after")
    def validate_bounded_read(self) -> HostReadResponse:
        try:
            payload = base64.b64decode(self.data_base64, validate=True)
        except ValueError as exc:
            raise ValueError(
                "host helper response data must be canonical base64"
            ) from exc
        if len(payload) > HOST_HELPER_MAX_READ_BYTES:
            raise ValueError("host helper response exceeds the read bound")
        if hashlib.sha256(payload).hexdigest() != self.data_sha256:
            raise ValueError("host helper response hash does not match its bytes")
        if self.end_offset != self.start_offset + len(payload):
            raise ValueError("host helper response offsets do not bind its bytes")
        if self.status in {
            HostReadStatus.DATA,
            HostReadStatus.END_OF_FILE,
        }:
            if self.file_identity is None:
                raise ValueError("successful host reads require file identity")
            if self.end_offset > self.observed_size:
                raise ValueError("successful host read exceeds observed file size")
            if self.end_of_file and self.end_offset != self.observed_size:
                raise ValueError("end-of-file flag must bind the observed file size")
        elif payload:
            raise ValueError("failed host reads cannot disclose source bytes")
        if self.status == HostReadStatus.DATA and not payload:
            raise ValueError("data response requires source bytes")
        if self.status == HostReadStatus.END_OF_FILE and payload:
            raise ValueError("end-of-file response cannot contain bytes")
        if self.status == HostReadStatus.END_OF_FILE and not self.end_of_file:
            raise ValueError("end-of-file status requires its terminal flag")
        return self

    @property
    def data(self) -> bytes:
        return base64.b64decode(self.data_base64, validate=True)


class HostHealthResponse(FrozenContractModel):
    protocol_version: Literal[1] = 1
    response_kind: Literal["health"] = "health"
    request_id: UUID
    session_id: UUID
    state: HostHelperState
    configured_capabilities: int = Field(ge=0, le=HOST_HELPER_MAX_CAPABILITIES)
    revoked_capabilities: int = Field(ge=0, le=HOST_HELPER_MAX_CAPABILITIES)
    last_read_request_id: UUID | None = None
    last_read_status: HostReadStatus | None = None


class HostBootstrapResponse(FrozenContractModel):
    protocol_version: Literal[1] = 1
    response_kind: Literal["initialize"] = "initialize"
    request_id: UUID
    session_id: UUID
    state: Literal[HostHelperState.READY] = HostHelperState.READY
    configured_capabilities: int = Field(ge=1, le=HOST_HELPER_MAX_CAPABILITIES)


class HostShutdownResponse(FrozenContractModel):
    protocol_version: Literal[1] = 1
    response_kind: Literal["shutdown"] = "shutdown"
    request_id: UUID
    session_id: UUID
    state: Literal[HostHelperState.STOPPING] = HostHelperState.STOPPING


type HostProtocolRequest = Annotated[
    HostReadRequest | HostHealthRequest | HostShutdownRequest,
    Field(discriminator="operation"),
]
type HostProtocolResponse = Annotated[
    HostBootstrapResponse
    | HostReadResponse
    | HostHealthResponse
    | HostShutdownResponse,
    Field(discriminator="response_kind"),
]

_REQUEST_ADAPTER: TypeAdapter[HostProtocolRequest] = TypeAdapter(HostProtocolRequest)
_RESPONSE_ADAPTER: TypeAdapter[HostProtocolResponse] = TypeAdapter(HostProtocolResponse)


def credential_sha256(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def parse_bootstrap_request(payload: bytes) -> HostHelperBootstrapRequest:
    if len(payload) > HOST_HELPER_MAX_REQUEST_BYTES:
        raise ValueError("host helper bootstrap exceeds the protocol bound")
    return HostHelperBootstrapRequest.model_validate_json(payload)


def parse_protocol_request(payload: bytes) -> HostProtocolRequest:
    if len(payload) > HOST_HELPER_MAX_REQUEST_BYTES:
        raise ValueError("host helper request exceeds the protocol bound")
    return _REQUEST_ADAPTER.validate_json(payload)


def parse_protocol_response(payload: bytes) -> HostProtocolResponse:
    if len(payload) > HOST_HELPER_MAX_RESPONSE_BYTES:
        raise ValueError("host helper response exceeds the protocol bound")
    return _RESPONSE_ADAPTER.validate_json(payload)


def encode_protocol_message(message: FrozenContractModel) -> bytes:
    payload = canonical_json_bytes(message.model_dump(mode="json", warnings="error"))
    if len(payload) > HOST_HELPER_MAX_RESPONSE_BYTES:
        raise ValueError("host helper protocol message exceeds the response bound")
    return payload
