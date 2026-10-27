from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import secrets
import shutil
import signal
import stat
import struct
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


def elf_object(
    strings: bytes, dynamic: list[tuple[int, int]], symbols: tuple[int, ...] = ()
) -> bytes:
    """A minimal ELF64 little-endian shared object: one identity PT_LOAD, a
    PT_DYNAMIC segment and its string table, optionally symbols with DT_HASH."""
    strtab = 64 + 2 * 56
    body = bytearray(strings)
    while (strtab + len(body)) % 8:
        body.append(0)
    tags = [*dynamic, (5, strtab), (10, len(strings))]
    if symbols:
        symtab = strtab + len(body)
        for name in (0, *symbols):
            body += struct.pack("<IBBHQQ", name, 0, 0, 0, 0, 0)
        table = strtab + len(body)
        count = len(symbols) + 1
        body += struct.pack(f"<II{1 + count}I", 1, count, *([0] * (1 + count)))
        while (strtab + len(body)) % 8:
            body.append(0)
        tags += [(6, symtab), (11, 24), (4, table)]
    at = strtab + len(body)
    entries = b"".join(struct.pack("<qQ", tag, value) for tag, value in (*tags, (0, 0)))
    size = at + len(entries)
    header = struct.pack(
        "<4sBBBB8xHHIQQQIHHHHHH",
        b"\x7fELF",
        2,
        1,
        1,
        0,
        3,
        62,
        1,
        0,
        64,
        0,
        0,
        64,
        56,
        2,
        64,
        0,
        0,
    )
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, size, size, 8)
    segment = struct.pack("<IIQQQQQQ", 2, 6, at, at, at, len(entries), len(entries), 8)
    return header + load + segment + bytes(body) + entries


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
            "setpriv_sha256": "a" * 64,
        }
        return python, wheel, facts

    def seal(
        self, root: Path, python: str, wheel: Path, facts: dict[str, Any]
    ) -> dict[str, str]:
        original_hash = hosted.file_hash
        original_stat = Path.lstat
        system = {Path("/usr/bin/sudo"), Path("/usr/bin/env"), Path("/usr/bin/setpriv")}
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
            ("setpriv_sha256", "0" * 64),
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
            system = {
                Path("/usr/bin/sudo"),
                Path("/usr/bin/env"),
                Path("/usr/bin/setpriv"),
            }
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

    def test_client_writable_is_world_or_shared_group_write(self) -> None:
        def info(mode: int, gid: int) -> os.stat_result:
            return os.stat_result((mode, 0, 0, 1, 0, gid, 0, 0, 0, 0))

        for mode, gid, expected in (
            (stat.S_IFREG | 0o644, 1001, False),
            (stat.S_IFREG | 0o646, 7, True),
            (stat.S_IFREG | 0o664, 1001, True),
            (stat.S_IFREG | 0o664, 7, False),
            (stat.S_IFDIR | 0o775, 1001, True),
            (stat.S_IFDIR | 0o755, 1001, False),
        ):
            with self.subTest(mode=oct(mode), gid=gid):
                self.assertIs(hosted.client_writable(info(mode, gid), 1001), expected)

    def test_precheck_refuses_inputs_the_run_as_worker_refuses(self) -> None:
        def tree(root: Path) -> tuple[str, Path, list[str], int]:
            (root / "bin").mkdir(mode=0o755)
            python = root / "bin/python"
            python.write_bytes(b"interpreter")
            python.chmod(0o755)
            helper = root / "query_host_cli_identity.py"
            helper.write_bytes(b"helper")
            helper.chmod(0o644)
            lib = root / "lib"
            (lib / "pkg").mkdir(mode=0o755, parents=True)
            lib.chmod(0o755)
            (lib / "a.py").write_bytes(b"a")
            (lib / "pkg/b.py").write_bytes(b"b")
            for item in (lib / "a.py", lib / "pkg/b.py"):
                item.chmod(0o644)
            root.chmod(0o755)
            roots = [str(lib), str(root / "missing.zip")]
            return str(python), helper, roots, lib.stat().st_gid

        client = os.geteuid() + 1
        changes: list[tuple[str, Any]] = [
            ("world-writable module", lambda r: (r / "lib/a.py").chmod(0o666)),
            ("shared-group directory", lambda r: (r / "lib/pkg").chmod(0o775)),
            ("world-writable ancestor", lambda r: r.chmod(0o777)),
            ("symlink in import root", lambda r: (r / "lib/c.py").symlink_to("a.py")),
            ("missing helper", lambda r: (r / "query_host_cli_identity.py").unlink()),
        ]
        for label, change in [("clean", None), *changes]:
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "installed"
                root.mkdir()
                python, helper, roots, gid = tree(root)
                if change is None:
                    hosted.check_client_unwritable(python, helper, roots, client, gid)
                    continue
                change(root)
                with self.assertRaises(ValueError):
                    hosted.check_client_unwritable(python, helper, roots, client, gid)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "installed"
            root.mkdir()
            python, helper, roots, gid = tree(root)
            with self.assertRaisesRegex(ValueError, "client-owned"):
                hosted.check_client_unwritable(python, helper, roots, os.geteuid(), gid)
            # A sticky, non-client directory over a non-client child stays usable,
            # but cannot protect a missing search entry from creation.
            root.chmod(0o1777)
            hosted.check_client_unwritable(python, helper, roots[:1], client, gid)
            with self.assertRaisesRegex(ValueError, "client-writable"):
                hosted.check_client_unwritable(python, helper, roots, client, gid)

    def test_driver_sets_umask_022_before_creating_anything(self) -> None:
        events: list[tuple[str, object]] = []

        def umask(mask: int) -> int:
            events.append(("umask", mask))
            return 0o002

        argv = [
            "run_hosted_qualification.py",
            "part",
            "--part",
            "1",
            "--artifacts",
            "/fictional/artifacts",
            "--evidence",
            "/fictional/evidence",
            "--root",
            "/fictional/root",
            "--source-sha",
            "a" * 40,
        ]
        env = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_OS": "Linux",
            "N1_TEST_DATABASE_URL": (
                "postgresql://postgres:synthetic@127.0.0.1:5432/postgres"
            ),
        }
        with (
            patch.dict(os.environ, env),
            patch.object(sys, "argv", argv),
            patch.object(signal, "signal"),
            patch.object(os, "umask", side_effect=umask),
            patch.object(
                Path,
                "mkdir",
                autospec=True,
                side_effect=lambda path, *a, **k: events.append(("mkdir", str(path))),
            ),
            patch.object(
                hosted,
                "qualify",
                side_effect=lambda *a: events.append(("qualify", None)),
            ),
        ):
            hosted.main()
        self.assertEqual(events[0], ("umask", 0o022))
        self.assertEqual(events[1], ("mkdir", "/fictional/root"))
        self.assertEqual(events[-1], ("qualify", None))

    def test_search_path_rewrite_is_exactly_its_prefix_and_never_shared(self) -> None:
        lib = "/opt/hostedtoolcache/Python/3.13.16/x64/lib"
        new = b"/tmp/mqh-abcdefghijk"
        self.assertEqual(len(new), 20)
        strings = b"\0" + lib.encode() + b"\0libc.so.6\0"
        runpath, needed = 1, 2 + len(lib)
        data = elf_object(strings, [(1, needed), (29, runpath)])
        rewritten = bytearray(data)
        offsets = hosted.relocate_search_path(rewritten, lib, new)
        self.assertEqual(len(offsets), 1)
        self.assertEqual(len(rewritten), len(data))
        differing = [i for i, (a, b) in enumerate(zip(data, rewritten)) if a != b]
        self.assertTrue(set(differing) <= set(range(offsets[0], offsets[0] + 20)))
        view = hosted.DynamicView(bytes(rewritten))
        self.assertEqual(
            [value for _, value in view.search_paths()],
            ["/tmp/mqh-abcdefghijk/Python/3.13.16/x64/lib"],
        )
        # A string sharing only the untouched tail is kept.
        tail = elf_object(strings, [(1, runpath + 25), (29, runpath)])
        self.assertTrue(hosted.relocate_search_path(bytearray(tail), lib, new))
        # Another dynamic or symbol string inside the prefix, or running into it.
        spanning = b"\0zz" + lib.encode() + b"\0"
        for blob, dynamic, symbols in (
            (strings, [(1, runpath + 5), (29, runpath)], ()),
            (strings, [(29, runpath)], (runpath + 3,)),
            (spanning, [(1, 1), (29, 3)], ()),
        ):
            with self.subTest(dynamic=dynamic, symbols=symbols):
                with self.assertRaisesRegex(ValueError, "shares its bytes"):
                    hosted.relocate_search_path(
                        bytearray(elf_object(blob, dynamic, symbols)), lib, new
                    )
        # Only an exact source search path is rewritten; R4 judges the rest.
        other = b"\0" + lib.encode() + b":/usr/local/lib\0"
        self.assertEqual(
            hosted.relocate_search_path(
                bytearray(elf_object(other, [(29, 1)])), lib, new
            ),
            [],
        )

    def toolcache(self, top: Path) -> tuple[Path, Path]:
        """A world-writable synthetic toolcache whose ELF objects search its lib."""
        base = top / "tc/Python/3.13.16/x64"
        lib = str(base / "lib").encode()
        for folder in ("bin", "lib/python3.13/lib-dynload"):
            (base / folder).mkdir(parents=True)
        objects = {
            "bin/python3.13": elf_object(b"\0" + lib + b"\0", [(29, 1)]),
            "lib/libpython3.13.so.1.0": elf_object(b"\0" + lib + b"\0", [(29, 1)]),
            "lib/python3.13/lib-dynload/_x.so": elf_object(
                b"\0" + lib + b"\0", [(29, 1)]
            ),
        }
        for name, data in objects.items():
            (base / name).write_bytes(data)
            (base / name).chmod(0o777)
        (base / "lib/python3.13/os.py").write_bytes(b"# stdlib")
        (base / "lib/python3.13/os.py").chmod(0o666)
        (base / "bin/python3").symlink_to("python3.13")
        for directory in [base, *base.rglob("*")]:
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o777)
        root = top / "rr"  # the same byte length as top / "tc"
        root.mkdir(mode=0o755)
        return base, root

    def test_private_copy_relocates_and_normalizes_a_world_writable_toolcache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base, root = self.toolcache(Path(directory))
            with patch.object(hosted, "private_root", return_value=root):
                made, copy, record = hosted.private_interpreter(base)
            self.assertEqual(made, root)
            self.assertEqual(copy, root / "Python/3.13.16/x64")
            self.assertEqual(
                record["normalized_copy_tree_sha256"], record["source_tree_sha256"]
            )
            self.assertNotEqual(
                record["copy_tree_sha256"], record["source_tree_sha256"]
            )
            self.assertEqual(
                [o["path"] for o in record["rewritten_objects"]],
                [
                    "bin/python3.13",
                    "lib/libpython3.13.so.1.0",
                    "lib/python3.13/lib-dynload/_x.so",
                ],
            )
            for entry in [copy, *copy.rglob("*")]:
                info = entry.lstat()
                if entry.is_symlink():
                    continue
                mode = stat.S_IMODE(info.st_mode)
                self.assertIn(mode, (0o755,) if entry.is_dir() else (0o644, 0o755))
            self.assertEqual(
                stat.S_IMODE((copy / "lib/python3.13/os.py").stat().st_mode), 0o644
            )
            view = hosted.DynamicView((copy / "bin/python3.13").read_bytes())
            self.assertEqual([v for _, v in view.search_paths()], [str(copy / "lib")])
            hosted.check_private_copy(copy)

    def test_private_copy_keeps_each_digests_lines_as_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base, root = self.toolcache(Path(directory))
            evidence = Path(directory) / "evidence"
            evidence.mkdir()
            unlisted = hosted.tree_digest(base)
            with patch.object(hosted, "private_root", return_value=root):
                _, _, record = hosted.private_interpreter(base, listings=evidence)
            self.assertEqual(record["source_tree_sha256"], unlisted)
            for name, key in (
                (hosted.SOURCE_TREE_LISTING, "source_tree_sha256"),
                (hosted.COPY_TREE_LISTING, "normalized_copy_tree_sha256"),
            ):
                data = (evidence / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), record[key])
                self.assertNotIn(directory.encode(), data)  # relative entries only
                entries = [json.loads(line) for line in data.splitlines()]
                self.assertIn(["link", "bin/python3", "python3.13"], entries)
                self.assertIn(
                    [
                        "file",
                        "lib/python3.13/os.py",
                        hashlib.sha256(b"# stdlib").hexdigest(),
                    ],
                    entries,
                )

    def test_private_copy_refuses_escaping_links_paths_and_entries(self) -> None:
        cases: list[tuple[str, Any]] = [
            ("link leaves the copy", lambda b: (b / "bin/escape").symlink_to("/etc")),
            (
                "search path leaves the copy",
                lambda b: (b / "lib/extra.so").write_bytes(
                    elf_object(
                        b"\0" + str(b / "lib").encode() + b":/usr/local/lib\0",
                        [(29, 1)],
                    )
                ),
            ),
            (
                "search path leaves the copy",
                lambda b: (b / "lib/origin.so").write_bytes(
                    elf_object(b"\0$ORIGIN/../../../../..\0", [(29, 1)])
                ),
            ),
            ("unsupported entry", lambda b: os.mkfifo(b / "lib/pipe")),
        ]
        for reason, change in cases:
            with self.subTest(reason), tempfile.TemporaryDirectory() as directory:
                base, root = self.toolcache(Path(directory))
                change(base)
                with (
                    patch.object(hosted, "private_root", return_value=root),
                    self.assertRaisesRegex(ValueError, reason),
                ):
                    hosted.private_interpreter(base)

    def test_private_root_is_fresh_exclusive_and_the_prefix_length(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            sticky = SimpleNamespace(st_mode=stat.S_IFDIR | 0o1777, st_uid=0)
            prefix = "x" * (len(str(parent)) + len("/mqh-") + 11)
            original = Path.lstat
            names = iter("aaaaaaaaaaa" + "bbbbbbbbbbb")
            (parent / "mqh-aaaaaaaaaaa").mkdir()  # a taken name is never adopted
            with (
                patch.object(
                    Path,
                    "lstat",
                    autospec=True,
                    side_effect=lambda p: sticky if p == parent else original(p),
                ),
                patch.object(secrets, "choice", side_effect=lambda _: next(names)),
            ):
                root = hosted.private_root(prefix, parent)
            self.assertEqual(root, parent / "mqh-bbbbbbbbbbb")
            self.assertEqual(len(str(root)), len(prefix))
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)
            with self.assertRaisesRegex(ValueError, "root-owned sticky"):
                hosted.private_root(prefix, parent)

    def test_precheck_refusals_name_a_category_and_numbers_never_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "installed"
            root.mkdir()
            python, helper = root / "python", root / "helper.py"
            for item in (python, helper):
                item.write_bytes(b"x")
                item.chmod(0o755)
            root.chmod(0o755)
            gid = root.stat().st_gid
            client = os.geteuid() + 1
            root.chmod(0o777)
            with self.assertRaises(ValueError) as raised:
                hosted.check_client_unwritable(str(python), helper, [], client, gid)
            message = str(raised.exception)
            self.assertIn("category=ancestor", message)
            self.assertIn("mode=0o40777", message)
            self.assertNotIn(directory, message)
            root.chmod(0o755)
            planted = Path(directory) / "planted.so"
            planted.write_bytes(b"x")
            with self.assertRaisesRegex(
                ValueError, "maps an unbound object: category=library"
            ):
                hosted.check_client_unwritable(
                    str(python), helper, [], client, gid, (str(planted),), (root,)
                )
            hosted.check_client_unwritable(
                str(python), helper, [], client, gid, (str(python),), (root,)
            )

    def test_qualify_runs_everything_from_the_venv_on_on_the_private_copy(self) -> None:
        source = (ROOT / "scripts/run_hosted_qualification.py").read_text()
        body = source[source.index("def qualify(") :]
        copy = body.index("private_interpreter(base, listings=evidence)")
        cleared = body.index(
            'for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH")'
        )
        venv = body.index('"venv", [str(copy_python), "-m", "venv"')
        mapped = body.index('"installed-mapped-objects"')
        acceptance = body.index('f"acceptance-{i}"')
        self.assertLess(copy, cleared)
        self.assertLess(cleared, venv)
        self.assertLess(venv, mapped)
        self.assertLess(mapped, acceptance)
        self.assertIn('test_root = private / "installed"', body)


if __name__ == "__main__":
    unittest.main()
