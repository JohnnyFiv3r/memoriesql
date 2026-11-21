"""Fail closed when files git tracks, or would track, cross the approved preview
boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENIED_ROOTS = {"apps", "artifacts"}
# The only repositories tracked text may link: this one, and the public CPython
# builds whose hash-pinned releases the hosted qualification installs. Either may
# be written with one trailing ".git", the clone form.
LINKED_REPOSITORIES = {("JohnnyFiv3r", "memoriesql"), ("actions", "python-versions")}
NO_GIT = (
    "the boundary check needs a git checkout: git was not found on PATH. "
    "Install git and run the check from a clone of the repository."
)
NOT_A_CHECKOUT = (
    "the boundary check needs a git checkout: this copy of the repository is not "
    "one. Run the check from a clone, not from a source archive."
)
CANNOT_LIST = (
    "the boundary check needs a git checkout: git could not list the files here "
    "({reason}). Fix that and run the check again."
)


def _git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *arguments], capture_output=True, check=False
        )
    except FileNotFoundError:
        raise ValueError(NO_GIT) from None


def _reason(result: subprocess.CompletedProcess[bytes]) -> str:
    lines = result.stderr.decode(errors="replace").splitlines()
    return next(
        (line.strip() for line in lines if line.strip()),
        f"git exited {result.returncode}",
    )


def repository_files() -> tuple[Path, ...]:
    """Files in git's index plus untracked files git does not ignore.

    Nothing is published except through git, so its list is the boundary. When
    git cannot give it, the check refuses and never walks the disk instead.
    """
    top = _git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        if not (ROOT / ".git").exists():
            raise ValueError(NOT_A_CHECKOUT)
        raise ValueError(CANNOT_LIST.format(reason=_reason(top)))
    # A copy with no repository of its own, inside some other repository: that
    # one's file list and ignore rules must not stand in for this one's.
    if Path(os.fsdecode(top.stdout.strip())).resolve() != ROOT:
        raise ValueError(NOT_A_CHECKOUT)
    listed = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed.returncode != 0:
        raise ValueError(CANNOT_LIST.format(reason=_reason(listed)))
    names = {os.fsdecode(name) for name in listed.stdout.split(b"\0") if name}
    # A tracked file deleted from the working tree is listed but cannot be read.
    return tuple(
        path for path in (ROOT / name for name in sorted(names)) if path.is_file()
    )


def verify() -> dict[str, object]:
    files = repository_files()
    roots = {path.relative_to(ROOT).parts[0] for path in files}
    denied_roots = sorted(roots & DENIED_ROOTS)
    if denied_roots:
        raise ValueError(f"denied public repository roots: {denied_roots}")

    inventory = json.loads((ROOT / "contracts/migration-inventory.json").read_text())
    allowed_sql = {"migrations/" + row["filename"] for row in inventory["migrations"]}
    actual_sql = {
        str(path.relative_to(ROOT))
        for path in files
        if path.relative_to(ROOT).parts[0] == "migrations"
    }
    if actual_sql != allowed_sql:
        raise ValueError("unregistered migration source")
    for row in inventory["migrations"]:
        if (
            hashlib.sha256(
                (ROOT / "migrations" / row["filename"]).read_bytes()
            ).hexdigest()
            != row["sha256"]
        ):
            raise ValueError("historical migration byte drift")
    text_failures: list[str] = []
    for path in files:
        if path.suffix.lower() in {".png", ".whl", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        provider_identifiers = re.findall(
            r"memoriesql\.(?:[c]odex-passive|[c]laude-code)[^'\s\"]*", text
        )
        if provider_identifiers and str(path.relative_to(ROOT)) not in {
            "migrations/0014_transcript_span_fold.sql",
            "tests/fixtures/schema_snapshot.json",
        }:
            text_failures.append(
                f"{path.relative_to(ROOT)}:historical identifier outside exception"
            )
        if re.search(r"/(?:Users|home)/[^/\s]+/", text):
            text_failures.append(f"{path.relative_to(ROOT)}:absolute home path")
        for owner, repository in re.findall(
            r"https://github\.com/([\w-]+)/([\w.-]+)", text
        ):
            if (owner, repository.removesuffix(".git")) not in LINKED_REPOSITORIES:
                text_failures.append(
                    f"{path.relative_to(ROOT)}:non-public repository URL"
                )
    if text_failures:
        raise ValueError("private-boundary text leaked: " + ", ".join(text_failures))

    registry = json.loads(
        (ROOT / "contracts/public-registry.json").read_text(encoding="utf-8")
    )
    return {
        "default_policy": registry["default_policy"],
        "record_count": len(registry["records"]),
        "denied_roots": denied_roots,
        "private_boundary_leaks": [],
    }


if __name__ == "__main__":
    try:
        result = verify()
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2, sort_keys=True))
