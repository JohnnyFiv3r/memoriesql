from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.run_hosted_qualification import check_shards


class InstalledDiscoveryTests(unittest.TestCase):
    def run_fixture(self, *, failure: bool = False, skip: bool = False) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = Path(__file__).resolve().parents[2] / "scripts/run_installed_acceptance.py"
            shutil.copyfile(script, root / script.name)
            (root / "test_base.py").write_text(textwrap.dedent("""\
                import unittest
                class Base(unittest.TestCase):
                    def test_one(self): pass
                    def test_two(self): pass
                """))
            (root / "test_imported.py").write_text(textwrap.dedent("""\
                from test_base import Base
                class Child(Base):
                    def test_child(self): pass
                """))
            (root / "test_custom.py").write_text(textwrap.dedent(f"""\
                import unittest
                from pathlib import Path
                from test_base import Base
                class Custom(Base):
                    def test_custom(self):
                        if {skip!r}: self.skipTest('fictional skip')
                        self.assertFalse({failure!r}, 'fictional failure')
                def load_tests(loader, standard, pattern):
                    counter = Path('hook-count.txt')
                    count = int(counter.read_text()) if counter.exists() else 0
                    counter.write_text(str(count + 1))
                    return unittest.TestSuite([Custom('test_one'), Custom('test_custom')])
                """))
            reports = []
            for index in (1, 2, 3):
                (root / "hook-count.txt").unlink(missing_ok=True)
                result = subprocess.run(
                    [sys.executable, "-I", script.name, "--shard", f"{index}/3",
                     "--result-json", f"acceptance-{index}.json"],
                    cwd=root, capture_output=True, text=True, timeout=30,
                )
                report = json.loads((root / f"acceptance-{index}.json").read_text())
                expected = sum(report["discovered_modules"][m] for m in report["selected_modules"])
                self.assertEqual(report["tests_run"], expected, result.stdout + result.stderr)
                self.assertEqual((root / "hook-count.txt").read_text(), "1")
                self.assertEqual(result.returncode, 1 if report["failures"] else 0)
                self.assertEqual(report["executed_tests"], {
                    m: report["discovered_tests"][m] for m in report["selected_modules"]
                })
                reports.append(report)
            if not failure and not skip:
                check_shards(root)
            else:
                with self.assertRaises(ValueError):
                    check_shards(root)
            return reports

    def test_imported_inherited_and_custom_loaded_instances_execute_once_as_planned(self) -> None:
        reports = self.run_fixture()
        plan = reports[0]["discovered_tests"]
        self.assertEqual(plan["test_base"], {
            "test_base.Base.test_one": 2, "test_base.Base.test_two": 2,
        })
        self.assertEqual(len(plan["test_imported"]), 3)
        self.assertEqual(plan["test_custom"], {
            "test_custom.Custom.test_one": 1, "test_custom.Custom.test_custom": 1,
        })
        self.assertEqual(sum(r["tests_run"] for r in reports), 9)
        self.assertEqual(sum(len(v) for v in plan.values()), 7)

    def test_skip_and_failure_propagate_without_losing_identity_counts(self) -> None:
        for field in ("skipped", "failures"):
            with self.subTest(field=field):
                reports = self.run_fixture(skip=field == "skipped", failure=field == "failures")
                self.assertEqual(sum(r[field] for r in reports), 1)


class IdentityCoverageTests(unittest.TestCase):
    def reports(self) -> list[dict[str, Any]]:
        plan = {m: {f"{m}.Case.test_one": 2, f"{m}.Case.test_two": 1}
                for m in ("one", "two", "three")}
        return [{"discovered_modules": {m: 3 for m in plan},
                 "discovered_tests": deepcopy(plan), "selected_modules": [m],
                 "executed_tests": {m: deepcopy(plan[m])}, "tests_run": 3,
                 "skipped": 0, "errors": 0, "failures": 0} for m in plan]

    def check(self, reports: list[dict[str, Any]]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, report in enumerate(reports, 1):
                (root / f"acceptance-{index}.json").write_text(json.dumps(report))
            check_shards(root)

    def test_explicit_discovered_multiplicity_is_preserved(self) -> None:
        self.check(self.reports())

    def test_missing_extra_duplicate_substituted_and_changed_identities_are_refused(self) -> None:
        for mode in ("missing", "extra", "duplicate", "substitute", "discovery",
                     "missing_inventory", "duplicate_module", "unknown_module"):
            reports = self.reports()
            actual = reports[0]["executed_tests"]["one"]
            if mode == "missing":
                del actual["one.Case.test_two"]
            elif mode == "extra":
                actual["one.Case.test_extra"] = 1
            elif mode == "duplicate":
                actual["one.Case.test_one"] = 3
            elif mode == "substitute":
                del actual["one.Case.test_two"]
                actual["one.Case.test_one"] = 3
            elif mode == "discovery":
                reports[1]["discovered_tests"]["one"] = {"one.Other.test": 3}
            elif mode == "missing_inventory":
                del reports[0]["executed_tests"]
            elif mode == "duplicate_module":
                reports[0]["selected_modules"] = ["one", "one"]
            else:
                reports[0]["selected_modules"] = ["unknown"]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.check(reports)


if __name__ == "__main__":
    unittest.main()
