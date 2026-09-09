"""Check the explicit public migration export without consulting other sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify() -> None:
    inventory = json.loads((ROOT / "contracts/migration-inventory.json").read_text())
    rows = inventory["migrations"]
    if inventory["default_policy"] != "deny" or len(rows) != 14:
        raise ValueError("invalid migration inventory")
    for version, row in enumerate(rows, 1):
        name = row["filename"]
        if Path(name).name != name or not name.startswith(f"{version:04d}_"):
            raise ValueError("invalid migration filename")
        path = ROOT / "migrations" / name
        if (
            path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
        ):
            raise ValueError("historical migration drift")
    provenance = json.loads(
        (ROOT / "docs/provenance/migration-extraction.json").read_text()
    )
    expected = {"migrations/" + row["filename"] for row in rows} | {
        "src/memoriesql/infrastructure/postgres/migration_runner.py",
        "src/memoriesql/infrastructure/postgres/schema_inspection.py",
    }
    exports = provenance["exports"]
    if {row["public_path"] for row in exports} != expected or len(exports) != len(
        expected
    ):
        raise ValueError("migration extraction inventory mismatch")
    for row in exports:
        digest = hashlib.sha256((ROOT / row["public_path"]).read_bytes()).hexdigest()
        if digest != row["public_sha256"]:
            raise ValueError("migration export drift")
        if row["disposition"] == "copy" and digest != row["source_sha256"]:
            raise ValueError("historical source drift")
    for row in provenance["test_material"]:
        digest = hashlib.sha256((ROOT / row["public_path"]).read_bytes()).hexdigest()
        if digest != row["source_sha256"] or digest != row["public_sha256"]:
            raise ValueError("historical schema fixture drift")
    # Initializers are deliberately inert; no eager N2 dependency closure.
    for path in (
        "src/memoriesql/infrastructure/__init__.py",
        "src/memoriesql/infrastructure/postgres/__init__.py",
    ):
        if (
            ROOT / path
        ).read_text() != '"""Canonical PostgreSQL migration substrate."""\n':
            raise ValueError("unexpected initializer dependency")
    print("Migration export: 14 unchanged SQL, 2 audited modules, inert initializers.")


if __name__ == "__main__":
    verify()
