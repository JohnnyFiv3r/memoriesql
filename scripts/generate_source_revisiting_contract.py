"""Generate only the explicit forward author-controlled revisiting record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import source_revisiting as sr
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-source-revisiting-v1.json"


def record() -> dict[str, object]:
    registry = sr.load_source_revisiting_task_registry(
        BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
    )
    models = (
        sr.ActivateSourceRevisiting,
        sr.SourceRevisitingActivationReceipt,
        sr.RevisitingExecutionInput,
        sr.SourceDelivery,
        sr.ReadSourceEvidence,
        sr.SourceEvidenceRead,
        sr.SourceAuthorStep,
        sr.ApplySourceRevisiting,
    )
    return dict(
        id="memoriesql.source-revisiting.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Explicit revision-3 complete authorship with pinned typed source revisits, separately accounted delivery and unique mandatory coverage.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            task_definition=sr.SOURCE_REVISITING_TASK.canonical_payload(),
            task_contract_hash=sr.SOURCE_REVISITING_TASK.contract_hash,
            registry_hash=registry.registry_hash,
            delivery_units=sr.DELIVERY_UNITS,
            delivery_json_bytes=sr.DISPATCH_JSON_BYTES + 2048,
            trust_boundary="Separately qualified revision-3 dispatch attestor validates actual request delivery after accounted successful return; no production trust or provider admission.",
            coverage="Union of exact target normalized intervals in one live attempt; raw/optional/repeated delivery never adds duplicate required coverage.",
            comprehension_proven=False,
            source_completeness_independently_proven=False,
            semantic_policy="Existing caller-supplied registered instructions; navigation contract adds no semantic selection policy.",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("source revisiting contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
