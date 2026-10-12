"""Database-free checks of the trusted query host socket boundary.

A stub host stands in for the database: these tests prove framing, peer-uid
refusal before any work, impostor-socket refusal before any byte is sent,
byte-exact pass-through and the private file discipline. The database-backed
acceptance lives in tests/runtime/query_host_acceptance.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pwd
import shutil
import socket
import stat
import struct
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from pydantic import SecretStr

from memoriesql.application.agent_sql_catalog import SqlAdmissionError
from memoriesql.infrastructure.results_broker import PROTOCOL_VERSION, registry
from memoriesql.infrastructure.results_broker import admin as broker_admin
from memoriesql.infrastructure.results_broker.client import BrokerTransport
from memoriesql.infrastructure.results_broker.config import (
    BrokerConfig,
    ConfigurationRefused,
    DatabaseEndpoint,
    DatabaseLogin,
    commit_config,
    load_config,
    stage_config,
)
from memoriesql.infrastructure.results_broker.server import (
    HostRefused,
    Server,
    TrustedHost,
)
from memoriesql.infrastructure.results_broker.wire import (
    MAX_FRAME_BYTES,
    READ_UNAVAILABLE_REPLY,
    UNAVAILABLE_REPLY,
    FrameError,
    decode_header,
    encode_header,
    receive_frame,
    send_frame,
)

SECRET = "fictional-paired-agent-secret-0000000001"
WORKSPACE = UUID("00000000-0000-4000-8000-00000000f001")
EXECUTOR_REPLY = b'{"contract_version":1,"outcome":"available","run":{"x":1}}'


def short_directory(test: unittest.TestCase) -> Path:
    """AF_UNIX paths are limited to ~104 bytes; keep socket directories short."""
    base = "/tmp" if os.path.isdir("/tmp") else None
    path = Path(tempfile.mkdtemp(prefix="mqh", dir=base))
    test.addCleanup(shutil.rmtree, path, True)
    return path


def fictional_config(root: Path, *, client_uid: int | None = None) -> BrokerConfig:
    (root / "state").mkdir(mode=0o700, exist_ok=True)
    (root / "run").mkdir(mode=0o750, exist_ok=True)
    return BrokerConfig(
        workspace_id=WORKSPACE,
        client_uid=os.geteuid() if client_uid is None else client_uid,
        socket_path=root / "run" / "b.sock",
        state_dir=root / "state",
        database=DatabaseEndpoint(host="127.0.0.1", port=5432, name="fictional"),
        control=DatabaseLogin(role="fictional_control", password=SecretStr("c" * 43)),
        reader=DatabaseLogin(role="fictional_reader", password=SecretStr("r" * 43)),
        profile_sha256="0" * 64,
    )


class StubExecutor:
    def __init__(self, calls: list[tuple[str, bytes]]) -> None:
        self.calls = calls

    def start_run(self) -> bytes:
        self.calls.append(("start_run", b""))
        return EXECUTOR_REPLY

    def handle(self, request: bytes) -> bytes:
        self.calls.append(("handle", request))
        return b'{"echo_len":' + str(len(request)).encode() + b',"outcome":"x"}'

    def recover_abandoned(self) -> int:
        self.calls.append(("recover", b""))
        return 0


class StubHost:
    """Duck-typed TrustedHost: no database, records what reached it."""

    def __init__(self, config: BrokerConfig, *, agent: bool = True) -> None:
        self.config = config
        self.agent = agent
        self.authenticated: list[str] = []
        self.calls: list[tuple[str, bytes]] = []
        self.cleanups: list[float] = []

    def paired_agent(self, credential_sha256: str) -> bool:
        self.authenticated.append(credential_sha256)
        return self.agent

    def executor(self, credential_sha256: str) -> StubExecutor:
        return StubExecutor(self.calls)

    def control(self) -> Any:
        raise AssertionError("readers are patched in these tests")

    def cleanup(self) -> dict[str, Any]:
        self.cleanups.append(time.monotonic())
        return {"outcome": "available", "cleanup": {"pending": 0, "failed": 0}}


class Framing(unittest.TestCase):
    def test_frames_round_trip_and_refuse_oversize_truncation_and_lateness(
        self,
    ) -> None:
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        send_frame(left, b"abc")
        self.assertEqual(receive_frame(right, 16), b"abc")
        send_frame(left, b"")
        self.assertEqual(receive_frame(right, 16), b"")
        with self.assertRaises(FrameError):
            send_frame(left, b"x" * (MAX_FRAME_BYTES + 1))
        left.sendall(struct.pack(">I", 17) + b"x" * 17)
        with self.assertRaises(FrameError):
            receive_frame(right, 16)
        right.recv(17)
        left.sendall(struct.pack(">I", 8) + b"1234")
        with self.assertRaises(FrameError):
            receive_frame(right, 16, deadline=time.monotonic() + 0.2)
        left.close()
        with self.assertRaises(FrameError):
            receive_frame(right, 16)

    def test_header_is_closed_versioned_and_bounded(self) -> None:
        header = decode_header(encode_header("handle", SECRET, WORKSPACE))
        self.assertEqual(
            (header.operation, header.credential, header.workspace_id),
            ("handle", SECRET, WORKSPACE),
        )
        valid = json.loads(encode_header("handle", SECRET, WORKSPACE))
        self.assertEqual(valid["v"], PROTOCOL_VERSION)
        for mutation in (
            {**valid, "principal_id": str(uuid4())},
            {**valid, "v": True},
            {**valid, "v": 2},
            {**valid, "op": "close_run"},
            {**valid, "op": "query"},
            {**valid, "credential": "short"},
            {**valid, "credential": SECRET + " "},
            {**valid, "credential": SECRET + "é"},
            {**valid, "workspace_id": "not-a-uuid"},
            {k: v for k, v in valid.items() if k != "workspace_id"},
        ):
            with self.subTest(mutation=sorted(mutation)):
                with self.assertRaises(FrameError):
                    decode_header(json.dumps(mutation).encode())
        duplicated = (
            b'{"credential":"'
            + SECRET.encode()
            + b'","op":"handle","op":"start_run","v":1,"workspace_id":"'
            + str(WORKSPACE).encode()
            + b'"}'
        )
        for raw in (duplicated, b"\xff", b"[]", b'{"v":NaN}'):
            with self.assertRaises(FrameError):
                decode_header(raw)
        with self.assertRaises(FrameError):
            encode_header("close_run", SECRET, WORKSPACE)


class ClientPeerProof(unittest.TestCase):
    def listener(self, directory: Path) -> socket.socket:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(directory / "b.sock"))
        server.listen(4)
        server.settimeout(0.5)
        self.addCleanup(server.close)
        return server

    def test_socket_in_a_directory_of_the_callers_own_uid_gets_nothing(
        self,
    ) -> None:
        directory = short_directory(self)
        impostor = self.listener(directory)
        transport = BrokerTransport(directory / "b.sock", SECRET, WORKSPACE)
        self.assertEqual(transport.start_run(), UNAVAILABLE_REPLY)
        self.assertEqual(transport.read("inspect", b"{}"), READ_UNAVAILABLE_REPLY)
        # Refused before connecting: the impostor never even sees a connection.
        with self.assertRaises(TimeoutError):
            impostor.accept()

    def test_pinned_peer_mismatch_refuses_before_connecting(self) -> None:
        directory = short_directory(self)
        impostor = self.listener(directory)
        transport = BrokerTransport(
            directory / "b.sock",
            SECRET,
            WORKSPACE,
            expected_peer_uid=os.geteuid() + 1,
        )
        self.assertEqual(transport.handle(b'{"x":1}'), UNAVAILABLE_REPLY)
        with self.assertRaises(TimeoutError):
            impostor.accept()

    def test_kernel_peer_mismatch_after_connect_sends_no_byte(self) -> None:
        directory = short_directory(self)
        impostor = self.listener(directory)
        received: list[bytes] = []

        def accept() -> None:
            connection, _ = impostor.accept()
            connection.settimeout(2.0)
            with connection:
                try:
                    received.append(connection.recv(65536))
                except TimeoutError:
                    received.append(b"<timeout>")

        worker = threading.Thread(target=accept)
        worker.start()
        transport = BrokerTransport(
            directory / "b.sock",
            SECRET,
            WORKSPACE,
            expected_peer_uid=os.geteuid(),
        )
        # The socket file matches, but the process behind it does not.
        with patch(
            "memoriesql.infrastructure.results_broker.client.peer_uid",
            lambda connection: os.geteuid() + 1,
        ):
            self.assertEqual(transport.handle(b'{"x":1}'), UNAVAILABLE_REPLY)
        worker.join(5)
        self.assertEqual(received, [b""])

    def test_symlinked_socket_path_is_refused(self) -> None:
        directory = short_directory(self)
        self.listener(directory)
        os.symlink(directory / "b.sock", directory / "link.sock")
        transport = BrokerTransport(
            directory / "link.sock",
            SECRET,
            WORKSPACE,
            expected_peer_uid=os.geteuid(),
        )
        self.assertEqual(transport.start_run(), UNAVAILABLE_REPLY)


class ServerBoundary(unittest.TestCase):
    def serve(self, host: StubHost) -> Path:
        path = host.config.socket_path
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(8)
        server = Server(cast(TrustedHost, host), listener)
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()

        def stop() -> None:
            server.stop()
            worker.join(10)

        self.addCleanup(stop)
        return path

    def transport(self, path: Path, **overrides: Any) -> BrokerTransport:
        values: dict[str, Any] = dict(
            credential=SECRET, workspace_id=WORKSPACE, expected_peer_uid=os.geteuid()
        )
        values.update(overrides)
        return BrokerTransport(
            path,
            values["credential"],
            values["workspace_id"],
            expected_peer_uid=values["expected_peer_uid"],
        )

    def test_other_uid_is_refused_before_authentication_or_work(self) -> None:
        root = short_directory(self)
        host = StubHost(fictional_config(root, client_uid=os.geteuid() + 1))
        path = self.serve(host)
        self.assertEqual(self.transport(path).start_run(), UNAVAILABLE_REPLY)
        self.assertEqual(
            self.transport(path).read("source", b"{}"), READ_UNAVAILABLE_REPLY
        )
        self.assertEqual(host.authenticated, [])
        self.assertEqual(host.calls, [])

    def test_executor_bytes_pass_through_unchanged(self) -> None:
        root = short_directory(self)
        host = StubHost(fictional_config(root))
        path = self.serve(host)
        transport = self.transport(path)
        request = json.dumps({"kind": "query", "sql": "SELECT 1", "z": [1, 2]})
        self.assertEqual(transport.start_run(), EXECUTOR_REPLY)
        self.assertEqual(
            transport.handle(request.encode()),
            b'{"echo_len":' + str(len(request)).encode() + b',"outcome":"x"}',
        )
        self.assertEqual(
            [call for call in host.calls if call[0] != "recover"],
            [("start_run", b""), ("handle", request.encode())],
        )
        # Dead-owner recovery precedes every run or query admission.
        self.assertEqual(
            [c[0] for c in host.calls],
            ["recover", "start_run", "recover", "handle"],
        )
        self.assertEqual(len(set(host.authenticated)), 1)
        self.assertNotIn(SECRET, "".join(host.authenticated))

    def test_request_log_correlates_by_identifier_never_content(self) -> None:
        root = short_directory(self)
        host = StubHost(fictional_config(root))
        path = self.serve(host)
        run, receipt = str(uuid4()), str(uuid4())
        replies = [
            json.dumps(
                {
                    "outcome": "available",
                    "run_ref": run,
                    "access_receipt_ref": receipt,
                    "page": {"rows": [{"values": ["fictional row content"]}]},
                }
            ).encode(),
            json.dumps(
                {
                    "outcome": "available",
                    "run_ref": "fictional non-identifier",
                    "access_receipt_ref": {"nested": run},
                }
            ).encode(),
        ]
        log = io.StringIO()
        with (
            patch.object(StubExecutor, "handle", lambda _self, _body: replies.pop(0)),
            contextlib.redirect_stderr(log),
        ):
            for _ in range(2):
                self.transport(path).handle(b"{}")
            # The host logs each request after replying; wait for both lines.
            for _ in range(200):
                if log.getvalue().count('"event": "request"') == 2:
                    break
                time.sleep(0.01)
        events = [
            json.loads(line)
            for line in log.getvalue().splitlines()
            if '"event": "request"' in line
        ]
        self.assertEqual(len(events), 2, log.getvalue())
        self.assertEqual(
            (events[0]["run_ref"], events[0]["access_receipt_ref"]), (run, receipt)
        )
        self.assertNotIn("run_ref", events[1])
        self.assertNotIn("access_receipt_ref", events[1])
        self.assertNotIn("fictional row content", log.getvalue())
        self.assertNotIn("fictional non-identifier", log.getvalue())

    def test_reader_bytes_pass_through_unchanged(self) -> None:
        root = short_directory(self)
        host = StubHost(fictional_config(root))
        path = self.serve(host)
        seen: list[tuple[str, bytes]] = []

        def reader(command: str, request: bytes, connect: Any, **_: Any) -> bytes:
            seen.append((command, request))
            return b'{"outcome":"available","reader":"' + command.encode() + b'"}'

        with patch("memoriesql.infrastructure.results_broker.server.read_core", reader):
            for command in ("inspect", "source", "relations"):
                self.assertEqual(
                    self.transport(path).read(command, b'{"bead_id":"b"}'),
                    b'{"outcome":"available","reader":"' + command.encode() + b'"}',
                )
        self.assertEqual(
            seen,
            [(c, b'{"bead_id":"b"}') for c in ("inspect", "source", "relations")],
        )

    def test_unpaired_wrong_workspace_and_malformed_requests_are_refused(
        self,
    ) -> None:
        root = short_directory(self)
        host = StubHost(fictional_config(root), agent=False)
        path = self.serve(host)
        self.assertEqual(self.transport(path).start_run(), UNAVAILABLE_REPLY)
        self.assertEqual(
            self.transport(path).read("inspect", b"{}"), READ_UNAVAILABLE_REPLY
        )
        host.agent = True
        other = self.transport(path, workspace_id=uuid4())
        self.assertEqual(other.start_run(), UNAVAILABLE_REPLY)
        self.assertEqual(host.calls, [])
        # A start_run carrying a body is not a start_run.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.connect(str(path))
            send_frame(raw, encode_header("start_run", SECRET, WORKSPACE))
            send_frame(raw, b"{}")
            self.assertEqual(receive_frame(raw, MAX_FRAME_BYTES), UNAVAILABLE_REPLY)
        # A malformed header is answered without detail.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.connect(str(path))
            send_frame(raw, b'{"op":"close_run"}')
            send_frame(raw, b"")
            self.assertEqual(receive_frame(raw, MAX_FRAME_BYTES), UNAVAILABLE_REPLY)
        # An oversized declared body is refused, not buffered.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.connect(str(path))
            send_frame(raw, encode_header("handle", SECRET, WORKSPACE))
            raw.sendall(struct.pack(">I", MAX_FRAME_BYTES + 1))
            self.assertEqual(receive_frame(raw, MAX_FRAME_BYTES), UNAVAILABLE_REPLY)
        self.assertEqual([c for c in host.calls if c[0] == "handle"], [])

    def test_expiry_cleanup_runs_at_start_and_on_schedule_until_stop(self) -> None:
        root = short_directory(self)
        host = StubHost(fictional_config(root))
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(host.config.socket_path))
        listener.listen(1)
        server = Server(cast(TrustedHost, host), listener, cleanup_interval=0.2)
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 10
        while len(host.cleanups) < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        server.stop()
        worker.join(10)
        self.assertGreaterEqual(len(host.cleanups), 3)
        settled = len(host.cleanups)
        time.sleep(0.5)
        self.assertEqual(len(host.cleanups), settled)

    def test_stop_unlinks_its_socket(self) -> None:
        root = short_directory(self)
        host = StubHost(fictional_config(root))
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(host.config.socket_path))
        listener.listen(1)
        server = Server(cast(TrustedHost, host), listener)
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        server.stop()
        worker.join(10)
        self.assertFalse(host.config.socket_path.exists())


class PrivateFiles(unittest.TestCase):
    def test_configuration_requires_owner_only_modes_and_hides_secrets(
        self,
    ) -> None:
        root = short_directory(self)
        config_dir = root / "config"
        config_dir.mkdir(mode=0o700)
        path = config_dir / "broker.json"
        config = fictional_config(root)
        commit_config(stage_config(path, config), path)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(load_config(path), config)
        self.assertNotIn("c" * 43, repr(config))
        self.assertNotIn("r" * 43, str(config))
        os.chmod(path, 0o640)
        with self.assertRaises(ConfigurationRefused) as refused:
            load_config(path)
        self.assertNotIn("c" * 43, str(refused.exception))
        os.chmod(path, 0o600)
        os.chmod(config_dir, 0o750)
        with self.assertRaises(ConfigurationRefused):
            load_config(path)
        os.chmod(config_dir, 0o700)
        os.symlink(path, config_dir / "link.json")
        with self.assertRaises(ConfigurationRefused):
            load_config(config_dir / "link.json")
        (config_dir / "bad.json").write_text('{"version": 1}')
        os.chmod(config_dir / "bad.json", 0o600)
        with self.assertRaises(ConfigurationRefused):
            load_config(config_dir / "bad.json")

    def test_run_registry_records_lists_without_digests_and_prunes(self) -> None:
        state = short_directory(self)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        run_ref = uuid4()
        old_ref = uuid4()
        registry.record_run(
            state,
            {
                "run_ref": str(old_ref),
                "started_at": "2025-12-29T00:00:00Z",
                "expires_at": "2025-12-29T00:30:00Z",
            },
            credential_sha256="a" * 64,
            workspace_id=WORKSPACE,
            now=now - timedelta(days=2),
        )
        registry.record_run(
            state,
            {
                "run_ref": str(run_ref),
                "started_at": "2026-01-01T00:00:00Z",
                "expires_at": "2026-01-01T00:30:00Z",
            },
            credential_sha256="b" * 64,
            workspace_id=WORKSPACE,
            now=now,
        )
        self.assertEqual(registry.run_owner(state, run_ref), ("b" * 64, WORKSPACE))
        self.assertIsNone(registry.run_owner(state, old_ref))
        listing = registry.recorded_runs(state, now=now)
        self.assertEqual([r["run_ref"] for r in listing], [str(run_ref)])
        self.assertTrue(listing[0]["active"])
        self.assertNotIn("b" * 64, json.dumps(listing))
        registry.mark_closed(state, run_ref, "2026-01-01T00:05:00Z")
        self.assertFalse(registry.recorded_runs(state, now=now)[0]["active"])
        self.assertEqual(os.stat(state / "runs.json").st_mode & 0o777, 0o600)

    def test_scram_verifier_has_postgres_shape_and_fresh_salt(self) -> None:
        first = broker_admin.scram_sha256_verifier("p" * 43)
        second = broker_admin.scram_sha256_verifier("p" * 43)
        self.assertRegex(
            first,
            r"^SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]{24}\$[A-Za-z0-9+/=]{44}:[A-Za-z0-9+/=]{44}$",
        )
        self.assertNotEqual(first, second)
        self.assertNotIn("p" * 43, first)
        with self.assertRaises(ValueError):
            broker_admin.scram_sha256_verifier("pässword" * 5)

    def test_integrity_finds_paths_the_client_uid_could_replace(self) -> None:
        root = short_directory(self)
        owned = root / "code"
        owned.mkdir(mode=0o755)
        self.assertIn(
            str(owned), broker_admin.integrity_findings([owned], os.geteuid())
        )
        unknown_uid = next(uid for uid in range(61000, 2**31) if not _uid_exists(uid))
        findings = broker_admin.integrity_findings([owned], unknown_uid)
        self.assertNotIn(str(owned), findings)
        os.chmod(owned, 0o757)
        self.assertIn(str(owned), broker_admin.integrity_findings([owned], unknown_uid))


def _uid_exists(uid: int) -> bool:
    try:
        pwd.getpwuid(uid)
    except KeyError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()


class LostCommit:
    """An administrator connection whose COMMIT reply is lost or refused."""

    def __init__(self, error: psycopg.Error | None = None) -> None:
        self.error = error or psycopg.OperationalError("connection lost")

    def __enter__(self) -> LostCommit:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    @contextlib.contextmanager
    def transaction(self) -> Any:
        yield
        raise self.error


class Commits(LostCommit):
    """An administrator connection whose COMMIT succeeds."""

    @contextlib.contextmanager
    def transaction(self) -> Any:
        yield


class Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class SettlingSession:
    """An administrator session reporting transaction statuses, then verifiers."""

    def __init__(self, statuses: list[str | None], stored: dict[str, str]) -> None:
        self.statuses, self.stored = statuses, stored
        self.reads: list[str] = []

    def __enter__(self) -> SettlingSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, query: str, _params: object = None) -> Rows:
        if "pg_xact_status" in query:
            self.reads.append("status")
            status = (
                self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            )
            return Rows([(status,)])
        self.reads.append("verifiers")
        return Rows(list(self.stored.items()))


class ProvisioningOutcome(unittest.TestCase):
    """A lost connection never discards secrets the database may already require."""

    def setUp(self) -> None:
        self.root = short_directory(self)
        (self.root / "config").mkdir(mode=0o700)
        self.path = self.root / "config" / "broker.json"
        self.pinned: list[tuple[bool, bool]] = []
        self.settled: list[str | None] = []

    def pin(
        self, path: Path, config: BrokerConfig, *, rotated: bool, recovered: bool
    ) -> int:
        self.assertEqual(load_config(path), config)
        self.pinned.append((rotated, recovered))
        return broker_admin.EXIT_OK

    def provision(
        self,
        connection: object,
        settlement: broker_admin.Settlement,
        *,
        apply: BaseException | None = None,
    ) -> tuple[int, dict[str, Any]]:
        def settle(_url: str, _config: BrokerConfig, transaction: str | None) -> Any:
            self.settled.append(transaction)
            return settlement

        stream = io.StringIO()
        with (
            patch.object(psycopg, "connect", return_value=connection),
            patch.object(broker_admin, "_current_transaction", return_value="4242"),
            patch.object(broker_admin, "_apply_roles", side_effect=apply),
            patch.object(broker_admin, "_settlement", side_effect=settle),
            patch.object(broker_admin, "_pin", side_effect=self.pin),
            contextlib.redirect_stdout(stream),
        ):
            code = broker_admin.provision(
                self.path,
                workspace_id=WORKSPACE,
                client_uid=os.geteuid(),
                database_host="127.0.0.1",
                database_port=5432,
                database_name="fictional",
                admin_url=lambda: "postgresql://fictional",
            )
        lines = stream.getvalue().strip().splitlines()
        return code, json.loads(lines[-1]) if lines else {}

    def kept(self) -> list[Path]:
        return list((self.root / "config").glob(".broker.json.*.new"))

    def records(self) -> list[Path]:
        return list((self.root / "config").glob(".broker.json.*.new.transaction"))

    def test_an_unknown_commit_keeps_the_file_and_a_later_run_settles_it(self) -> None:
        code, result = self.provision(LostCommit(), "unreadable")
        self.assertEqual(code, broker_admin.EXIT_FAILED, result)
        self.assertEqual(result["reason"], "provisioning_outcome_unknown")
        self.assertIn("once the database is reachable", result["next"])
        (kept,) = self.kept()
        self.assertEqual(str(kept), result["kept_configuration"])
        self.assertEqual(stat.S_IMODE(kept.stat().st_mode), 0o600)
        # The transaction that sent the role changes is recorded beside it.
        (record,) = self.records()
        self.assertEqual(record.read_text(), "4242")
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertFalse(self.path.exists())
        staged = load_config(kept)
        # The database accepted it after all: the rerun settles it against the
        # recorded transaction, adopts it and pins, changing no role again.
        code, _ = self.provision(object(), "committed")
        self.assertEqual(code, broker_admin.EXIT_OK)
        self.assertEqual(self.settled, ["4242", "4242"])
        self.assertEqual(self.kept(), [])
        self.assertEqual(self.records(), [])
        self.assertEqual(load_config(self.path), staged)
        self.assertEqual(self.pinned, [(False, True)])

    def test_a_commit_still_in_progress_is_kept_until_it_ends(self) -> None:
        code, result = self.provision(LostCommit(), "pending")
        self.assertEqual(code, broker_admin.EXIT_FAILED, result)
        self.assertEqual(result["reason"], "provisioning_outcome_unknown")
        self.assertIn("has not ended", result["next"])
        self.assertNotIn("remove", result["next"])
        self.assertEqual(len(self.kept()), 1)
        self.assertEqual([path.read_text() for path in self.records()], ["4242"])

    def test_a_lost_commit_that_took_effect_goes_live(self) -> None:
        code, _ = self.provision(LostCommit(), "committed")
        self.assertEqual(code, broker_admin.EXIT_OK)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.kept(), [])
        self.assertEqual(self.records(), [])
        self.assertEqual(self.pinned, [(False, True)])

    def test_a_commit_that_ended_without_effect_changes_nothing(self) -> None:
        for error, cause in (
            (psycopg.OperationalError("lost"), "connection lost during commit"),
            (psycopg.errors.SerializationFailure("x"), "SQLSTATE 40001 during commit"),
        ):
            with self.subTest(cause=cause):
                code, result = self.provision(LostCommit(error), "absent")
                self.assertEqual(code, broker_admin.EXIT_REFUSED, result)
                self.assertIn(cause, result["reason"])
                self.assertIn("nothing changed", result["reason"])
                self.assertEqual(self.kept(), [])
                self.assertEqual(self.records(), [])
                self.assertFalse(self.path.exists())
        self.assertEqual(self.pinned, [])

    def test_before_commit_nothing_is_settled_or_kept(self) -> None:
        # COMMIT was never sent, so no role change can commit: the staged
        # secrets are discarded without asking the database.
        for error, cause in (
            (psycopg.OperationalError("lost"), "connection lost before commit"),
            (psycopg.errors.DuplicateObject("role exists"), "SQLSTATE 42710"),
        ):
            with self.subTest(cause=cause):
                code, result = self.provision(LostCommit(), "unreadable", apply=error)
                self.assertEqual(code, broker_admin.EXIT_REFUSED, result)
                self.assertIn(cause, result["reason"])
                self.assertIn("nothing changed", result["reason"])
                self.assertEqual(self.kept(), [])
                self.assertEqual(self.records(), [])
        self.assertEqual(self.settled, [])
        with (
            patch.object(
                psycopg, "connect", side_effect=psycopg.OperationalError("unreachable")
            ),
            contextlib.redirect_stdout(io.StringIO()) as stream,
        ):
            code = broker_admin.provision(
                self.path,
                workspace_id=WORKSPACE,
                client_uid=os.geteuid(),
                database_host="127.0.0.1",
                database_port=5432,
                database_name="fictional",
                admin_url=lambda: "postgresql://fictional",
            )
        self.assertEqual(code, broker_admin.EXIT_REFUSED)
        self.assertIn("could not connect", stream.getvalue())
        self.assertEqual(self.kept(), [])

    def test_an_interruption_keeps_the_file_only_once_commit_was_sent(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            self.provision(LostCommit(), "unreadable", apply=KeyboardInterrupt())
        self.assertEqual((self.kept(), self.records()), ([], []))
        with self.assertRaises(KeyboardInterrupt):
            self.provision(LostCommit(cast(Any, KeyboardInterrupt())), "unreadable")
        self.assertEqual(len(self.kept()), 1)
        self.assertEqual([path.read_text() for path in self.records()], ["4242"])

    def test_the_record_stays_until_its_file_is_live(self) -> None:
        # The commit succeeded but making the file live failed: the file and
        # the record naming its transaction both stay, so a rerun settles it.
        with (
            patch.object(broker_admin, "commit_config", side_effect=OSError("disk")),
            self.assertRaises(OSError),
        ):
            self.provision(Commits(), "unreadable")
        (kept,) = self.kept()
        self.assertEqual(broker_admin._recorded_transaction(kept), "4242")
        code, _ = self.provision(object(), "committed")
        self.assertEqual(code, broker_admin.EXIT_OK)
        self.assertEqual(self.settled, ["4242"])
        self.assertEqual((self.kept(), self.records()), ([], []))
        self.assertEqual(self.pinned, [(False, True)])

    def test_a_kept_file_without_a_record_is_adopted_only_on_proof(self) -> None:
        # No record names the transaction that wrote it, as with a file kept by
        # an older release. Only a verifier match settles it.
        orphan = stage_config(self.path, fictional_config(self.root))
        unknowns: list[tuple[broker_admin.Settlement, str]] = [
            ("pending", "does not require this file's secrets"),
            ("unreadable", "once the database is reachable"),
        ]
        for settlement, guidance in unknowns:
            with self.subTest(settlement=settlement):
                code, result = self.provision(object(), settlement)
                self.assertEqual(code, broker_admin.EXIT_FAILED, result)
                self.assertEqual(result["kept_configuration"], str(orphan))
                self.assertIn(guidance, result["next"])
                self.assertEqual(self.kept(), [orphan])
        self.assertIn("remove the file", self.provision(object(), "pending")[1]["next"])
        code, _ = self.provision(object(), "committed")
        self.assertEqual(code, broker_admin.EXIT_OK)
        self.assertEqual(set(self.settled), {None})
        self.assertEqual(self.kept(), [])
        self.assertEqual(load_config(self.path), fictional_config(self.root))
        self.assertEqual(self.pinned, [(False, True)])

    def test_settlement_trusts_a_mismatch_only_after_the_transaction_ended(
        self,
    ) -> None:
        config = fictional_config(self.root)
        password = config.control.password.get_secret_value()
        verifier = broker_admin.scram_sha256_verifier(password, iterations=64)
        stale = broker_admin.scram_sha256_verifier("fictional-other", iterations=64)
        matching = {config.control.role: verifier, config.reader.role: verifier}
        reader = config.reader.model_copy(update={"password": SecretStr(password)})
        config = config.model_copy(update={"reader": reader})
        missing = {config.control.role: stale, config.reader.role: stale}
        cases: list[tuple[list[str | None], dict[str, str], str | None, str]] = [
            # Still in progress at the deadline: a commit may yet become visible.
            (["in progress"], missing, "7", "pending"),
            # A match is final even before the transaction is seen to end.
            (["in progress"], matching, "7", "committed"),
            (["in progress", "committed"], matching, "7", "committed"),
            (["in progress", "aborted"], missing, "7", "absent"),
            # Too old to be kept: the transaction ended long ago.
            ([None], missing, "7", "absent"),
            # Without a recorded transaction a mismatch is never final.
            (["aborted"], missing, None, "pending"),
            (["aborted"], matching, None, "committed"),
        ]
        for statuses, stored, transaction, expected in cases:
            with self.subTest(statuses=statuses, transaction=transaction):
                session = SettlingSession(list(statuses), stored)
                with (
                    patch.object(psycopg, "connect", return_value=session),
                    patch.object(broker_admin, "TRANSACTION_END_SECONDS", 0.5),
                ):
                    self.assertEqual(
                        broker_admin._settlement("fictional", config, transaction),
                        expected,
                    )
                # The verifiers are read only after the transaction's status.
                self.assertEqual(session.reads[-1], "verifiers")
                self.assertEqual(session.reads.count("verifiers"), 1)
        with patch.object(
            psycopg, "connect", side_effect=psycopg.OperationalError("unreachable")
        ):
            self.assertEqual(
                broker_admin._settlement("fictional", config, "7"), "unreadable"
            )

    def test_the_transaction_record_reads_back_only_as_written(self) -> None:
        staged = self.root / "config" / ".broker.json.0011223344556677.new"
        staged.write_text("{}")
        self.assertIsNone(broker_admin._recorded_transaction(staged))
        broker_admin._record_transaction(staged, "123456789012")
        self.assertEqual(broker_admin._recorded_transaction(staged), "123456789012")
        record = broker_admin._transaction_record(staged)
        for damaged in ("", "12x", "-1", "\u0663"):
            record.write_text(damaged, encoding="utf-8")
            self.assertIsNone(broker_admin._recorded_transaction(staged), damaged)
        broker_admin._discard(staged)
        self.assertFalse(staged.exists())
        self.assertFalse(record.exists())

    def test_scram_verifier_matches_only_its_own_password(self) -> None:
        verifier = broker_admin.scram_sha256_verifier("fictional-secret", iterations=64)
        self.assertTrue(broker_admin.scram_sha256_matches("fictional-secret", verifier))
        self.assertFalse(broker_admin.scram_sha256_matches("fictional-other", verifier))
        for broken in (
            None,
            "",
            "md5abc",
            verifier.replace("SCRAM-SHA-256", "X"),
            "SCRAM-SHA-256$x",
        ):
            self.assertFalse(
                broker_admin.scram_sha256_matches("fictional-secret", broken)
            )

    def test_a_pinning_refusal_names_its_cause_without_secrets(self) -> None:
        causes = {
            HostRefused("control login must be an INHERIT member"): "INHERIT member",
            SqlAdmissionError("unavailable", "default_procedure_privileges"): (
                "reviewed reader authority refused: default_procedure_privileges"
            ),
            psycopg.OperationalError("password authentication failed"): (
                "control login could not connect (unreachable or authentication refused)"
            ),
            psycopg.errors.InsufficientPrivilege("denied"): "SQLSTATE 42501",
            RuntimeError("missing"): "reviewed reader specification unavailable",
        }
        for error, expected in causes.items():
            with self.subTest(error=type(error).__name__):
                cause = broker_admin._pinning_cause(error)
                self.assertIn(expected, cause)
                self.assertNotIn("password authentication failed", cause)
