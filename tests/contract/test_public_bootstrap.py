from __future__ import annotations

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
        self.assertEqual(project["version"], "0.0.10")
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
        self.assertEqual(namespace["__version__"], "0.0.10")

    def test_registry_is_explicit_complete_and_default_deny(self) -> None:
        records = load_registry()
        self.assertEqual(len(records), 62)
        self.assertEqual(len({record["id"] for record in records}), 62)
        catalogs = load_catalog_definitions()
        self.assertEqual(tuple(item.kind for item in catalogs), CATALOG_KINDS)
        counts = {item.kind: 0 for item in catalogs}
        for record in records:
            counts[str(record["catalog"])] += 1
            self.assertEqual(record["classification"], "proposed_open_core")
            self.assertEqual(record["package_disposition"], "include")
        self.assertEqual(counts["json_schema"], 56)
        self.assertEqual(counts["python"], 5)
        self.assertEqual(counts["connector"], 1)
        self.assertEqual(sum(counts.values()), 62)

    def test_generated_catalogs_have_no_drift(self) -> None:
        for path, expected in expected_outputs().items():
            self.assertEqual(path.read_bytes(), expected)

    def test_boundary_scan_is_clean(self) -> None:
        result = verify()
        self.assertEqual(result["record_count"], 62)
        self.assertEqual(result["default_policy"], "deny")
        self.assertEqual(result["private_boundary_leaks"], [])

    def test_public_docs_state_scope_and_compatibility(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        package_readme = (ROOT / "PYPI_README.md").read_text(encoding="utf-8")
        combined = " ".join((readme + "\n" + package_readme).split())
        for expected in (
            "local-first memory foundation",
            "PostgreSQL",
            "structured observations",
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
        self.assertIn('tags: ["v0.0.10"]', workflow)
        self.assertEqual(workflow.count("refs/tags/v0.0.10"), 2)
        self.assertNotIn("v0.0.2", workflow)
        self.assertNotIn("v0.0.3", workflow)
        self.assertNotIn("v0.0.4", workflow)
        self.assertNotIn("v0.0.5", workflow)
        self.assertNotIn("v0.0.6", workflow)
        self.assertNotIn("v0.0.7", workflow)
        self.assertNotIn("v0.0.9", workflow)
        self.assertNotIn("v0.0.10a1", workflow)
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

    def test_candidate_ci_matches_committed_inventory(
        self,
    ) -> None:
        workflow = (ROOT / ".github/workflows/python-package.yml").read_text()
        publishing = (ROOT / ".github/workflows/publish-pypi.yml").read_text()
        self.assertIn("inspect_python_distribution.py build/dist-a", workflow)
        self.assertIn("--check-inventory", workflow)
        self.assertIn("--source-commit", workflow)
        self.assertNotIn("verify_release.py artifacts build/dist-a", workflow)
        self.assertIn("verify_release.py artifacts", publishing)
        self.assertIn('tags: ["v0.0.10"]', publishing)

    def test_ci_verifies_only_current_release_and_supported_interpreters(self) -> None:
        workflow = (ROOT / ".github/workflows/python-package.yml").read_text()
        # No historical lanes: the unsupported-interpreter catalog fallback and
        # the frozen-source reproduction were retired on 2026-09-22.
        self.assertNotIn("older-python", workflow)
        compatibility = workflow.split("  compatibility:")[1]
        self.assertIn('python-version: ["3.13", "3.14"]', compatibility)
        # The installed acceptance runs as parallel shards launched by the
        # workflow shell; the script itself denies subprocesses.
        self.assertIn('run_installed_acceptance.py --shard "$shard/$shards"', compatibility)
        self.assertIn('test "$failed" -eq 0', compatibility)



if __name__ == "__main__":
    unittest.main()
