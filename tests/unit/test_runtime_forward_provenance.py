"""Forward changes must remain explicit without relabeling historical copies."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_runtime_export import verify


class ForwardProvenance(unittest.TestCase):
    def test_forward_changes_require_base_and_preserve_default_deny(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            (root / "docs/provenance").mkdir(parents=True)
            (root / "src/memoriesql").mkdir(parents=True)
            (root / "docs/provenance/export-provenance.json").write_text("{}")
            name = "src/memoriesql/fictional.py"
            content = b"import asyncio\n"
            (root / name).write_bytes(content)
            row = dict(
                path=name,
                disposition="public-forward",
                reason="Fictional correction",
                sha256=hashlib.sha256(content).hexdigest(),
                source_sha256="1" * 64,
                base_public_sha256="1" * 64,
                base_public_commit="2" * 40,
            )
            inventory = dict(default_policy="deny", runtime=[row])
            manifest = root / "contracts/runtime-inventory.json"

            def check() -> dict[str, int]:
                manifest.write_text(json.dumps(inventory))
                return verify(root)

            self.assertEqual(check(), {"runtime_files": 1})
            for field in (
                "base_public_commit",
                "base_public_sha256",
                "source_sha256",
                "reason",
            ):
                original = row.pop(field)
                with self.assertRaisesRegex(ValueError, "lacks public provenance"):
                    check()
                row[field] = original
            row["disposition"] = "anything"
            with self.assertRaisesRegex(ValueError, "unknown runtime disposition"):
                check()
            row["disposition"] = "copy"
            with self.assertRaisesRegex(ValueError, "unchanged export differs"):
                check()
            inventory["default_policy"] = "allow"
            with self.assertRaisesRegex(ValueError, "deny by default"):
                check()
