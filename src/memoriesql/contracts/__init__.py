"""Read-only access to generated memoriesQL public contract catalogs."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import cast

CATALOG_KINDS = (
    "cli",
    "connector",
    "http_openapi",
    "json_schema",
    "mcp",
    "module",
    "python",
    "sql_recall_operations",
)


class CatalogNotFoundError(LookupError):
    """Raised when the requested public catalog is not bundled."""


class ContractNotFoundError(LookupError):
    """Raised when the requested public contract identifier is not bundled."""


def _normalize_kind(kind: str) -> str:
    normalized = kind.strip().lower().replace("-", "_")
    if normalized not in CATALOG_KINDS:
        raise CatalogNotFoundError(
            f"unknown catalog {kind!r}; expected one of {', '.join(CATALOG_KINDS)}"
        )
    return normalized


def available_catalogs() -> tuple[str, ...]:
    """Return the fixed catalog kinds bundled in this distribution."""

    return CATALOG_KINDS


def load_catalog(kind: str) -> dict[str, object]:
    """Load one generated catalog by kind without external I/O or discovery."""

    normalized = _normalize_kind(kind)
    resource_name = f"{normalized.replace('_', '-')}.json"
    resource = files(__package__).joinpath("_catalogs", resource_name)
    document = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("kind") != normalized:
        raise RuntimeError(f"bundled catalog integrity failure: {resource_name}")
    return cast(dict[str, object], document)


def iter_contracts(
    *, kind: str | None = None, status: str | None = None
) -> tuple[dict[str, object], ...]:
    """Return contract entries in stable catalog and identifier order."""

    kinds = (_normalize_kind(kind),) if kind is not None else CATALOG_KINDS
    entries: list[dict[str, object]] = []
    for catalog_kind in kinds:
        raw_entries = load_catalog(catalog_kind).get("entries")
        if not isinstance(raw_entries, list):
            raise RuntimeError(f"bundled catalog has invalid entries: {catalog_kind}")
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                raise RuntimeError(
                    f"bundled catalog has an invalid contract entry: {catalog_kind}"
                )
            entry = cast(dict[str, object], raw_entry)
            if status is None or entry.get("status") == status:
                entries.append(entry)
    return tuple(
        sorted(entries, key=lambda item: str(item.get("id", "")))
    )


def get_contract(identifier: str) -> dict[str, object]:
    """Return one exact generated contract entry by public identifier."""

    matches = tuple(
        entry for entry in iter_contracts() if entry.get("id") == identifier
    )
    if not matches:
        raise ContractNotFoundError(f"unknown contract identifier: {identifier}")
    if len(matches) != 1:
        raise RuntimeError(f"duplicate bundled contract identifier: {identifier}")
    return matches[0]


def contract_inventory(
    *, kind: str | None = None, status: str | None = None
) -> dict[str, object]:
    """Return the content-free developer-preview inventory as JSON-ready data."""

    kinds = (_normalize_kind(kind),) if kind is not None else CATALOG_KINDS
    catalogs = [load_catalog(catalog_kind) for catalog_kind in kinds]
    if status is not None:
        for catalog in catalogs:
            entries = catalog.get("entries")
            if not isinstance(entries, list):
                raise RuntimeError("bundled catalog has invalid entries")
            catalog["entries"] = [
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("status") == status
            ]
    return {
        "distribution": "memoriesql",
        "preview_status": "experimental_pre_alpha",
        "compatibility_guarantee": False,
        "desktop_product_included": False,
        "catalogs": catalogs,
    }


__all__ = [
    "CATALOG_KINDS",
    "CatalogNotFoundError",
    "ContractNotFoundError",
    "available_catalogs",
    "contract_inventory",
    "get_contract",
    "iter_contracts",
    "load_catalog",
]
