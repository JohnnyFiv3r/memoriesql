"""Agent-side transport to the trusted query host.

The client holds only the socket path, the workspace id and its own paired
secret. Before sending anything it checks, with the kernel's peer credentials,
that the listening process runs as the uid that owns the socket's directory
and that this uid is not its own: a socket created by the caller's own uid (or
anything it can run) is refused, so an impostor cannot harvest credentials.
Replies are returned byte for byte; the transport never raises for outcomes.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import time
from pathlib import Path
from uuid import UUID

from memoriesql.infrastructure.results_broker.wire import (
    MAX_FRAME_BYTES,
    READ_OPERATIONS,
    READ_UNAVAILABLE_REPLY,
    UNAVAILABLE_REPLY,
    FrameError,
    PeerIdentityUnavailable,
    encode_header,
    peer_uid,
    receive_frame,
    send_frame,
)

# The executor's own operation deadline is at most 30 s; admission, commit and
# disclosure bookkeeping ride on top of it.
DEFAULT_TIMEOUT_SECONDS = 120.0


class UntrustedHost(Exception):
    """The socket is not provably served by a different, directory-owning uid."""


class BrokerTransport:
    """`ResultsTransport` plus the existing readers, through the trusted host."""

    def __init__(
        self,
        socket_path: Path,
        credential: str,
        workspace_id: UUID,
        *,
        expected_peer_uid: int | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._path = Path(socket_path)
        self._credential = credential
        self._workspace = workspace_id
        # Explicit pinning is for hosts whose uid the caller already knows; by
        # default the directory owner is the only acceptable peer.
        self._pinned = expected_peer_uid
        self._timeout = timeout

    def start_run(self) -> bytes:
        return self._exchange("start_run", b"", UNAVAILABLE_REPLY)

    def handle(self, request: bytes) -> bytes:
        return self._exchange("handle", request, UNAVAILABLE_REPLY)

    def read(self, command: str, request: bytes) -> bytes:
        if command not in READ_OPERATIONS:
            return READ_UNAVAILABLE_REPLY
        return self._exchange(command, request, READ_UNAVAILABLE_REPLY)

    def _expected_peer(self) -> int:
        if self._pinned is not None:
            return self._pinned
        directory = os.stat(self._path.parent)
        if not stat.S_ISDIR(directory.st_mode) or directory.st_mode & 0o022:
            raise UntrustedHost("socket directory is writable by others")
        if directory.st_uid == os.geteuid():
            raise UntrustedHost("socket directory belongs to the caller")
        return directory.st_uid

    def _exchange(self, operation: str, body: bytes, fallback: bytes) -> bytes:
        deadline = time.monotonic() + self._timeout
        try:
            header = encode_header(operation, self._credential, self._workspace)
            if len(body) > MAX_FRAME_BYTES:
                return fallback
            expected = self._expected_peer()
            entry = os.lstat(self._path)
            if not stat.S_ISSOCK(entry.st_mode) or entry.st_uid != expected:
                return fallback
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self._timeout)
                connection.connect(str(self._path))
                # Nothing, not even the header, leaves before the peer is proven.
                if peer_uid(connection) != expected:
                    return fallback
                send_frame(connection, header)
                send_frame(connection, body)
                reply = receive_frame(connection, MAX_FRAME_BYTES, deadline=deadline)
        except (
            OSError,
            FrameError,
            PeerIdentityUnavailable,
            UntrustedHost,
            ValueError,
        ):
            return fallback
        try:
            value = json.loads(reply)
        except ValueError:
            return fallback
        return reply if isinstance(value, dict) else fallback


__all__ = [
    "BrokerTransport",
    "READ_UNAVAILABLE_REPLY",
    "UNAVAILABLE_REPLY",
    "UntrustedHost",
]
