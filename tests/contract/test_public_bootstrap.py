from __future__ import annotations

import hashlib
import json
import re
import runpy
import tomllib
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

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
        self.assertEqual(project["version"], "0.0.4")
        self.assertEqual(project["requires-python"], ">=3.13,<3.15")
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertIn("Development Status :: 2 - Pre-Alpha", project["classifiers"])
        self.assertEqual(project["license-files"], ["LICENSE", "NOTICE"])
        self.assertEqual(project["authors"], [{"name": "John Inniger"}])
        self.assertEqual(
            project["dependencies"],
            ["psycopg[binary]==3.3.3", "pydantic==2.13.3", "pydantic-ai-slim==2.27.0"],
        )
        self.assertEqual(
            project["urls"]["Repository"], "https://github.com/JohnnyFiv3r/memoriesql"
        )

    def test_source_version_fallback_matches_numeric_release(self) -> None:
        with patch("importlib.metadata.version", side_effect=PackageNotFoundError):
            namespace = runpy.run_path(str(ROOT / "src/memoriesql/__init__.py"))
        self.assertEqual(namespace["__version__"], "0.0.4")

    def test_registry_is_explicit_complete_and_default_deny(self) -> None:
        records = load_registry()
        self.assertEqual(len(records), 53)
        self.assertEqual(len({record["id"] for record in records}), 53)
        catalogs = load_catalog_definitions()
        self.assertEqual(tuple(item.kind for item in catalogs), CATALOG_KINDS)
        counts = {item.kind: 0 for item in catalogs}
        for record in records:
            counts[str(record["catalog"])] += 1
            self.assertEqual(record["classification"], "proposed_open_core")
            self.assertEqual(record["package_disposition"], "include")
        self.assertEqual(counts["json_schema"], 47)
        self.assertEqual(counts["python"], 5)
        self.assertEqual(counts["connector"], 1)
        self.assertEqual(sum(counts.values()), 53)

    def test_generated_catalogs_have_no_drift(self) -> None:
        for path, expected in expected_outputs().items():
            self.assertEqual(path.read_bytes(), expected)

    def test_boundary_scan_is_clean(self) -> None:
        result = verify()
        self.assertEqual(result["record_count"], 53)
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
        self.assertTrue(
            (ROOT / "assets/trademarks/memoriesql-readme-banner.png").is_file()
        )

    def test_publisher_has_narrow_owner_approved_controls(self) -> None:
        workflow = (ROOT / ".github/workflows/publish-pypi.yml").read_text()
        self.assertIn('tags: ["v0.0.4"]', workflow)
        self.assertEqual(workflow.count("refs/tags/v0.0.4"), 2)
        self.assertNotIn("v0.0.1a1", workflow)
        self.assertNotIn("v0.0.2", workflow)
        self.assertNotIn("v0.0.3", workflow)
        self.assertNotIn("v0.0.4a1", workflow)
        self.assertNotIn("v*", workflow)
        self.assertIn("github.repository == 'JohnnyFiv3r/memoriesql'", workflow)
        self.assertIn("github.repository_id == '1357510758'", workflow)
        self.assertIn("github.event.created == true", workflow)
        self.assertIn("name: pypi", workflow)
        self.assertIn("needs: verify", workflow)
        self.assertEqual(workflow.count("id-token: write"), 1)
        self.assertIn("scripts/verify_release.py ci", workflow)
        self.assertIn("scripts/verify_release.py artifacts", workflow)
        self.assertNotIn("workflow_dispatch", workflow)
        self.assertNotIn("pull_request", workflow)
        self.assertNotIn("python -m build", workflow)
        self.assertNotIn("skip-existing", workflow)
        publish_job = workflow.split("  publish:")[1]
        self.assertNotIn("run:", publish_job)
        self.assertNotIn("actions/checkout", publish_job)
        self.assertIn("run-id: ${{ steps.ci.outputs.run_id }}", workflow)
        self.assertIn("name: memoriesql-python-${{ github.sha }}", workflow)
        self.assertNotIn("id-token:", workflow.split("  publish:")[0])
        for action in re.findall(r"uses: (\S+)", workflow):
            self.assertRegex(action, r"@[0-9a-f]{40}$")
        template = (ROOT / "docs/templates/publish-pypi.yml.template").read_text()
        self.assertIn("NON-LIVE TEMPLATE", template)
        self.assertIn("FINAL_PUBLIC_REPOSITORY", template)
        self.assertIn("FINAL_PYPI_ENVIRONMENT", template)
        self.assertIn("id-token: write", template)

    def test_development_artifacts_cannot_replace_published_release_hashes(
        self,
    ) -> None:
        workflow = (ROOT / ".github/workflows/python-package.yml").read_text()
        publishing = (ROOT / ".github/workflows/publish-pypi.yml").read_text()
        self.assertIn("inspect_python_distribution.py build/dist-a", workflow)
        self.assertNotIn("verify_release.py artifacts build/dist-a", workflow)
        self.assertIn("verify_release.py artifacts", publishing)
        self.assertIn('tags: ["v0.0.4"]', publishing)

    def test_older_python_fallback_keeps_historical_prerelease_eligible(self) -> None:
        workflow = (ROOT / ".github/workflows/python-package.yml").read_text()
        older_python = workflow.split("  older-python:")[1]
        self.assertIn(
            "pip install --pre --no-index --find-links candidates memoriesql",
            older_python,
        )
        self.assertIn("memoriesql==0.0.1a1", older_python)
        self.assertIn(
            "pip install --no-deps candidates/memoriesql-0.0.4-py3-none-any.whl",
            older_python,
        )

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
        new_record = "contracts/records/memoriesql-evidence-package-v1.json"
        self.assertEqual(
            record_paths - paths,
            {
                new_record,
                "contracts/records/memoriesql-logical-unit-materialization-v1.json",
                "contracts/records/memoriesql-complete-input-execution-v1.json",
                "contracts/records/memoriesql-evidence-package-reader-v2.json",
            },
        )
        owned = next(
            row for row in manifest["public_only"] if row["public_path"] == new_record
        )
        self.assertNotIn("reviewed_spike_source_sha256", owned)
        self.assertEqual(
            hashlib.sha256((ROOT / new_record).read_bytes()).hexdigest(),
            owned["public_sha256"],
        )
        for entry in manifest["exports"]:
            data = (ROOT / entry["public_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["public_sha256"])
            self.assertRegex(entry["reviewed_spike_source_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(entry["classification"], "proposed_open_core")


if __name__ == "__main__":
    unittest.main()
