"""Generate only the two explicit public forward execution/reader records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from memoriesql.application import complete_input_execution as ci
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]


def records() -> dict[str, dict[str, Any]]:
    registry = ci.load_complete_input_task_registry(
        BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
    )
    models = (
        ci.ActivateCompleteInput,
        ci.CompleteInputActivationReceipt,
        ci.CompleteExecutionInput,
        ci.CompleteExecutionOutput,
        ci.ApplyCompleteInput,
        ci.EvidenceExecutionWindow,
        ci.InspectionCheckpoint,
    )
    return {
        "memoriesql-complete-input-execution-v1.json": {
            "id": "memoriesql.complete-input-execution.v1",
            "version": "1",
            "kind": "json_schema",
            "status": "available",
            "summary": "Explicit schema-17 binding transfer to revision-2 complete-input execution, trusted attempt-bound exposure and fenced canonical application.",
            "contract": {
                "schemas": {m.__name__: m.model_json_schema() for m in models},
                "task_definition": ci.COMPLETE_EXECUTION_TASK.canonical_payload(),
                "task_contract_hash": ci.COMPLETE_EXECUTION_TASK.contract_hash,
                "registry_hash": registry.registry_hash,
                "window_characters": ci.WINDOW_CHARACTERS,
                "window_canonical_json_bytes": ci.WINDOW_JSON_BYTES,
                "working_notes_characters": ci.CHECKPOINT_CHARACTERS,
                "trust_boundary": "administrator_reviewed_separate_dispatch_attestor; actual_request_messages_after_successful_provider_return",
                "coverage": "all_ordered_intervals_of_entire_original_sealed_inventory_in_one_live_attempt",
                "comprehension_proven": False,
                "source_completeness_independently_proven": False,
                "production_provisioning": "deferred_default_deny",
                "transaction_isolation": "read_committed",
            },
        },
        "memoriesql-evidence-package-reader-v2.json": {
            "id": "memoriesql.evidence-package-reader.v2",
            "version": "2",
            "kind": "json_schema",
            "status": "available",
            "summary": "Authorized bounded lossless batches of existing sealed evidence parts; reads are not execution exposure.",
            "contract": {
                "schemas": {
                    m.__name__: m.model_json_schema()
                    for m in (ci.ReadCompleteEvidence, ci.CompleteEvidenceBatch)
                },
                "parts_per_operation": ci.READER_PART_LIMIT,
                "response_canonical_json_bytes": ci.READER_RESPONSE_BYTES,
                "maximum_text_utf8_bytes_per_operation": ci.READER_PART_LIMIT * 65536,
                "storage_contract": "memoriesql.evidence-package.v1",
                "transaction_isolation": "read_committed",
                "read_is_exposure": False,
                "provider_calls_per_page": None,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for filename, record in records().items():
        path = ROOT / "contracts/records" / filename
        text = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        if args.check:
            if path.read_text() != text:
                raise SystemExit("complete input contract drift: " + filename)
        else:
            path.write_text(text)


if __name__ == "__main__":
    main()
