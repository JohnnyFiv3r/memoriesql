"""Generate only the explicit versioned classified-authorship public record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import bead_classification as bc
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-bead-classification-v1.json"


def record() -> dict[str, object]:
    registry = bc.load_classification_task_registry(
        BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
    )
    models = (
        bc.BeadTypePin,
        bc.BeadTypeDefinition,
        bc.ClassificationDecision,
        bc.ClassificationScore,
        bc.AlignmentScore,
        bc.ClassificationPacket,
        bc.ClassificationContribution,
        bc.ClassificationEvidenceReference,
        bc.ClassificationAuthorStep,
        bc.ClassificationExecutionInput,
        bc.ClassifiedExecutionOutput,
        bc.ActivateClassifiedAuthorship,
        bc.ClassificationActivationReceipt,
        bc.ApplyClassifiedAuthorship,
    )
    return dict(
        id="memoriesql.bead-classification.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Attributable bead classification through revision-5 authorship; immutable accepted contribution and explicit abstention.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            task_definition=bc.CLASSIFICATION_TASK.canonical_payload(),
            task_contract_hash=bc.CLASSIFICATION_TASK.contract_hash,
            registry_hash=registry.registry_hash,
            storage="bead_versions.classification_contribution; accepted bead type and vocabulary revision retain existing foreign keys. request_id identifies the one contribution and links existing intent/run/policy/usage. Task receipt distinguishes unsupported legacy, unaccepted work and accepted meaning.",
            bounds="One accepted contribution, at most 32 vocabulary definitions, 8 exact evidence selections, 262144 UTF-8 packet bytes, 12000 aggregate canonical output bytes. No truncation. Missing or withheld data is not empty.",
            ownership="Writes and migration 0023 are P-owned. Authorized stored-result reads and any later migration are Q-owned. No inference-on-read.",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != data:
            raise SystemExit("classification contract drift")
    else:
        PATH.write_text(data)


if __name__ == "__main__":
    main()
