from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from scripts import run_hosted_qualification as hosted
from scripts.prove_query_host import EXPECTED_TEST_IDS
from scripts.run_hosted_qualification import SCRIPTS, Commands
from tests.runtime import query_host_cli_identity as identity

ROOT = Path(__file__).resolve().parents[2]


class HostedQueryHostProof(unittest.TestCase):
    def binding_fixture(self, root: Path) -> tuple[str, Path, dict[str, Any]]:
        helper = root / "query_host_cli_identity.py"
        helper.write_bytes(b"fixed helper")
        product = root / "memoriesql/product.py"
        product.parent.mkdir()
        product.write_bytes(b"installed product")
        wheel = root / "reviewed.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("memoriesql/product.py", product.read_bytes())
        python = str(Path(sys.executable).absolute())
        facts = {
            "service_uid": os.geteuid(),
            "client_uid": os.geteuid() + 1,
            "gid": os.getegid(),
            "groups": [os.getegid()],
            "python": python,
            "python_resolved": str(Path(python).resolve()),
            "python_sha256": hosted.file_hash(Path(python).resolve()),
            "helper_sha256": hosted.file_hash(helper),
            "import_roots": [str(root)],
            "installed": [
                ["memoriesql/product.py", str(product), hosted.file_hash(product)]
            ],
            "sudo_sha256": "a" * 64,
            "env_sha256": "a" * 64,
        }
        return python, wheel, facts

    def seal(
        self, root: Path, python: str, wheel: Path, facts: dict[str, Any]
    ) -> dict[str, str]:
        original_hash = hosted.file_hash
        original_stat = Path.lstat
        system = {Path("/usr/bin/sudo"), Path("/usr/bin/env")}
        runner = Mock()
        runner.capture.return_value = json.dumps(facts)
        with (
            patch.dict(
                os.environ,
                {
                    hosted.HOST_ENV[2]: hosted.HOST_AUTHORITY_SHA256,
                    "RUNNER_ENVIRONMENT": "github-hosted",
                    "RUNNER_OS": "Linux",
                    "SOURCE_SHA": "a" * 40,
                },
                clear=True,
            ),
            patch.object(
                hosted,
                "file_hash",
                side_effect=lambda p: "a" * 64 if p in system else original_hash(p),
            ),
            patch.object(
                Path,
                "lstat",
                autospec=True,
                side_effect=lambda p: SimpleNamespace(st_uid=0, st_mode=0o100755)
                if p in system
                else original_stat(p),
            ),
        ):
            env = hosted.prepare_host_binding(runner, root, python, "a" * 40, wheel)
        runner.capture.assert_called_once_with(
            "query-host-facts", [python, "-I", "-c", hosted.HOST_FACTS_PROBE], root
        )
        runner.run.assert_not_called()
        return env

    def test_actual_seal_is_private_readable_by_unchanged_helper_and_exclusive(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python, wheel, facts = self.binding_fixture(root)
            env = self.seal(root, python, wheel, facts)
            plan = identity.read_binding(
                Path(env[hosted.HOST_ENV[0]]), env[hosted.HOST_ENV[1]]
            )
            self.assertEqual(plan["import_roots"], facts["import_roots"])
            self.assertEqual(plan["authority_sha256"], hosted.HOST_AUTHORITY_SHA256)
            self.assertEqual(plan["python"], "@installed-python")
            self.assertNotIn("targets", plan)
            self.assertNotIn("state_identity", plan)
            self.assertEqual(
                Path(env[hosted.HOST_ENV[0]]).stat().st_mode & 0o777, 0o600
            )
            with self.assertRaises(FileExistsError):
                self.seal(root, python, wheel, facts)

    def test_seal_rejects_identity_input_and_frame_substitution_before_publication(
        self,
    ) -> None:
        variants = [
            ("service_uid", 0),
            ("client_uid", 0),
            ("client_uid", os.geteuid()),
            ("gid", 0),
            ("groups", [0]),
            ("service_uid", os.geteuid() + 3),
            ("python_sha256", "0" * 64),
            ("helper_sha256", "0" * 64),
            ("python", "/substituted"),
            ("sudo_sha256", "0" * 64),
            ("import_roots", []),
            ("import_roots", ["relative"]),
            ("import_roots", ["/other"]),
            ("installed", []),
            ("installed", [["memoriesql/extra.py", "/extra", "0" * 64]]),
            ("import_roots", ["/" + "a" * 65536]),
        ]
        for key, value in variants:
            with (
                self.subTest(key=key, value=str(value)[:60]),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                python, wheel, facts = self.binding_fixture(root)
                facts[key] = value
                with self.assertRaises(ValueError):
                    self.seal(root, python, wheel, facts)
                self.assertFalse((root / "query-host-cli-binding.json").exists())

    def test_probe_verifies_actual_record_and_retains_complete_search_order(
        self,
    ) -> None:
        import base64
        import importlib.metadata
        import pwd

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "memoriesql/product.py"
            path.parent.mkdir()
            path.write_bytes(b"product")
            helper = root / "query_host_cli_identity.py"
            helper.write_bytes(b"helper")
            entry = importlib.metadata.PackagePath("memoriesql/product.py")
            entry.hash = importlib.metadata.FileHash(
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(b"product").digest())
                .rstrip(b"=")
                .decode()
            )
            entry.size = 7
            distribution = Mock(files=[entry])
            distribution.locate_file.side_effect = lambda p: root / str(p)
            original_read = Path.read_bytes
            original_stat = Path.lstat
            system = {Path("/usr/bin/sudo"), Path("/usr/bin/env")}
            search = [str(root), str(root / "missing.zip")]

            def read(p: Path) -> bytes:
                if p in system:
                    return b"system"
                return original_read(
                    helper if str(p) == "query_host_cli_identity.py" else p
                )

            with (
                patch.object(
                    importlib.metadata, "distribution", return_value=distribution
                ),
                patch.object(
                    pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=12345)
                ),
                patch.object(Path, "read_bytes", autospec=True, side_effect=read),
                patch.object(
                    Path,
                    "lstat",
                    autospec=True,
                    side_effect=lambda p: SimpleNamespace(st_uid=0, st_mode=0o100755)
                    if p in system
                    else original_stat(p),
                ),
                patch.object(sys, "path", search),
                redirect_stdout(io.StringIO()) as output,
            ):
                exec(hosted.HOST_FACTS_PROBE, {})
                facts = json.loads(output.getvalue())
                self.assertEqual(facts["import_roots"], search)
                self.assertEqual(facts["client_uid"], 12345)
                path.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "RECORD mismatch"):
                    exec(hosted.HOST_FACTS_PROBE, {})

    def test_child_environment_preserves_clocks_and_scopes_fixture_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Commands(root)
            child = Mock(pid=123)
            env = dict(
                zip(
                    hosted.HOST_ENV,
                    ("/binding", "b" * 64, hosted.HOST_AUTHORITY_SHA256),
                )
            )
            with (
                patch.object(subprocess, "Popen", return_value=child) as launch,
                patch.dict(os.environ, {**env, "SOURCE_SHA": "a" * 40}),
            ):
                runner.start("inert-child", ["not-executed"], root, env=env)
                actual = launch.call_args.kwargs["env"]
                self.assertEqual(
                    actual["N1_QUALIFICATION_DEADLINE"], repr(runner.deadline)
                )
                self.assertEqual(
                    actual["N1_QUALIFICATION_CLEANUP_DEADLINE"],
                    repr(runner.cleanup_deadline),
                )
                self.assertEqual(runner.cleanup_deadline - runner.deadline, 120)
                self.assertEqual({k: actual[k] for k in hosted.HOST_ENV}, env)
                runner.start("inert-other", ["not-executed"], root)
                self.assertTrue(
                    set(hosted.HOST_ENV).isdisjoint(launch.call_args.kwargs["env"])
                )
                with self.assertRaises(ValueError):
                    runner.start(
                        "inert-injection",
                        [],
                        root,
                        env={"N1_QUALIFICATION_DEADLINE": "0"},
                    )
            self.assertEqual(len(runner.rows), 2)

    def test_binding_requires_independent_authority_before_capture(self) -> None:
        for authority in (
            None,
            "",
            "a" * 64,
            "94faf05c3b1ecd50556d3f3ec3321852a1c44ec6337b165a2ff00fb54178976f",
        ):
            runner = Mock()
            with (
                self.subTest(authority=authority),
                patch.dict(os.environ, {}, clear=True),
            ):
                if authority is not None:
                    os.environ["N1_TEST_CLI_IDENTITY_AUTHORITY_SHA256"] = authority
                with self.assertRaises(ValueError):
                    hosted.prepare_host_binding(
                        runner, Path("/unused"), "/python", "a" * 40, Path("/wheel")
                    )
                runner.capture.assert_not_called()

    def test_source_and_platform_are_checked_before_capture(self) -> None:
        for key, value in (
            ("SOURCE_SHA", "b" * 40),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("RUNNER_OS", "Darwin"),
        ):
            environment = {
                hosted.HOST_ENV[2]: hosted.HOST_AUTHORITY_SHA256,
                "SOURCE_SHA": "a" * 40,
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                key: value,
            }
            runner = Mock()
            with (
                patch.dict(os.environ, environment, clear=True),
                self.assertRaises(ValueError),
            ):
                hosted.prepare_host_binding(
                    runner, Path("/unused"), "/python", "a" * 40, Path("/wheel")
                )
            runner.capture.assert_not_called()

    def test_existing_symlink_is_never_replaced_or_followed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python, wheel, facts = self.binding_fixture(root)
            target = root / "preserved"
            target.write_bytes(b"preserve")
            (root / "query-host-cli-binding.json").symlink_to(target)
            with self.assertRaises(FileExistsError):
                self.seal(root, python, wheel, facts)
            self.assertEqual(target.read_bytes(), b"preserve")

    def test_separate_host_proof_is_copied_and_propagates_actual_outcomes(self) -> None:
        self.assertIn("prove_query_host.py", SCRIPTS)
        tree = ast.parse((ROOT / "scripts/run_hosted_qualification.py").read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "qualify"
        )
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "runner"
            and node.func.attr == "run"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "query-host"
        ]
        self.assertEqual(
            len(calls), 1, "confined default discovery omits host acceptance"
        )
        invocation = compile(
            ast.fix_missing_locations(
                ast.Module(body=[ast.Expr(calls[0])], type_ignores=[])
            ),
            "<actual-hosted-host-invocation>",
            "exec",
        )
        names = [identity.rsplit(".", 1)[1] for identity in EXPECTED_TEST_IDS]
        cli_case = (
            "test_public_cli_query_and_reuse_cross_real_socket_with_current_authority"
        )
        self.assertIn(cli_case, names)
        self.assertEqual(len(names), 17)

        def source_for(outcome: str) -> str | None:
            if outcome == "empty":
                return None
            text = "import unittest\nfrom query_host_cli_identity import ContainmentFatal\nclass QueryHostAcceptance(unittest.TestCase):\n"
            for index, method in enumerate(names):
                if outcome == "missing-cli" and method == cli_case:
                    continue
                if outcome == "skip" and index == 0:
                    text += " @unittest.skip('fictional')\n"
                body = "pass"
                if outcome == "fail" and index == 0:
                    body = "self.fail('fictional')"
                if outcome == "fatal" and index == 0:
                    body = "raise ContainmentFatal('inert_partial_registration')"
                if outcome == "fatal" and index > 0:
                    body = "self.fail('later_proof_must_not_run')"
                text += f" def {method}(self): {body}\n"
            if outcome == "extra":
                text += " def test_unselected(self): pass\n"
            if outcome == "duplicate":
                text += (
                    "def load_tests(loader, tests, pattern):\n"
                    f" tests.addTest(QueryHostAcceptance({names[0]!r}))\n"
                    " return tests\n"
                )
            return text

        cases = {
            name: source_for(name)
            for name in (
                "pass",
                "fail",
                "skip",
                "missing-cli",
                "extra",
                "duplicate",
                "empty",
                "fatal",
            )
        }
        for name, source in cases.items():
            with self.subTest(outcome=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence = root / "evidence"
                evidence.mkdir()
                shutil.copyfile(
                    ROOT / "scripts/prove_query_host.py", root / "prove_query_host.py"
                )
                shutil.copyfile(
                    ROOT / "tests/runtime/query_host_cli_identity.py",
                    root / "query_host_cli_identity.py",
                )
                if source is not None:
                    (root / "query_host_acceptance.py").write_text(source)
                # The ordinary shard discovery cannot substitute for this proof.
                self.assertEqual(
                    unittest.TestLoader().discover(str(root)).countTestCases(), 0
                )
                runner = Commands(evidence, seconds=10)
                context = {
                    "runner": runner,
                    "installed_python": sys.executable,
                    "test_root": root,
                    "host_environment": dict(
                        zip(
                            hosted.HOST_ENV,
                            ("/inert", "0" * 64, hosted.HOST_AUTHORITY_SHA256),
                        )
                    ),
                }
                if name == "pass":
                    exec(invocation, context)
                else:
                    with self.assertRaises(RuntimeError):
                        exec(invocation, context)
                runner.close()
                self.assertEqual(len(runner.rows), 1)
                self.assertEqual(
                    runner.rows[0]["exit_code"], 0 if name == "pass" else 1
                )
                self.assertTrue(runner.rows[0]["complete"])
                self.assertTrue(runner.rows[0]["log_sha256"])
                self.assertFalse(runner.active)
                if name == "fatal":
                    log = (evidence / runner.rows[0]["log"]).read_text()
                    self.assertIn("Ran 1 test", log)
                    self.assertNotIn("later_proof_must_not_run", log)
                    self.assertTrue(
                        (root / "query-host-containment-fatal.json").exists()
                    )


if __name__ == "__main__":
    unittest.main()
