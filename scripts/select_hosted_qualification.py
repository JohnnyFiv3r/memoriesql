"""Select public fictional qualification; no credentials, API calls or dispatch."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = "JohnnyFiv3r/memoriesql"
STANDING_AUTHORITY = "owner:01a15724-1bc8-7830-b633-23ea16bd7b6b"
READY_LABEL = "qualification-ready"
PROOF_ROOTS = ("src/", "tests/", "scripts/", "migrations/", "contracts/", ".github/")
PROOF_FILES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "MANIFEST.in",
    "README.md",
    "PYPI_README.md",
    "LICENSE",
    "NOTICE",
}


def proof_input(path: str) -> bool:
    return (
        path.startswith(PROOF_ROOTS)
        or path in PROOF_FILES
        or (
            "/" not in path
            and path.startswith("requirements")
            and path.endswith(".txt")
        )
    )


def eligible_pr(pr: dict[str, Any]) -> bool:
    return (
        pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
        and not pr.get("draft", True)
        and READY_LABEL in {x.get("name") for x in pr.get("labels", [])}
    )


def select(
    event_name: str,
    event: dict[str, Any],
    sha: str,
    ref: str,
    changed: list[str],
) -> dict[str, Any]:
    repo = event.get("repository", {})
    if repo.get("full_name") != REPOSITORY or repo.get("private") is not False:
        raise ValueError("qualification requires the named public core repository")
    if not re.fullmatch("[0-9a-f]{40}", sha):
        raise ValueError("source SHA must be a full commit identity")
    qualifies = False
    reason = "cheap checks only"
    authority = STANDING_AUTHORITY
    purpose = "candidate"
    if event_name == "workflow_dispatch":
        inputs = event.get("inputs", {})
        if inputs.get("expected_sha") != sha:
            raise ValueError("dispatch SHA changed")
        purpose = inputs.get("purpose", "candidate")
        if purpose not in {"candidate", "release"}:
            raise ValueError("unknown qualification purpose")
        if purpose == "release":
            authority = str(inputs.get("owner_approval_ref", "")).strip()
            if ref != "refs/heads/main" or not authority:
                raise ValueError(
                    "release needs exact main and separate approval reference"
                )
        elif ref == "refs/heads/main":
            # The existing release verifier selects manual main runs. Do not
            # let a standing candidate dispatch masquerade as that release gate.
            raise ValueError(
                "candidate dispatch uses a branch; main dispatch is release-only"
            )
        qualifies, reason = True, "explicit exact-head " + purpose
    elif event_name == "pull_request":
        pr = event.get("pull_request", {})
        if pr.get("head", {}).get("sha") != sha:
            raise ValueError("PR source SHA mismatch")
        action = event.get("action")
        newly_ready = action in {"opened", "reopened", "ready_for_review"} or (
            action == "labeled" and event.get("label", {}).get("name") == READY_LABEL
        )
        qualifies = eligible_pr(pr) and (
            newly_ready or (action == "synchronize" and any(map(proof_input, changed)))
        )
        if qualifies:
            reason = "ready internal candidate: new readiness or material proof inputs"
    elif event_name == "push":
        qualifies = ref == "refs/heads/main" and any(map(proof_input, changed))
        if qualifies:
            reason = "material proof-input change on main; not release qualification"
    return {
        "source_sha": sha,
        "purpose": purpose,
        "qualify": qualifies,
        "authority": authority,
        "reason": reason,
        "changed_proof_inputs": [p for p in changed if proof_input(p)],
    }


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    event_name = os.environ["GITHUB_EVENT_NAME"]
    sha = os.environ["SOURCE_SHA"]
    changed: list[str] = []
    if event_name == "push" or (
        event_name == "pull_request"
        and event.get("action") == "synchronize"
        and eligible_pr(event.get("pull_request", {}))
    ):
        before = event.get("before")
        if not isinstance(before, str) or not re.fullmatch("[0-9a-f]{40}", before):
            raise ValueError("material-change selection requires exact before SHA")
        if set(before) == {"0"}:
            raise ValueError(
                "new branch has no qualified comparison; select explicitly"
            )
        # A missing comparison must fail closed, never mean documentation-only.
        subprocess.run(["git", "cat-file", "-e", before], check=True)
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", before, sha], text=True
        ).splitlines()
    decision = select(event_name, event, sha, os.environ["GITHUB_REF"], changed)
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sha:
        raise ValueError("checkout does not match selected source")
    evidence = Path("build/qualification-request.json")
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text(json.dumps(decision, sort_keys=True, indent=2) + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"qualify={str(decision['qualify']).lower()}\n")
        output.write(f"source_sha={sha}\n")
    print(json.dumps(decision, sort_keys=True))


if __name__ == "__main__":
    main()
