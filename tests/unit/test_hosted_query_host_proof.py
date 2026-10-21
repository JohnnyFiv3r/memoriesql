from __future__ import annotations

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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
        cases = {
            "pass": "import unittest\nclass Fictional(unittest.TestCase):\n def test_host(self): pass\n",
            "fail": "import unittest\nclass Fictional(unittest.TestCase):\n def test_host(self): self.fail('fictional')\n",
            "skip": "import unittest\nclass Fictional(unittest.TestCase):\n @unittest.skip('fictional')\n def test_host(self): pass\n",
            "empty": None,
        }
        for name, source in cases.items():
            with self.subTest(outcome=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence = root / "evidence"
                evidence.mkdir()
                shutil.copyfile(
                    ROOT / "scripts/prove_query_host.py", root / "prove_query_host.py"
                )
                if source is not None:
                    (root / "query_host_fictional_acceptance.py").write_text(source)
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
