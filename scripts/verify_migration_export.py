"""Check the explicit public migration inventory without consulting other sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify() -> None:
    inventory = json.loads((ROOT / "contracts/migration-inventory.json").read_text())
    rows = inventory["migrations"]
    if inventory["default_policy"] != "deny" or len(rows) != 26:
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
    # Initializers are deliberately inert; no eager N2 dependency closure.
    for path in (
        "src/memoriesql/infrastructure/__init__.py",
        "src/memoriesql/infrastructure/postgres/__init__.py",
    ):
        if (
            ROOT / path
        ).read_text() != '"""Canonical PostgreSQL migration substrate."""\n':
            raise ValueError("unexpected initializer dependency")
    print(f"Migration export: {len(rows)} inventoried SQL resources, 2 audited modules.")


if __name__ == "__main__":
    verify()
