"""The runtime inventory is an explicit, default-deny closure of shipped modules."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_runtime_export import verify


class RuntimeExport(unittest.TestCase):
    def test_closure_denies_unlisted_imports_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            package = root / "src/memoriesql"
            package.mkdir(parents=True)
            name = "src/memoriesql/fictional.py"
            (root / name).write_bytes(b"import asyncio\nimport memoriesql.contracts\n")
            (package / "contracts").mkdir()
            rows: list[dict[str, str]] = [{"path": name}]
            inventory: dict[str, object] = {"default_policy": "deny", "runtime": rows}
            manifest = root / "contracts/runtime-inventory.json"

            def check() -> dict[str, int]:
                manifest.write_text(json.dumps(inventory))
                return verify(root)

            self.assertEqual(check(), {"runtime_files": 1})
            for content, message in (
                (b"import memoriesql.unlisted\n", "unclosed runtime import"),
                (b"import requests\n", "unapproved runtime dependency"),
                (b"from . import sibling\n", "relative runtime import"),
            ):
                (root / name).write_bytes(content)
                with self.subTest(content=content):
                    with self.assertRaisesRegex(ValueError, message):
                        check()
            (root / name).write_bytes(b"import asyncio\n")
            rows.append({"path": name})
            with self.assertRaisesRegex(ValueError, "duplicate runtime path"):
                check()
            inventory["runtime"] = [{"path": "src/other/fictional.py"}]
            with self.assertRaisesRegex(ValueError, "unsafe runtime path"):
                check()
            inventory["runtime"] = [{"path": "src/memoriesql/missing.py"}]
            with self.assertRaisesRegex(ValueError, "unsafe runtime path"):
                check()
            inventory["runtime"] = [{"path": name}]
            inventory["default_policy"] = "allow"
            with self.assertRaisesRegex(ValueError, "deny by default"):
                check()
