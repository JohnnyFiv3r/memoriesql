"""Generate package catalogs from the explicit public export registry."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "contracts/public-registry.json"
RECORD_ROOT = ROOT / "contracts/records"
OUTPUT_ROOT = ROOT / "src/memoriesql/contracts/_catalogs"
CATALOG_FILES = {
    "cli": "cli.json",
    "connector": "connector.json",
    "http_openapi": "http-openapi.json",
    "json_schema": "json-schema.json",
    "mcp": "mcp.json",
    "module": "module.json",
    "python": "python.json",
    "sql_recall_operations": "sql-recall-operations.json",
}
EXPECTED_COUNTS = {
    "cli": 0,
    "connector": 1,
    "http_openapi": 0,
    "json_schema": 43,
    "mcp": 0,
    "module": 0,
    "python": 5,
    "sql_recall_operations": 0,
}
NOTES = {
    "cli": "No product command records are included in this preview.",
    "connector": "Only the provider-neutral synthetic reference connector is included.",
    "http_openapi": "No public HTTP records are included in this preview.",
    "json_schema": "Provider-neutral capture, host, source-range, transcript-fold, and harness-lifecycle schemas.",
    "mcp": "No MCP records are included in this preview.",
    "module": "No product module manifests are included in this preview.",
    "python": "Provider-neutral protocols, SDK surfaces, and synthetic reference records.",
    "sql_recall_operations": "No SQL recall records are included in this preview.",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def load_registry() -> tuple[dict[str, object], ...]:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("public registry must be an object")
    if document.get("default_policy") != "deny":
        raise ValueError("public registry must remain default-deny")
    if document.get("reviewed_spike_commit") != "ea29499d7107802dfa2566332ccb2b6ed0119bd1":
        raise ValueError("unexpected reviewed spike commit")
    raw_records = document.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("public registry records must be a list")

    records = tuple(cast(dict[str, object], record) for record in raw_records)
    if len(records) != 49:
        raise ValueError(f"expected 49 explicitly approved records, found {len(records)}")
    identifiers = [record.get("id") for record in records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("public registry contains duplicate identifiers")
    if identifiers != sorted(identifiers, key=str):
        raise ValueError("public registry records are not sorted by identifier")

    registered_paths: set[Path] = set()
    for record in records:
        if record.get("classification") != "proposed_open_core":
            raise ValueError(f"unapproved classification for {record.get('id')}")
        if record.get("package_disposition") != "include":
            raise ValueError(f"unapproved package disposition for {record.get('id')}")
        catalog = record.get("catalog")
        if catalog not in CATALOG_FILES:
            raise ValueError(f"unknown catalog for {record.get('id')}: {catalog}")
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path.startswith("contracts/records/"):
            raise ValueError(f"record path escapes the public registry: {raw_path}")
        path = ROOT / raw_path
        if path.parent != RECORD_ROOT or path.suffix != ".json":
            raise ValueError(f"record path is not a direct JSON child: {raw_path}")
        registered_paths.add(path)
        data = path.read_bytes()
        if _sha256(data) != record.get("public_sha256"):
            raise ValueError(f"public record hash drift: {raw_path}")
        payload = json.loads(data)
        if payload.get("id") != record.get("id"):
            raise ValueError(f"record identifier mismatch: {raw_path}")
        if str(payload.get("version")) != record.get("version"):
            raise ValueError(f"record version mismatch: {raw_path}")

    discovered_paths = set(RECORD_ROOT.glob("*.json"))
    if discovered_paths != registered_paths:
        missing = sorted(path.name for path in registered_paths - discovered_paths)
        denied = sorted(path.name for path in discovered_paths - registered_paths)
        raise ValueError(f"default-deny record mismatch; missing={missing}; denied={denied}")
    counts = Counter(cast(str, record["catalog"]) for record in records)
    if {kind: counts[kind] for kind in CATALOG_FILES} != EXPECTED_COUNTS:
        raise ValueError(f"catalog count drift: {dict(counts)}")
    return records


def expected_outputs() -> dict[Path, bytes]:
    records = load_registry()
    outputs: dict[Path, bytes] = {}
    for kind, filename in CATALOG_FILES.items():
        entries = [
            json.loads((ROOT / cast(str, record["path"])).read_text(encoding="utf-8"))
            for record in records
            if record["catalog"] == kind
        ]
        catalog = {
            "_generated": True,
            "canonical_sources": ["contracts/public-registry.json"],
            "entries": entries,
            "interface_version": "1",
            "kind": kind,
            "note": NOTES[kind],
            "status": "available" if entries else "not_implemented",
        }
        outputs[OUTPUT_ROOT / filename] = _json_bytes(catalog)
    return outputs


def generate(*, check: bool) -> None:
    failures: list[str] = []
    for path, expected in expected_outputs().items():
        if check:
            if not path.exists() or path.read_bytes() != expected:
                failures.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if failures:
        raise SystemExit("generated catalog drift: " + ", ".join(failures))


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(arguments)
    generate(check=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
