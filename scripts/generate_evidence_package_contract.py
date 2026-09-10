"""Generate the one explicitly registered public-authored evidence contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import evidence_packages as ep

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-evidence-package-v1.json"


def record() -> dict[str, object]:
    models = (
        ep.CreateEvidencePackage,
        ep.AppendEvidencePart,
        ep.SealEvidencePackage,
        ep.InspectEvidencePackage,
        ep.PageEvidenceInventory,
        ep.ReadEvidencePart,
        ep.PackageReceipt,
        ep.PackageStatus,
        ep.InventoryPage,
        ep.EvidencePartPage,
    )
    return {
        "id": "memoriesql.evidence-package.v1",
        "version": "1",
        "kind": "json_schema",
        "status": "available",
        "summary": "Immutable evidence inventories and bounded authorized reads; source completeness remains producer-attested, with no semantic execution.",
        "contract": {
            "schemas": {model.__name__: model.model_json_schema() for model in models},
            "limits": {
                "parts": ep.PACKAGE_MAX_PARTS,
                "part_utf8_bytes": ep.PART_MAX_UTF8_BYTES,
                "package_utf8_bytes": ep.PACKAGE_MAX_UTF8_BYTES,
                "lineage_slices_per_part": ep.PART_MAX_LINEAGE,
                "source_bytes_per_append": ep.PART_MAX_SOURCE_BYTES,
                "metadata_json_bytes": ep.METADATA_MAX_JSON_BYTES,
                "command_json_bytes": ep.COMMAND_MAX_JSON_BYTES,
                "inventory_page_parts": ep.INVENTORY_PAGE_MAX_PARTS,
                "read_characters": ep.READ_MAX_CHARACTERS,
                "response_json_bytes": ep.RESPONSE_MAX_JSON_BYTES,
                "operation_timeout_ms": ep.OPERATION_TIMEOUT_MS,
                "lock_timeout_ms": ep.LOCK_TIMEOUT_MS,
            },
            "source_completeness_independently_proven": False,
            "server_reads_prove_author_exposure": False,
            "creates_observations_or_tasks": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("evidence package schema drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
