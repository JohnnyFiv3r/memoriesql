"""Fail closed when staged public files cross the approved preview boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", ".mypy_cache", ".ruff_cache", "__pycache__", "build", "dist"}
DENIED_ROOTS = {"apps", "artifacts", "migrations"}


def repository_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not (set(path.relative_to(ROOT).parts) & IGNORED_PARTS)
    )


def verify() -> dict[str, object]:
    files = repository_files()
    roots = {path.relative_to(ROOT).parts[0] for path in files}
    denied_roots = sorted(roots & DENIED_ROOTS)
    if denied_roots:
        raise ValueError(f"denied public repository roots: {denied_roots}")

    text_failures: list[str] = []
    for path in files:
        if path.suffix.lower() in {".png", ".whl", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"/(?:Users|home)/[^/\s]+/", text):
            text_failures.append(f"{path.relative_to(ROOT)}:absolute home path")
        for owner, repository in re.findall(
            r"https://github\.com/([\w-]+)/([\w.-]+)", text
        ):
            if (owner, repository) != ("JohnnyFiv3r", "memoriesql"):
                text_failures.append(f"{path.relative_to(ROOT)}:non-public repository URL")
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
    print(json.dumps(verify(), indent=2, sort_keys=True))
