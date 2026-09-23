from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import tomllib
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.inspect_python_distribution import (
    AUTHORED_SDIST_FILES,
    development_inventory,
    verify_authored_sdist_members,
)
from scripts.verify_release import (
    APPROVED_VERSION,
    RELEASE_INVENTORY,
    REPOSITORY,
    REPOSITORY_ID,
    select_ci_run,
    verify_artifacts,
)

SHA = "a" * 40


def good_run() -> dict[str, Any]:
    return {
        "id": 123,
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "path": ".github/workflows/python-package.yml",
        "repository": {"full_name": REPOSITORY, "id": REPOSITORY_ID},
        "status": "completed",
        "conclusion": "success",
    }


def select(runs: list[dict[str, Any]]) -> int:
    return select_ci_run(runs, sha=SHA, main_sha=SHA, tag="v0.0.11", version="0.0.11")


class ReleaseControlTests(unittest.TestCase):
    def test_accepts_successful_exact_head_ci(self) -> None:
        self.assertEqual(select([good_run()]), 123)

    def test_rejects_wrong_head_tag_and_version(self) -> None:
        arguments = {
            "sha": SHA,
            "main_sha": SHA,
            "tag": "v0.0.11",
            "version": "0.0.11",
        }
        for field, wrong in (
            ("sha", "malformed"),
            ("main_sha", "b" * 40),
            ("tag", "v0.0.10"),
            ("tag", "v0.0.12"),
            ("tag", "v0.0.11rc1"),
            ("version", "0.0.10"),
            ("version", "0.0.12"),
            ("version", "0.0.11a1"),
            ("version", "0.0.11b1"),
            ("version", "0.0.11rc1"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                select_ci_run([good_run()], **(arguments | {field: wrong}))

    def test_rejects_wrong_ci_identity_and_missing_ci(self) -> None:
        with self.assertRaises(ValueError):
            select([])
        for field, wrong in (
            ("head_sha", "b" * 40),
            ("head_branch", "feature"),
            ("event", "pull_request"),
            ("path", ".github/workflows/other.yml"),
            ("repository", {"full_name": REPOSITORY, "id": 999}),
            ("repository", {"full_name": "unapproved/repository", "id": REPOSITORY_ID}),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                select([good_run() | {field: wrong}])

    def test_no_fallback_to_old_green_run(self) -> None:
        for status, conclusion in (
            ("in_progress", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
        ):
            newer = good_run() | {"id": 124, "status": status, "conclusion": conclusion}
            with self.subTest(status=status), self.assertRaises(ValueError):
                select([good_run(), newer])

    def test_verifies_exact_files_and_ignores_non_authoritative_ci_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inventory = self.make_artifacts(directory)
            self.assertEqual(len(verify_artifacts(directory, inventory)), 2)
            (directory / "python-package-artifacts.json").write_text("{}")
            self.assertEqual(len(verify_artifacts(directory, inventory)), 2)

    def test_rejects_other_inventory_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inventory = self.make_artifacts(directory)
            for version in (None, "0.0.10", "0.0.12", "0.0.11a1", "0.0.11rc1"):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    verify_artifacts(directory, inventory | {"version": version})

    def test_rejects_missing_extra_tampered_and_linked_files(self) -> None:
        for mode in ("missing", "extra", "bytes", "size", "symlink"):
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                inventory = self.make_artifacts(directory)
                wheel = directory / inventory["artifacts"][0]["filename"]
                if mode == "missing":
                    wheel.unlink()
                elif mode == "extra":
                    (directory / "unexpected.whl").write_bytes(b"extra")
                elif mode == "bytes":
                    wheel.write_bytes(b"xxxxx")
                elif mode == "size":
                    wheel.write_bytes(b"longer bytes")
                else:
                    wheel.unlink()
                    wheel.symlink_to(inventory["artifacts"][1]["filename"])
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    verify_artifacts(directory, inventory)

    def test_rejects_changed_or_duplicated_inventory_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inventory = self.make_artifacts(directory)
            for name in ("../outside.whl", "memoriesql-0.0.11.tar.gz"):
                altered = deepcopy(inventory)
                altered["artifacts"][0]["filename"] = name
                with self.subTest(name=name), self.assertRaises(ValueError):
                    verify_artifacts(directory, altered)

    def test_only_current_version_is_eligible_even_with_matching_tag(self) -> None:
        for version in ("0.0.10", "0.0.11rc1", "0.0.12"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                select_ci_run(
                    [good_run()],
                    sha=SHA,
                    main_sha=SHA,
                    tag=f"v{version}",
                    version=version,
                )

    def test_candidate_identity(self) -> None:
        root = Path(__file__).resolve().parents[2]
        verification = root / "docs/verification"
        self.assertEqual(
            RELEASE_INVENTORY,
            verification / "runtime-0.0.11-package-artifact-inventory.json",
        )
        candidate = json.loads(RELEASE_INVENTORY.read_bytes())
        self.assertEqual(candidate["version"], APPROVED_VERSION)
        self.assertEqual(candidate["records"]["count"], 63)
        self.assertEqual(
            {r["filename"] for r in candidate["artifacts"]},
            {"memoriesql-0.0.11-py3-none-any.whl", "memoriesql-0.0.11.tar.gz"},
        )
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["version"], APPROVED_VERSION)
    def test_development_receipt_cannot_authorize_release_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            development = self.make_artifacts(directory)
            (directory / "python-package-artifacts.json").write_text(
                json.dumps(development)
            )
            with self.assertRaises(ValueError):
                verify_artifacts(directory, json.loads(RELEASE_INVENTORY.read_text()))

    def test_development_inventory_rejects_wrong_commit(self) -> None:
        with patch(
            "scripts.inspect_python_distribution.subprocess.check_output",
            return_value=SHA,
        ):
            result = development_inventory({"artifacts": []}, SHA)
            self.assertEqual(result["qualification"], "unreleased-development")
            for wrong in ("b" * 40, "main", ""):
                with self.subTest(commit=wrong), self.assertRaises(ValueError):
                    development_inventory({"artifacts": []}, wrong)

    def test_authored_sdist_bytes_are_checked_even_without_wheel_effect(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for tampered in (None, "setup.py", "MANIFEST.in", "README.md"):
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w") as archive:
                for name in sorted(AUTHORED_SDIST_FILES):
                    content = (root / name).read_bytes()
                    if name == tampered:
                        content += b"\n# sdist-only tampering\n"
                    member = tarfile.TarInfo(f"memoriesql-0.0.11/{name}")
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            payload.seek(0)
            with (
                self.subTest(tampered=tampered),
                tarfile.open(fileobj=payload) as archive,
            ):
                if tampered is None:
                    verify_authored_sdist_members(archive)
                else:
                    with self.assertRaisesRegex(ValueError, "sdist differs"):
                        verify_authored_sdist_members(archive)

    @staticmethod
    def make_artifacts(directory: Path) -> dict[str, Any]:
        rows = []
        for filename in (
            "memoriesql-0.0.11-py3-none-any.whl",
            "memoriesql-0.0.11.tar.gz",
        ):
            payload = b"valid"
            (directory / filename).write_bytes(payload)
            rows.append(
                {
                    "filename": filename,
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        return {"version": "0.0.11", "artifacts": rows}
