"""Generate the explicit revision-8 complete-input execution record; no discovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import complete_input_execution as ci
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-complete-input-execution-v2.json"


def record() -> dict[str, object]:
    registry = ci.load_repairable_complete_input_task_registry(
        BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
    )
    models = (ci.ActivateRepairableCompleteInput, ci.RepairableActivationReceipt,
              ci.RepairableExecutionInput, ci.CompleteExecutionOutput,
              ci.ApplyRepairableCompleteInput)
    return dict(
        id="memoriesql.complete-input-execution.v2", version="2", kind="json_schema",
        status="available",
        summary="Revision-8 complete-input execution: revision 2 with one more request, so a unit that uses all eight inspection windows keeps one request for the evidence-free repair round.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            task_definition=ci.REPAIRABLE_EXECUTION_TASK.canonical_payload(),
            task_contract_hash=ci.REPAIRABLE_EXECUTION_TASK.contract_hash,
            registry_hash=registry.registry_hash,
            unchanged_from_revision_2="inputs, output contract, trusted exposure, apply and render semantics",
            request_headroom="request_limit 9: up to eight inspection windows and one evidence-free repair frame (guard audit P1-f D1/D2); the per-attempt dollar ceiling is unchanged",
            opt_in="activate_complete_input_v7 under a dispatch policy provisioned for execution revision 8; revision-2 tasks, executions and policies keep their pins",
            production_provisioning="deferred_default_deny",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("repairable complete input contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
