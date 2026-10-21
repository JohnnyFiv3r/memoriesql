"""Inert controls of the actual fixed helper. No sudo, UID, PID or socket probes."""

from __future__ import annotations

import io
import json
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.runtime import query_host_cli_identity as identity


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
        identity.check_entry(SimpleNamespace(**baseline), 1001, private=True)
        for change in (
            {"st_uid": 65534},
            {"st_nlink": 2},
            {"st_mode": stat.S_IFLNK | 0o600},
            {"st_mode": stat.S_IFREG | 0o620},
        ):
            with self.assertRaises(identity.ContainmentFatal):
                identity.check_entry(
                    SimpleNamespace(**{**baseline, **change}), 1001, private=True
                )

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
            patch.dict(identity.os.environ, {}, clear=True),
            patch.object(identity.subprocess, "Popen") as spawn,
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
                patch.object(identity.time, "monotonic", return_value=0),
                patch.object(
                    identity.select, "select", return_value=([stream], [], [])
                ),
                patch.object(identity.os, "read", return_value=data),
                self.assertRaises(identity.ContainmentFatal),
            ):
                identity.Channel(stream, 1).read()
        with (
            patch.object(identity.time, "monotonic", return_value=1),
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.Channel(stream, 1).read()

    def test_actual_cli_pretty_json_requires_bounded_eof(self) -> None:
        stream = Mock()
        raw = json.dumps(
            {"outcome": "available", "page": {"rows": [1]}}, indent=2
        ).encode()
        with (
            patch.object(identity.os, "set_blocking"),
            patch.object(identity.time, "monotonic", return_value=0),
            patch.object(identity.select, "select", return_value=([stream], [], [])),
            patch.object(identity.os, "read", side_effect=[raw[:10], raw[10:], b""]),
        ):
            self.assertEqual(identity.cli_reply(stream, 1)["outcome"], "available")
        with (
            patch.object(identity.os, "set_blocking"),
            patch.object(identity.time, "monotonic", return_value=0),
            patch.object(identity.select, "select", return_value=([], [], [])),
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.cli_reply(stream, 1)
        with (
            patch.object(identity.os, "set_blocking"),
            patch.object(identity.time, "monotonic", return_value=0),
            patch.object(identity.select, "select", return_value=([stream], [], [])),
            patch.object(
                identity.os, "read", return_value=b"x" * (identity.MAX_OUTPUT + 1)
            ),
            self.assertRaises(identity.ContainmentFatal),
        ):
            identity.cli_reply(stream, 1)

    def test_partial_write_is_bounded_and_never_resets_deadline(self) -> None:
        stream = Mock()
        with (
            patch.object(identity.os, "set_blocking"),
            patch.object(identity.time, "monotonic", return_value=0),
            patch.object(identity.select, "select", return_value=([], [stream], [])),
            patch.object(identity.os, "write", return_value=0),
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
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(result.shouldStop)
        self.assertTrue(result.containment_fatal)
        self.assertTrue(result.fatal_evidence_failed)
        self.assertFalse(later)


if __name__ == "__main__":
    unittest.main()
