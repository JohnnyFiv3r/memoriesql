"""Generate the explicit revision-2 relation assessment record; no discovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import relation_assessment as ra
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-relation-assessment-v2.json"


def record() -> dict[str, object]:
    registry = ra.load_repairable_relation_assessment_task_registry(
        BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
    )
    models = (ra.RepairableRelationAssessmentInput, ra.ActivateRepairableRelationAssessment,
              ra.RepairableRelationActivationReceipt, ra.ApplyRepairableRelationAssessment)
    return dict(
        id="memoriesql.relation-assessment.v2", version="2", kind="json_schema",
        status="available",
        summary="Revision-2 relation assessment: revision 1 with one more request, for one evidence-free author repair frame that may only complete missing pair dispositions and drop proposals with unregistered labels.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            task_definition=ra.REPAIRABLE_RELATION_ASSESSMENT_TASK.canonical_payload(),
            task_contract_hash=ra.REPAIRABLE_RELATION_ASSESSMENT_TASK.contract_hash,
            registry_hash=registry.registry_hash,
            unchanged_from_revision_1="packets, specialist, output contract, deliveries, apply, run tree and render semantics",
            request_headroom="request_limit 3: the author, at most one author repair frame and the batched specialist; the per-attempt dollar ceiling is unchanged",
            repair_frame=dict(
                keys=["repair_frame", "instruction", "relation_packet", "previous_output", "refusal_codes"],
                instruction=ra.RELATION_REPAIR_INSTRUCTION,
                relation_packet="the author packet this attempt already delivered, without its evidence",
                refusal_codes=["relation.pair_missing: <first bead>/<second bead>", "relation.label_unregistered: <proposal>"],
                edit_rules="proposals unchanged except drops of listed proposals; dispositions already given unchanged; new dispositions only for listed pairs, related where an unchanged proposal relates the pair or not_assessed with a reason; anything else settles invalid_output",
                records="no relation delivery, exposure or attestation; intent and usage as for any request",
            ),
            opt_in="activate_relation_assessment_v2; revision-1 tasks keep their pins and two requests",
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
            raise SystemExit("repairable relation assessment contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
