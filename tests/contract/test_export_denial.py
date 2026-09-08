from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts import generate_catalogs, verify_export_provenance


class ExportDenialTests(unittest.TestCase):
    def test_record_directory_denies_unregistered_files_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            shutil.copytree(generate_catalogs.ROOT / "contracts", root / "contracts")
            record_root = root / "contracts/records"
            stack.enter_context(patch.object(generate_catalogs, "ROOT", root))
            stack.enter_context(patch.object(generate_catalogs, "RECORD_ROOT", record_root))
            stack.enter_context(
                patch.object(generate_catalogs, "REGISTRY_PATH", root / "contracts/public-registry.json")
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
            patch.object(verify_export_provenance, "public_files", return_value=tracked | {"unlisted.txt"}),
            self.assertRaisesRegex(ValueError, "public file allowlist mismatch"),
        ):
            verify_export_provenance.verify()

    def test_provenance_detects_payload_change_even_with_updated_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "record.json"
            original = b'{"id":"synthetic.original"}'
            record.write_bytes(b'{"id":"synthetic.changed"}')
            manifest = root / verify_export_provenance.MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "reviewed_spike_commit": verify_export_provenance.SPIKE_COMMIT,
                "exports": [{
                    "public_path": "record.json",
                    "public_sha256": hashlib.sha256(record.read_bytes()).hexdigest(),
                    "reviewed_spike_commit": verify_export_provenance.SPIKE_COMMIT,
                    "reviewed_spike_source_sha256": "0" * 64,
                    "reviewed_spike_record_sha256": hashlib.sha256(original).hexdigest(),
                    "classification": "proposed_open_core",
                    "reason": "Synthetic test record",
                }],
                "public_only": [],
            }))
            with (
                patch.object(verify_export_provenance, "public_files", return_value={"record.json", verify_export_provenance.MANIFEST}),
                self.assertRaisesRegex(ValueError, "approved record payload drift"),
            ):
                verify_export_provenance.verify(root)


if __name__ == "__main__":
    unittest.main()
