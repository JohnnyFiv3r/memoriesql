from __future__ import annotations

import hashlib
import json
import tomllib
import unittest
from pathlib import Path

from memoriesql.contracts import CATALOG_KINDS
from scripts.generate_catalogs import (
    expected_outputs,
    load_catalog_definitions,
    load_registry,
)
from scripts.verify_public_boundary import verify

ROOT = Path(__file__).resolve().parents[2]


class PublicBootstrapTests(unittest.TestCase):
    def test_metadata_is_exact_pre_alpha_public_preview(self) -> None:
        document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = document["project"]
        self.assertEqual(project["name"], "memoriesql")
        self.assertEqual(project["version"], "0.0.1a1")
        self.assertEqual(project["requires-python"], ">=3.11,<3.15")
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertEqual(project["license-files"], ["LICENSE", "NOTICE"])
        self.assertEqual(project["authors"], [{"name": "John Inniger"}])
        self.assertNotIn("dependencies", project)
        self.assertEqual(
            project["urls"]["Repository"], "https://github.com/JohnnyFiv3r/memoriesql"
        )

    def test_registry_is_explicit_complete_and_default_deny(self) -> None:
        records = load_registry()
        self.assertEqual(len(records), 49)
        self.assertEqual(len({record["id"] for record in records}), 49)
        catalogs = load_catalog_definitions()
        self.assertEqual(tuple(item.kind for item in catalogs), CATALOG_KINDS)
        counts = {item.kind: 0 for item in catalogs}
        for record in records:
            counts[str(record["catalog"])] += 1
            self.assertEqual(record["classification"], "proposed_open_core")
            self.assertEqual(record["package_disposition"], "include")
        self.assertEqual(counts["json_schema"], 43)
        self.assertEqual(counts["python"], 5)
        self.assertEqual(counts["connector"], 1)
        self.assertEqual(sum(counts.values()), 49)

    def test_generated_catalogs_have_no_drift(self) -> None:
        for path, expected in expected_outputs().items():
            self.assertEqual(path.read_bytes(), expected)

    def test_boundary_scan_is_clean(self) -> None:
        result = verify()
        self.assertEqual(result["record_count"], 49)
        self.assertEqual(result["default_policy"], "deny")
        self.assertEqual(result["private_boundary_leaks"], [])

    def test_public_docs_state_scope_and_compatibility(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        package_readme = (ROOT / "PYPI_README.md").read_text(encoding="utf-8")
        combined = readme + "\n" + package_readme
        for expected in (
            "49 provider-neutral",
            "12 experimental",
            "no general compatibility guarantee",
            "new contract version",
            "new distribution version",
            "not the memoriesQL desktop product",
        ):
            self.assertIn(expected, combined)
        self.assertIn("assets/trademarks/memoriesql-readme-banner.png", readme)
        self.assertTrue((ROOT / "assets/trademarks/memoriesql-readme-banner.png").is_file())

    def test_publisher_is_only_an_inert_template(self) -> None:
        workflow_files = set((ROOT / ".github/workflows").glob("*"))
        self.assertFalse(any("publish" in path.name for path in workflow_files))
        template = (ROOT / "docs/templates/publish-pypi.yml.template").read_text()
        self.assertIn("NON-LIVE TEMPLATE", template)
        self.assertIn("FINAL_PUBLIC_REPOSITORY", template)
        self.assertIn("FINAL_PYPI_ENVIRONMENT", template)
        self.assertIn("id-token: write", template)

    def test_export_provenance_hashes_are_current(self) -> None:
        manifest = json.loads(
            (ROOT / "docs/provenance/export-provenance.json").read_text()
        )
        self.assertEqual(
            manifest["reviewed_spike_commit"],
            "ea29499d7107802dfa2566332ccb2b6ed0119bd1",
        )
        paths = {entry["public_path"] for entry in manifest["exports"]}
        record_paths = {
            str(record["path"])
            for record in json.loads(
                (ROOT / "contracts/public-registry.json").read_text()
            )["records"]
        }
        self.assertTrue(record_paths <= paths)
        for entry in manifest["exports"]:
            data = (ROOT / entry["public_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["public_sha256"])
            self.assertRegex(entry["reviewed_spike_source_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(entry["classification"], "proposed_open_core")


if __name__ == "__main__":
    unittest.main()
