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


def select_ci_run(
    runs: list[dict[str, Any]], *, sha: str, main_sha: str, tag: str, version: str
) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha != main_sha:
        raise ValueError("release must target the exact current main commit")
    if version != "0.0.1a1" or tag != f"v{version}":
        raise ValueError("release tag must match the approved preview version")
    candidates = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
        and run.get("path") == ".github/workflows/python-package.yml"
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("repository", {}).get("id") == REPOSITORY_ID
    ]
    if not candidates:
        raise ValueError("no package CI for this exact main commit")
    latest = max(candidates, key=lambda run: int(run["id"]))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("latest exact-head package CI has not passed")
    run_id = int(latest["id"])
    if run_id <= 0:
        raise ValueError("invalid package CI run ID")
    return run_id


def verify_artifacts(directory: Path, inventory: dict[str, Any]) -> tuple[Path, ...]:
    rows = inventory["artifacts"]
    expected = {
        "memoriesql-0.0.1a1-py3-none-any.whl",
        "memoriesql-0.0.1a1.tar.gz",
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
    else:
        inventory = json.loads(
            (ROOT / "docs/verification/package-artifact-inventory.json").read_text()
        )
        paths = verify_artifacts(args.directory, inventory)
        if args.output is not None:
            args.output.mkdir(exist_ok=False)
            for path in paths:
                shutil.copyfile(path, args.output / path.name)
        print(json.dumps({"verified_artifacts": [path.name for path in paths]}))


if __name__ == "__main__":
    main()
