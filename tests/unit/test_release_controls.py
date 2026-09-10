from __future__ import annotations

import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.verify_release import (
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
    return select_ci_run(runs, sha=SHA, main_sha=SHA, tag="v0.0.3", version="0.0.3")


class ReleaseControlTests(unittest.TestCase):
    def test_accepts_successful_exact_head_ci(self) -> None:
        self.assertEqual(select([good_run()]), 123)

    def test_rejects_wrong_head_tag_and_version(self) -> None:
        arguments = {
            "sha": SHA,
            "main_sha": SHA,
            "tag": "v0.0.3",
            "version": "0.0.3",
        }
        for field, wrong in (
            ("sha", "malformed"),
            ("main_sha", "b" * 40),
            ("tag", "v0.0.1"),
            ("version", "0.0.1a1"),
            ("version", "0.0.2"),
            ("tag", "v0.0.2"),
            ("version", "0.0.4"),
            ("version", "0.0.3a1"),
            ("version", "0.0.3b1"),
            ("version", "0.0.3rc1"),
            ("tag", "v0.0.3a1"),
            ("tag", "v0.0.3b1"),
            ("tag", "v0.0.3rc1"),
            ("tag", "v0.0.1a1"),
            ("tag", "v0.0.4"),
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

    def test_rejects_historical_and_future_inventory_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inventory = self.make_artifacts(directory)
            for version in (
                None,
                "0.0.1a1",
                "0.0.2",
                "0.0.3a1",
                "0.0.3b1",
                "0.0.3rc1",
                "0.0.4",
            ):
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
            for name in ("../outside.whl", "memoriesql-0.0.3.tar.gz"):
                altered = deepcopy(inventory)
                altered["artifacts"][0]["filename"] = name
                with self.subTest(name=name), self.assertRaises(ValueError):
                    verify_artifacts(directory, altered)

    def test_candidate_integrity_does_not_authorize_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inventory = self.make_artifacts(directory)
            for row in inventory["artifacts"]:
                original = directory / row["filename"]
                row["filename"] = row["filename"].replace("0.0.3", "0.0.4")
                original.rename(directory / row["filename"])
            inventory["version"] = "0.0.4"
            self.assertEqual(
                len(verify_artifacts(directory, inventory, expected_version="0.0.4")), 2
            )
            with self.assertRaises(ValueError):
                verify_artifacts(directory, inventory)
            with self.assertRaises(ValueError):
                select_ci_run(
                    [good_run()], sha=SHA, main_sha=SHA, tag="v0.0.4", version="0.0.4"
                )

    @staticmethod
    def make_artifacts(directory: Path) -> dict[str, Any]:
        rows = []
        for filename in (
            "memoriesql-0.0.3-py3-none-any.whl",
            "memoriesql-0.0.3.tar.gz",
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
        return {"version": "0.0.3", "artifacts": rows}
