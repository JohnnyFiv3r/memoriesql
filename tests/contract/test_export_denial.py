from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts import generate_catalogs, verify_export_provenance


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

    def test_provenance_enforces_license_dispositions(self) -> None:
        for section in ("exports", "public_only"):
            for filename, correct in (
                ("source.py", "Apache-2.0"),
                ("assets/trademarks/mark.png", "trademark_asset_not_apache"),
            ):
                for license_value in (
                    None,
                    "unknown",
                    "Apache-2.0",
                    "trademark_asset_not_apache",
                ):
                    with self.subTest(
                        section=section, filename=filename, license=license_value
                    ):
                        with tempfile.TemporaryDirectory() as directory:
                            root = Path(directory)
                            target = root / filename
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(b"synthetic content")
                            row = {
                                "public_path": filename,
                                "public_sha256": hashlib.sha256(
                                    target.read_bytes()
                                ).hexdigest(),
                                "classification": "proposed_open_core",
                                "reason": "Synthetic license fixture",
                                "reviewed_spike_commit": verify_export_provenance.SPIKE_COMMIT,
                                "reviewed_spike_source_sha256": "0" * 64,
                            }
                            if license_value is not None:
                                row["license"] = license_value
                            manifest = root / verify_export_provenance.MANIFEST
                            manifest.parent.mkdir(parents=True)
                            manifest.write_text(
                                json.dumps(
                                    {
                                        "reviewed_spike_commit": verify_export_provenance.SPIKE_COMMIT,
                                        "exports": [row]
                                        if section == "exports"
                                        else [],
                                        "public_only": [row]
                                        if section == "public_only"
                                        else [],
                                    }
                                )
                            )
                            with patch.object(
                                verify_export_provenance,
                                "public_files",
                                return_value={
                                    filename,
                                    verify_export_provenance.MANIFEST,
                                },
                            ):
                                if license_value == correct:
                                    verify_export_provenance.verify(root)
                                else:
                                    with self.assertRaisesRegex(
                                        ValueError, "invalid license disposition"
                                    ):
                                        verify_export_provenance.verify(root)

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

    def test_provenance_requires_complete_file_inventory(self) -> None:
        tracked = verify_export_provenance.public_files(verify_export_provenance.ROOT)
        with (
            patch.object(
                verify_export_provenance,
                "public_files",
                return_value=tracked | {"unlisted.txt"},
            ),
            self.assertRaisesRegex(ValueError, "public file allowlist mismatch"),
        ):
            verify_export_provenance.verify()

    def test_provenance_detects_payload_change_even_with_updated_file_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "record.json"
            original = b'{"id":"synthetic.original"}'
            record.write_bytes(b'{"id":"synthetic.changed"}')
            manifest = root / verify_export_provenance.MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "reviewed_spike_commit": verify_export_provenance.SPIKE_COMMIT,
                        "exports": [
                            {
                                "public_path": "record.json",
                                "public_sha256": hashlib.sha256(
                                    record.read_bytes()
                                ).hexdigest(),
                                "reviewed_spike_commit": verify_export_provenance.SPIKE_COMMIT,
                                "reviewed_spike_source_sha256": "0" * 64,
                                "reviewed_spike_record_sha256": hashlib.sha256(
                                    original
                                ).hexdigest(),
                                "classification": "proposed_open_core",
                                "license": "Apache-2.0",
                                "reason": "Synthetic test record",
                            }
                        ],
                        "public_only": [],
                    }
                )
            )
            with (
                patch.object(
                    verify_export_provenance,
                    "public_files",
                    return_value={"record.json", verify_export_provenance.MANIFEST},
                ),
                self.assertRaisesRegex(ValueError, "approved record payload drift"),
            ):
                verify_export_provenance.verify(root)


if __name__ == "__main__":
    unittest.main()
