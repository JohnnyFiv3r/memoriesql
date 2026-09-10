"""Generate package catalogs from the explicit public export registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "contracts/public-registry.json"
RECORD_ROOT = ROOT / "contracts/records"
OUTPUT_ROOT = ROOT / "src/memoriesql/contracts/_catalogs"


@dataclass(frozen=True)
class CatalogDefinition:
    kind: str
    filename: str
    note: str
    status: str
    expected_record_count: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode()


def _read_registry() -> dict[str, object]:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("public registry must be an object")
    if document.get("default_policy") != "deny":
        raise ValueError("public registry must remain default-deny")
    if (
        document.get("reviewed_spike_commit")
        != "ea29499d7107802dfa2566332ccb2b6ed0119bd1"
    ):
        raise ValueError("unexpected reviewed spike commit")
    return cast(dict[str, object], document)


def _catalog_definitions(document: dict[str, object]) -> tuple[CatalogDefinition, ...]:
    raw_catalogs = document.get("catalogs")
    if not isinstance(raw_catalogs, list) or not raw_catalogs:
        raise ValueError("public registry must declare its catalogs")
    catalogs: list[CatalogDefinition] = []
    kinds: set[str] = set()
    filenames: set[str] = set()
    for raw in raw_catalogs:
        if not isinstance(raw, dict):
            raise ValueError("catalog declaration must be an object")
        kind, filename, note = raw.get("kind"), raw.get("filename"), raw.get("note")
        disposition, count = raw.get("disposition"), raw.get("expected_record_count")
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", kind):
            raise ValueError("invalid catalog kind")
        if not isinstance(filename, str) or not re.fullmatch(
            r"[a-z][a-z0-9-]*\.json", filename
        ):
            raise ValueError("unsafe catalog filename")
        if kind in kinds or filename in filenames:
            raise ValueError("duplicate catalog kind or filename")
        if not isinstance(note, str) or not note.strip():
            raise ValueError("catalog declaration requires a note")
        if type(count) is not int or count < 0:
            raise ValueError("catalog declaration requires a nonnegative record count")
        if disposition not in ("include", "empty") or (
            (disposition == "empty") != (count == 0)
        ):
            raise ValueError("catalog disposition and record count disagree")
        kinds.add(kind)
        filenames.add(filename)
        catalogs.append(
            CatalogDefinition(
                kind,
                filename,
                note,
                "available" if disposition == "include" else "not_implemented",
                count,
            )
        )
    if [catalog.kind for catalog in catalogs] != sorted(kinds):
        raise ValueError("catalog declarations must be sorted by kind")
    return tuple(catalogs)


def load_catalog_definitions() -> tuple[CatalogDefinition, ...]:
    return _catalog_definitions(_read_registry())


def _records(
    document: dict[str, object], catalogs: tuple[CatalogDefinition, ...]
) -> tuple[dict[str, object], ...]:
    raw_records = document.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("public registry records must be a list")

    if not all(isinstance(record, dict) for record in raw_records):
        raise ValueError("public registry records must be objects")
    records = tuple(cast(dict[str, object], record) for record in raw_records)
    if len(records) != 51:
        raise ValueError(
            f"expected 51 explicitly approved records, found {len(records)}"
        )
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
        if catalog not in {definition.kind for definition in catalogs}:
            raise ValueError(f"unknown catalog for {record.get('id')}: {catalog}")
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path.startswith(
            "contracts/records/"
        ):
            raise ValueError(f"record path escapes the public registry: {raw_path}")
        path = ROOT / raw_path
        if path.parent != RECORD_ROOT or path.suffix != ".json":
            raise ValueError(f"record path is not a direct JSON child: {raw_path}")
        if path.is_symlink() or path.resolve() != RECORD_ROOT.resolve() / path.name:
            raise ValueError(f"record path is redirected: {raw_path}")
        registered_paths.add(path)
        data = path.read_bytes()
        if _sha256(data) != record.get("public_sha256"):
            raise ValueError(f"public record hash drift: {raw_path}")
        payload = json.loads(data)
        if payload.get("id") != record.get("id"):
            raise ValueError(f"record identifier mismatch: {raw_path}")
        if str(payload.get("version")) != record.get("version"):
            raise ValueError(f"record version mismatch: {raw_path}")

    discovered_paths = set(RECORD_ROOT.iterdir())
    if discovered_paths != registered_paths:
        missing = sorted(path.name for path in registered_paths - discovered_paths)
        denied = sorted(path.name for path in discovered_paths - registered_paths)
        raise ValueError(
            f"default-deny record mismatch; missing={missing}; denied={denied}"
        )
    counts = Counter(cast(str, record["catalog"]) for record in records)
    if any(counts[item.kind] != item.expected_record_count for item in catalogs):
        raise ValueError(f"catalog count drift: {dict(counts)}")
    return records


def load_registry() -> tuple[dict[str, object], ...]:
    document = _read_registry()
    return _records(document, _catalog_definitions(document))


def expected_outputs() -> dict[Path, bytes]:
    document = _read_registry()
    catalogs = _catalog_definitions(document)
    records = _records(document, catalogs)
    outputs: dict[Path, bytes] = {}
    for definition in catalogs:
        entries = [
            json.loads((ROOT / cast(str, record["path"])).read_text(encoding="utf-8"))
            for record in records
            if record["catalog"] == definition.kind
        ]
        catalog = {
            "_generated": True,
            "canonical_sources": ["contracts/public-registry.json"],
            "entries": entries,
            "interface_version": "1",
            "kind": definition.kind,
            "note": definition.note,
            "status": definition.status,
        }
        outputs[OUTPUT_ROOT / definition.filename] = _json_bytes(catalog)
    return outputs


def generate(*, check: bool) -> None:
    expected = expected_outputs()
    unexpected = set(OUTPUT_ROOT.glob("*.json")) - set(expected)
    if unexpected:
        raise ValueError("unregistered generated catalog")
    failures: list[str] = []
    for path, content in expected.items():
        if check:
            if not path.exists() or path.read_bytes() != content:
                failures.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
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
