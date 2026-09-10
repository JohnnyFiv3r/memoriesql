"""Generate the explicitly registered public logical-unit binding contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import logical_unit_materialization as lu

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-logical-unit-materialization-v1.json"


def record() -> dict[str, object]:
    models = (
        lu.MaterializeLogicalUnit,
        lu.LogicalUnitMaterializationReceipt,
        lu.InspectLogicalEvent,
        lu.LogicalEventProgress,
        lu.CompleteUnitTaskInput,
    )
    return {
        "id": "memoriesql.logical-unit-materialization.v1",
        "version": "1",
        "kind": "json_schema",
        "status": "available",
        "summary": "Atomic qualified occurrence materialization and exact sealed-package task binding; complete-input execution is unavailable.",
        "contract": {
            "schemas": {model.__name__: model.model_json_schema() for model in models},
            "binding_spec": lu.COMPLETE_UNIT_BINDING_SPEC,
            "task_contract_hash": lu.COMPLETE_UNIT_TASK_CONTRACT_HASH,
            "registry_hash": lu.COMPLETE_UNIT_REGISTRY_HASH,
            "command_json_bytes": lu.MATERIALIZATION_COMMAND_MAX_BYTES,
            "event_metadata_json_bytes": lu.MATERIALIZATION_METADATA_MAX_BYTES,
            "execution_available": False,
            "source_completeness_independently_proven": False,
            "producer_policy": "administrator_reviewed_scoped_trust_required",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("logical unit materialization schema drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
