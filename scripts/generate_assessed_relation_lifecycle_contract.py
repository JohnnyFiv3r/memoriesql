"""Generate the explicitly approved assessed lifecycle contract, without discovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from memoriesql.application import assessed_relation_lifecycle as lifecycle

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-assessed-relation-lifecycle-v1.json"


def record() -> dict[str, object]:
    models = (
        lifecycle.RecordAssessedRelationEvent,
        lifecycle.AssessedRelationEventReceipt,
        lifecycle.AssessedRelationEventRefusal,
        lifecycle.InspectBeadRelationsV3,
        lifecycle.BeadRelationsInspectionV3,
        lifecycle.RelationInspectionUnavailable,
        lifecycle.RelationInspectionRefusal,
    )
    return dict(
        id="memoriesql.assessed-relation-lifecycle.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Human-only append-only assessed relation governance, canonical current/as-of eligibility and qualified roots; no model work.",
        contract=dict(
            approved_commit="2baffc688291dbfdeccaa46f7ca05f6e9ef0e12c",
            approved_document_sha256="69a0d985466c6610a3a2abc73bcef9242749ff9535cb709e32867c7e06414d2a",
            approval_ref="https://github.com/JohnnyFiv3r/memoriesql/pull/46#issuecomment-5849423968",
            schemas={m.__name__: m.model_json_schema() for m in models},
            event_result=TypeAdapter(lifecycle.EventResult).json_schema(),
            inspection_result=TypeAdapter(lifecycle.InspectionResult).json_schema(),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("assessed lifecycle contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
