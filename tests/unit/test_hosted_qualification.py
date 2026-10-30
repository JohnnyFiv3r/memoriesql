from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.inspect_python_distribution import AUTHORED_SDIST_FILES
from scripts.run_hosted_qualification import (
    ACCEPTANCE_PARTS,
    ACCEPTANCE_SHARDS,
    HOST_COMMANDS,
    HOST_PROOF_PART,
    PART_RECORD,
    PLAN_INPUTS,
    POSTGRES,
    SETUP_COMMANDS,
    SHARDS_PER_PART,
    Commands,
    ResourceSampler,
    acceptance_plan,
    aggregate,
    check_shards,
    extract_artifact,
    file_hash,
    part_artifact_name,
    part_shards,
    qualify,
    tree_digest,
)
from scripts.select_hosted_qualification import READY_LABEL, REPOSITORY, select

SHA = "a" * 40
REF = "refs/heads/codex/candidate"
REPO = Path(__file__).resolve().parents[2]


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
    def test_all_package_authored_inputs_select_full_proof(self) -> None:
        for name, ref in (("pull_request", REF), ("push", "refs/heads/main")):
            with self.subTest(event=name):
                missed = [
                    path
                    for path in sorted(AUTHORED_SDIST_FILES)
                    if not select(name, event(), SHA, ref, [path])["qualify"]
                ]
                self.assertEqual(missed, [])

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
        workflow = (REPO / ".github/workflows/python-package.yml").read_text()
        for start, end, log in (
            (
                "      - name: Qualify reviewed installed bytes for one acceptance part\n",
                "      - name: Retain and stop the exact owned PostgreSQL service\n",
                "driver.log",
            ),
            (
                "      - name: Aggregate every part into this interpreter's proof\n",
                "      - name: Retain the interpreter's aggregated proof\n",
                "aggregate.log",
            ),
        ):
            with self.subTest(log=log):
                step = workflow.split(start)[1].split(end)[0]
                self.check_step_failure(step, log)

    def check_step_failure(self, step: str, log: str) -> None:
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
                    "PART": "1",
                    "PROOF_ROOT": str(root / "proof"),
                    "PARTS_RESULT": "success",
                    "PYTHON_MINOR": "3.13",
                    "AGGREGATION_ROOT": str(root / "aggregation"),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertIn(
                "deliberate fictional driver failure",
                (root / "build/qualification" / log).read_text(),
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
                        qualify(root, root, root, root, SHA, 2)
                metadata = json.loads((root / PART_RECORD).read_text())
                self.assertFalse(metadata["part_complete"])
                # A part never claims a proof; only the aggregation can.
                self.assertNotIn("proof_complete", metadata)
                self.assertFalse((root / "qualification.json").exists())
                self.assertEqual((metadata["part"], metadata["shards"]), (2, [3, 4]))
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


class PinnedInterpreterTests(unittest.TestCase):
    INSTALL = "      - name: Install the pinned CPython build into the tool cache\n"
    BUILD_MTIME = 315532800

    def compatibility(self) -> str:
        workflow = (REPO / ".github/workflows/python-package.yml").read_text()
        return workflow.split("  compatibility:")[1].split("  qualification:")[0]

    def test_every_part_activates_its_interpreters_pinned_build(self) -> None:
        compatibility = self.compatibility()
        pins = re.findall(
            r'          - python-version: "(3\.\d+)"\n'
            r'            python-build: "(\S+)"\n'
            r"            python-build-url: (\S+)\n"
            r"            python-build-sha256: ([0-9a-f]{64})\n",
            compatibility,
        )
        self.assertEqual([pin[0] for pin in pins], ["3.13", "3.14"])
        for minor, build, url, _ in pins:
            self.assertTrue(build.startswith(minor + "."))
            self.assertRegex(
                url,
                "^https://github.com/actions/python-versions/releases/download/"
                rf"{re.escape(build)}-\d+/python-{re.escape(build)}-linux-24.04-x64.tar.gz$",
            )
        # setup-python runs once, after the install, on the exact installed release.
        self.assertEqual(compatibility.count("actions/setup-python@"), 1)
        self.assertIn(
            "      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065\n"
            "        with:\n"
            "          python-version: ${{ steps.python-build.outputs.version }}\n",
            compatibility.split(self.INSTALL)[1],
        )

    def build_archive(self, root: Path) -> Path:
        build = root / "build"
        (build / "bin").mkdir(parents=True)
        (build / "lib/python3.14/__pycache__").mkdir(parents=True)
        (build / "bin/python3.14").write_text("fictional interpreter\n")
        (build / "bin/python3.14").chmod(0o755)
        (build / "bin/python3").symlink_to("python3.14")
        (build / "lib/python3.14/os.py").write_text("fictional = True\n")
        (build / "lib/python3.14/__pycache__/os.cpython-314.pyc").write_bytes(b"pyc")
        (build / "setup.sh").write_text('touch "$RUNNER_TOOL_CACHE/setup-ran"\n')
        for path in (*build.rglob("*"), build):
            if not path.is_symlink():
                os.utime(path, (self.BUILD_MTIME, self.BUILD_MTIME))
        archive = root / "python-3.14.10-linux-24.04-x64.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(build, arcname=".")
        return archive

    def install(
        self, root: Path, archive: Path, cache: Path, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        step = self.compatibility().split(self.INSTALL)[1].split("      - uses:")[0]
        self.assertIn("        shell: bash\n", step)
        body = textwrap.dedent(step.split("        run: |\n")[1])
        (root / "temp").mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", body],
            cwd=root,
            env=os.environ
            | {
                "PYTHON_MINOR": "3.14",
                "PYTHON_BUILD": "3.14.10",
                "PYTHON_BUILD_URL": archive.as_uri(),
                "PYTHON_BUILD_SHA256": file_hash(archive),
                "RUNNER_TEMP": str(root / "temp"),
                "RUNNER_TOOL_CACHE": str(cache),
                "GITHUB_OUTPUT": str(root / "output"),
            }
            | overrides,
            capture_output=True,
            text=True,
        )

    def test_parts_install_one_identical_tree_without_running_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.build_archive(root)
            image, fresh = root / "image-cache", root / "fresh-cache"
            # A runner image's own copy of the same release is replaced, not merged.
            (image / "Python/3.14.10/x64").mkdir(parents=True)
            (image / "Python/3.14.10/x64/image-only").write_text("image\n")
            for cache in (image, fresh):
                result = self.install(root, archive, cache)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                prefix = cache / "Python/3.14.10/x64"
                self.assertTrue((cache / "Python/3.14.10/x64.complete").is_file())
                self.assertFalse((prefix / "image-only").exists())
                self.assertFalse((prefix / "setup.sh").exists())
                self.assertFalse((cache / "setup-ran").exists())
                self.assertEqual(os.readlink(prefix / "python"), "./bin/python3.14")
                self.assertEqual(os.readlink(prefix / "bin/python314"), "python3.14")
                self.assertEqual(os.readlink(prefix / "bin/python"), "python3.14")
                # Source times survive, so the build's compiled files stay current.
                self.assertEqual(
                    (prefix / "lib/python3.14/os.py").stat().st_mtime, self.BUILD_MTIME
                )
            self.assertEqual((root / "output").read_text(), "version=3.14.10\n" * 2)
            self.assertEqual(
                tree_digest(image / "Python/3.14.10/x64"),
                tree_digest(fresh / "Python/3.14.10/x64"),
            )

    def test_an_altered_build_or_another_minor_installs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.build_archive(root)
            for overrides in (
                {"PYTHON_BUILD_SHA256": hashlib.sha256(b"other").hexdigest()},
                {"PYTHON_MINOR": "3.13"},
            ):
                with self.subTest(overrides=overrides):
                    cache = root / "cache"
                    result = self.install(root, archive, cache, **overrides)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((cache / "Python").exists())
                    self.assertFalse((root / "output").exists())


class QualificationCoverageTests(unittest.TestCase):
    def reports(self) -> list[dict[str, Any]]:
        return [
            {
                "discovered_modules": {"one": 2, "two": 1, "three": 3},
                "discovered_tests": {
                    m: {f"{m}.Case.test": n}
                    for m, n in (("one", 2), ("two", 1), ("three", 3))
                },
                "selected_modules": [module],
                "executed_tests": {module: {f"{module}.Case.test": count}},
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
            check_shards(root, len(reports))

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


class AcceptancePartTests(unittest.TestCase):
    def test_three_parts_of_two_shards_partition_one_six_way_plan(self) -> None:
        self.assertEqual((ACCEPTANCE_PARTS, SHARDS_PER_PART), (3, 2))
        self.assertEqual(ACCEPTANCE_SHARDS, 6)
        self.assertEqual(HOST_PROOF_PART, 1)
        self.assertEqual(
            inspect.signature(check_shards).parameters["count"].default,
            ACCEPTANCE_SHARDS,
        )
        shards = [part_shards(part) for part in range(1, ACCEPTANCE_PARTS + 1)]
        self.assertEqual(shards, [[1, 2], [3, 4], [5, 6]])
        for part in (0, ACCEPTANCE_PARTS + 1):
            with self.subTest(part=part), self.assertRaises(ValueError):
                part_shards(part)
        source = (REPO / "scripts/run_hosted_qualification.py").read_text()
        body = source[
            source.index("def qualify(") : source.index("def part_artifact_name(")
        ]
        self.assertIn("for i in shards\n", body)
        self.assertIn('f"{i}/{ACCEPTANCE_SHARDS}"', body)
        # Restarts and the host proof only follow the host part's acceptance.
        self.assertLess(
            body.index("if part != HOST_PROOF_PART:"),
            body.index('sampler.mark("restarts")'),
        )

    def test_the_plan_is_the_acceptance_runners_own_placement(self) -> None:
        weights = REPO / PLAN_INPUTS[1]
        modules = {name: 1 for name in json.loads(weights.read_text())["seconds"]}
        plan = acceptance_plan(REPO / PLAN_INPUTS[0], weights)(modules)
        self.assertEqual(len(plan), ACCEPTANCE_SHARDS)
        placed = [module for shard in plan for module in shard]
        self.assertEqual(sorted(placed), sorted(modules))

    def test_every_report_of_the_plan_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(1, ACCEPTANCE_SHARDS):
                (root / f"acceptance-{index}.json").write_text("{}")
            # One shard report short: the suite was not proved, whatever ran.
            with self.assertRaises((OSError, ValueError, KeyError)):
                check_shards(root)

    def test_a_part_checks_only_its_shards_and_their_plan(self) -> None:
        discovered = {"one": 1, "two": 1, "three": 1, "four": 1}
        tests = {m: {f"{m}.Case.test": 1} for m in discovered}
        plan = [["one"], ["two"], ["three", "four"]]

        def report(modules: list[str]) -> dict[str, Any]:
            return {
                "discovered_modules": discovered,
                "discovered_tests": tests,
                "selected_modules": modules,
                "executed_tests": {m: tests[m] for m in modules},
                "tests_run": len(modules),
                "skipped": 0,
                "errors": 0,
                "failures": 0,
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "acceptance-2.json").write_text(json.dumps(report(["two"])))
            (root / "acceptance-3.json").write_text(
                json.dumps(report(["three", "four"]))
            )
            check_shards(root, 3, [2, 3], lambda _: plan)
            for invalid in ([], [2, 2], [0], [4]):
                with self.subTest(indices=invalid), self.assertRaises(ValueError):
                    check_shards(root, 3, invalid, lambda _: plan)
            # A shard that ran a module planned elsewhere fails, even when complete.
            (root / "acceptance-2.json").write_text(json.dumps(report(["one"])))
            with self.assertRaises(ValueError):
                check_shards(root, 3, [2, 3], lambda _: plan)


class ResourceSamplerTests(unittest.TestCase):
    def proc(self, root: Path, *, pressure: bool = True) -> Path:
        (root / "stat").write_text("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4\n")
        (root / "meminfo").write_text(
            "MemTotal:       16000000 kB\nMemFree:  1 kB\nMemAvailable:   12000000 kB\n"
        )
        (root / "loadavg").write_text("2.50 2.00 1.50 3/400 12345\n")
        if pressure:
            (root / "pressure").mkdir()
            for kind in ("cpu", "io", "memory"):
                (root / "pressure" / kind).write_text(
                    "some avg10=12.50 avg60=1.00 avg300=0.10 total=1\n"
                    "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                )
        return root

    def test_a_sample_reads_cpu_memory_load_and_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sampler = ResourceSampler(root / "out.jsonl", proc=self.proc(root))
            record = sampler.sample()
        self.assertEqual(record["cpu"][:5], [100, 0, 50, 800, 50])
        self.assertEqual(
            record["mem_kib"], {"MemTotal": 16000000, "MemAvailable": 12000000}
        )
        self.assertEqual(record["load1"], 2.5)
        self.assertEqual(
            record["pressure_some_avg10"], {"cpu": 12.5, "io": 12.5, "memory": 12.5}
        )
        self.assertEqual(record["phase"], "setup")

    def test_absent_pressure_files_are_simply_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sampler = ResourceSampler(
                root / "out.jsonl", proc=self.proc(root, pressure=False)
            )
            self.assertEqual(sampler.sample()["pressure_some_avg10"], {})

    def test_the_summary_is_per_phase_cpu_shares_and_extremes(self) -> None:
        sampler = ResourceSampler(Path("unused.jsonl"), interval=15)

        def at(t: float, phase: str, cpu: list[int], available: int) -> dict[str, Any]:
            return {
                "t": t,
                "phase": phase,
                "load1": t / 10,
                "cpu": cpu,
                "mem_kib": {"MemTotal": 16000000, "MemAvailable": available},
                "pressure_some_avg10": {"cpu": t},
            }

        sampler.samples = [
            at(0, "setup", [100, 0, 50, 800, 50], 15000000),
            at(15, "acceptance", [400, 0, 100, 1000, 100], 9000000),
            at(30, "acceptance", [700, 0, 150, 1100, 100], 8000000),
        ]
        summary = sampler.stop()
        acceptance = summary["phases"]["acceptance"]
        self.assertEqual(acceptance["intervals"], 2)
        # (300+50)/600 busy and 50/600 iowait, then (300+50)/450 busy, no iowait.
        self.assertEqual(acceptance["busy_mean"], round((350 / 600 + 350 / 450) / 2, 3))
        self.assertEqual(acceptance["iowait_mean"], round((50 / 600) / 2, 3))
        self.assertEqual(acceptance["load1_max"], 3.0)
        self.assertEqual(acceptance["mem_available_min_kib"], 8000000)
        self.assertEqual(acceptance["cpu_pressure_avg10_max"], 30)
        self.assertEqual(summary["mem_total_kib"], 16000000)
        self.assertEqual(summary["samples"], 3)
        self.assertNotIn("setup", summary["phases"])

    def test_sampling_writes_lines_and_a_read_failure_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "runner-resources.jsonl"
            sampler = ResourceSampler(out, proc=self.proc(root), interval=0.01)
            sampler.start()
            deadline = time.monotonic() + 30
            while len(sampler.samples) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            sampler.mark("acceptance")
            (root / "stat").unlink()  # a later read fails: sampling just ends
            summary = sampler.stop()
            lines = out.read_text().splitlines()
        self.assertGreaterEqual(summary["samples"], 3)
        self.assertEqual(len(lines), summary["samples"])
        self.assertEqual(json.loads(lines[0])["phase"], "setup")
        self.assertFalse(sampler.thread.is_alive())


if __name__ == "__main__":
    unittest.main()


class AggregationTests(unittest.TestCase):
    MINOR = "3.13"
    RUN = "77"
    ATTEMPT = "1"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.checkout = self.base / "checkout"
        (self.checkout / "scripts").mkdir(parents=True)
        (self.checkout / "tests/fixtures").mkdir(parents=True)
        shutil.copyfile(REPO / PLAN_INPUTS[0], self.checkout / PLAN_INPUTS[0])
        weights = {f"test_m{i}": float(10 * (i + 1)) for i in range(9)}
        (self.checkout / PLAN_INPUTS[1]).write_text(json.dumps({"seconds": weights}))
        self.package = self.base / "package"
        self.package.mkdir()
        (self.package / "memoriesql-0.0.12-py3-none-any.whl").write_bytes(b"wheel")
        (self.package / "memoriesql-0.0.12.tar.gz").write_bytes(b"sdist")
        self.discovered = {module: 2 for module in weights}
        self.tests = {
            m: {f"{m}.Case.test_a": 1, f"{m}.Case.test_b": 1} for m in weights
        }
        self.plan = acceptance_plan(
            self.checkout / PLAN_INPUTS[0], self.checkout / PLAN_INPUTS[1]
        )(self.discovered)
        self.files = {part: self.part_files(part) for part in range(1, 4)}
        self.listing_changes: list[Any] = []
        self.zip_changes: dict[int, bytes] = {}

    def part_files(self, part: int) -> dict[str, bytes]:
        evidence: dict[str, bytes] = {}
        names = [
            *SETUP_COMMANDS,
            *(f"acceptance-{i}" for i in part_shards(part)),
            *(HOST_COMMANDS if part == HOST_PROOF_PART else ()),
        ]
        rows = []
        for step, name in enumerate(names):
            log = f"{name} fictional ok\n".encode()
            evidence[f"{name}.log"] = log
            # Shards run concurrently; every other command runs alone, in order.
            started = 100.0 if name.startswith("acceptance-") else 10.0 * step
            rows.append(
                {
                    "name": name,
                    "log": f"{name}.log",
                    "complete": True,
                    "exit_code": 0,
                    "log_sha256": hashlib.sha256(log).hexdigest(),
                    "started": started + (2000.0 if name in HOST_COMMANDS else 0.0),
                    "finished": started
                    + (2000.0 if name in HOST_COMMANDS else 0.0)
                    + (1500.0 + part if name.startswith("acceptance-") else 5.0),
                }
            )
        evidence["commands.json"] = json.dumps(rows).encode()
        for index in part_shards(part):
            modules = self.plan[index - 1]
            evidence[f"acceptance-{index}.json"] = json.dumps(
                {
                    "discovered_modules": self.discovered,
                    "discovered_tests": self.tests,
                    "selected_modules": modules,
                    "executed_tests": {m: self.tests[m] for m in modules},
                    "tests_run": 2 * len(modules),
                    "skipped": 0,
                    "errors": 0,
                    "failures": 0,
                }
            ).encode()
        record = {
            "source_sha": SHA,
            "run_id": self.RUN,
            "run_attempt": self.ATTEMPT,
            "postgres_index": POSTGRES,
            "part": part,
            "parts": ACCEPTANCE_PARTS,
            "shards": part_shards(part),
            "global_shards": ACCEPTANCE_SHARDS,
            "host_proof_part": HOST_PROOF_PART,
            "part_complete": True,
            "sdist_wheel_equal": True,
            "client_unwritable_inputs": True,
            "artifacts": {
                p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
                for p in sorted(self.package.iterdir())
            },
            "interpreter": {
                "build": "setup-python",
                "version": "3.13.16",
                "architecture": "x64",
                "source_python_sha256": "1" * 64,
                "source_tree_sha256": "2" * 64,
                "normalized_copy_tree_sha256": "2" * 64,
                # The relocated copy differs by its private path in every part.
                "python_sha256": str(part) * 64,
            },
            "installed_environment": {"python": "3.13.16", "postgres": "18.4"},
            "copied_inputs": {
                path: file_hash(self.checkout / path) for path in PLAN_INPUTS
            },
            "runner_resources": {"phases": {"acceptance": {"busy_mean": 0.6}}},
        }
        if part == HOST_PROOF_PART:
            record["host_binding_sha256"] = "3" * 64
        evidence[PART_RECORD] = json.dumps(record).encode()
        evidence["dependency-hashes.json"] = b'{"psycopg.whl": "4"}'
        evidence["postgres-image-id.txt"] = b"sha256:5\n"
        evidence["postgres-cleanup-exit.txt"] = b"0\n"
        files = {f"qualification/{name}": data for name, data in evidence.items()}
        files["reviewed-package/python-package-artifacts.json"] = b"{}"
        return files

    def edit(self, part: int, name: str, change: Any) -> None:
        key = f"qualification/{name}"
        value = json.loads(self.files[part][key])
        change(value)
        self.files[part][key] = json.dumps(value).encode()

    @staticmethod
    def archive(files: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in sorted(files.items()):
                archive.writestr(name, data)
        return buffer.getvalue()

    def fetch(self) -> Any:
        zips = {100 + part: self.archive(files) for part, files in self.files.items()}
        artifacts = [
            {
                "id": artifact_id,
                "name": part_artifact_name(
                    self.MINOR, artifact_id - 100, SHA, self.RUN, self.ATTEMPT
                ),
                "expired": False,
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "workflow_run": {"id": int(self.RUN), "head_sha": SHA},
            }
            for artifact_id, data in zips.items()
        ]
        artifacts.append(
            {"id": 1, "name": f"memoriesql-python-{SHA}", "expired": False}
        )
        for change in self.listing_changes:
            change(artifacts)
        zips.update(self.zip_changes)
        listing = json.dumps({"total_count": len(artifacts), "artifacts": artifacts})

        def fetch(arguments: list[str]) -> bytes:
            if arguments[-1] == "per_page=100":
                self.assertEqual(
                    arguments[3],
                    f"repos/JohnnyFiv3r/memoriesql/actions/runs/{self.RUN}/artifacts",
                )
                return listing.encode()
            return zips[int(arguments[1].split("/")[-2])]

        return fetch

    def run_aggregate(
        self, parts_result: str = "success", head: str = SHA
    ) -> dict[str, Any]:
        evidence = self.base / "evidence"
        root = self.base / "root"
        for directory in (evidence, root):
            shutil.rmtree(directory, ignore_errors=True)
        evidence.mkdir()
        root.mkdir()
        with patch(
            "scripts.run_hosted_qualification.checkout_identity", return_value=head
        ):
            aggregate(
                self.checkout,
                self.package,
                evidence,
                root,
                SHA,
                self.MINOR,
                parts_result,
                self.RUN,
                self.ATTEMPT,
                self.fetch(),
            )
        return dict(json.loads((evidence / "qualification.json").read_text()))

    def assert_refused(self, **kwargs: Any) -> None:
        with self.assertRaises((ValueError, KeyError, OSError)):
            self.run_aggregate(**kwargs)
        metadata = json.loads((self.base / "evidence/qualification.json").read_text())
        self.assertFalse(metadata["proof_complete"])
        self.assertIn("failure", metadata)

    def test_every_part_binds_into_one_complete_proof(self) -> None:
        metadata = self.run_aggregate()
        self.assertTrue(metadata["proof_complete"])
        self.assertEqual(
            metadata["census"], {"occurrences": 18, "ids": 18, "modules": 9}
        )
        manifest = json.loads(
            (self.base / "evidence/aggregation-manifest.json").read_text()
        )
        self.assertEqual(sorted(manifest["parts"]), ["1", "2", "3"])
        for part, row in manifest["parts"].items():
            self.assertEqual(row["id"], 100 + int(part))
            self.assertTrue(row["digest"].startswith("sha256:"))
            self.assertEqual(len(row["record_sha256"]), 64)
            self.assertGreater(row["driver_span_seconds"], 1500)
        self.assertEqual(metadata["checkout_head"], SHA)
        self.assertEqual(
            metadata["shard_index_map"], {"1": [1, 2], "2": [3, 4], "3": [5, 6]}
        )

    def test_a_failed_part_job_or_another_checkout_is_never_a_proof(self) -> None:
        for result in ("failure", "cancelled", "skipped", ""):
            with self.subTest(result=result):
                self.assert_refused(parts_result=result)
        self.assert_refused(head="b" * 40)

    def test_missing_duplicate_expired_and_altered_artifacts_are_refused(self) -> None:
        def drop(artifacts: list[dict[str, Any]]) -> None:
            artifacts[:] = [a for a in artifacts if a["id"] != 103]

        def duplicate(artifacts: list[dict[str, Any]]) -> None:
            artifacts.append(dict(artifacts[0], id=999))

        def expire(artifacts: list[dict[str, Any]]) -> None:
            artifacts[1]["expired"] = True

        def other_run(artifacts: list[dict[str, Any]]) -> None:
            artifacts[2]["workflow_run"] = {"id": 78, "head_sha": SHA}

        def no_digest(artifacts: list[dict[str, Any]]) -> None:
            artifacts[0]["digest"] = None

        for change in (drop, duplicate, expire, other_run, no_digest):
            with self.subTest(change=change.__name__):
                self.listing_changes = [change]
                self.assert_refused()
        self.listing_changes = []
        self.zip_changes = {102: self.archive({"qualification/x": b"other"})}
        self.assert_refused()

    def test_parts_must_share_one_attempt_source_package_and_environment(self) -> None:
        cases = {
            "attempt": lambda r: r.update(run_attempt="2"),
            "source": lambda r: r.update(source_sha="b" * 40),
            "incomplete": lambda r: r.update(part_complete=False),
            "package": lambda r: r["artifacts"].popitem(),
            "interpreter": lambda r: r["interpreter"].update(
                source_tree_sha256="9" * 64
            ),
            "minor": lambda r: r["interpreter"].update(version="3.14.8"),
            "environment": lambda r: r["installed_environment"].update(postgres="18.5"),
            "inputs": lambda r: r["copied_inputs"].update({PLAN_INPUTS[0]: "0" * 64}),
            "host": lambda r: r.update(host_proof_part=2),
        }
        original = deepcopy(self.files)
        for name, change in cases.items():
            with self.subTest(case=name):
                self.files = deepcopy(original)
                self.edit(2 if name != "minor" else 1, PART_RECORD, change)
                if name == "minor":
                    for part in (2, 3):
                        self.edit(part, PART_RECORD, change)
                self.assert_refused()
        # Every part agrees, but not with the exact source being aggregated.
        self.files = deepcopy(original)
        (self.checkout / PLAN_INPUTS[1]).write_text('{"seconds": {}}')
        self.assert_refused()
        (self.checkout / PLAN_INPUTS[1]).write_text(
            json.dumps(
                {"seconds": {m: 10.0 * (i + 1) for i, m in enumerate(self.tests)}}
            )
        )
        for name, data in (
            ("dependency-hashes.json", b'{"psycopg.whl": "other"}'),
            ("postgres-image-id.txt", b"sha256:other\n"),
            ("postgres-cleanup-exit.txt", b"1\n"),
        ):
            with self.subTest(file=name):
                self.files = deepcopy(original)
                self.files[3][f"qualification/{name}"] = data
                self.assert_refused()

    def test_the_union_must_be_the_recomputed_plan_without_failures(self) -> None:
        original = deepcopy(self.files)
        moved = self.plan[0][0]

        def take(report: dict[str, Any]) -> None:
            report["selected_modules"].append(moved)
            report["executed_tests"][moved] = self.tests[moved]
            report["tests_run"] += 2

        def give(report: dict[str, Any]) -> None:
            report["selected_modules"].remove(moved)
            del report["executed_tests"][moved]
            report["tests_run"] -= 2

        self.edit(1, "acceptance-2.json", take)
        self.edit(1, "acceptance-1.json", give)
        self.assert_refused()
        for field in ("failures", "errors", "skipped"):
            with self.subTest(field=field):
                self.files = deepcopy(original)
                self.edit(2, "acceptance-3.json", lambda r: r.update({field: 1}))
                self.assert_refused()
        self.files = deepcopy(original)
        del self.files[3]["qualification/acceptance-6.json"]
        self.assert_refused()

    def test_each_part_holds_exactly_its_own_shard_reports(self) -> None:
        original = deepcopy(self.files)
        stray = self.files[1]["qualification/acceptance-1.json"]
        self.files[2]["qualification/acceptance-1.json"] = stray
        self.assert_refused()
        self.files = deepcopy(original)
        self.files[3]["qualification/acceptance-7.json"] = stray
        self.assert_refused()

    def test_restarts_and_host_proof_start_after_the_host_parts_shards(self) -> None:
        def early(rows: list[dict[str, Any]]) -> None:
            first = next(row for row in rows if row["name"] == HOST_COMMANDS[0])
            first["started"] = 1000.0

        self.edit(HOST_PROOF_PART, "commands.json", early)
        self.assert_refused()

    def test_restarts_and_host_proof_run_exactly_once_in_the_host_part(self) -> None:
        original = deepcopy(self.files)
        host_row = {
            "name": "query-host",
            "log": "query-host.log",
            "complete": True,
            "exit_code": 0,
            "log_sha256": hashlib.sha256(b"query-host fictional ok\n").hexdigest(),
            "started": 9000.0,
            "finished": 9005.0,
        }
        self.edit(2, "commands.json", lambda rows: rows.append(host_row))
        self.files[2]["qualification/query-host.log"] = b"query-host fictional ok\n"
        self.assert_refused()
        self.files = deepcopy(original)
        self.edit(1, "commands.json", lambda rows: rows.pop())
        self.assert_refused()
        self.files = deepcopy(original)
        self.edit(1, "commands.json", lambda rows: rows[-1].update(exit_code=1))
        self.assert_refused()
        self.files = deepcopy(original)
        self.files[1]["qualification/query-host.log"] = b"altered\n"
        self.assert_refused()

    def test_artifact_members_cannot_escape_or_link(self) -> None:
        for member in ("../escape", "/absolute", "qualification/../../escape"):
            with self.subTest(member=member):
                data = self.archive({member: b"x"})
                with self.assertRaises(ValueError):
                    extract_artifact(data, self.base / f"x-{abs(hash(member))}")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            link = zipfile.ZipInfo("qualification/link")
            link.external_attr = 0o120777 << 16
            archive.writestr(link, "/etc/passwd")
        with self.assertRaises(ValueError):
            extract_artifact(buffer.getvalue(), self.base / "linked")
