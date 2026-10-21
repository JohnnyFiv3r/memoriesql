from __future__ import annotations

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.prove_query_host import EXPECTED_TEST_IDS
from scripts.run_hosted_qualification import SCRIPTS, Commands

ROOT = Path(__file__).resolve().parents[2]


class HostedQueryHostProof(unittest.TestCase):
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
        cli_case = "test_public_cli_query_and_reuse_cross_real_socket_with_current_authority"
        self.assertIn(cli_case, names)
        self.assertEqual(len(names), 17)

        def source_for(outcome: str) -> str | None:
            if outcome == "empty":
                return None
            text = "import unittest\nclass QueryHostAcceptance(unittest.TestCase):\n"
            for index, method in enumerate(names):
                if outcome == "missing-cli" and method == cli_case:
                    continue
                if outcome == "skip" and index == 0:
                    text += " @unittest.skip('fictional')\n"
                body = "self.fail('fictional')" if outcome == "fail" and index == 0 else "pass"
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

        cases = {name: source_for(name) for name in (
            "pass", "fail", "skip", "missing-cli", "extra", "duplicate", "empty"
        )}
        for name, source in cases.items():
            with self.subTest(outcome=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence = root / "evidence"
                evidence.mkdir()
                shutil.copyfile(
                    ROOT / "scripts/prove_query_host.py", root / "prove_query_host.py"
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


if __name__ == "__main__":
    unittest.main()
