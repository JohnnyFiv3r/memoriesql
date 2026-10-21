"""Inert controls of the actual fixed helper. No sudo, UID, PID or socket probes."""

from __future__ import annotations

import hashlib
import io
import json
import os
import select
import stat
import subprocess
import sys
import time
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from tests.runtime import query_host_cli_identity as identity


def stat_info(
    *, st_uid: int, st_mode: int, st_nlink: int = 1, st_dev: int = 0, st_ino: int = 0
) -> os.stat_result:
    """Construct only fictional metadata, with the real stat_result shape."""
    return os.stat_result((st_mode, st_ino, st_dev, st_nlink, st_uid, 0, 0, 0, 0, 0))


class QueryHostCliIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {
            "client_uid": 65534,
            "service_uid": 1001,
            "gid": 1001,
            "groups": [1001],
        }
        self.process = {
            "pid": 123,
            "start": 456,
            "parent": 122,
            "uids": [65534] * 4,
            "gids": [1001] * 4,
            "groups": [1001],
            "caps": [0, 0, 0],
        }

    def worker_inputs(
        self, writable: str, search: list[str] | None = None
    ) -> tuple[ExitStack, dict[str, Any]]:
        stack = ExitStack()
        paths = search or ["/safe/import"]
        plan = {
            **self.plan,
            "python": "/safe/python",
            "helper": "/safe/helper",
            "sql": "/safe/query.sql",
            "deadline": 1,
            "cleanup_deadline": 121,
            "inputs": [
                (p, hashlib.sha256(b"immutable").hexdigest())
                for p in ("/safe/python", "/safe/helper", "/safe/query.sql")
            ],
            "import_roots": [paths[0]],
        }

        def info(
            path: str | os.PathLike[str], *args: object, **kwargs: object
        ) -> os.stat_result:
            regular = str(path) in {
                "/safe/python",
                "/safe/helper",
                "/safe/query.sql",
            } or str(path).endswith("module.py")
            mode = stat.S_IFREG if regular else stat.S_IFDIR
            if str(path) == "/venv/bin/python":
                mode = stat.S_IFLNK
            return stat_info(st_uid=1001, st_mode=mode | 0o555)

        stack.enter_context(patch.object(os, "closerange"))
        stack.enter_context(patch.object(os, "getpid", return_value=123))
        stack.enter_context(
            patch.object(identity, "process", return_value=self.process)
        )
        stack.enter_context(patch.object(sys, "path", paths))
        stack.enter_context(patch.object(time, "monotonic", return_value=0))
        stack.enter_context(patch.object(os, "lstat", side_effect=info))
        stack.enter_context(patch.object(os, "readlink", return_value="/safe/python"))
        stack.enter_context(
            patch.object(
                os, "access", side_effect=lambda p, *a, **k: str(p) == writable
            )
        )
        stack.enter_context(patch.object(Path, "stat", autospec=True, side_effect=info))
        stack.enter_context(
            patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda p: str(p) == "/venv/bin/python",
            )
        )
        stack.enter_context(
            patch.object(
                Path,
                "exists",
                autospec=True,
                side_effect=lambda p: str(p) != "/future/search",
            )
        )
        stack.enter_context(
            patch.object(
                Path,
                "resolve",
                autospec=True,
                side_effect=lambda p, **k: Path("/safe/python")
                if str(p) == "/venv/bin/python"
                else p,
            )
        )
        stack.enter_context(patch.object(Path, "read_bytes", return_value=b"immutable"))
        stack.enter_context(
            patch.object(
                Path, "rglob", return_value=iter([Path(paths[0]) / "module.py"])
            )
        )
        return stack, plan

    def test_actual_worker_rejects_writable_import_root_and_ancestor(self) -> None:
        for writable in ("/imports/root", "/imports"):
            stack, plan = self.worker_inputs(writable, ["/imports/root"])
            with (
                stack,
                self.subTest(writable=writable),
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.check_worker(plan)

    def test_actual_worker_rejects_omitted_nonexistent_search_entry(self) -> None:
        stack, plan = self.worker_inputs("/future", ["/safe/import", "/future/search"])
        with stack, self.assertRaises(identity.ContainmentFatal):
            identity.check_worker(plan)

    def test_actual_worker_checks_invoked_interpreter_symlink_ancestor(self) -> None:
        stack, plan = self.worker_inputs("/venv/bin")
        plan["python"] = "/venv/bin/python"
        with stack, self.assertRaises(identity.ContainmentFatal):
            identity.check_worker(plan)

    def test_actual_worker_accepts_complete_readonly_bound_search_and_link(
        self,
    ) -> None:
        stack, plan = self.worker_inputs("/not-present")
        plan["python"] = "/venv/bin/python"
        with stack:
            identity.check_worker(plan)

    def test_actual_path_check_rejects_creation_or_chmod_under_client_control(
        self,
    ) -> None:
        for scenario in ("absent", "client-owned"):

            def info(path: str | os.PathLike[str]) -> os.stat_result:
                if str(path) == "/sticky/future" and scenario == "absent":
                    raise FileNotFoundError
                return stat_info(
                    st_uid=65534 if str(path) == "/sticky/future" else 0,
                    st_mode=stat.S_IFDIR
                    | 0o555
                    | (stat.S_ISVTX if str(path) == "/sticky" else 0),
                )

            with (
                self.subTest(scenario=scenario),
                patch.object(os, "lstat", side_effect=info),
                patch.object(
                    os,
                    "access",
                    side_effect=lambda p, *a, **k: str(p) == "/sticky",
                ),
                patch.object(time, "monotonic", return_value=0),
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.check_trusted_path(
                    Path("/sticky/future"), self.plan, 1, allow_missing=True
                )

    def test_actual_path_check_accepts_existing_protected_sticky_child(self) -> None:
        def info(path: str | os.PathLike[str]) -> os.stat_result:
            return stat_info(
                st_uid=1001,
                st_mode=stat.S_IFDIR
                | 0o555
                | (stat.S_ISVTX if str(path) == "/sticky" else 0),
            )

        with (
            patch.object(os, "lstat", side_effect=info),
            patch.object(
                os,
                "access",
                side_effect=lambda p, *a, **k: str(p) == "/sticky",
            ),
            patch.object(time, "monotonic", return_value=0),
        ):
            identity.check_trusted_path(Path("/sticky/protected"), self.plan, 1)

    def test_actual_prepare_requires_independent_valid_authority(self) -> None:
        for field, independent in (
            ("__omitted__", "a" * 64),
            (None, None),
            (None, "a" * 64),
            ("a" * 64, None),
            ("", ""),
            ([], None),
            ("a" * 63, "a" * 63),
            ("z" * 64, "z" * 64),
            ("a" * 64, "b" * 64),
            ("a" * 64, "a" * 64),
        ):
            plan = {
                **self.plan,
                "source_sha": "source",
                "root": "@fresh-fixture",
                "authority_sha256": field,
                "python": "@installed-python",
                "helper": "@copied-helper",
                "inputs": [],
                "sudo_sha256": hashlib.sha256(b"immutable").hexdigest(),
                "env_sha256": hashlib.sha256(b"immutable").hexdigest(),
            }
            if field == "__omitted__":
                del plan["authority_sha256"]
            valid = field == "a" * 64 and independent == "a" * 64
            env = {
                "N1_TEST_CLI_IDENTITY_BINDING": "/safe/binding",
                "N1_TEST_CLI_IDENTITY_BINDING_SHA256": "a" * 64,
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "SOURCE_SHA": "source",
                identity.NORMAL_ENV: "1",
                identity.CLEANUP_ENV: "121",
            }
            if independent is not None:
                env["N1_TEST_CLI_IDENTITY_AUTHORITY_SHA256"] = independent
            system = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o555)
            with (
                self.subTest(field=field),
                patch.dict(os.environ, env, clear=True),
                patch.object(identity, "read_binding", return_value=plan),
                patch.object(os, "geteuid", return_value=1001),
                patch.object(os, "getegid", return_value=1001),
                patch.object(time, "monotonic", return_value=0),
                patch.object(Path, "read_bytes", return_value=b"immutable"),
                patch.object(Path, "chmod"),
                patch.object(Path, "mkdir"),
                patch.object(os, "lstat", return_value=system),
                patch.object(subprocess, "Popen") as spawn,
                nullcontext()
                if valid
                else self.assertRaises(identity.ContainmentFatal),
            ):
                session = identity.Session.prepare(
                    Path("/safe/root"), "fictional", "fictional", "fictional"
                )
                self.assertIsInstance(session, identity.Session)
            spawn.assert_not_called()

    def test_actual_close_ack_uses_original_cleanup_deadline(self) -> None:
        session = identity.Session(
            {**self.plan, "deadline": 1, "cleanup_deadline": 121},
            "fictional",
            "fictional",
            "fictional",
        )
        session.monitor = Mock()
        session.monitor.wait.return_value = 0
        session.state_identity = [7, 8]
        session.channel = SimpleNamespace(deadline=1)
        seen: list[float] = []

        def acknowledgement() -> dict[str, bool]:
            seen.append(session.channel.deadline)
            identity.remaining(session.channel.deadline)
            return {"closed": True}

        session.channel.read = acknowledgement
        cleanup_monitor = Mock()
        cleanup_monitor.wait.return_value = 0
        cleanup_channel = Mock()
        cleanup_channel.read.return_value = {
            "cleanup": "known_targets_absent_state_contents_deleted"
        }
        state = Mock()
        with (
            patch.object(time, "monotonic", return_value=2),
            patch.object(identity, "send"),
            patch.object(
                session, "launch", return_value=(cleanup_monitor, cleanup_channel)
            ),
            patch.object(identity, "private_state", return_value=state),
            patch.object(os, "lstat", return_value=SimpleNamespace(st_dev=7, st_ino=8)),
            patch.object(identity, "latch"),
        ):
            session.close()
        self.assertEqual(seen, [121])
        self.assertTrue(session.ledger.complete())

    def worker_output(
        self, stdout: bytes, stderr: bytes
    ) -> tuple[ExitStack, dict[str, Any]]:
        stack = ExitStack()
        plan = {
            **self.plan,
            "deadline": 1,
            "cleanup_deadline": 121,
            "sql": "/safe/query.sql",
            "python": "/safe/python",
            "helper": "/safe/helper",
        }
        state = Mock()
        state.stat.return_value = SimpleNamespace(st_dev=7, st_ino=8)
        stack.enter_context(patch.object(identity, "private_state", return_value=state))
        stack.enter_context(
            patch.object(identity, "process", return_value=self.process)
        )
        stack.enter_context(patch.object(identity, "send"))
        stack.enter_context(patch.object(time, "monotonic", return_value=0))
        stack.enter_context(patch.object(os, "set_blocking"))
        request = [
            "query",
            "--file",
            "/safe/query.sql",
            "--intent",
            "enumerate",
            "--view",
            "historical",
            "--page-size",
            "1",
            "--json",
        ]
        incoming = Mock()
        incoming.read.side_effect = [
            {"env": {"MEMORIESQL_LOCAL_CREDENTIAL": "fictional-output-canary"}},
            {"args": request},
            {"spawn": True},
            {"registered": True},
            {"op": "stop"},
        ]
        ready = Mock()
        ready.read.return_value = {"identity": self.process}
        stack.enter_context(
            patch.object(identity, "Channel", side_effect=[incoming, ready])
        )
        output, error = Mock(), Mock()
        output.fileno.return_value, error.fileno.return_value = 10, 11
        child = Mock(stdout=output, stderr=error)
        child.wait.return_value = 0
        stack.enter_context(patch.object(subprocess, "Popen", return_value=child))
        chunks = {10: [stdout, b""], 11: [stderr, b""] if stderr else [b""]}
        stack.enter_context(
            patch.object(
                select,
                "select",
                side_effect=lambda reads, *args: (list(reads), [], []),
            )
        )
        stack.enter_context(
            patch.object(os, "read", side_effect=lambda fd, size: chunks[fd].pop(0))
        )
        return stack, plan

    def test_actual_worker_rejects_nonempty_stderr_even_whitespace(self) -> None:
        stack, plan = self.worker_output(b'{"outcome":"available"}', b" ")
        with stack, self.assertRaises(identity.ContainmentFatal):
            identity.worker(plan, Path("/safe/manifest"), "a" * 64)

    def test_actual_worker_rejects_credential_bearing_valid_json(self) -> None:
        for value in (
            b'{"credential":"fictional-output-canary"}',
            b'{"credential":"\\u0066ictional-output-canary"}',
        ):
            stack, plan = self.worker_output(value, b"")
            with (
                stack,
                self.subTest(escaped=b"\\u" in value),
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.worker(plan, Path("/safe/manifest"), "a" * 64)

    def test_actual_worker_accepts_empty_stderr_and_no_credential(self) -> None:
        stack, plan = self.worker_output(b'{"outcome":"available"}', b"")
        with stack:
            self.assertEqual(identity.worker(plan, Path("/safe/manifest"), "a" * 64), 0)

    def test_actual_worker_can_receive_stop_after_normal_deadline(self) -> None:
        plan = {**self.plan, "deadline": 1, "cleanup_deadline": 121}
        state = Mock()
        state.stat.return_value = SimpleNamespace(st_dev=7, st_ino=8)
        clock = [0]

        class Incoming:
            def __init__(self, stream: object, deadline: float) -> None:
                self.deadline = deadline
                self.calls = 0

            def read(inner) -> dict[str, object]:
                identity.remaining(inner.deadline)
                inner.calls += 1
                if inner.calls == 1:
                    clock[0] = 2
                    return {"env": {}}
                return {"op": "stop"}

        with (
            patch.object(identity, "private_state", return_value=state),
            patch.object(identity, "process", return_value=self.process),
            patch.object(identity, "send"),
            patch.object(identity, "Channel", Incoming),
            patch.object(time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(subprocess, "Popen") as spawn,
        ):
            self.assertEqual(identity.worker(plan, Path("/safe/manifest"), "a" * 64), 0)
        spawn.assert_not_called()

    def test_actual_identity_rejects_retained_ids_groups_and_capabilities(self) -> None:
        identity.check_identity(self.process, self.plan)
        for field, value in (
            ("uids", [65534, 65534, 0, 65534]),
            ("uids", [65534, 65534, 65534, 0]),
            ("gids", [1001, 1001, 0, 1001]),
            ("groups", [1001, 27]),
            ("caps", [0, 1, 0]),
        ):
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.check_identity({**self.process, field: value}, self.plan)

    def test_actual_registration_requires_birth_identity_and_lineage(self) -> None:
        for change in ({"start": 457}, {"parent": 999}, {"uids": [0] * 4}):
            ledger = identity.Ledger()
            ledger.birth()
            with (
                patch.object(
                    identity, "process", return_value={**self.process, **change}
                ),
                patch.object(identity, "latch"),
                self.assertRaises(identity.ContainmentFatal),
            ):
                ledger.register(self.process, self.plan, parent=122)
            self.assertFalse(ledger.complete())
        ledger = identity.Ledger()
        ledger.birth()
        with patch.object(identity, "process", return_value=self.process):
            ledger.register(self.process, self.plan, parent=122)
        self.assertTrue(ledger.complete())

    def test_unknown_birth_cannot_be_cleared_by_later_registration_or_close(
        self,
    ) -> None:
        ledger = identity.Ledger()
        ledger.birth()
        with (
            patch.object(identity, "latch"),
            self.assertRaises(identity.ContainmentFatal),
        ):
            ledger.unknown("monitor_died_before_registration")
        with (
            patch.object(identity, "latch"),
            patch.object(identity, "process") as probe,
            self.assertRaises(identity.ContainmentFatal),
        ):
            ledger.register(self.process, self.plan)
        probe.assert_not_called()
        session = identity.Session(self.plan, "fictional", "fictional", "fictional")
        session.ledger = ledger
        session.closed = True
        with self.assertRaises(identity.ContainmentFatal):
            session.close()
        self.assertFalse(ledger.complete())

    def test_substituted_or_caller_writable_evidence_is_rejected(self) -> None:
        baseline = {"st_uid": 1001, "st_mode": stat.S_IFREG | 0o600, "st_nlink": 1}
        identity.check_entry(stat_info(**baseline), 1001, private=True)
        for change in (
            {"st_uid": 65534},
            {"st_nlink": 2},
            {"st_mode": stat.S_IFLNK | 0o600},
            {"st_mode": stat.S_IFREG | 0o620},
        ):
            with self.assertRaises(identity.ContainmentFatal):
                identity.check_entry(
                    stat_info(**{**baseline, **change}), 1001, private=True
                )
        # JSON decoding supplies an object, not permission to treat an arbitrary
        # JSON value as the independently bound manifest.
        invalid_manifests: tuple[object, ...] = (None, [], True, "not-an-object")
        for value in invalid_manifests:
            raw = json.dumps(value).encode()
            with (
                self.subTest(manifest=value),
                patch.object(os, "open", return_value=99),
                patch.object(os, "read", return_value=raw),
                patch.object(os, "fstat", return_value=stat_info(**baseline)),
                patch.object(os, "close"),
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.read_binding(
                    Path("/inert/manifest"), hashlib.sha256(raw).hexdigest()
                )

        invalid_nonces: tuple[object, ...] = (None, [], 0)
        for nonce in invalid_nonces:
            with (
                self.subTest(nonce=nonce),
                patch.object(Path, "is_symlink") as probe,
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.private_state({"root": "/inert", "nonce": nonce})
            probe.assert_not_called()

    def test_fixed_launcher_allows_only_two_bound_actions(self) -> None:
        plan = {
            **self.plan,
            "python": "/approved/python",
            "helper": "/approved/helper.py",
        }
        for action in ("run-case", "cleanup"):
            argv = identity.sudo_argv(
                plan, action, Path("/approved/manifest"), "a" * 64
            )
            self.assertEqual(
                argv[:10],
                [
                    "/usr/bin/sudo",
                    "-n",
                    "-u",
                    "#65534",
                    "-g",
                    "#1001",
                    "--",
                    "/usr/bin/env",
                    "-i",
                    "/approved/python",
                ],
            )
            self.assertNotIn("sh", argv)
        with self.assertRaises(identity.ContainmentFatal):
            identity.sudo_argv(plan, "child", Path("/approved/manifest"), "a" * 64)

    def test_missing_binding_fails_before_any_privileged_or_process_operation(
        self,
    ) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(subprocess, "Popen") as spawn,
            patch.object(identity, "process") as probe,
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.Session.prepare(
                Path("/fictional"), "fictional", "fictional", "fictional"
            )
        spawn.assert_not_called()
        probe.assert_not_called()

    def test_protocol_eof_output_limit_and_deadline_fail(self) -> None:
        stream = Mock()
        for data in (b"", b"x" * (identity.MAX_FRAME + 1)):
            with (
                patch.object(time, "monotonic", return_value=0),
                patch.object(select, "select", return_value=([stream], [], [])),
                patch.object(os, "read", return_value=data),
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.Channel(stream, 1).read()
        with (
            patch.object(time, "monotonic", return_value=1),
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.Channel(stream, 1).read()

    def test_actual_cli_pretty_json_requires_bounded_eof(self) -> None:
        stream = Mock()
        stderr = Mock()
        stream.fileno.return_value, stderr.fileno.return_value = 10, 11
        chunks = {10: [], 11: [b""]}
        raw = json.dumps(
            {"outcome": "available", "page": {"rows": [1]}}, indent=2
        ).encode()
        chunks[10] = [raw[:10], raw[10:], b""]
        with (
            patch.object(os, "set_blocking"),
            patch.object(time, "monotonic", return_value=0),
            patch.object(
                select,
                "select",
                side_effect=lambda reads, *a: (list(reads), [], []),
            ),
            patch.object(os, "read", side_effect=lambda fd, size: chunks[fd].pop(0)),
        ):
            self.assertEqual(
                identity.cli_reply(stream, 1, stderr, "fictional-output-canary")[
                    "outcome"
                ],
                "available",
            )
        with (
            patch.object(os, "set_blocking"),
            patch.object(time, "monotonic", return_value=0),
            patch.object(select, "select", return_value=([], [], [])),
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.cli_reply(stream, 1, stderr, "fictional-output-canary")
        with (
            patch.object(os, "set_blocking"),
            patch.object(time, "monotonic", return_value=0),
            patch.object(select, "select", return_value=([stream], [], [])),
            patch.object(os, "read", return_value=b"x" * (identity.MAX_OUTPUT + 1)),
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.cli_reply(stream, 1, stderr, "fictional-output-canary")

    def test_partial_write_is_bounded_and_never_resets_deadline(self) -> None:
        stream = Mock()
        with (
            patch.object(os, "set_blocking"),
            patch.object(time, "monotonic", return_value=0),
            patch.object(select, "select", return_value=([], [stream], [])),
            patch.object(os, "write", return_value=0),
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.send(stream, {"registered": True}, 1)

    def test_actual_result_stops_before_later_case_even_when_latch_fails(self) -> None:
        from scripts.prove_query_host import ContainmentResult

        later: list[bool] = []

        class Cases(unittest.TestCase):
            def test_a(self) -> None:
                raise identity.ContainmentFatal("fictional_unknown_birth")

            def test_b(self) -> None:
                later.append(True)

        # Runner imports the same flat-copied helper name used on hosted jobs.
        with (
            patch.dict("sys.modules", {"query_host_cli_identity": identity}),
            patch.object(
                identity, "latch", side_effect=OSError("fictional evidence failure")
            ),
        ):
            result = unittest.TextTestRunner(
                stream=io.StringIO(), resultclass=ContainmentResult
            ).run(unittest.defaultTestLoader.loadTestsFromTestCase(Cases))
        assert isinstance(result, ContainmentResult)
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(result.shouldStop)
        self.assertTrue(result.containment_fatal)
        self.assertTrue(result.fatal_evidence_failed)
        self.assertFalse(later)


if __name__ == "__main__":
    unittest.main()
