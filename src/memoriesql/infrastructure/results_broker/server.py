"""`memoriesql broker serve`: the credential-isolated trusted query host.

One process, running as the dedicated service user, owns both connection
classes. It refuses to serve unless its private configuration, its trusted
login and PR-05's reviewed reader profile are exactly as provisioned. Each
connection must come from the configured client uid (kernel peer credentials)
and each request must present a currently valid paired agent credential for
the configured workspace; the host hashes it and the executor authenticates
exactly that principal. Owner operations such as closing a run are never
reachable from this socket.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import stat
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.errors import InsufficientPrivilege

from memoriesql.application.agent_sql_catalog import SqlAdmissionError
from memoriesql.application.authorization import LocalCredential
from memoriesql.domain.authorization import PrincipalKind
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QueryAuthorityProfile,
    qualify_query_authority,
    verify_query_login,
)
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
)
from memoriesql.infrastructure.postgres.authorization import (
    PostgresAuthorizationPort,
)
from memoriesql.infrastructure.postgres.query_reader_provisioning import (
    reviewed_query_reader_profile,
)
from memoriesql.infrastructure.results_broker.config import (
    BrokerConfig,
    ConfigurationRefused,
    load_config,
)
from memoriesql.infrastructure.results_broker.readers import read_core
from memoriesql.infrastructure.results_broker.registry import record_run
from memoriesql.infrastructure.results_broker.wire import (
    MAX_FRAME_BYTES,
    MAX_HEADER_BYTES,
    READ_OPERATIONS,
    READ_UNAVAILABLE_REPLY,
    UNAVAILABLE_REPLY,
    FrameError,
    PeerIdentityUnavailable,
    RequestHeader,
    decode_header,
    peer_pid,
    peer_uid,
    receive_frame,
    send_frame,
)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_FAILED = 3
REQUEST_DEADLINE_SECONDS = 10.0
REPLY_DEADLINE_SECONDS = 30.0
MAX_CONNECTIONS = 8
DRAIN_SECONDS = 90.0
_TRUSTED_ROLE = "memoriesql_application"


class HostRefused(Exception):
    """Operator-actionable refusal to serve; the message never holds a secret."""


def log(event: str, **fields: Any) -> None:
    """One JSON line to the service log; never credentials, digests or content."""
    record = {"at": datetime.now(UTC).isoformat(), "event": event, **fields}
    sys.stderr.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    sys.stderr.flush()


def check_control_login(connection: Connection[Any]) -> None:
    """Refuse a trusted login that is over- or under-privileged for this host.

    It must inherit `memoriesql_application` (a NOINHERIT member fails every
    read silently) and must not be a superuser or hold elevation it never uses,
    so that a stolen host configuration is no more than the host's authority.
    """
    inherit_refusal = (
        "control login must be an INHERIT member of memoriesql_application"
    )
    try:
        row = connection.execute(
            """SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
                      rolreplication
               FROM pg_roles WHERE rolname = current_user"""
        ).fetchone()
    except InsufficientPrivilege:
        # Schema 33 revokes PUBLIC EXECUTE on builtins (even operators); only
        # logins inheriting memoriesql_application can evaluate this at all.
        raise HostRefused(inherit_refusal) from None
    if row is None:
        raise HostRefused("control login is not a database role")
    superuser, bypass, create_role, create_db, replication = row
    if superuser:
        raise HostRefused("control login must not be a superuser")
    if bypass or create_role or create_db or replication:
        raise HostRefused(
            "control login must be NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION"
        )
    try:
        inherited = connection.execute(
            "SELECT pg_has_role(current_user, %s, 'USAGE') "
            "AND pg_has_role(current_user, %s, 'MEMBER')",
            (_TRUSTED_ROLE, _TRUSTED_ROLE),
        ).fetchone()
    except InsufficientPrivilege:
        # Builtins are executable only through memoriesql_application: being
        # refused here is itself the symptom of a login that does not inherit it.
        inherited = None
    if not inherited or not inherited[0]:
        raise HostRefused(inherit_refusal)


@dataclass(frozen=True)
class TrustedHost:
    config: BrokerConfig
    profile: QueryAuthorityProfile

    def control(self) -> Connection[Any]:
        return psycopg.connect(self.config.control_conninfo(), autocommit=True)

    def reader(self) -> Connection[Any]:
        return psycopg.connect(self.config.reader_conninfo(), autocommit=True)

    def executor(self, credential_sha256: str) -> PostgresAgentSqlResults:
        return PostgresAgentSqlResults(
            control_factory=self.control,
            reader_factory=self.reader,
            authority_profile=self.profile,
            credential_sha256=credential_sha256,
            workspace_id=self.config.workspace_id,
        )

    def paired_agent(self, credential_sha256: str) -> bool:
        """True only for a current paired agent principal of this workspace."""
        try:
            with self.control() as connection:
                with connection.transaction():
                    connection.execute("SET LOCAL lock_timeout='500ms'")
                    connection.execute("SET LOCAL statement_timeout='2500ms'")
                    connection.execute("SET LOCAL ROLE memoriesql_application")
                    context = PostgresAuthorizationPort(connection).begin_context(
                        credential_sha256=credential_sha256,
                        requested_workspace_id=self.config.workspace_id,
                    )
        except (PermissionError, psycopg.Error, ValueError):
            return False
        return (
            context.principal_kind is PrincipalKind.AGENT
            and context.pairing_grant_id is not None
            and context.workspace_id == self.config.workspace_id
        )


def prepare_host(config: BrokerConfig) -> TrustedHost:
    """Preflight both logins and the pinned reviewed profile before serving."""
    if config.profile_sha256 is None:
        raise HostRefused(
            "reader is not provisioned; run `memoriesql broker provision`"
        )
    if config.control.role == config.reader.role:
        raise HostRefused("control and reader logins must be different roles")
    try:
        with psycopg.connect(config.control_conninfo(), autocommit=True) as control:
            check_control_login(control)
            profile = reviewed_query_reader_profile(control, config.reader.role)
            authority = qualify_query_authority(control, profile)
        if authority.profile_sha256 != config.profile_sha256:
            raise HostRefused(
                "reviewed reader profile does not match the pinned profile"
            )
        with psycopg.connect(config.reader_conninfo(), autocommit=True) as reader:
            verify_query_login(reader, authority)
    except HostRefused:
        raise
    except SqlAdmissionError as error:
        raise HostRefused(
            f"reviewed reader authority refused: {error.construct}"
        ) from None
    except psycopg.Error as error:
        # SQLSTATE only: server messages can echo roles, hosts or settings.
        raise HostRefused(
            f"database preflight failed (SQLSTATE {error.sqlstate})"
        ) from None
    except (RuntimeError, ValueError):
        raise HostRefused("reviewed reader specification unavailable") from None
    return TrustedHost(config, profile)


def bind_listener(config: BrokerConfig) -> socket.socket:
    """Bind the socket inside a directory only the service user can modify."""
    path = config.socket_path
    try:
        directory = os.lstat(path.parent)
    except OSError:
        raise HostRefused("socket directory unavailable") from None
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid():
        raise HostRefused(
            "socket directory must be a directory owned by the service user"
        )
    if directory.st_mode & 0o027:
        raise HostRefused("socket directory must be mode 0750 or stricter")
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
            raise HostRefused("socket path is occupied by something else")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(1.0)
            probe.connect(str(path))
        except OSError:
            path.unlink()
        else:
            raise HostRefused("another trusted host is already serving this socket")
        finally:
            probe.close()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(0o117)
    try:
        listener.bind(str(path))
    finally:
        os.umask(previous)
    os.chmod(path, 0o660)
    listener.listen(16)
    return listener


class Server:
    """Accept loop with bounded concurrency and a draining stop."""

    def __init__(self, host: TrustedHost, listener: socket.socket) -> None:
        self._host = host
        self._listener = listener
        self._socket_identity = os.lstat(host.config.socket_path).st_ino
        self._stop = threading.Event()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._workers: set[threading.Thread] = set()
        self._workers_lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._listener.settimeout(0.5)
        log("serving", workspace_id=self._host.config.workspace_id)
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            if not self._slots.acquire(blocking=False):
                log("refused", reason="busy")
                connection.close()
                continue
            worker = threading.Thread(
                target=self._serve_connection, args=(connection,), daemon=True
            )
            with self._workers_lock:
                self._workers.add(worker)
            worker.start()
        self._close_listener()
        self._drain()

    def _close_listener(self) -> None:
        self._listener.close()
        path = self._host.config.socket_path
        try:
            if os.lstat(path).st_ino == self._socket_identity:
                path.unlink()
        except OSError:
            pass

    def _drain(self) -> None:
        deadline = time.monotonic() + DRAIN_SECONDS
        with self._workers_lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        pending = sum(worker.is_alive() for worker in workers)
        log("stopped", undrained=pending)

    def _serve_connection(self, connection: socket.socket) -> None:
        started = time.monotonic()
        operation = None
        outcome = None
        pid = peer_pid(connection)
        try:
            try:
                uid = peer_uid(connection)
            except PeerIdentityUnavailable:
                log("refused", reason="peer_identity", peer_pid=pid)
                return
            if uid != self._host.config.client_uid:
                log("refused", reason="peer_uid", peer_uid=uid, peer_pid=pid)
                return
            deadline = started + REQUEST_DEADLINE_SECONDS
            try:
                header = decode_header(
                    receive_frame(connection, MAX_HEADER_BYTES, deadline=deadline)
                )
                body = receive_frame(connection, MAX_FRAME_BYTES, deadline=deadline)
            except (FrameError, OSError):
                log("refused", reason="frame", peer_pid=pid)
                self._reply(connection, UNAVAILABLE_REPLY)
                return
            operation = header.operation
            reply = self._dispatch(header, body)
            outcome = _outcome(reply)
            self._reply(connection, reply)
        except Exception:
            outcome = "host_error"
            self._reply(
                connection,
                READ_UNAVAILABLE_REPLY
                if operation in READ_OPERATIONS
                else UNAVAILABLE_REPLY,
            )
        finally:
            connection.close()
            self._slots.release()
            with self._workers_lock:
                self._workers.discard(threading.current_thread())
            if operation is not None:
                log(
                    "request",
                    op=operation,
                    outcome=outcome,
                    peer_pid=pid,
                    ms=round((time.monotonic() - started) * 1000),
                )

    @staticmethod
    def _reply(connection: socket.socket, data: bytes) -> None:
        try:
            connection.settimeout(REPLY_DEADLINE_SECONDS)
            send_frame(connection, data)
        except (OSError, FrameError):
            pass

    def _dispatch(self, header: RequestHeader, body: bytes) -> bytes:
        refusal = (
            READ_UNAVAILABLE_REPLY
            if header.operation in READ_OPERATIONS
            else UNAVAILABLE_REPLY
        )
        config = self._host.config
        if header.workspace_id != config.workspace_id:
            return refusal
        try:
            credential_sha256 = LocalCredential(header.credential).sha256()
        except ValueError:
            return refusal
        if not self._host.paired_agent(credential_sha256):
            return refusal
        executor = self._host.executor(credential_sha256)
        if header.operation in ("start_run", "handle"):
            self._recover(executor)
        if header.operation == "start_run":
            if body:
                return refusal
            reply = executor.start_run()
            self._record(reply, credential_sha256)
            return reply
        if header.operation == "handle":
            return executor.handle(body)
        return read_core(
            header.operation,
            body,
            self._host.control,
            credential_sha256=credential_sha256,
            workspace_id=config.workspace_id,
        )

    @staticmethod
    def _recover(executor: PostgresAgentSqlResults) -> None:
        """Settle this workspace's work whose owner died, before new admission.

        PR-05 settles a delivery only when its owning session has ended and its
        restricted reader is confirmed gone, so live work (including this very
        process's own, which holds its owner lock) is untouched. Running it on
        every run or query request means that work stranded by an earlier host
        death stops blocking the workspace as soon as its reader has ended,
        without replaying the lost step and without restarting the host.
        """
        try:
            settled = executor.recover_abandoned()
        except Exception:
            log("recovery_failed")
            return
        if settled:
            log("recovered", deliveries=settled)

    def _record(self, reply: bytes, credential_sha256: str) -> None:
        try:
            data = json.loads(reply)
        except ValueError:
            return
        if data.get("outcome") != "available" or not isinstance(data.get("run"), dict):
            return
        try:
            record_run(
                self._host.config.state_dir,
                data["run"],
                credential_sha256=credential_sha256,
                workspace_id=self._host.config.workspace_id,
            )
        except Exception:
            # The run exists regardless; only the operator close path loses it.
            log("run_registry_failed")


def _outcome(reply: bytes) -> str | None:
    try:
        value = json.loads(reply)
    except ValueError:
        return None
    outcome = value.get("outcome") if isinstance(value, dict) else None
    return outcome if isinstance(outcome, str) else None


def open_server(config: BrokerConfig) -> Server:
    host = prepare_host(config)
    return Server(host, bind_listener(config))


def serve(config_path: Path) -> int:
    """Serve until SIGTERM/SIGINT, then drain; 2 when refused, 3 on failure."""
    try:
        server = open_server(load_config(config_path))
    except (ConfigurationRefused, HostRefused) as refusal:
        log("refused_to_start", reason=str(refusal))
        return EXIT_REFUSED
    except Exception:
        log("failed_to_start")
        return EXIT_FAILED

    def stop(signum: int, frame: FrameType | None) -> None:
        server.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.run()
    return EXIT_OK
