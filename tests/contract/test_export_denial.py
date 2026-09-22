from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts import generate_catalogs


class ExportDenialTests(unittest.TestCase):
    def test_catalog_wrapper_metadata_comes_from_registry(self) -> None:
        document = json.loads(generate_catalogs.REGISTRY_PATH.read_text())
        document["catalogs"][0]["filename"] = "synthetic-cli.json"
        document["catalogs"][0]["note"] = "Synthetic registry-owned note."
        with patch.object(generate_catalogs, "_read_registry", return_value=document):
            outputs = generate_catalogs.expected_outputs()
        self.assertNotIn(generate_catalogs.OUTPUT_ROOT / "cli.json", outputs)
        wrapper = json.loads(
            outputs[generate_catalogs.OUTPUT_ROOT / "synthetic-cli.json"]
        )
        self.assertEqual(wrapper["note"], "Synthetic registry-owned note.")
        self.assertEqual(wrapper["entries"], [])
        self.assertEqual(wrapper["status"], "not_implemented")

    def test_registry_denies_invalid_catalog_declarations(self) -> None:
        original = json.loads(generate_catalogs.REGISTRY_PATH.read_text())
        mutations = []
        missing = deepcopy(original)
        missing.pop("catalogs")
        mutations.append(missing)
        duplicate = deepcopy(original)
        duplicate["catalogs"].append(duplicate["catalogs"][0])
        mutations.append(duplicate)
        unsafe = deepcopy(original)
        unsafe["catalogs"][0]["filename"] = "../unregistered.json"
        mutations.append(unsafe)
        disposition = deepcopy(original)
        disposition["catalogs"][0]["disposition"] = "include"
        mutations.append(disposition)
        wrong_count = deepcopy(original)
        wrong_count["catalogs"][1]["expected_record_count"] = 2
        mutations.append(wrong_count)
        undeclared = deepcopy(original)
        undeclared["catalogs"].pop(1)
        mutations.append(undeclared)
        for document in mutations:
            with (
                self.subTest(
                    document=document["catalogs"]
                    if "catalogs" in document
                    else "missing"
                ),
                patch.object(
                    generate_catalogs, "_read_registry", return_value=document
                ),
                self.assertRaises(ValueError),
            ):
                generate_catalogs.load_registry()

    def test_unregistered_empty_catalog_is_denied(self) -> None:
        document = json.loads(generate_catalogs.REGISTRY_PATH.read_text())
        document["catalogs"].pop(0)
        with (
            patch.object(generate_catalogs, "_read_registry", return_value=document),
            self.assertRaisesRegex(ValueError, "unregistered generated catalog"),
        ):
            generate_catalogs.generate(check=True)

    def test_record_directory_denies_unregistered_files_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            shutil.copytree(generate_catalogs.ROOT / "contracts", root / "contracts")
            record_root = root / "contracts/records"
            stack.enter_context(patch.object(generate_catalogs, "ROOT", root))
            stack.enter_context(
                patch.object(generate_catalogs, "RECORD_ROOT", record_root)
            )
            stack.enter_context(
                patch.object(
                    generate_catalogs,
                    "REGISTRY_PATH",
                    root / "contracts/public-registry.json",
                )
            )
            unexpected = record_root / "unregistered.txt"
            unexpected.write_text("synthetic unapproved content")
            with self.assertRaisesRegex(ValueError, "default-deny"):
                generate_catalogs.load_registry()
            unexpected.unlink()
            record = next(record_root.glob("*.json"))
            outside = root / "outside.json"
            record.rename(outside)
            record.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "redirected"):
                generate_catalogs.load_registry()

    def test_generated_catalog_directory_denies_unregistered_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unregistered.json").write_text("{}")
            with (
                patch.object(generate_catalogs, "OUTPUT_ROOT", root),
                patch.object(generate_catalogs, "expected_outputs", return_value={}),
                self.assertRaisesRegex(ValueError, "unregistered generated"),
            ):
                generate_catalogs.generate(check=True)



if __name__ == "__main__":
    unittest.main()
