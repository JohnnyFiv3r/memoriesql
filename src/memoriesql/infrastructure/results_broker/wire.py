"""Closed framing and peer identity for the trusted query host socket.

Every frame is a four-byte big-endian length followed by that many bytes. One
request is a header frame and a body frame; one reply is a single frame. The
body and reply are the executor's (or existing reader's) bytes, never parsed
or re-encoded in transit. Framing errors are answered without detail.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from memoriesql.application.agent_sql_results import CONTRACT_VERSION
from memoriesql.application.investigation_contracts import result_json_bytes
from memoriesql.infrastructure.results_broker import PROTOCOL_VERSION

MAX_FRAME_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 4096
MIN_CREDENTIAL_CHARACTERS = 32
MAX_CREDENTIAL_CHARACTERS = 512
READ_OPERATIONS = ("inspect", "source", "relations")
OPERATIONS = ("start_run", "handle", *READ_OPERATIONS)

# The executor's own canonical unavailable reply: no path, cause or detail.
UNAVAILABLE_REPLY = result_json_bytes(
    {
        "contract_version": CONTRACT_VERSION,
        "outcome": "unavailable",
        "error": {"code": "unavailable"},
    }
)


def read_reply(payload: dict[str, str]) -> bytes:
    """Encode an existing reader refusal exactly as the in-process path reports it."""
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


READ_UNAVAILABLE_REPLY = read_reply(
    {"outcome": "unavailable", "reason": "resource_unavailable"}
)
READ_FAILED_REPLY = read_reply({"outcome": "failed", "reason": "core_read_failed"})


class FrameError(Exception):
    """A malformed, oversized, late or truncated frame; never carries content."""


class PeerIdentityUnavailable(Exception):
    """The kernel could not report the peer's identity; the caller fails closed."""


@dataclass(frozen=True)
class RequestHeader:
    operation: str
    credential: str
    workspace_id: UUID


def send_frame(connection: socket.socket, data: bytes) -> None:
    if len(data) > MAX_FRAME_BYTES:
        raise FrameError("frame exceeds its bound")
    connection.sendall(struct.pack(">I", len(data)) + data)


def receive_frame(
    connection: socket.socket, limit: int, *, deadline: float | None = None
) -> bytes:
    """Read one whole frame no larger than `limit`, within an absolute deadline."""
    (size,) = struct.unpack(">I", _receive_exact(connection, 4, deadline))
    if size > limit:
        raise FrameError("frame exceeds its bound")
    return _receive_exact(connection, size, deadline)


def _receive_exact(
    connection: socket.socket, size: int, deadline: float | None
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise FrameError("frame deadline elapsed")
            connection.settimeout(left)
        try:
            chunk = connection.recv(min(remaining, 65536))
        except TimeoutError:
            raise FrameError("frame deadline elapsed") from None
        if not chunk:
            raise FrameError("connection closed inside a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_header(operation: str, credential: str, workspace_id: UUID) -> bytes:
    header = RequestHeader(operation, credential, workspace_id)
    _validate(header)
    return json.dumps(
        {
            "credential": credential,
            "op": operation,
            "v": PROTOCOL_VERSION,
            "workspace_id": str(workspace_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")


def decode_header(data: bytes) -> RequestHeader:
    """Parse the closed header; unknown, duplicate or mistyped fields are refused."""
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, ValueError):
        raise FrameError("malformed header") from None
    if not isinstance(value, dict) or set(value) != {
        "credential",
        "op",
        "v",
        "workspace_id",
    }:
        raise FrameError("malformed header")
    version, operation = value["v"], value["op"]
    credential, workspace = value["credential"], value["workspace_id"]
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise FrameError("unsupported protocol version")
    if not all(type(item) is str for item in (operation, credential, workspace)):
        raise FrameError("malformed header")
    try:
        workspace_id = UUID(workspace)
    except ValueError:
        raise FrameError("malformed header") from None
    header = RequestHeader(operation, credential, workspace_id)
    _validate(header)
    return header


def _validate(header: RequestHeader) -> None:
    if header.operation not in OPERATIONS:
        raise FrameError("unknown operation")
    credential = header.credential
    if not (
        MIN_CREDENTIAL_CHARACTERS <= len(credential) <= MAX_CREDENTIAL_CHARACTERS
        and all("!" <= character <= "~" for character in credential)
    ):
        raise FrameError("malformed credential")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def _no_constant(name: str) -> Any:
    raise ValueError("non-finite number")


# struct xucred {u_int cr_version; uid_t cr_uid; short cr_ngroups; gid_t cr_groups[16]}
_XUCRED_SIZE = 76
_SOL_LOCAL = 0
_LOCAL_PEERPID = 0x002


def peer_uid(connection: socket.socket) -> int:
    """The effective uid the kernel recorded for the connecting peer."""
    try:
        if sys.platform == "darwin":
            data = connection.getsockopt(
                _SOL_LOCAL, socket.LOCAL_PEERCRED, _XUCRED_SIZE
            )
            version, uid = struct.unpack_from("=II", data)
            if version != 0:
                raise PeerIdentityUnavailable("unknown credential layout")
            return int(uid)
        if sys.platform.startswith("linux"):
            data = connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            _pid, uid, _gid = struct.unpack("3i", data)
            return int(uid)
    except (OSError, struct.error):
        raise PeerIdentityUnavailable("peer identity unavailable") from None
    raise PeerIdentityUnavailable("unsupported platform")


def peer_pid(connection: socket.socket) -> int | None:
    """Best-effort peer process id for operator logs; never an authority."""
    try:
        if sys.platform == "darwin":
            data = connection.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID, 4)
            return int(struct.unpack("=i", data)[0])
        if sys.platform.startswith("linux"):
            data = connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            return int(struct.unpack("3i", data)[0])
    except (OSError, struct.error):
        return None
    return None
