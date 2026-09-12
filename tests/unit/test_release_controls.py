from __future__ import annotations

import hashlib
import json
import tempfile
import tomllib
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

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
    return select_ci_run(runs, sha=SHA, main_sha=SHA, tag="v0.0.5", version="0.0.5")


class ReleaseControlTests(unittest.TestCase):
    def test_accepts_successful_exact_head_ci(self) -> None:
        self.assertEqual(select([good_run()]), 123)

    def test_rejects_wrong_head_tag_and_version(self) -> None:
        arguments = {
            "sha": SHA,
            "main_sha": SHA,
            "tag": "v0.0.5",
            "version": "0.0.5",
        }
        for field, wrong in (
            ("sha", "malformed"),
            ("main_sha", "b" * 40),
            ("tag", "v0.0.1"),
            ("version", "0.0.1a1"),
            ("version", "0.0.2"),
            ("version", "0.0.3"),
            ("version", "0.0.4"),
            ("tag", "v0.0.4"),
            ("tag", "v0.0.3"),
            ("tag", "v0.0.2"),
            ("version", "0.0.6"),
            ("version", "0.0.5a1"),
            ("version", "0.0.5b1"),
            ("version", "0.0.5rc1"),
            ("tag", "v0.0.5a1"),
            ("tag", "v0.0.5b1"),
            ("tag", "v0.0.5rc1"),
            ("tag", "v0.0.1a1"),
            ("tag", "v0.0.6"),
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
                "0.0.3",
                "0.0.4",
                "0.0.5a1",
                "0.0.5b1",
                "0.0.5rc1",
                "0.0.6",
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
            for name in ("../outside.whl", "memoriesql-0.0.5.tar.gz"):
                altered = deepcopy(inventory)
                altered["artifacts"][0]["filename"] = name
                with self.subTest(name=name), self.assertRaises(ValueError):
                    verify_artifacts(directory, altered)

    def test_only_current_version_is_eligible_even_with_matching_tag(self) -> None:
        for version in ("0.0.3", "0.0.4", "0.0.5rc1", "0.0.6"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                select_ci_run(
                    [good_run()],
                    sha=SHA,
                    main_sha=SHA,
                    tag=f"v{version}",
                    version=version,
                )

    def test_candidate_identity_and_historical_inventories(self) -> None:
        root = Path(__file__).resolve().parents[2]
        verification = root / "docs/verification"
        self.assertEqual(
            RELEASE_INVENTORY,
            verification / "runtime-0.0.5-package-artifact-inventory.json",
        )
        candidate = json.loads(RELEASE_INVENTORY.read_bytes())
        self.assertEqual(candidate["version"], APPROVED_VERSION)
        self.assertEqual(candidate["records"]["count"], 53)
        self.assertEqual(
            {r["filename"] for r in candidate["artifacts"]},
            {"memoriesql-0.0.5-py3-none-any.whl", "memoriesql-0.0.5.tar.gz"},
        )
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["version"], APPROVED_VERSION)
        # These entire published/historical inventory files are immutable.
        historical = {
            "immutable-observations-candidate-artifacts.json": "b56d1cc43a01d1b4b48e9b3bf2411635546626367a8bdff0b99ddc0cfde49f16",
            "package-artifact-inventory.json": "9b0adadbea208fe539d09eeb98f87110805edbaf643c074ceb9517d317709477",
            "runtime-0.0.2-package-artifact-inventory.json": "1a329f4e545971c7485f667b9aabb426b250e6c8550a9c25dd9302a74a2afd14",
            "runtime-0.0.3-package-artifact-inventory.json": "e4f359f3c3bc159b16687c44cb8111f2815e7e8b31cd92e2663cea0c7b94c401",
            "runtime-package-artifact-inventory.json": "980a0b77f0943f190419761d0d2965d5985ce7ddb1af5bb85ac57af77784af5f",
        }
        for name, expected in historical.items():
            with self.subTest(inventory=name):
                self.assertEqual(
                    hashlib.sha256((verification / name).read_bytes()).hexdigest(),
                    expected,
                )

    @staticmethod
    def make_artifacts(directory: Path) -> dict[str, Any]:
        rows = []
        for filename in (
            "memoriesql-0.0.5-py3-none-any.whl",
            "memoriesql-0.0.5.tar.gz",
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
        return {"version": "0.0.5", "artifacts": rows}
