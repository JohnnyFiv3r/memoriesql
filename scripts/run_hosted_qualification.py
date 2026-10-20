"""Run the existing installed fictional proof, retaining every child outcome."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

POSTGRES = (
    "postgres@sha256:a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636"
)
RESTARTS = (
    "prove_assessed_relation_restart.py",
    "prove_fold_recovery_restart.py",
    "prove_database_time_recovery_restart.py",
    "prove_query_result_restart.py",
)
SCRIPTS = (
    "run_installed_acceptance.py",
    "prove_package_isolation.py",
    "prove_release_installation.py",
    *RESTARTS,
)
RUNTIME_DEPS = (
    "psycopg[binary]==3.3.3",
    "pydantic==2.13.3",
    "pydantic-ai-slim==2.27.0",
    "sqlglot==30.19.0",
)
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_COMMANDS = 40


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_file_limit() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))


class Commands:
    def __init__(self, evidence: Path, seconds: int = 3000) -> None:
        self.evidence = evidence
        self.deadline = time.monotonic() + seconds
        self.cleanup_deadline = self.deadline + 120
        self.rows: list[dict[str, Any]] = []
        self.active: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []

    def save(self) -> None:
        temporary = self.evidence / "commands.json.tmp"
        temporary.write_text(json.dumps(self.rows, indent=2) + "\n")
        temporary.replace(self.evidence / "commands.json")

    def start(
        self, name: str, args: list[str], cwd: Path
    ) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
        if not re.fullmatch("[a-z0-9-]+", name) or len(self.rows) >= MAX_COMMANDS:
            raise ValueError("invalid or excessive qualification command")
        log = self.evidence / (name + ".log")
        row: dict[str, Any] = {
            "name": name,
            "argv": args,
            "started": time.time(),
            "exit_code": None,
            "complete": False,
            "log": log.name,
        }
        self.rows.append(row)
        self.save()
        with log.open("xb") as stream:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=child_file_limit,
            )
        row["pid"] = process.pid
        self.active.append((process, row))
        self.save()
        return process, row

    def finish(
        self,
        child: tuple[subprocess.Popen[bytes], dict[str, Any]],
        *,
        cleanup: bool = False,
    ) -> int:
        process, row = child
        try:
            deadline = self.cleanup_deadline if cleanup else self.deadline
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            row["deadline_exhausted"] = True
            self.stop(process)
        row.update(exit_code=process.returncode, complete=True, finished=time.time())
        log = self.evidence / row["log"]
        row.update(log_sha256=file_hash(log), log_bytes=log.stat().st_size)
        self.active.remove(child)
        self.save()
        if row.get("deadline_exhausted") or row["log_bytes"] >= MAX_LOG_BYTES:
            return 1
        return int(process.returncode) if process.returncode is not None else 1

    @staticmethod
    def stop(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def run(
        self, name: str, args: list[str], cwd: Path, *, cleanup: bool = False
    ) -> None:
        if self.finish(self.start(name, args, cwd), cleanup=cleanup):
            raise RuntimeError("qualification command failed: " + name)

    def capture(self, name: str, args: list[str], cwd: Path) -> str:
        self.run(name, args, cwd)
        return (self.evidence / (name + ".log")).read_text()

    def close(self) -> None:
        for child in list(self.active):
            self.stop(child[0])
            self.finish(child)


def identity_inventory(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        raise ValueError("missing installed test identity inventory")
    for module, cases in value.items():
        if not isinstance(module, str) or not isinstance(cases, dict) or not cases:
            raise ValueError("invalid installed test module inventory")
        if any(
            not isinstance(identity, str) or type(number) is not int or number < 1
            for identity, number in cases.items()
        ):
            raise ValueError("invalid installed test identity multiplicity")
    return value


def check_shards(root: Path, count: int = 3) -> None:
    discovered: dict[str, int] | None = None
    identities: dict[str, dict[str, int]] | None = None
    selected: set[str] = set()
    for index in range(1, count + 1):
        report = json.loads((root / f"acceptance-{index}.json").read_text())
        if discovered is None:
            discovered = report["discovered_modules"]
            identities = identity_inventory(report.get("discovered_tests"))
            if {m: sum(c.values()) for m, c in identities.items()} != discovered:
                raise ValueError("installed identity and module counts disagree")
        if report["discovered_modules"] != discovered:
            raise ValueError("shards discovered different suites")
        if identity_inventory(report.get("discovered_tests")) != identities:
            raise ValueError("shards discovered different test identities")
        modules = set(report["selected_modules"])
        if (
            modules & selected
            or len(modules) != len(report["selected_modules"])
            or not modules <= discovered.keys()
            or report["skipped"]
            or report["errors"]
            or report["failures"]
        ):
            raise ValueError("overlap, skips or failed installed tests")
        if report["tests_run"] != sum(discovered[m] for m in modules):
            raise ValueError("incomplete installed shard")
        assert identities is not None
        if identity_inventory(report.get("executed_tests")) != {
            module: identities[module] for module in modules
        }:
            raise ValueError("installed test identities or multiplicities differ")
        selected |= modules
    if not discovered or selected != set(discovered):
        raise ValueError("missing installed test modules")


def qualify(
    checkout: Path, artifacts: Path, evidence: Path, root: Path, sha: str
) -> None:
    runner = Commands(evidence)
    python = str(Path(sys.executable).resolve())
    metadata: dict[str, Any] = {
        "source_sha": sha,
        "postgres_index": POSTGRES,
        "python": sys.version,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "runner_os": platform.platform(),
        "architecture": platform.machine(),
        "proof_complete": False,
        "qualification_requires": [
            "successful interpreter job",
            "verified PostgreSQL stop",
        ],
        "child_file_limit_bytes": MAX_LOG_BYTES,
    }
    try:
        runner.run(
            "verify-source", ["git", "diff", "--exit-code", "HEAD", "--"], checkout
        )
        actual = runner.capture(
            "source-head", ["git", "rev-parse", "HEAD"], checkout
        ).strip()
        if actual != sha or not re.fullmatch("[0-9a-f]{40}", sha):
            raise ValueError("checkout source identity mismatch")
        wheels = list(artifacts.glob("*.whl"))
        sdists = list(artifacts.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise ValueError("expected one reviewed wheel and sdist")
        wheel, sdist = wheels[0], sdists[0]
        metadata["artifacts"] = {
            p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
            for p in (wheel, sdist)
        }
        runner.run(
            "verify-artifacts",
            [
                python,
                "scripts/inspect_python_distribution.py",
                str(artifacts),
                "--source-commit",
                sha,
                "--check-inventory",
                str(artifacts / "python-package-artifacts.json"),
            ],
            checkout,
        )
        dependencies = root / "dependencies"
        runner.run(
            "dependencies",
            [
                python,
                "-m",
                "pip",
                "download",
                *RUNTIME_DEPS,
                "--only-binary=:all:",
                "--dest",
                str(dependencies),
            ],
            checkout,
        )
        (evidence / "dependency-hashes.json").write_text(
            json.dumps(
                {p.name: file_hash(p) for p in sorted(dependencies.iterdir())}, indent=2
            )
            + "\n"
        )
        runner.run(
            "build-tools",
            [python, "-m", "pip", "install", "setuptools==80.9.0", "wheel==0.45.1"],
            checkout,
        )
        rebuilt = root / "from-sdist"
        runner.run(
            "sdist-wheel",
            [
                python,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                str(sdist),
                "--wheel-dir",
                str(rebuilt),
            ],
            checkout,
        )
        rebuilt_wheels = list(rebuilt.glob("*.whl"))
        if len(rebuilt_wheels) != 1 or file_hash(wheel) != file_hash(rebuilt_wheels[0]):
            raise ValueError("reviewed and sdist-rebuilt wheel bytes differ")
        metadata["sdist_wheel_equal"] = True
        test_root = root / "installed"
        runner.run("venv", [python, "-m", "venv", str(test_root / "venv")], checkout)
        installed_python = str(test_root / "venv/bin/python")
        runner.run(
            "install-wheel",
            [
                installed_python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(dependencies),
                str(wheel),
            ],
            checkout,
        )
        inputs = [checkout / "scripts" / name for name in SCRIPTS]
        inputs += [
            checkout / "contracts" / name
            for name in ("runtime-inventory.json", "public-registry.json")
        ]
        inputs += sorted((checkout / "tests/migrations").glob("*.py"))
        inputs += sorted((checkout / "tests/runtime").glob("*.py"))
        inputs += [checkout / "tests/unit/test_contract_catalog_preview.py"]
        inputs += sorted((checkout / "tests/fixtures").glob("*.json"))
        # Copy only committed public files; never a local evidence directory.
        tracked = set(
            runner.capture("tracked-inputs", ["git", "ls-files"], checkout).splitlines()
        )
        names: set[str] = set()
        for source in inputs:
            if (
                source.is_symlink()
                or str(source.relative_to(checkout)) not in tracked
                or source.name in names
            ):
                raise ValueError("unexpected copied test input: " + source.name)
            names.add(source.name)
            shutil.copyfile(source, test_root / source.name)
        metadata["copied_inputs"] = {
            str(p.relative_to(checkout)): file_hash(p) for p in inputs
        }
        environment_probe = (
            "import importlib.metadata as m,json,os,psycopg,sys; "
            "c=psycopg.connect(os.environ['N1_TEST_DATABASE_URL']); "
            "v=c.execute('SHOW server_version').fetchone()[0]; c.close(); "
            "assert v.startswith('18.4'), v; "
            "print(json.dumps({'python':sys.version,'postgres':v,"
            "'distributions':{d.metadata['Name']:d.version for d in m.distributions()}}))"
        )
        metadata["installed_environment"] = json.loads(
            runner.capture(
                "installed-environment",
                [installed_python, "-I", "-c", environment_probe],
                test_root,
            )
        )
        runner.run(
            "package-isolation",
            [installed_python, "-I", "prove_package_isolation.py"],
            test_root,
        )
        runner.run(
            "release-installation",
            [installed_python, "-I", "prove_release_installation.py"],
            test_root,
        )
        children = [
            runner.start(
                f"acceptance-{i}",
                [
                    installed_python,
                    "-I",
                    "run_installed_acceptance.py",
                    "--shard",
                    f"{i}/3",
                    "--result-json",
                    f"acceptance-{i}.json",
                ],
                test_root,
            )
            for i in range(1, 4)
        ]
        exits = [
            runner.finish(child) for child in children
        ]  # collect every exit, including peers of failures
        for report in test_root.glob("acceptance-*.json"):
            shutil.copyfile(report, evidence / report.name)
        if any(exits):
            raise RuntimeError("installed acceptance failed; every shard exit retained")
        check_shards(test_root)
        restart_failures = []
        for index, script in enumerate(RESTARTS, 1):
            try:
                runner.run(
                    f"restart-{index}-prepare",
                    [installed_python, "-I", script, "prepare"],
                    test_root,
                )
                runner.run(
                    f"restart-{index}-recover",
                    [installed_python, "-I", script, "recover"],
                    test_root,
                )
            except Exception as error:
                restart_failures.append(str(error))
            finally:
                # Cleanup shares one reserved global deadline, not a new budget per child.
                try:
                    runner.run(
                        f"restart-{index}-cleanup",
                        [installed_python, "-I", script, "cleanup"],
                        test_root,
                        cleanup=True,
                    )
                except Exception as error:
                    restart_failures.append(str(error))
        if restart_failures:
            raise RuntimeError("; ".join(restart_failures))
        metadata["proof_complete"] = True
    except BaseException as error:
        metadata["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            runner.close()
        except BaseException as error:
            metadata["proof_complete"] = False
            metadata["cleanup_failure"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            (evidence / "qualification.json").write_text(
                json.dumps(metadata, indent=2) + "\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
    ):
        raise ValueError("this driver is restricted to isolated Linux Actions jobs")
    if os.environ.get("N1_TEST_DATABASE_URL") != (
        "postgresql://postgres:synthetic@127.0.0.1:5432/postgres"
    ):
        raise ValueError("only the isolated fictional loopback service is eligible")
    os.environ.pop("PYTHONPATH", None)

    # Interruptions still close owned child groups and retain failure metadata.
    def interrupted(signum: int, frame: Any) -> None:
        raise RuntimeError(f"qualification interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    # Created by this job, not a reusable local qualification environment.
    args.root.mkdir()
    args.evidence.mkdir(parents=True, exist_ok=True)
    qualify(
        Path.cwd().resolve(),
        args.artifacts.resolve(),
        args.evidence.resolve(),
        args.root.resolve(),
        args.source_sha,
    )


if __name__ == "__main__":
    main()
