from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.run_hosted_qualification import Commands, check_shards, qualify
from scripts.select_hosted_qualification import READY_LABEL, REPOSITORY, select

SHA = "a" * 40
REF = "refs/heads/codex/candidate"


def event() -> dict[str, Any]:
    return {
        "repository": {"full_name": REPOSITORY, "private": False},
        "action": "synchronize",
        "pull_request": {
            "head": {"sha": SHA, "repo": {"full_name": REPOSITORY}},
            "draft": False,
            "labels": [{"name": READY_LABEL}],
        },
    }


class QualificationSelectionTests(unittest.TestCase):
    def test_material_ready_candidate_and_main_push_qualify(self) -> None:
        for name, ref in (("pull_request", REF), ("push", "refs/heads/main")):
            for path in (
                "src/example.py",
                "tests/x.py",
                "requirements-dev.txt",
                "contracts/example.json",
                ".github/workflows/python-package.yml",
            ):
                with self.subTest(event=name, path=path):
                    decision = select(name, event(), SHA, ref, [path])
                    self.assertTrue(decision["qualify"])
                    self.assertEqual(decision["source_sha"], SHA)
                    self.assertEqual(decision["purpose"], "candidate")

    def test_docs_only_changes_use_cheap_checks(self) -> None:
        for name, ref in (("pull_request", REF), ("push", "refs/heads/main")):
            self.assertFalse(
                select(name, event(), SHA, ref, ["AGENTS.md", "docs/ci-policy.md"])[
                    "qualify"
                ]
            )

    def test_explicit_ready_label_selects_even_a_docs_candidate(self) -> None:
        ready = event() | {"action": "labeled", "label": {"name": READY_LABEL}}
        self.assertTrue(select("pull_request", ready, SHA, REF, [])["qualify"])
        unrelated = ready | {"label": {"name": "documentation"}}
        self.assertFalse(select("pull_request", unrelated, SHA, REF, [])["qualify"])

    def test_fork_draft_and_unselected_intermediate_candidates_do_not_qualify(
        self,
    ) -> None:
        for mode in ("fork", "draft", "no-label"):
            candidate = event()
            pr = candidate["pull_request"]
            if mode == "fork":
                pr["head"]["repo"]["full_name"] = "someone/fork"
            elif mode == "draft":
                pr["draft"] = True
            else:
                pr["labels"] = []
            with self.subTest(mode=mode):
                self.assertFalse(
                    select("pull_request", candidate, SHA, REF, ["src/example.py"])[
                        "qualify"
                    ]
                )

    def test_private_repository_and_wrong_source_fail_closed(self) -> None:
        for mode in ("private", "repository", "sha", "head"):
            candidate = event()
            sha = SHA
            if mode == "private":
                candidate["repository"]["private"] = True
            elif mode == "repository":
                candidate["repository"]["full_name"] = "other/repo"
            elif mode == "head":
                candidate["pull_request"]["head"]["sha"] = "b" * 40
            else:
                sha = "aabbcc"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                select("pull_request", candidate, sha, REF, [])

    def test_candidate_dispatch_is_exact_branch_and_cannot_satisfy_release_gate(
        self,
    ) -> None:
        candidate = event() | {"inputs": {"expected_sha": SHA, "purpose": "candidate"}}
        self.assertTrue(select("workflow_dispatch", candidate, SHA, REF, [])["qualify"])
        with self.assertRaisesRegex(ValueError, "release-only"):
            select("workflow_dispatch", candidate, SHA, "refs/heads/main", [])
        candidate["inputs"]["expected_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "SHA changed"):
            select("workflow_dispatch", candidate, SHA, REF, [])

    def test_release_requires_main_and_a_distinct_approval_reference(self) -> None:
        release = event() | {
            "inputs": {
                "expected_sha": SHA,
                "purpose": "release",
                "owner_approval_ref": "owner:release-exact-head",
            }
        }
        decision = select("workflow_dispatch", release, SHA, "refs/heads/main", [])
        self.assertEqual(decision["authority"], "owner:release-exact-head")
        with self.assertRaises(ValueError):
            select("workflow_dispatch", release, SHA, REF, [])
        release["inputs"]["owner_approval_ref"] = "  "
        with self.assertRaises(ValueError):
            select("workflow_dispatch", release, SHA, "refs/heads/main", [])


class QualificationProcessTests(unittest.TestCase):
    def test_workflow_propagates_driver_failure_through_retained_log(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2] / ".github/workflows/python-package.yml"
        ).read_text()
        step = workflow.split(
            "      - name: Qualify reviewed installed bytes, all shards and restart processes\n"
        )[1].split(
            "      - name: Retain and stop the exact owned PostgreSQL service\n"
        )[0]
        body = textwrap.dedent(step.split("        run: |\n")[1])
        # GitHub's documented unspecified shell is bash -e; explicit bash also
        # sets pipefail. Exercise the actual run block with a fictional driver.
        shell = ["bash", "-e"]
        if "        shell: bash\n" in step:
            shell = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/run_hosted_qualification.py").write_text(
                "print('deliberate fictional driver failure', flush=True)\nraise SystemExit(7)\n"
            )
            result = subprocess.run(
                [*shell, "-c", body],
                cwd=root,
                env=os.environ
                | {
                    "PATH": str(Path(sys.executable).parent)
                    + os.pathsep
                    + os.environ["PATH"],
                    "SOURCE_SHA": SHA,
                    "PROOF_ROOT": str(root / "proof"),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertIn(
                "deliberate fictional driver failure",
                (root / "build/qualification/driver.log").read_text(),
            )

    def test_failure_does_not_lose_peer_exit_or_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Commands(root, seconds=5)
            children = [
                runner.start(
                    "failed", [sys.executable, "-c", "print('failure'); exit(7)"], root
                ),
                runner.start(
                    "peer", [sys.executable, "-c", "print('peer complete')"], root
                ),
            ]
            self.assertEqual([runner.finish(child) for child in children], [7, 0])
            records = json.loads((root / "commands.json").read_text())
            self.assertTrue(all(r["complete"] and r["log_sha256"] for r in records))
            self.assertEqual((root / "peer.log").read_text().strip(), "peer complete")
            self.assertFalse(runner.active)

    def test_deadline_failure_preserves_a_separate_bounded_cleanup_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Commands(root, seconds=5)
            runner.deadline = time.monotonic() - 1
            child = runner.start(
                "timeout", [sys.executable, "-c", "import time; time.sleep(30)"], root
            )
            self.assertNotEqual(runner.finish(child), 0)
            self.assertTrue(runner.rows[0]["deadline_exhausted"])
            runner.run(
                "cleanup", [sys.executable, "-c", "print('clean')"], root, cleanup=True
            )
            self.assertEqual(runner.rows[1]["exit_code"], 0)
            self.assertFalse(runner.active)

    def test_close_terminates_owned_children_and_retains_actual_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Commands(root, seconds=5)
            child = runner.start(
                "owned", [sys.executable, "-c", "import time; time.sleep(30)"], root
            )
            runner.close()
            self.assertIsNotNone(child[0].poll())
            self.assertTrue(runner.rows[0]["complete"])
            self.assertFalse(runner.active)

    def test_failed_launch_is_durable_and_cannot_look_successful(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Commands(root)
            with self.assertRaises(FileNotFoundError):
                runner.start("missing", [str(root / "absent-executable")], root)
            row = json.loads((root / "commands.json").read_text())[0]
            self.assertFalse(row["complete"])
            self.assertIsNone(row["exit_code"])

    def test_driver_failure_and_close_failure_always_retain_incomplete_metadata(
        self,
    ) -> None:
        for close_fails in (False, True):
            with (
                self.subTest(close_fails=close_fails),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                with patch("scripts.run_hosted_qualification.Commands") as commands:
                    commands.return_value.run.side_effect = RuntimeError(
                        "synthetic proof failure"
                    )
                    if close_fails:
                        commands.return_value.close.side_effect = RuntimeError(
                            "synthetic cleanup failure"
                        )
                    with self.assertRaises(RuntimeError):
                        qualify(root, root, root, root, SHA)
                metadata = json.loads((root / "qualification.json").read_text())
                self.assertFalse(metadata["proof_complete"])
                self.assertIn("synthetic proof failure", metadata["failure"])
                self.assertEqual("cleanup_failure" in metadata, close_fails)

    def test_installed_report_counts_real_discovery_and_skips_without_database(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                Path(__file__).resolve().parents[2]
                / "scripts/run_installed_acceptance.py"
            )
            shutil.copyfile(script, root / script.name)
            (root / "test_fictional.py").write_text(
                "import unittest\nclass Fictional(unittest.TestCase):\n"
                " def test_pass(self): pass\n"
                " @unittest.skip('fictional skip')\n def test_skip(self): pass\n"
            )
            result = subprocess.run(
                [sys.executable, "-I", script.name, "--result-json", "report.json"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["discovered_modules"], {"test_fictional": 2})
            self.assertEqual(report["selected_modules"], ["test_fictional"])
            self.assertEqual(report["tests_run"], 2)
            self.assertEqual(report["skipped"], 1)


class QualificationCoverageTests(unittest.TestCase):
    def reports(self) -> list[dict[str, Any]]:
        return [
            {
                "discovered_modules": {"one": 2, "two": 1, "three": 3},
                "selected_modules": [module],
                "tests_run": count,
                "skipped": 0,
                "errors": 0,
                "failures": 0,
            }
            for module, count in (("one", 2), ("two", 1), ("three", 3))
        ]

    def check(self, reports: list[dict[str, Any]]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, report in enumerate(reports, 1):
                (root / f"acceptance-{index}.json").write_text(json.dumps(report))
            check_shards(root)

    def test_all_modules_exactly_once(self) -> None:
        self.check(self.reports())

    def test_overlap_missing_skipped_failed_incomplete_and_different_suite_fail(
        self,
    ) -> None:
        original = self.reports()
        for mode in (
            "overlap",
            "missing",
            "skipped",
            "errors",
            "failures",
            "count",
            "suite",
        ):
            reports = deepcopy(original)
            if mode == "overlap":
                reports[1]["selected_modules"] = ["one"]
                reports[1]["tests_run"] = 2
            elif mode == "missing":
                reports[1]["selected_modules"] = []
                reports[1]["tests_run"] = 0
            elif mode == "count":
                reports[1]["tests_run"] = 0
            elif mode == "suite":
                reports[1]["discovered_modules"]["unrun"] = 1
            else:
                reports[1][mode] = 1
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.check(reports)


if __name__ == "__main__":
    unittest.main()
