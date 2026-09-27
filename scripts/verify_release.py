"""Fail closed before releasing exact-head CI artifacts; never upload or build."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "JohnnyFiv3r/memoriesql"
REPOSITORY_ID = 1357510758
APPROVED_VERSION = "0.0.12"
RELEASE_INVENTORY = (
    ROOT / "docs/verification/runtime-0.0.12-package-artifact-inventory.json"
)


def select_ci_run(
    runs: list[dict[str, Any]], *, sha: str, main_sha: str, tag: str, version: str
) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha != main_sha:
        raise ValueError("release must target the exact current main commit")
    if version != APPROVED_VERSION or tag != f"v{version}":
        raise ValueError("release tag must match the approved runtime version")
    candidates = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") == "workflow_dispatch"
        and run.get("path") == ".github/workflows/python-package.yml"
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("repository", {}).get("id") == REPOSITORY_ID
    ]
    if not candidates:
        raise ValueError("no explicitly requested qualification for this exact main commit")
    latest = max(candidates, key=lambda run: int(run["id"]))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("latest exact-head package CI has not passed")
    run_id = int(latest["id"])
    if run_id <= 0:
        raise ValueError("invalid package CI run ID")
    return run_id


def verify_qualification_jobs(
    document: dict[str, Any], *, run_id: int, sha: str
) -> None:
    """A green workflow with skipped database jobs cannot qualify a release."""
    jobs = document.get("jobs", [])
    required = {"package", "compatibility (3.13)", "compatibility (3.14)"}
    if document.get("total_count") != len(jobs) or len(jobs) != len(required):
        raise ValueError("incomplete or unexpected qualification job inventory")
    if {job.get("name") for job in jobs} != required:
        raise ValueError("qualification requires package and both supported interpreters")
    for job in jobs:
        if (
            job.get("run_id") != run_id
            or job.get("head_sha") != sha
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
        ):
            raise ValueError("every exact-run qualification job must actually pass")


def verify_artifacts(
    directory: Path,
    inventory: dict[str, Any],
) -> tuple[Path, ...]:
    if inventory.get("version") != APPROVED_VERSION:
        raise ValueError("inventory must match the approved runtime version")
    rows = inventory["artifacts"]
    expected = {
        f"memoriesql-{APPROVED_VERSION}-py3-none-any.whl",
        f"memoriesql-{APPROVED_VERSION}.tar.gz",
    }
    if len(rows) != 2 or {row["filename"] for row in rows} != expected:
        raise ValueError("inventory must contain only the approved wheel and sdist")
    names = {path.name for path in directory.iterdir()}
    if names not in (expected, expected | {"python-package-artifacts.json"}):
        raise ValueError("unexpected or missing release files")
    paths = []
    for row in rows:
        path = directory / row["filename"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("release artifact must be a regular file")
        payload = path.read_bytes()
        if len(payload) != row["size_bytes"]:
            raise ValueError("release artifact size differs from reviewed inventory")
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ValueError("release artifact hash differs from reviewed inventory")
        paths.append(path)
    return tuple(paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ci = commands.add_parser("ci")
    ci.add_argument("--runs", type=Path, required=True)
    ci.add_argument("--sha", required=True)
    ci.add_argument("--main-sha", required=True)
    ci.add_argument("--tag", required=True)
    qualification = commands.add_parser("qualification")
    qualification.add_argument("--jobs", type=Path, required=True)
    qualification.add_argument("--run-id", type=int, required=True)
    qualification.add_argument("--sha", required=True)
    artifacts = commands.add_parser("artifacts")
    artifacts.add_argument("directory", type=Path)
    artifacts.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "ci":
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        run_id = select_ci_run(
            json.loads(args.runs.read_text())["workflow_runs"],
            sha=args.sha,
            main_sha=args.main_sha,
            tag=args.tag,
            version=project["version"],
        )
        print(f"run_id={run_id}")
    elif args.command == "qualification":
        verify_qualification_jobs(
            json.loads(args.jobs.read_text()), run_id=args.run_id, sha=args.sha
        )
        print("exact-head installed qualification verified")
    else:
        inventory = json.loads(RELEASE_INVENTORY.read_text())
        paths = verify_artifacts(args.directory, inventory)
        if args.output is not None:
            args.output.mkdir(exist_ok=False)
            for path in paths:
                shutil.copyfile(path, args.output / path.name)
        print(json.dumps({"verified_artifacts": [path.name for path in paths]}))


if __name__ == "__main__":
    main()
